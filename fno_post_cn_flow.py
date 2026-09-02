#!/usr/bin/env python3
"""Post original invoice 00000003, then its credit note SCN-20000000 referencing that IRN."""
import json, requests
from msal import ConfidentialClientApplication
from fno_config import *
from fno_config import API_BASE_URL, KARISHMA
from api_client import EInvoiceAPIClient

# KARISHMA imported from fno_config
app = ConfidentialClientApplication(FNO_CLIENT_ID, FNO_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{FNO_TENANT_ID}")
tok = app.acquire_token_for_client(scopes=FNO_SCOPE)["access_token"]
api = EInvoiceAPIClient(base_url=API_BASE_URL, api_key=KARISHMA["api_key"])

def get(entity, params, full=False):
    acc = "application/json;odata.metadata=full" if full else "application/json"
    r = requests.get(f"{FNO_DATA_URL}/{entity}", params=params,
                     headers={"Authorization": f"Bearer {tok}", "Accept": acc}, timeout=90)
    r.raise_for_status(); return r.json().get("value", [])

def build(invno, voucher, type_code, cancel_refs=None):
    hdr = get("SalesInvoiceJournalHeaders", {"cross-company": "true",
        "$filter": f"dataAreaId eq 'KL' and InvoiceNumber eq '{invno}' and LedgerVoucher eq '{voucher}'"}, full=True)[0]
    issue = hdr["InvoiceDate"][:10]
    lines = get("SalesInvoiceV2Lines", {"cross-company": "true",
        "$filter": f"dataAreaId eq 'KL' and InvoiceNumber eq '{invno}' and LedgerVoucher eq '{voucher}'"})
    acct = hdr["InvoiceCustomerAccountNumber"]
    cust = get("CustomersV3", {"$top": 1, "cross-company": "true",
        "$filter": f"dataAreaId eq 'KL' and CustomerAccount eq '{acct}'"})
    cust = cust[0] if cust else {}
    al = []
    for ln in lines:
        qty = abs(float(ln.get("InvocedQuantity", ln.get("InvoicedQuantity", 1)) or 1)) or 1
        price = abs(float(ln.get("SalesPrice", 0) or 0))
        la = abs(float(ln.get("LineAmount", 0) or 0)); tax = abs(float(ln.get("LineTotalTaxAmount", 0) or 0))
        rate = round(tax/la*100, 2) if la else 0.0
        if price <= 0 and qty: price = la/qty
        al.append({"description": ln.get("ProductDescription") or "Item", "invoiced_quantity": qty,
            "price_amount": price, "hsn_code": "9820.10", "price_unit": "EA", "product_category": "General",
            "tax_rate": rate, "tax_category_id": "STANDARD_VAT" if rate>0 else "ZERO_VAT",
            "discount_rate": 0, "internal_id": ln.get("ProductNumber") or voucher})
    p = {"document_identifier": f"{voucher}-KL-{issue.replace('-','')}", "invoice_type": "STANDARD",
        "issue_date": issue, "due_date": issue, "invoice_type_code": type_code,
        "document_currency_code": hdr.get("CurrencyCode","NGN"), "transaction_category": "B2B",
        "accounting_customer_party": {"party_name": cust.get("OrganizationName") or f"Customer {acct}",
            "email": cust.get("PrimaryContactEmail") or "noemail@placeholder.com",
            "telephone": cust.get("PrimaryContactPhone") or "+2340000000000", "tin": "00000000-0001",
            "business_description": "Customer", "postal_address": {"street_name": hdr.get("InvoiceAddressStreet") or "N/A",
                "city_name": hdr.get("InvoiceAddressCity") or "Lagos", "postal_zone": hdr.get("InvoiceAddressZipCode") or "100001",
                "country": hdr.get("InvoiceAddressCountryRegionISOCode") or "NG"}},
        "invoice_lines": al}
    if cancel_refs is not None:
        p["cancel_references"] = cancel_refs
    return p

# STEP 1: original invoice 00000003
print("=== STEP 1: original invoice 00000003 (SINV-10000002) ===")
p1 = build("00000003", "SINV-10000002", "381")
r1 = api.generate_invoice(p1)
print("  success:", r1.get("success"), "status:", r1.get("status"))
print("  resp:", json.dumps(r1.get("data"))[:600])
irn3 = ((r1.get("data") or {}).get("data") or {}).get("irn") if r1.get("success") else None
print("  original IRN:", irn3)

# STEP 2: credit note referencing original IRN
print("=== STEP 2: credit note SCN-20000000 (type 380) ===")
refs = [{"irn": irn3, "reason": "Return / credit note"}] if irn3 else [{"irn": "UNKNOWN"}]
p2 = build("00000001", "SCN-20000000", "380", cancel_refs=refs)
print("  cancel_references:", json.dumps(refs))
r2 = api.generate_invoice(p2)
print("  success:", r2.get("success"), "status:", r2.get("status"))
print("  resp:", json.dumps(r2.get("data"))[:900])
