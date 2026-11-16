#!/bin/bash
# supervisor-dns-fix.sh
#
# Gives the Supervisor's workload network its own DNS server address, so it
# no longer shares 10.2.1.1 with the management network.
#
# Why: the 9.1 Supervisor control plane VMs route all traffic for the
# workload DNS server out of their workload network interface, and they use
# strict reverse-path filtering. When the management and workload networks
# use the same DNS address, replies to management DNS lookups come back on
# the "wrong" interface and are dropped. The Supervisor then reports
# "Unable to connect to the management DNS servers" and a Supervisor update
# stops at 50%. 10.1.1.1 is the same lab DNS server (the Holorouter), reached
# through a different address, so the answers do not change.
#
# Safe to run more than once. Run it before updating the Supervisor.
# Usage: ./supervisor-dns-fix.sh [vcenter-fqdn] [workload-dns-ip]

VC=${1:-vc-wld01-b.site-b.vcf.lab}
NEW_DNS=${2:-10.1.1.1}
USER_NAME='administrator@wld.sso'
PASSWORD='VMware123!VMware123!'
H=https://$VC

say() { echo "$(date +%H:%M:%S) $*"; }
jget() { python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
$1"; }

SID=$(curl -sk -m 20 -X POST -u "$USER_NAME:$PASSWORD" $H/api/session | tr -d '"')
[ ${#SID} -lt 20 ] && { say "Could not log in to $VC"; exit 1; }
A=(-sk -m 30 -H "vmware-api-session-id: $SID" -H 'Content-Type: application/json')

if ! dig +time=3 +tries=1 @$NEW_DNS $VC +short | grep -q .; then
  say "$NEW_DNS does not answer DNS queries for $VC; nothing changed"; exit 1
fi

for c in $(curl "${A[@]}" $H/api/vcenter/namespace-management/clusters | jget 'print(" ".join(c["cluster"] for c in d))'); do
  read -r mgmt work status <<<"$(curl "${A[@]}" $H/api/vcenter/namespace-management/clusters/$c | \
    jget 'print(",".join(d.get("master_DNS") or []), ",".join(d.get("worker_DNS") or []), d.get("config_status"))')"
  say "Supervisor on $c: management DNS $mgmt, workload DNS $work, status $status"
  if [ "$work" = "$NEW_DNS" ]; then say "  already done"; continue; fi
  if ! echo ",$mgmt," | grep -q ",$work,"; then say "  workload DNS differs from management DNS already; leaving it"; continue; fi
  r=$(curl "${A[@]}" -w ' HTTP%{http_code}' -X PATCH -d "{\"worker_DNS\":[\"$NEW_DNS\"]}" $H/api/vcenter/namespace-management/clusters/$c)
  say "  set workload DNS to $NEW_DNS: $r"
  for i in $(seq 1 40); do
    sleep 15
    read -r work status <<<"$(curl "${A[@]}" $H/api/vcenter/namespace-management/clusters/$c | jget 'print(",".join(d.get("worker_DNS") or []), d.get("config_status"))')"
    [ "$work" = "$NEW_DNS" ] && [ "$status" = "RUNNING" ] && { say "  done: workload DNS $work, status $status"; break; }
    [ $i = 40 ] && say "  still $status after 10 minutes; check the Supervisor in the vSphere Client"
  done
done
