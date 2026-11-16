#!/usr/bin/env python3
"""Move a VCF Automation Classic Tenant organization from vIDM to VCF SSO (VCF Identity Broker).

Lab script for Region B (auto-01b, org vidm-01b, broker vidb-b). It:
  1. backs up the organization's current OIDC (vIDM) settings to a JSON file,
  2. points the organization at the identity broker, copying the broker settings
     that "Join VCF SSO" wrote to the System (provider) organization,
  3. imports the gg-vra-*@vcf.lab groups and gives each one its roles.

Usage: vcfa-tenant-to-vcfsso.py <client-id> <client-secret>
  (client ID and secret from VCF Operations > Identity & Access > Other Components)
"""
import json, ssl, sys, urllib.request, urllib.error, base64

HOST = "https://auto-01b.site-b.vcf.lab"
ORG_NAME = "vidm-01b"
ADMIN, PASSWORD = "admin@System", "VMware123!VMware123!"
DOMAIN = "vcf.lab"
GROUP_ROLES = {
    "gg-vra-org-owners": ["Organization Owner"],
    "gg-vra-cloud-assembly-admins": ["Organization Member", "Assembler Administrator"],
    "gg-vra-cloud-assembly-users": ["Organization Member", "Assembler User"],
    "gg-vra-cloud-assembly-viewers": ["Organization Member", "Assembler Viewer"],
    "gg-vra-service-broker-admins": ["Organization Member", "Service Broker Administrator"],
    "gg-vra-service-broker-users": ["Organization Member", "Service Broker User"],
    "gg-vra-service-broker-viewers": ["Organization Member", "Service Broker Viewer"],
    "gg-vra-orchestrator-admins": ["Organization Member", "Orchestrator Administrator"],
    "gg-vra-orchestrator-designers": ["Organization Member", "Orchestrator Workflow Designer"],
    "gg-vra-orchestrator-viewers": ["Organization Member", "Orchestrator Viewer"],
}
CTX = ssl._create_unverified_context()


def req(method, path, body=None, accept="application/json;version=40.0", token=None, extra=None):
    h = {"Accept": accept, "Content-Type": "application/*+json" if accept.startswith("application/*") else "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(extra or {})
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, context=CTX) as x:
            return x.status, x.headers, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    client_id, client_secret = sys.argv[1:]

    auth = base64.b64encode(f"{ADMIN}:{PASSWORD}".encode()).decode()
    st, hd, _ = req("POST", "/cloudapi/1.0.0/sessions/provider", extra={"Authorization": "Basic " + auth})
    token = hd.get("X-VMWARE-VCLOUD-ACCESS-TOKEN")
    if st != 200 or not token:
        sys.exit(f"Login failed ({st})")

    _, _, b = req("GET", "/cloudapi/1.0.0/orgs?pageSize=50", token=token)
    orgs = {o["name"]: o["id"].split(":")[-1] for o in json.loads(b)["values"]}
    sys_id, org_id = orgs["System"], orgs[ORG_NAME]

    A = "application/*+json;version=40.0"
    _, _, cur = req("GET", f"/api/admin/org/{org_id}/settings/oauth", accept=A, token=token)
    open(f"{ORG_NAME}-oauth-backup.json", "w").write(cur)
    print(f"Backed up the current settings to {ORG_NAME}-oauth-backup.json")

    _, _, sysb = req("GET", f"/api/admin/org/{sys_id}/settings/oauth", accept=A, token=token)
    s, d = json.loads(sysb), json.loads(cur)
    if not s.get("vidb"):
        sys.exit("The System organization is not on VCF SSO yet. Join VCF Automation to VCF SSO first.")
    for k in ("issuerId", "userAuthorizationEndpoint", "accessTokenEndpoint", "userInfoEndpoint", "scope", "jwksUri",
              "autoRefreshKey", "keyRefreshStrategy", "keyRefreshFrequencyInHours", "wellKnownEndpoint",
              "endSessionEndpoint", "customUiButtonLabel", "vidb", "oAuthKeyConfigurations", "oidcAttributeMapping"):
        d[k] = s[k]
    d.update({"clientId": client_id, "clientSecret": client_secret, "scimEndpoint": None, "enabled": True,
              "postLogoutRedirectUri": f"{HOST}/login?service=tenant:{ORG_NAME}"})
    if json.loads(cur).get("vidb"):
        print("The organization already uses VCF SSO; skipping the switch.")
    else:
        st, _, b = req("PUT", f"/api/admin/org/{org_id}/settings/oauth", d, accept=A, token=token)
        print("Switch organization to VCF SSO:", st)
        if st >= 300:
            sys.exit(b[:500])

    T = {"X-VMWARE-VCLOUD-TENANT-CONTEXT": org_id}
    _, _, b = req("GET", "/cloudapi/1.0.0/roles?pageSize=100", token=token, extra=T)
    roles = {r["name"]: r["id"] for r in json.loads(b)["values"]}
    _, _, b = req("GET", "/cloudapi/1.0.0/groups?pageSize=128", token=token, extra=T)
    existing = {g["name"] for g in json.loads(b)["values"]}
    for g, rs in GROUP_ROLES.items():
        if f"{g}@{DOMAIN}" in existing:
            print(f"Import {g}@{DOMAIN}: already there")
            continue
        body = {"name": f"{g}@{DOMAIN}", "providerType": "OAUTH", "orgEntityRef": {"id": f"urn:vcloud:org:{org_id}"},
                "roleEntityRefs": [{"name": r, "id": roles[r]} for r in rs]}
        st, _, b = req("POST", "/cloudapi/1.0.0/groups", body, token=token, extra=T)
        print(f"Import {g}@{DOMAIN}: {st} {', '.join(rs) if st < 300 else b[:200]}")


if __name__ == "__main__":
    main()
