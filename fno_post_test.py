#!/usr/bin/env python3
"""
fno_post_test.py
================
End-to-end single-invoice test for Karishma F&O:
  1. Read newest KL invoice header + lines from SalesInvoiceJournalHeaders.
  2. Enrich customer (name/TIN) from CustomersV3.
  3. Build the Cryptware NRS payload and POST /invoice/generate (preprod).
  4. Write the returned IRN / QR URL / status back to the F&O invoice
     (PATCH NRS_IRN / NRS_QRCodeURL / NRS_Status) and read it back.

Run: python fno_post_test.py
"""

import json
import requests
from msal import ConfidentialClientApplication
from fno_config import (FNO_TENANT_ID, FNO_CLIENT_ID, FNO_CLIENT_SECRET,
                        FNO_SCOPE, FNO_DATA_URL, DATA_AREA_ID)
from fno_config import API_BASE_URL, KARISHMA
from api_client import EInvoiceAPIClient

# KARISHMA imported from fno_config


def token():
    app = ConfidentialClientApplication(
        FNO_CLIENT_ID, FNO_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{FNO_TENANT_ID}")
    return app.acquire_token_for_client(scopes=FNO_SCOPE)["access_token"]


def get(tok, entity, params, full=False):
    acc = "application/json;odata.metadata=full" if full else "application/json"
    r = requests.get(f"{FNO_DATA_URL}/{entity}", params=params,
                     headers={"Authorization": f"Bearer {tok}", "Accept": acc}, timeout=90)
    r.raise_for_status()
    return r.json().get("value", [])


def main():
    tok = token()

    # 1. newest KL invoice (full metadata -> editLink key)
    hdr = get(tok, "SalesInvoiceJournalHeaders",
              {"$top": 1, "$orderby": "InvoiceDate desc", "cross-company": "true",
               "$filter": f"dataAreaId eq '{DATA_AREA_ID}'"}, full=True)[0]
    invno = hdr["InvoiceNumber"]
    editlink = hdr["@odata.editLink"].lstrip("./")
    issue_date = hdr["InvoiceDate"][:10]
    print(f"[1] Invoice {invno} | {issue_date} | {hdr['CurrencyCode']} | "
          f"total {hdr['TotalInvoiceAmount']} tax {hdr['TotalTaxAmount']}")

    # 2. lines
    lines = get(tok, "SalesInvoiceV2Lines",
                {"cross-company": "true",
                 "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and InvoiceNumber eq '{invno}'"})
    print(f"[2] {len(lines)} line(s)")

    # 3. customer enrichment
    acct = hdr["InvoiceCustomerAccountNumber"]
    cust = get(tok, "CustomersV3",
               {"$top": 1, "cross-company": "true",
                "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and CustomerAccount eq '{acct}'"})
    cust = cust[0] if cust else {}
    cust_name = cust.get("OrganizationName") or cust.get("CustomerName") or f"Customer {acct}"
    cust_tin = (cust.get("PrimaryContactEmail") and None) or \
        cust.get("TaxExemptNumber") or cust.get("VATNum") or "00000000-0001"
    print(f"[3] Customer {acct}: {cust_name} | TIN {cust_tin}")

    # 4. build Cryptware payload
    api_lines = []
    for ln in lines:
        qty = float(ln.get("InvoicedQuantity", 1) or 1)
        price = float(ln.get("SalesPrice", 0) or 0)
        line_amt = float(ln.get("LineAmount", 0) or 0)
        tax = float(ln.get("LineTotalTaxAmount", 0) or 0)
        rate = round(tax / line_amt * 100, 2) if line_amt else 0.0
        if price <= 0 and qty:
            price = line_amt / qty
        api_lines.append({
            "description": ln.get("ProductDescription") or ln.get("ProductName") or "Item",
            "invoiced_quantity": qty,
            "price_amount": price,
            "hsn_code": "9820.10",
            "price_unit": "EA",
            "product_category": "General",
            "tax_rate": rate,
            "tax_category_id": "STANDARD_VAT" if rate > 0 else "ZERO_VAT",
            "discount_rate": 0,
            "internal_id": ln.get("ProductNumber") or invno,
        })

    payload = {
        "document_identifier": f"{invno}-KL-{issue_date.replace('-', '')}",
        "invoice_type": "STANDARD",
        "issue_date": issue_date,
        "due_date": issue_date,
        "invoice_type_code": "381",
        "document_currency_code": hdr.get("CurrencyCode", "NGN"),
        "transaction_category": "B2B",
        "accounting_customer_party": {
            "party_name": cust_name,
            "email": cust.get("PrimaryContactEmail") or "noemail@placeholder.com",
            "telephone": cust.get("PrimaryContactPhone") or "+2340000000000",
            "tin": cust_tin,
            "business_description": "Customer",
            "postal_address": {
                "street_name": hdr.get("InvoiceAddressStreet") or "N/A",
                "city_name": hdr.get("InvoiceAddressCity") or "Lagos",
                "postal_zone": hdr.get("InvoiceAddressZipCode") or "100001",
                "country": hdr.get("InvoiceAddressCountryRegionISOCode") or "NG",
            },
        },
        "invoice_lines": api_lines,
    }
    print("[4] Payload:\n" + json.dumps(payload, indent=2))

    # 5. POST to Cryptware
    api = EInvoiceAPIClient(base_url=API_BASE_URL, api_key=KARISHMA["api_key"])
    print(f"[5] POST {API_BASE_URL} /invoice/generate ...")
    result = api.generate_invoice(payload)
    print("    success:", result.get("success"), "| status:", result.get("status"))
    print("    response:", json.dumps(result.get("data"), indent=2)[:1500])
    if not result.get("success"):
        print("    error:", result.get("error"))
        # still attempt nothing to write back
        return

    inner = (result["data"].get("data") or result["data"])
    irn = inner.get("irn") or ""
    qr = inner.get("qr_code_url") or inner.get("qr_code") or ""
    status = inner.get("status") or "SUBMITTED"
    print(f"[5] IRN={irn} | QR={qr[:60]} | status={status}")

    # 6. write back to F&O
    body = {"NRS_IRN": irn, "NRS_QRCodeURL": qr, "NRS_Status": status}
    url = f"{FNO_DATA_URL}/{editlink}"
    print(f"[6] PATCH {url}\n    body: {json.dumps(body)}")
    pr = requests.patch(url, json=body, headers={
        "Authorization": f"Bearer {tok}", "Content-Type": "application/json",
        "Accept": "application/json", "If-Match": "*"}, timeout=90)
    print("    PATCH status:", pr.status_code, pr.text[:300])

    # 7. read back
    back = get(tok, "SalesInvoiceJournalHeaders",
               {"$top": 1, "cross-company": "true",
                "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and InvoiceNumber eq '{invno}'"})
    if back:
        b = back[0]
        print(f"[7] Read-back: NRS_IRN={b.get('NRS_IRN')} | "
              f"NRS_Status={b.get('NRS_Status')} | NRS_QRCodeURL={b.get('NRS_QRCodeURL')}")


if __name__ == "__main__":
    main()
