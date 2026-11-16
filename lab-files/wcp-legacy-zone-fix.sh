#!/bin/bash
# wcp-legacy-zone-fix.sh
#
# Run this on the console just BEFORE you click SWITCHOVER NOW for the
# vc-wld01-b upgrade to 9.1.1. Leave it running until it says it is done.
#
# Why: the Supervisor on cluster-wld01-01b was deployed on vSphere Zone
# z-wld01-b. When the 9.1.1 vCenter starts during switchover, the Supervisor
# service (wcp) expects the cluster to be in a zone called
# vmware-system-legacy-<cluster-id>. A cluster can only be in one zone, so wcp
# waits, the service start-up times out after about 15 minutes and the
# upgrade rolls back to 8.0. The zone cannot be changed on 8.0 because the
# running Supervisor locks it, so this script waits for the new 9.1.1
# appliance and moves the cluster to the legacy zone there.
#
# Usage: ./wcp-legacy-zone-fix.sh [vcenter-fqdn]

VC=${1:-vc-wld01-b.site-b.vcf.lab}
USER_NAME='administrator@wld.sso'
PASSWORD='VMware123!VMware123!'
H=https://$VC
LOG=~/wcp-legacy-zone-fix-$(date +%Y%m%d-%H%M).log
MAX_MINUTES=180

say() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }

version() {
  curl -sk -m 15 -H 'Content-Type: text/xml' -H 'SOAPAction: urn:vim25/8.0' \
    -d '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:v="urn:vim25"><soapenv:Body><v:RetrieveServiceContent><v:_this type="ServiceInstance">ServiceInstance</v:_this></v:RetrieveServiceContent></soapenv:Body></soapenv:Envelope>' \
    $H/sdk 2>/dev/null | grep -oE '<version>[^<]*' | cut -d'>' -f2
}

jget() { python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
$1"; }

say "Watching $VC for the 9.1.1 appliance (log: $LOG)"
say "Click SWITCHOVER NOW in VCF Operations when it is offered. Leave this window open."
end=$(( $(date +%s) + MAX_MINUTES*60 ))
last=""
while [ $(date +%s) -lt $end ]; do
  v=$(version)
  [ "$v" != "$last" ] && { say "vCenter version answering: ${v:-no answer}"; last=$v; }
  case "$v" in 9.*) ;; *) sleep 15; continue ;; esac

  SID=$(curl -sk -m 20 -X POST -u "$USER_NAME:$PASSWORD" $H/api/session 2>/dev/null | tr -d '"')
  if [ ${#SID} -lt 20 ]; then sleep 15; continue; fi
  A=(-sk -m 30 -H "vmware-api-session-id: $SID" -H 'Content-Type: application/json')

  clusters=$(curl "${A[@]}" $H/api/vcenter/cluster | jget 'print(" ".join(c["cluster"] for c in d))')
  [ -z "$clusters" ] && { sleep 15; continue; }

  all_ok=1
  for c in $clusters; do
    legacy="vmware-system-legacy-$c"
    cur=$(curl "${A[@]}" "$H/api/vcenter/consumption-domains/zone-associations/cluster?clusters=$c" \
          | jget 'print(" ".join(a["zone"] for a in d.get("associations",[])))')
    if [ -z "$cur" ]; then
      say "$c is not in any zone, nothing to do"; continue
    fi
    if [ "$cur" = "$legacy" ]; then
      say "$c is already in $legacy"; continue
    fi
    say "$c is in zone '$cur'; moving it to $legacy"
    zones=$(curl "${A[@]}" $H/api/vcenter/consumption-domains/zones | jget 'print(" ".join(z["zone"] for z in d.get("items",[])))')
    if ! echo " $zones " | grep -q " $legacy "; then
      r=$(curl "${A[@]}" -w ' HTTP%{http_code}' -X POST \
          -d "{\"zone\":\"$legacy\",\"description\":\"Legacy zone for the Supervisor on $c (9.1 upgrade)\"}" \
          $H/api/vcenter/consumption-domains/zones)
      say "  create zone $legacy: $r"
    fi
    for z in $cur; do
      r=$(curl "${A[@]}" -X POST -d "[\"$c\"]" "$H/api/vcenter/consumption-domains/zones/cluster/$z/associations?action=remove")
      say "  remove $c from $z: $r"
    done
    r=$(curl "${A[@]}" -X POST -d "[\"$c\"]" "$H/api/vcenter/consumption-domains/zones/cluster/$legacy/associations?action=add")
    say "  add $c to $legacy: $r"
    now=$(curl "${A[@]}" "$H/api/vcenter/consumption-domains/zone-associations/cluster?clusters=$c" \
          | jget 'print(" ".join(a["zone"] for a in d.get("associations",[])))')
    if [ "$now" = "$legacy" ]; then say "  done: $c is now in $legacy"; else say "  not yet ($now), will retry"; all_ok=0; fi
  done
  if [ $all_ok = 1 ]; then
    say "Finished. The Supervisor service can now start; the switchover will continue on its own."
    exit 0
  fi
  sleep 15
done
say "Gave up after $MAX_MINUTES minutes without finding the 9.1.1 appliance."
exit 1
