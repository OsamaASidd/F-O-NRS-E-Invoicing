#!/usr/bin/env python3
"""Post the KL credit note (voucher SCN-20000000) to Cryptware NRS, then try writeback."""
import json, requests
from msal import ConfidentialClientApplication
from fno_config import *
from fno_config import API_BASE_URL, KARISHMA
from api_client import EInvoiceAPIClient

VOUCHER = "SCN-20000000"
INVNO   = "00000001"
# KARISHMA imported from fno_config

app = ConfidentialClientApplication(FNO_CLIENT_ID, FNO_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{FNO_TENANT_ID}")
tok = app.acquire_token_for_client(scopes=FNO_SCOPE)["access_token"]

def get(entity, params, full=False):
    acc = "application/json;odata.metadata=full" if full else "application/json"
    r = requests.get(f"{FNO_DATA_URL}/{entity}", params=params,
                     headers={"Authorization": f"Bearer {tok}", "Accept": acc}, timeout=90)
    r.raise_for_status(); return r.json().get("value", [])

# 1. credit note header (disambiguate by voucher)
hdr = get("SalesInvoiceJournalHeaders",
          {"cross-company": "true",
           "$filter": f"dataAreaId eq 'KL' and InvoiceNumber eq '{INVNO}' and LedgerVoucher eq '{VOUCHER}'"},
          full=True)[0]
editlink = hdr["@odata.editLink"].lstrip("./")
issue_date = hdr["InvoiceDate"][:10]
print(f"[1] Credit note {INVNO} / {VOUCHER} | {issue_date} | {hdr['CurrencyCode']} | "
      f"total {hdr['TotalInvoiceAmount']} tax {hdr['TotalTaxAmount']}")

# 2. lines for this voucher
lines = get("SalesInvoiceV2Lines",
            {"cross-company": "true",
             "$filter": f"dataAreaId eq 'KL' and InvoiceNumber eq '{INVNO}' and LedgerVoucher eq '{VOUCHER}'"})
print(f"[2] {len(lines)} line(s)")

# 3. customer
acct = hdr["InvoiceCustomerAccountNumber"]
cust = get("CustomersV3", {"$top": 1, "cross-company": "true",
           "$filter": f"dataAreaId eq 'KL' and CustomerAccount eq '{acct}'"})
cust = cust[0] if cust else {}
cust_name = cust.get("OrganizationName") or f"Customer {acct}"
print(f"[3] Customer {acct}: {cust_name}")

# 4. payload — credit note = type code 380, amounts as absolute values
api_lines = []
for ln in lines:
    qty = abs(float(ln.get("InvoicedQuantity", 1) or 1)) or 1
    price = abs(float(ln.get("SalesPrice", 0) or 0))
    line_amt = abs(float(ln.get("LineAmount", 0) or 0))
    tax = abs(float(ln.get("LineTotalTaxAmount", 0) or 0))
    rate = round(tax / line_amt * 100, 2) if line_amt else 0.0
    if price <= 0 and qty: price = line_amt / qty
    api_lines.append({
        "description": ln.get("ProductDescription") or ln.get("ProductName") or "Item",
        "invoiced_quantity": qty, "price_amount": price, "hsn_code": "9820.10",
        "price_unit": "EA", "product_category": "General", "tax_rate": rate,
        "tax_category_id": "STANDARD_VAT" if rate > 0 else "ZERO_VAT",
        "discount_rate": 0, "internal_id": ln.get("ProductNumber") or VOUCHER})

payload = {
    "document_identifier": f"{VOUCHER}-KL-{issue_date.replace('-', '')}",
    "invoice_type": "STANDARD", "issue_date": issue_date, "due_date": issue_date,
    "invoice_type_code": "380",   # 380 = Credit Note
    "document_currency_code": hdr.get("CurrencyCode", "NGN"), "transaction_category": "B2B",
    "accounting_customer_party": {
        "party_name": cust_name, "email": cust.get("PrimaryContactEmail") or "noemail@placeholder.com",
        "telephone": cust.get("PrimaryContactPhone") or "+2340000000000",
        "tin": "00000000-0001", "business_description": "Customer",
        "postal_address": {"street_name": hdr.get("InvoiceAddressStreet") or "N/A",
            "city_name": hdr.get("InvoiceAddressCity") or "Lagos",
            "postal_zone": hdr.get("InvoiceAddressZipCode") or "100001",
            "country": hdr.get("InvoiceAddressCountryRegionISOCode") or "NG"}},
    "invoice_lines": api_lines}
print("[4] Payload:\n" + json.dumps(payload, indent=2))

# 5. POST
api = EInvoiceAPIClient(base_url=API_BASE_URL, api_key=KARISHMA["api_key"])
res = api.generate_invoice(payload)
print("[5] success:", res.get("success"), "| status:", res.get("status"))
print("    response:", json.dumps(res.get("data"), indent=2)[:1500])
if not res.get("success"):
    print("    error:", res.get("error")); raise SystemExit

inner = res["data"].get("data") or res["data"]
irn = inner.get("irn") or ""; qr = inner.get("qr_code_url") or ""; status = inner.get("status") or "SUBMITTED"
print(f"[5] IRN={irn} | status={status}")

# 6. writeback
body = {"NRS_IRN": irn, "NRS_QRCodeURL": qr, "NRS_Status": status}
pr = requests.patch(f"{FNO_DATA_URL}/{editlink}", json=body, headers={
    "Authorization": f"Bearer {tok}", "Content-Type": "application/json",
    "Accept": "application/json", "If-Match": "*"}, timeout=90)
print("[6] PATCH status:", pr.status_code, pr.text[:200])
