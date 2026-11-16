#!/bin/bash
#
# Copyright (c) 2026 Broadcom. All Rights Reserved.
# Broadcom Confidential. The term "Broadcom" refers to Broadcom Inc.
# and/or its subsidiaries.

set -eu

export KUBECTL="${KUBECTL:-kubectl}"

#----------------------- Logging -----------------------
logDir="/var/log/vmware/prelude"
timestamp=$(date '+%Y-%m-%d-%H-%M-%S')
pr="VCFCON-41983"
LOG_FILE="$logDir/patch-pr$pr-$timestamp.log"

mkdir -p "$logDir"
exec > >(tee -a "$LOG_FILE") 2>&1

function log() {
  local msg="$1"
  local level="$2"
  local dt=$(date '+%Y-%m-%d %H:%M:%S')
  local hostName="$(hostname -f)"

  echo "[$level][$dt][$hostName] $msg" >&2
}

function log_info() {
  log "$1" "INFO"
}

function log_error() {
  log "$1" "ERROR"
}

function die() {
    log_error "$1"
    exit 1
}

#----------------------- DB helpers -----------------------
function get_postgres_pod() {
    local pod
    pod=$("$KUBECTL" -n prelude get pods -l app=postgres -o jsonpath='{.items[0].metadata.name}')
    [[ -z "$pod" ]] && die "Failed to find postgres pod in prelude namespace"
    echo "$pod"
}

function execute_db_query() {
    local query="$1"
    "$KUBECTL" -n prelude exec "$POSTGRES_POD" -- bash -c "psql -qAt postgresql://provisioning-db:${PASSWD}@\$PGPOOL_SERVICE_HOST:\$PGPOOL_SERVICE_PORT/provisioning-db -c \"${query}\""
}

#----------------------- Main logic -----------------------
log_info "### Starting endpoint certificates patch ($pr)"

PASSWD=$("$KUBECTL" -n prelude get secrets db-credentials -o jsonpath="{.data.provisioning-db}" | base64 -d)
[[ -z "$PASSWD" ]] && die "Failed to retrieve database password"

POSTGRES_POD=$(get_postgres_pod)
log_info "Using postgres pod: $POSTGRES_POD"

cert=/tmp/cert_file_fqdn.pem
log_info "Exporting ingress certificate chain to $cert"
vracli certificate ingress --list > "$cert"

mapfile -d '' certificates < <(
    awk '
        /-----BEGIN CERTIFICATE-----/ {
            in_cert = 1;
            cert_block = $0;
            next;
        }

        in_cert && /-----END CERTIFICATE-----/ {
            cert_block = cert_block "\n" $0;
            printf "%s\0", cert_block;
            in_cert = 0;
            cert_block = "";
            next;
        }

        in_cert {
            cert_block = cert_block "\n" $0;
        }
    ' "$cert"
)

if (( ${#certificates[@]} == 0 )); then
    rm -f "$cert"
    die "No certificates found in '$cert' or file does not exist."
fi

log_info "Parsed ${#certificates[@]} certificate(s) from ingress chain"

log_info "Clearing existing cert_ext entries from ssl_trust_certificate_state"
execute_db_query "delete from ssl_trust_certificate_state where document_self_link like 'cert_ext_%';"

log_info "Inserting ingress certificate chain into ssl_trust_certificate_state"
for i in "${!certificates[@]}"; do
    execute_db_query "INSERT INTO ssl_trust_certificate_state (document_self_link,certificate) VALUES ('cert_ext_$((i+1))','${certificates[$i]}');"
    log_info "Inserted cert_ext_$((i+1))"
done

rm -f "$cert"
log_info "### Endpoint certificates patch completed successfully"
