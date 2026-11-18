#!/usr/bin/env bash
# set-ops-thumbprint.sh - put the current VCF Operations (ops-a) certificate
# thumbprint into the Lab 2 converge specification.
#
# VCF Operations (ops-a) is deployed in Lab 1 and gets its own self-signed
# certificate, so its SHA-256 thumbprint is different in every lab. The
# converge specification needs the thumbprint of the ops-a that is running
# now, or the VCF Installer validation fails on it.
#
# Usage (console terminal):
#   ~/"Lab Files"/set-ops-thumbprint.sh
#   ~/"Lab Files"/set-ops-thumbprint.sh /path/to/spec.json    # another file
set -euo pipefail

HOST=ops-a.site-a.vcf.lab
SPEC=${1:-"$HOME/Lab Files/VCF-9.1.1-converge-from-vSphere-9.1.1.json"}

[ -f "$SPEC" ] || { echo "Specification not found: $SPEC" >&2; exit 1; }

TP=$(echo | timeout 15 openssl s_client -connect "$HOST:443" -servername "$HOST" 2>/dev/null \
     | openssl x509 -noout -fingerprint -sha256 2>/dev/null | sed 's/^.*=//')
if [ -z "$TP" ]; then
    echo "Could not read the certificate of $HOST. Is VCF Operations running?" >&2
    exit 1
fi

python3 - "$SPEC" "$HOST" "$TP" <<'PY'
import json, sys
path, host, tp = sys.argv[1:4]
with open(path) as f:
    spec = json.load(f)
nodes = [n for n in spec.get('vcfOperationsSpec', {}).get('nodes', []) if n.get('hostname') == host]
if not nodes:
    sys.exit(f'No vcfOperationsSpec node for {host} in {path}')
old = nodes[0].get('sslThumbprint')
if old == tp:
    print(f'{host} thumbprint is already correct:\n  {tp}')
    sys.exit(0)
nodes[0]['sslThumbprint'] = tp
with open(path, 'w') as f:
    json.dump(spec, f, indent=2)
    f.write('\n')
print(f'Updated the {host} thumbprint in {path}\n  old: {old}\n  new: {tp}')
PY
