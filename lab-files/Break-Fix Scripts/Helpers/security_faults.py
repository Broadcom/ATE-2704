"""Student security exercises. Invoked by PowerShell; secrets only on stdin.

No mutation occurs on import or Inspect. Original certificate keys stay in a
root-only backup on the VCSA; the Fix action retrieves the key into memory.
"""
from pathlib import Path
import base64
import datetime as dt
import fcntl
import hashlib
import io
import ipaddress
import json
import os
import shlex
import socket
import ssl
import subprocess
import sys
import time
import uuid
import zipfile
from urllib.parse import urlsplit, urlunsplit

import requests
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

ROOT = Path(__file__).resolve().parents[1]
NSX_ID = '11-NsxAdminPasswordExpired'
VC_ID = '12-VcenterCertificateTrust'
TLS_PATH = '/api/vcenter/certificate-management/vcenter/tls'


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def fingerprint(cert):
    return cert.fingerprint(hashes.SHA256()).hex()


def fetch_leaf(host, trust):
    ctx = ssl.create_default_context(cafile=str(trust))
    with socket.create_connection((host, 443), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as channel:
            return x509.load_der_x509_certificate(channel.getpeercert(binary_form=True))


class Appliance:
    def __init__(self, target, cfg, trust):
        self.target, self.cfg, self.trust = target, cfg, trust
        self.si = SmartConnect(host=target['Server'], user=cfg['user'],
                               pwd=cfg['password'], sslContext=ssl.create_default_context(cafile=str(trust)),
                               httpConnectionTimeout=25)
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = str(trust)
        self.content = self.si.RetrieveContent()
        if self.content.about.instanceUuid != target['InstanceUuid']:
            self.close()
            raise RuntimeError('vCenter UUID mismatch')
        view = self.content.viewManager.CreateContainerView(self.content.rootFolder, [vim.VirtualMachine], True)
        try:
            matches = [v for v in view.view if v.name == target['VmName']]
        finally:
            view.Destroy()
        if len(matches) != 1 or matches[0].config.instanceUuid != target['VmUuid']:
            self.close()
            raise RuntimeError('Appliance VM identity mismatch')
        self.vm = matches[0]
        if not self.vm.guest.guestOperationsReady:
            self.close()
            raise RuntimeError('VMware Tools Guest Operations is not ready')
        self.auth = vim.vm.guest.NamePasswordAuthentication(username='root', password=cfg['guest_password'], interactiveSession=False)
        self.pm = self.content.guestOperationsManager.processManager
        self.fm = self.content.guestOperationsManager.fileManager
        # Download public host CA roots over the already verified vCenter TLS connection.
        response = self.session.get('https://' + target['Server'] + '/certs/download.zip', timeout=30)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            roots = [archive.read(n).decode() for n in archive.namelist() if n.startswith('certs/lin/') and n.endswith('.0')]
        if not roots:
            raise RuntimeError('VMCA host trust bundle unavailable')
        self.transfer_trust = ROOT / 'State' / ('guest-ca-' + target['InstanceUuid'] + '.pem')
        self.transfer_trust.parent.mkdir(mode=0o700, exist_ok=True)
        self.transfer_trust.write_text(Path(trust).read_text() + '\n' + '\n'.join(roots))
        self.transfer_trust.chmod(0o600)

    def execute(self, script, timeout=75):
        # Only scripts without passwords/private keys are sent to Guest Operations.
        path = '/var/tmp/lab-security-' + uuid.uuid4().hex + '.json'
        command = 'umask 077; /usr/bin/python3 - <<\'LABPY\' > ' + path + ' 2>/dev/null\n' + script + '\nLABPY\n'
        pid = self.pm.StartProgramInGuest(self.vm, self.auth,
            vim.vm.guest.ProcessManager.ProgramSpec(programPath='/bin/bash', arguments='-lc ' + shlex.quote(command)))
        end = time.monotonic() + timeout
        while True:
            rows = self.pm.ListProcessesInGuest(self.vm, self.auth, pids=[pid])
            if rows and rows[0].endTime:
                code = rows[0].exitCode
                break
            if time.monotonic() > end:
                raise RuntimeError('Appliance operation timed out; saved state is retained')
            time.sleep(2)
        try:
            info = self.fm.InitiateFileTransferFromGuest(self.vm, self.auth, path)
            u = urlsplit(info.url)
            host = self.vm.runtime.host.name
            host_ip = next(v.spec.ip.ipAddress for v in self.vm.runtime.host.config.network.vnic if v.device == 'vmk0')
            if u.scheme != 'https' or u.hostname not in ('*', host, host_ip):
                raise RuntimeError('Unexpected Guest Operations transfer endpoint')
            url = urlunsplit((u.scheme, host + ':' + str(u.port or 443), u.path, u.query, ''))
            try:
                result = self.session.get(url, verify=str(self.transfer_trust), timeout=30)
                if result.status_code != 200:
                    raise RuntimeError('Guest result transfer failed')
                data = json.loads(result.text)
            except requests.RequestException:
                raise RuntimeError('Guest result transfer failed; transfer URL withheld') from None
            if code or data.get('error'):
                raise RuntimeError(data.get('error', 'Appliance action failed; inspect saved state'))
            return data
        finally:
            self.fm.DeleteFileInGuest(self.vm, self.auth, path)

    def close(self):
        if getattr(self, 'si', None):
            Disconnect(self.si)
        if getattr(self, 'session', None):
            self.session.close()


def nsx_user(appliance, reset=None, original=None):
    # Same loopback node API and root identity header used by the installed NSX CLI.
    # The transport is confined to the Manager guest, accessed through authenticated
    # vCenter Guest Operations. No SSH/root network login or PAM file edits.
    change = None if reset is None else {'password_reset_required': reset}
    expected_userid = original['userid'] if original else None
    script = '''import http.client,json
c=http.client.HTTPConnection('127.0.0.1',7441,timeout=30)
headers={'X-NSX-Username':'root','Content-Type':'application/json'}
def call(method,path,body=None):
 c.request(method,'/api/v1'+path,body=json.dumps(body) if body is not None else None,headers=headers)
 r=c.getresponse(); raw=r.read()
 if r.status!=200: raise RuntimeError('Local NSX node API HTTP '+str(r.status))
 return json.loads(raw)
users=call('GET','/node/users')['results']
users=[u for u in users if u.get('username')=='admin']
assert len(users)==1,'Expected one admin user'
u=users[0]
expected_userid=EXPECTED_USERID
assert expected_userid is None or u['userid']==expected_userid,'NSX admin user identity changed'
change=CHANGE
if change is not None:
 call('PUT','/node/users/'+str(u['userid']),change)
 u=call('GET','/node/users/'+str(u['userid']))
print(json.dumps({k:u.get(k) for k in ('userid','username','status','password_reset_required','password_change_frequency','password_change_warning')}))
'''.replace('CHANGE', repr(change)).replace('EXPECTED_USERID', repr(expected_userid))
    return appliance.execute(script)


def cert_backup(appliance, run_id, expected_sha, read_key=False):
    folder = '/root/lab-faults/' + str(uuid.UUID(run_id))
    script = '''import pathlib,subprocess,json,hashlib,ssl,os
os.umask(0o077)
p=pathlib.Path(FOLDER)
read_key=READ_KEY
if not read_key:
 p.mkdir(parents=True,mode=0o700,exist_ok=False)
 for action,name in [('getcert','original.crt'),('getkey','original.key')]:
  subprocess.run(['/usr/lib/vmware-vmafd/bin/vecs-cli','entry',action,'--store','MACHINE_SSL_CERT','--alias','__MACHINE_CERT','--output',str(p/name)],check=True,capture_output=True)
  (p/name).chmod(0o600)
cert=(p/'original.crt').read_text()
first=cert[:cert.index('-----END CERTIFICATE-----')+len('-----END CERTIFICATE-----')]
assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(first)).hexdigest()==EXPECTED_SHA,'Backup certificate fingerprint mismatch'
result={'backupDirectory':str(p),'certificate':cert}
if read_key:result['key']=(p/'original.key').read_text()
print(json.dumps(result))
'''.replace('FOLDER', repr(folder)).replace('READ_KEY', repr(read_key)).replace('EXPECTED_SHA', repr(expected_sha))
    return appliance.execute(script)


def certificate_action(cfg, target, trust, action, **fields):
    data = dict(server=target['Server'], uuid=target['InstanceUuid'], user=cfg['user'], password=cfg['password'], action=action, **fields)
    env = dict(os.environ, SSL_CERT_FILE=str(trust))
    result = subprocess.run(['pwsh', '-NoLogo', '-NoProfile', '-NonInteractive', '-File',
                             str(ROOT / 'Helpers/VCenterCertificateAction.ps1')],
                            input=json.dumps(data), capture_output=True, text=True, timeout=660, env=env)
    # Never forward PowerCLI error bodies, input, or key material to the terminal.
    if result.returncode:
        raise RuntimeError('Native PowerCLI certificate action failed: ' + action + '; saved state is retained')
    return result.stdout


def make_certificates(original, run_id, server, ip):
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Lab Fault CA ' + run_id)])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=5))
          .not_valid_after(now+dt.timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
          .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),critical=False).sign(ca_key,hashes.SHA256()))
    sans = list(original.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
    if x509.DNSName(server) not in sans: sans.append(x509.DNSName(server))
    if x509.IPAddress(ipaddress.ip_address(ip)) not in sans: sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
    leaf = (x509.CertificateBuilder().subject_name(original.subject).issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=5))
            .not_valid_after(now+dt.timedelta(days=364)).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName(sans),critical=False)
            .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,ExtendedKeyUsageOID.CLIENT_AUTH]),critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False).sign(ca_key,hashes.SHA256()))
    private = key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    return ca, leaf, private


