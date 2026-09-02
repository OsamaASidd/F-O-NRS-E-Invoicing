#!/usr/bin/env python3
"""
diag_fno.py
===========
Read-only connectivity + discovery probe for Karishma Dynamics 365 F&O.
Nothing is written. Steps:
  1. Acquire an Entra token with the F&O scope (client-credentials).
  2. Read the OData service document ($metadata) and locate the custom
     NRS_* fields -> tells us the exact entity + property names.
  3. List invoice-related entity sets.
  4. Read one sample invoice row for the KL legal entity.
"""

import re
import sys
import time
import requests
from msal import ConfidentialClientApplication
from fno_config import (
    FNO_TENANT_ID, FNO_CLIENT_ID, FNO_CLIENT_SECRET,
    FNO_SCOPE, FNO_DATA_URL, DATA_AREA_ID,
)


def hdr(t): print("\n" + "=" * 64 + f"\n  {t}\n" + "=" * 64)
def ok(t):  print(f"    [OK] {t}")
def err(t): print(f"    [ERR] {t}")
def info(t): print(f"    [..] {t}")


def get(token, url, params=None, timeout=60, tries=5):
    """GET with F&O throttle handling (429 -> honor Retry-After, back off)."""
    for attempt in range(1, tries + 1):
        r = requests.get(url, params=params, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=timeout)
        if r.status_code != 429:
            return r
        wait = int(r.headers.get("Retry-After", 0) or (5 * attempt))
        info(f"429 throttled; waiting {wait}s (attempt {attempt}/{tries})")
        time.sleep(wait)
    return r


def get_token():
    hdr("1. TOKEN")
    app = ConfidentialClientApplication(
        client_id=FNO_CLIENT_ID,
        client_credential=FNO_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{FNO_TENANT_ID}",
    )
    res = app.acquire_token_for_client(scopes=FNO_SCOPE)
    if "access_token" not in res:
        err(f"{res.get('error')}: {res.get('error_description', res)}")
        return None
    ok(f"token acquired (scope {FNO_SCOPE[0]}), expires in {res.get('expires_in')}s")
    return res["access_token"]


def list_invoice_entitysets(token):
    hdr("2. INVOICE-RELATED ENTITY SETS (service document)")
    r = get(token, FNO_DATA_URL, timeout=90)
    if r.status_code != 200:
        err(f"service doc returned {r.status_code}: {r.text[:200]}")
        return []
    names = [e.get("name") for e in r.json().get("value", [])]
    inv = [n for n in names if n and re.search(r"invoic|custinvoice|salesinvoice", n, re.I)]
    info(f"{len(names)} entity sets total; invoice-related:")
    for n in sorted(inv):
        print(f"        - {n}")
    return inv


def probe_entity(token, entityset):
    """Read one KL row from an entity set (no $select) to reveal all fields, incl. NRS_*."""
    params = {"$top": 1, "cross-company": "true",
              "$filter": f"dataAreaId eq '{DATA_AREA_ID}'"}
    r = get(token, f"{FNO_DATA_URL}/{entityset}", params=params, timeout=60)
    if r.status_code != 200:
        return None, r
    rows = r.json().get("value", [])
    return (rows[0] if rows else {}), r


def main():
    token = get_token()
    if not token:
        return 1

    inv_sets = list_invoice_entitysets(token)

    # Candidate posted-customer-invoice entity sets, most likely first.
    candidates = [s for s in [
        "SalesInvoiceHeadersV2", "SalesInvoiceHeaders",
        "CustomerInvoiceJournalHeaders", "CustInvoiceJournalHeaders",
    ] if s in inv_sets] or inv_sets

    hdr("3. PROBE CANDIDATE ENTITIES FOR NRS_* FIELDS")
    for es in candidates:
        row, r = probe_entity(token, es)
        if row is None:
            err(f"{es}: HTTP {r.status_code} {r.text[:120]}")
            continue
        nrs_keys = [k for k in row.keys() if "NRS" in k.upper()]
        if nrs_keys:
            ok(f"{es}: found NRS fields -> {nrs_keys}")
            hdr(f"4. SAMPLE ROW FROM {es} (KL)")
            for k, v in row.items():
                mark = "  <== NRS" if "NRS" in k.upper() else ""
                print(f"        {k} = {v}{mark}")
            hdr("DONE")
            return 0
        else:
            info(f"{es}: {len(row)} fields, no NRS_* present"
                 + ("" if row else " (no KL rows)"))

    info("No NRS_* fields found on probed entities. If the fields were added to a "
         "specific data entity, tell me its name and I'll target it directly.")
    hdr("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
