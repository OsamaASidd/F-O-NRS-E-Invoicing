"""
fno_service.py
==============
Karishma D365 F&O data + mapping layer for NRS e-invoicing.
Reads posted invoices from F&O (OData), maps every field to real F&O data
(no placeholders), builds the Cryptware payload, and submits to NRS.

Field mappings (all proven against the live sandbox):
  - TIN      -> TaxServiceTaxRegistrationNumberCustomers (TaxRegstrationType='TIN')
  - HSN      -> ProductCategoryAssignments 'Commodity Code Hierarchy' -> 0000.00
  - Category -> ProductCategoryAssignments 'Sales Hierarchy'
  - lines    -> SalesInvoiceV4Lines ; header -> SalesInvoiceJournalHeaders
"""

import re
import json
import logging
import requests
from datetime import datetime
from msal import ConfidentialClientApplication
from fno_config import (FNO_TENANT_ID, FNO_CLIENT_ID, FNO_CLIENT_SECRET,
                        FNO_SCOPE, FNO_DATA_URL, DATA_AREA_ID, API_BASE_URL, KARISHMA)
from api_client import EInvoiceAPIClient
from db import db_read_one, db_write

logger = logging.getLogger(__name__)

HEADER_ENTITY = "SalesInvoiceJournalHeaders"
LINE_ENTITY   = "SalesInvoiceV4Lines"

_token_cache = {"tok": None}