def wait_certificate(target, trust, expected, seconds=240):
    end = time.monotonic()+seconds
    while True:
        try:
            if fingerprint(fetch_leaf(target['Server'], trust)) == expected:
                return
        except (OSError,ssl.SSLError):
            pass
        if time.monotonic() >= end:
            raise RuntimeError('Expected vCenter certificate did not become active; run Fix with the saved state')
        time.sleep(5)


def standalone_topology(appliance):
    session=appliance.session
    base='https://'+appliance.target['Server']
    response=session.post(base+'/api/session',auth=(appliance.cfg['user'],appliance.cfg['password']),timeout=30)
    if response.status_code!=201:raise RuntimeError('Native topology session creation failed')
    headers={'vmware-api-session-id':response.json()}
    try:
        response=session.get(base+'/api/vcenter/topology/nodes',headers=headers,timeout=30)
        if response.status_code!=200:raise RuntimeError('Cannot verify standalone vCenter SSO topology')
        nodes=response.json()
        if len(nodes)!=1 or nodes[0].get('replication_partners'):
            raise RuntimeError('Certificate exercise requires a standalone vCenter SSO topology')
        return {'nodeCount':len(nodes),'linkedMode':False}
    finally:
        session.delete(base+'/api/session',headers=headers,timeout=15)


