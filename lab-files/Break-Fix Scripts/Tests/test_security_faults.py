"""Offline tests only: generate certificates and check recovery boundaries."""
from pathlib import Path
import datetime as dt
import importlib.util
import subprocess
import tempfile
import unittest
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.x509.oid import NameOID

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('lab_security',ROOT/'Helpers/security_faults.py')
security=importlib.util.module_from_spec(spec);spec.loader.exec_module(security)

class CertificateExerciseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        now=dt.datetime.now(dt.timezone.utc);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'vc-mgmt-a.site-a.vcf.lab')])
        original=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(days=1))
                  .not_valid_after(now+dt.timedelta(days=30))
                  .add_extension(x509.SubjectAlternativeName([x509.DNSName('vc-mgmt-a.site-a.vcf.lab')]),critical=False)
                  .sign(key,hashes.SHA256()))
        cls.ca,cls.leaf,cls.private=security.make_certificates(original,str(uuid.uuid4()),'vc-mgmt-a.site-a.vcf.lab','10.1.1.10')

    def test_fault_is_untrusted_issuer_not_invalid_name_or_expired(self):
        self.ca.public_key().verify(self.leaf.signature,self.leaf.tbs_certificate_bytes,padding.PKCS1v15(),self.leaf.signature_hash_algorithm)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'leaf.pem').write_text(security.pem(self.leaf));(p/'ca.pem').write_text(security.pem(self.ca))
            good=subprocess.run(['openssl','verify','-CAfile',str(p/'ca.pem'),'-verify_hostname','vc-mgmt-a.site-a.vcf.lab',str(p/'leaf.pem')],capture_output=True)
            self.assertEqual(good.returncode,0,good.stderr.decode())
            bad=subprocess.run(['openssl','verify','-CAfile',str(ROOT/'Certificates/LabRootCA.crt'),str(p/'leaf.pem')],capture_output=True)
            self.assertNotEqual(bad.returncode,0)
            self.assertIn(b'unable to get local issuer certificate',bad.stderr)
        key=serialization.load_pem_private_key(self.private.encode(),password=None)
        self.assertEqual(key.public_key().public_numbers(),self.leaf.public_key().public_numbers())
        self.assertFalse(self.leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)

    def test_recovery_accepts_original_and_own_fault_only(self):
        state={'OriginalSha256':'original','FaultSha256':'exercise'}
        security.ensure_known_certificate('original',state)
        security.ensure_known_certificate('exercise',state)
        with self.assertRaises(RuntimeError):security.ensure_known_certificate('student-replacement',state)

    def test_prepared_but_unmodified_certificate_can_be_recovered(self):
        security.ensure_known_certificate('original',{'OriginalSha256':'original'})
        with self.assertRaises(RuntimeError):security.ensure_known_certificate('another',{'OriginalSha256':'original'})

if __name__=='__main__':unittest.main(verbosity=2)