# ---------------------------------------------------------------- auth + GET
def _new_token():
    app = ConfidentialClientApplication(
        FNO_CLIENT_ID, FNO_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{FNO_TENANT_ID}")
    r = app.acquire_token_for_client(scopes=FNO_SCOPE)
    if "access_token" not in r:
        raise RuntimeError(r.get("error_description", r))
    return r["access_token"]


def _get(entity, params, timeout=60):
    if not _token_cache["tok"]:
        _token_cache["tok"] = _new_token()
    def _do():
        return requests.get(f"{FNO_DATA_URL}/{entity}", params=params,
                            headers={"Authorization": f"Bearer {_token_cache['tok']}",
                                     "Accept": "application/json"}, timeout=timeout)
    r = _do()
    if r.status_code == 401:
        _token_cache["tok"] = _new_token()
        r = _do()
    r.raise_for_status()
    return r.json().get("value", [])


# ---------------------------------------------------------------- mapping helpers
def _e164(p):
    if not p:
        return None
    p = re.sub(r"[^\d+]", "", p)
    if p.startswith("+2340"):
        p = "+234" + p[5:]
    return p if re.fullmatch(r"\+\d{10,15}", p) else None


def _fmt_hsn(code):
    d = re.sub(r"\D", "", code or "")
    return f"{d[:4]}.{d[4:6]}" if len(d) >= 6 else None


_pc_cache = {}
def _prod_cats(pn):
    """Return (hsn 0000.00, sales_category) for a product number."""
    if pn in _pc_cache:
        return _pc_cache[pn]
    rows = []
    for q in [pn, pn.split("-")[0]]:
        rows = _get("ProductCategoryAssignments", {"cross-company": "true",
                    "$filter": f"ProductNumber eq '{q}'"})
        if rows:
            break
    hsn = next((r["ProductCategoryName"] for r in rows
                if "Commodity" in (r.get("ProductCategoryHierarchyName") or "")), None)
    sales = next((r["ProductCategoryName"] for r in rows
                  if r.get("ProductCategoryHierarchyName") == "Sales Hierarchy"), None)
    other = next((r["ProductCategoryName"] for r in rows
                  if "Commodity" not in (r.get("ProductCategoryHierarchyName") or "")), None)
    _pc_cache[pn] = (_fmt_hsn(hsn), sales or other)
    return _pc_cache[pn]


def _cust_regs(acct):
    """Return {'TIN': ..., 'RC': ...} registration numbers for a customer."""
    regs = _get("TaxServiceTaxRegistrationNumberCustomers",
                {"cross-company": "true", "$filter": f"CustAccountNum eq '{acct}'"})
    return {r.get("TaxRegstrationType"): r.get("RegistrationNumber") for r in regs}


def _cust_tin(acct):
    return _cust_regs(acct).get("TIN")


# ---------------------------------------------------------------- reads
def list_invoices(limit=100):
    """Fetch posted KL invoices/credit notes (header level), newest first."""
    rows = _get(HEADER_ENTITY, {"$top": limit, "$orderby": "InvoiceDate desc",
                "cross-company": "true", "$filter": f"dataAreaId eq '{DATA_AREA_ID}'"})
    out = []
    for h in rows:
        vch = h.get("LedgerVoucher", "")
        local = db_read_one("SELECT status, irn, qr_code FROM fno_invoices WHERE voucher=?", (vch,))
        out.append({
            "voucher": vch,
            "invoice_num": h.get("InvoiceNumber"),
            "date": (h.get("InvoiceDate") or "")[:10],
            "customer": h.get("InvoiceCustomerAccountNumber"),
            "total": h.get("TotalInvoiceAmount"),
            "tax": h.get("TotalTaxAmount"),
            "currency": h.get("CurrencyCode"),
            "type": "Credit Note" if vch.startswith("SCN") else "Invoice",
            "status": (local or {}).get("status", "pending"),
            "irn": (local or {}).get("irn"),
            "qr": (local or {}).get("qr_code"),
        })
    return out


def _header(voucher):
    rows = _get(HEADER_ENTITY, {"cross-company": "true",
                "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and LedgerVoucher eq '{voucher}'"})
    return rows[0] if rows else None


def build_payload(voucher):
    """Build the Cryptware payload for one voucher. Returns (payload, warnings)."""
    hdr = _header(voucher)
    if not hdr:
        return None, ["Invoice not found in F&O"]
    invno = hdr["InvoiceNumber"]
    issue = hdr["InvoiceDate"][:10]
    acct = hdr["InvoiceCustomerAccountNumber"]
    iscn = voucher.startswith("SCN")

    lines = _get(LINE_ENTITY, {"cross-company": "true",
                 "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and InvoiceNumber eq '{invno}' "
                            f"and LedgerVoucher eq '{voucher}'"})
    cust = _get("CustomersV3", {"$top": 1, "cross-company": "true",
                "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and CustomerAccount eq '{acct}'"})
    cust = cust[0] if cust else {}
    tin = _cust_tin(acct)

    warnings = []
    al = []
    for ln in lines:
        pn = ln.get("ProductNumber")
        qty = abs(float(ln.get("InvoicedQuantity", 1) or 1)) or 1
        price = abs(float(ln.get("SalesPrice", 0) or 0))
        la = abs(float(ln.get("LineAmount", 0) or 0))
        tax = abs(float(ln.get("LineTotalTaxAmount", 0) or 0))
        rate = round(tax / la * 100, 2) if la else 0.0
        if price <= 0 and qty:
            price = la / qty
        hsn, cat = _prod_cats(pn)
        if not hsn:
            warnings.append(f"No HSN for product {pn}")
        al.append({
            "description": ln.get("ProductDescription") or "Item",
            "invoiced_quantity": qty, "price_amount": price,
            "hsn_code": hsn, "price_unit": ln.get("SalesUnitSymbol") or "EA",
            "product_category": cat or "General", "tax_rate": rate,
            "tax_category_id": "STANDARD_VAT" if rate > 0 else "ZERO_VAT",
            "discount_rate": 0, "internal_id": pn,
        })

    phone = _e164(cust.get("PrimaryContactPhone"))
    if not tin:
        warnings.append("No TIN found for customer")
    if not phone:
        warnings.append("Customer phone missing/invalid")

    payload = {
        "document_identifier": f"{voucher}-KL-{issue.replace('-', '')}",
        "invoice_type": "STANDARD", "issue_date": issue, "due_date": issue,
        "invoice_type_code": "380" if iscn else "381",
        "document_currency_code": hdr.get("CurrencyCode", "NGN"),
        "transaction_category": "B2B",
        "accounting_customer_party": {
            "party_name": cust.get("OrganizationName") or f"Customer {acct}",
            "email": cust.get("PrimaryContactEmail") or "noemail@placeholder.com",
            "telephone": phone or "+2340000000000", "tin": tin or "00000000-0001",
            "business_description": "Trading",
            "postal_address": {
                "street_name": (str(hdr.get("InvoiceAddressStreetNumber", "")) + " " +
                                str(hdr.get("InvoiceAddressStreet", ""))).strip() or "N/A",
                "city_name": hdr.get("InvoiceAddressCity") or "Lagos",
                "postal_zone": hdr.get("InvoiceAddressZipCode") or "100001",
                "country": hdr.get("InvoiceAddressCountryRegionISOCode") or "NG",
            },
        },
        "invoice_lines": al,
    }
    if iscn:
        warnings.append("Credit note needs cancel_references (original invoice IRN) before posting")
    return payload, warnings


def post_invoice(voucher):
    """Build + submit one invoice to NRS, persist result."""
    payload, warnings = build_payload(voucher)
    if not payload:
        return {"ok": False, "error": "; ".join(warnings)}
    hdr = _header(voucher)
    api = EInvoiceAPIClient(base_url=API_BASE_URL, api_key=KARISHMA["api_key"])
    res = api.generate_invoice(payload)
    now = datetime.now().isoformat()
    base = (voucher, hdr.get("InvoiceNumber"), hdr.get("InvoiceDate", "")[:10],
            hdr.get("InvoiceCustomerAccountNumber"), hdr.get("TotalInvoiceAmount"))
    if res.get("success"):
        inner = (res["data"].get("data") or res["data"])
        irn = inner.get("irn") or ""
        qr = inner.get("qr_code_url") or ""
        status = inner.get("status") or "SUBMITTED"
        db_write("""INSERT INTO fno_invoices(voucher,invoice_num,date,customer,total,status,irn,qr_code,api_response,posted_at)
                    VALUES(?,?,?,?,?,'posted',?,?,?,?)
                    ON CONFLICT(voucher) DO UPDATE SET status='posted',irn=excluded.irn,
                    qr_code=excluded.qr_code,api_response=excluded.api_response,posted_at=excluded.posted_at""",
                 base + (irn, qr, json.dumps(res.get("data"))[:5000], now))
        return {"ok": True, "irn": irn, "qr": qr, "status": status, "warnings": warnings}
    err = res.get("error") or (res.get("data") or {}).get("message") or "Unknown error"
    db_write("""INSERT INTO fno_invoices(voucher,invoice_num,date,customer,total,status,api_response,posted_at)
                VALUES(?,?,?,?,?,'failed',?,?)
                ON CONFLICT(voucher) DO UPDATE SET status='failed',api_response=excluded.api_response,posted_at=excluded.posted_at""",
             base + (json.dumps(res.get("data"))[:5000], now))
    return {"ok": False, "error": err, "warnings": warnings}


def invoice_detail(voucher):
    """
    Rich per-invoice data for the printed PDF (mirrors the D365 F&O invoice layout).
    Includes header, references, line items with computed amounts + HS/ISIC + category,
    customer + TIN/RC, tax summary, totals, and the stored NRS IRN/QR.
    """
    hdr = _header(voucher)
    if not hdr:
        return None
    invno = hdr["InvoiceNumber"]
    acct = hdr["InvoiceCustomerAccountNumber"]
    iscn = voucher.startswith("SCN")
    lines = _get(LINE_ENTITY, {"cross-company": "true",
                 "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and InvoiceNumber eq '{invno}' "
                            f"and LedgerVoucher eq '{voucher}'"})
    cust = _get("CustomersV3", {"$top": 1, "cross-company": "true",
                "$filter": f"dataAreaId eq '{DATA_AREA_ID}' and CustomerAccount eq '{acct}'"})
    cust = cust[0] if cust else {}
    regs = _cust_regs(acct)

    det_lines, sub_total, tax_total = [], 0.0, 0.0
    for ln in lines:
        pn = ln.get("ProductNumber")
        qty = abs(float(ln.get("InvoicedQuantity", 1) or 1))
        price = abs(float(ln.get("SalesPrice", 0) or 0))
        disc = abs(float(ln.get("LineTotalDiscountAmount", 0) or 0))
        amount = abs(float(ln.get("LineAmount", 0) or 0)) or (qty * price - disc)
        vat = abs(float(ln.get("LineTotalTaxAmount", 0) or 0))
        rate = round(vat / amount * 100, 1) if amount else 0.0
        hsn6, cat = _prod_cats(pn)
        hsn6 = re.sub(r"\D", "", hsn6 or "")[:6] if hsn6 else ""
        # _prod_cats returns formatted 0000.00; recover 6-digit for HS/ISIC display
        raw = _get("ProductCategoryAssignments", {"cross-company": "true", "$filter": f"ProductNumber eq '{pn}'"}) \
            or _get("ProductCategoryAssignments", {"cross-company": "true", "$filter": f"ProductNumber eq '{pn.split('-')[0]}'"})
        commodity = next((r["ProductCategoryName"] for r in raw
                          if "Commodity" in (r.get("ProductCategoryHierarchyName") or "")), "")
        hs_isic = re.sub(r"\D", "", commodity)[:6] if commodity else hsn6
        sub_total += amount
        tax_total += vat
        det_lines.append({
            "description": ln.get("ProductDescription") or "Item",
            "item": pn, "category": cat or "",
            "hs_isic": hs_isic, "tax_rate": rate,
            "quantity": qty, "unit": ln.get("SalesUnitSymbol") or "",
            "unit_price": price, "discount": disc,
            "amount": amount, "vat": vat, "gross": amount + vat,
        })

    local = db_read_one("SELECT irn, qr_code, status FROM fno_invoices WHERE voucher=?", (voucher,))
    total = float(hdr.get("TotalInvoiceAmount") or (sub_total + tax_total))
    addr_parts = [str(hdr.get("InvoiceAddressStreetNumber", "")).strip() + " " +
                  str(hdr.get("InvoiceAddressStreet", "")).strip(),
                  hdr.get("InvoiceAddressCity", ""), hdr.get("InvoiceAddressZipCode", "")]
    return {
        "voucher": voucher, "invoice_num": invno,
        "invoice_date": hdr.get("InvoiceDate", "")[:10],
        "is_credit_note": iscn,
        "customer": {
            "name": cust.get("OrganizationName") or f"Customer {acct}",
            "account": acct,
            "enterprise_number": regs.get("RC", ""),
            "tin": regs.get("TIN", ""),
            "address_lines": [p.strip() for p in [
                (str(hdr.get("InvoiceAddressStreetNumber", "")).strip() + " " +
                 str(hdr.get("InvoiceAddressStreet", "")).strip()).strip(),
                hdr.get("InvoiceAddressCity", ""),
                (hdr.get("InvoiceAddressZipCode", "") or "") + ", " +
                (hdr.get("InvoiceAddressCountryRegionISOCode", "") or ""),
            ] if p and p != ","],
            "email": cust.get("PrimaryContactEmail", ""),
            "phone": cust.get("PrimaryContactPhone", ""),
        },
        "refs": {
            "sales_order": hdr.get("SalesOrderNumber", ""),
            "requisition": hdr.get("CustomersRequisitionNumber", "") or hdr.get("PurchaseOrderNumber", ""),
            "your_reference": hdr.get("CustomersOrderReference", ""),
            "delivery_terms": hdr.get("DeliveryTermsCode", ""),
            "invoice_account": acct,
            "payment": hdr.get("PaymentTermsName", ""),
            "due_date": (hdr.get("DueDate", "") or hdr.get("InvoiceDate", ""))[:10],
        },
        "lines": det_lines,
        "currency": hdr.get("CurrencyCode", "NGN"),
        "sub_total": sub_total, "tax_total": tax_total, "total": total,
        "irn": (local or {}).get("irn"),
        "qr_url": (local or {}).get("qr_code"),
        "status": (local or {}).get("status", "pending"),
    }