def ensure_known_certificate(current, state):
    if current not in (state['OriginalSha256'], state.get('FaultSha256')):
        raise RuntimeError('Unexpected live certificate; refusing to overwrite a student-issued certificate')


def main(cfg):
    config = json.loads((ROOT/'LabConfig.json').read_text())
    scenario = next(s for s in config['Scenarios'] if s['Id']==cfg['id'])
    target = config['SecurityTargets'][cfg['id']]
    ca = ROOT/config['CertificateFile']
    state_dir = ROOT/'State'
    state_dir.mkdir(mode=0o700,exist_ok=True)
    state_dir.chmod(0o700)
    path = state_dir/(cfg['id']+'.json')
    with (state_dir/'security.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state = json.loads(path.read_text()) if path.exists() else None
        if cfg['action']=='Break':
            active=[p for p in state_dir.glob('*.json') if json.loads(p.read_text()).get('Status')!='Restored']
            if active: raise RuntimeError('Another fault is active or incomplete. Run its paired Fix first.')
        if cfg['action']=='Fix':
            if not state: raise RuntimeError('Original state is missing; refusing to guess recovery settings')
            if any(state.get(k)!=v for k,v in {'Id':cfg['id'],'Target':scenario['Target'],'Identity':target['InstanceUuid'],'VmUuid':target['VmUuid']}.items()):
                raise RuntimeError('Saved lab identity mismatch')
        trust=ca
        if cfg['id']==VC_ID and cfg['action']=='Fix' and state.get('FaultRootPem'):
            trust=state_dir/('recovery-ca-'+state['RunId']+'.pem')
            trust.write_text(ca.read_text()+'\n'+state['FaultRootPem']);trust.chmod(0o600)
            current=fingerprint(fetch_leaf(target['Server'],trust))
            ensure_known_certificate(current,state)
        appliance=Appliance(target,cfg,trust)
        try:
            is_nsx=cfg['id']==NSX_ID
            if not is_nsx and not appliance.content.about.version.startswith('8.0.3'):
                raise RuntimeError('Certificate exercise is validated for vCenter 8.0.3 only; revalidate after upgrading')
            if is_nsx:
                # This loopback transport is checked against the deployed NSX 4.2.1 build.
                version=appliance.execute("import http.client,json\nc=http.client.HTTPConnection('127.0.0.1',7441,timeout=20)\nc.request('GET','/api/v1/node/version',headers={'X-NSX-Username':'root'})\nr=c.getresponse();assert r.status==200,'NSX version unavailable'\nd=json.loads(r.read());print(json.dumps({'version':d.get('product_version')}))")
                if not str(version['version']).startswith('4.2.1'):
                    raise RuntimeError('Revalidate the native NSX node API transport after upgrading')
                original=nsx_user(appliance)
            else:
                original=fetch_leaf(target['Server'],trust)
                topology=standalone_topology(appliance)
                if cfg['action']=='Fix':ensure_known_certificate(fingerprint(original),state)
                # Authenticate root and verify the certificate store without exporting its key.
                appliance.execute("import subprocess,json\nr=subprocess.run(['/usr/lib/vmware-vmafd/bin/vecs-cli','entry','list','--store','MACHINE_SSL_CERT'],capture_output=True)\nassert r.returncode==0,'Machine SSL store unavailable'\nprint(json.dumps({'rootGuestOperations':True}))")
            if cfg['action']=='Inspect':
                if is_nsx:
                    result={'admin':original,'rootRecoveryVerified':True}
                else:
                    certificate_action(cfg,target,trust,'Inspect')
                    result={'certificateSha256':fingerprint(original),'rootBackupAccessVerified':True,'nativeCertificateCmdletsAvailable':True,'topology':topology}
                print(json.dumps({'Id':cfg['id'],'Target':scenario['Target'],'ReadOnly':True,**result}));return
            if cfg['action']=='Break':
                if is_nsx and (original['status']!='ACTIVE' or original['password_reset_required']):
                    raise RuntimeError('Admin must be active before starting this exercise')
                if state:
                    history=state_dir/'History';history.mkdir(mode=0o700,exist_ok=True)
                    save(history/(cfg['id']+'-'+state['RunId']+'.json'),state)
                state={'SchemaVersion':1,'Id':cfg['id'],'Kind':scenario['Kind'],'Scope':scenario['Scope'],'Target':scenario['Target'],
                       'Identity':target['InstanceUuid'],'VmUuid':target['VmUuid'],'RunId':str(uuid.uuid4()),'CreatedAtUtc':stamp(),
                       'RestoredAtUtc':None,'Status':'Prepared'}
                if is_nsx:state['Original']=original
                else:state['OriginalSha256']=fingerprint(original)
                save(path,state)
            try:
                if is_nsx:
                    desired=cfg['action']=='Break'
                    # Native API flag represents forced password expiry, not a password reset.
                    if not desired and original['status']=='ACTIVE' and not original['password_reset_required']:
                        if original['userid']!=state['Original']['userid']:raise RuntimeError('NSX admin user identity changed')
                        result=original
                    else:
                        result=nsx_user(appliance,desired,state['Original'])
                    if result['password_reset_required']!=desired or result['status']!=('PASSWORD_EXPIRED' if desired else 'ACTIVE'):
                        raise RuntimeError('Native NSX password state did not verify')
                elif cfg['action']=='Break':
                    # Keep the original private key only in a root-owned directory on VCSA.
                    backup=cert_backup(appliance,state['RunId'],state['OriginalSha256'])
                    state['BackupDirectory']=backup['backupDirectory'];save(path,state)
                    root,leaf,key=make_certificates(original,state['RunId'],target['Server'],target['Ip'])
                    state.update(FaultRootPem=pem(root),FaultRootSha256=fingerprint(root),FaultSha256=fingerprint(leaf))
                    save(path,state)  # Persist both identities before any trust/certificate mutation.
                    certificate_action(cfg,target,trust,'Trust',root=state['FaultRootPem'])
                    recovery=state_dir/('recovery-ca-'+state['RunId']+'.pem')
                    recovery.write_text(ca.read_text()+'\n'+state['FaultRootPem']);recovery.chmod(0o600)
                    try:certificate_action(cfg,target,trust,'Install',cert=pem(leaf)+pem(root),key=key)
                    except RuntimeError:
                        # A certificate reload can close the API response. Verify the actual outcome.
                        wait_certificate(target,recovery,state['FaultSha256'],seconds=90)
                    key=None
                    wait_certificate(target,recovery,state['FaultSha256'])
                    try:fetch_leaf(target['Server'],ca)
                    except ssl.SSLCertVerificationError:pass
                    else:raise RuntimeError('Expected client trust failure was not observed')
                else:
                    # Refuse to overwrite an unrelated replacement; the identity check ran before login.
                    if fingerprint(original)!=state['OriginalSha256']:
                        backup=cert_backup(appliance,state['RunId'],state['OriginalSha256'],read_key=True)
                        try:certificate_action(cfg,target,trust,'Install',cert=backup['certificate'],key=backup['key'])
                        except RuntimeError:wait_certificate(target,ca,state['OriginalSha256'],seconds=90)
                        finally:backup=None
                    wait_certificate(target,ca,state['OriginalSha256'])
                    if state.get('FaultRootSha256'):
                        certificate_action(cfg,target,ca,'RemoveRoot',rootSha256=state['FaultRootSha256'])
                state['Status']='Active' if cfg['action']=='Break' else 'Restored'
                if cfg['action']=='Fix':state['RestoredAtUtc']=stamp()
                save(path,state)
                print(json.dumps({'Id':cfg['id'],'Action':cfg['action'],'Target':scenario['Target'],'Verified':True,'StateFile':str(path)}))
            except Exception:
                state['Status']='ApplyFailed' if cfg['action']=='Break' else 'RestoreFailed';save(path,state);raise
        finally:appliance.close()


if __name__=='__main__':
    os.umask(0o077)
    try:
        main(json.load(sys.stdin))
    except Exception as exc:
        # Known operational errors are deliberately credential-free. Library errors
        # may include URLs/tickets, so only disclose their type.
        safe=str(exc) if type(exc) is RuntimeError else type(exc).__name__+'; detailed output withheld'
        print(safe,file=sys.stderr)
        raise SystemExit(1)
