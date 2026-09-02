"""
fno_client.py
=============
Microsoft Dynamics 365 Finance & Operations (F&O) OData client for Karishma.
Client-credentials (daemon) OAuth via MSAL, same pattern as the Business Central
client (d365_client.py) but for the F&O data-entity API.

Differences vs Business Central:
  - Scope is the F&O environment URL + /.default (not businesscentral).
  - Base is {env}/data (OData v4 data entities).
  - Company is a `dataAreaId` filter (KL), not a companies({guid}) segment.
  - Adds write_back(): PATCH the NRS_* custom fields onto the invoice header.
  - 429 throttle handling (F&O priority-based throttling, honors Retry-After).

NOTE: Field/entity names marked [VERIFY] are standard SalesInvoiceHeadersV2/
LinesV2 names and must be confirmed against the live $metadata once the sandbox
stops throttling reads. The sample invoice screenshot confirms the custom fields
live on the Customer invoice journal (CustInvoiceJour), surfaced here via
SalesInvoiceHeadersV2 (that entity returned 429, not 404 — i.e. it exists).
"""

import time
import logging
import requests
from msal import ConfidentialClientApplication
from fno_config import (
    FNO_TENANT_ID, FNO_CLIENT_ID, FNO_CLIENT_SECRET,
    FNO_SCOPE, FNO_DATA_URL, DATA_AREA_ID,
)

logger = logging.getLogger(__name__)

# Posted sales invoice header / line data entities.
# CONFIRMED against the live sandbox: the NRS_* custom fields are exposed on
# SalesInvoiceJournalHeaders (NOT SalesInvoiceHeadersV2, which lacks them).
# Lines pair via SalesInvoiceV2Lines (collection "SalesInvoiceLinesV2" does not exist).
HEADER_ENTITY = "SalesInvoiceJournalHeaders"
LINE_ENTITY   = "SalesInvoiceV2Lines"

# Custom writeback field OData property names — CONFIRMED present on the header row.
NRS_IRN_FIELD    = "NRS_IRN"
NRS_QR_FIELD     = "NRS_QRCodeURL"
NRS_STATUS_FIELD = "NRS_Status"


class FnoClient:
    """Dynamics 365 F&O OData client. Token cached in memory, auto-refreshed."""

    def __init__(self, data_area_id=None):
        self._msal_app = ConfidentialClientApplication(
            client_id=FNO_CLIENT_ID,
            client_credential=FNO_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{FNO_TENANT_ID}",
        )
        self._data_area_id = data_area_id or DATA_AREA_ID

    # ------------------------------------------------------------------
    # TOKEN
    # ------------------------------------------------------------------

    def _get_token(self):
        result = self._msal_app.acquire_token_silent(FNO_SCOPE, account=None)
        if not result:
            result = self._msal_app.acquire_token_for_client(scopes=FNO_SCOPE)
        if "access_token" not in result:
            raise RuntimeError(f"Token error: {result.get('error_description', result)}")
        return result["access_token"]

    def _headers(self, write=False):
        h = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }
        if write:
            h["Content-Type"] = "application/json"
        return h

    # ------------------------------------------------------------------
    # REQUEST (with F&O throttle handling)
    # ------------------------------------------------------------------

    def _request(self, method, url, params=None, payload=None, timeout=60, tries=4):
        """
        Issue a request, retrying on 429 (F&O priority-based throttling).
        Honors Retry-After; caps total wait so callers don't block forever.
        """
        last = None
        for attempt in range(1, tries + 1):
            resp = requests.request(
                method, url,
                headers=self._headers(write=(method in ("PATCH", "POST"))),
                params=params, json=payload, timeout=timeout,
            )
            last = resp
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            wait = min(int(resp.headers.get("Retry-After", 0) or 5 * attempt), 60)
            logger.warning(f"F&O 429 throttled on {url}; retry in {wait}s "
                           f"({attempt}/{tries})")
            time.sleep(wait)
        # Exhausted retries on 429
        raise requests.exceptions.HTTPError(
            f"F&O throttled (429) after {tries} attempts: {last.text[:200]}",
            response=last)

    def _fetch_all(self, entity, params=None):
        """Fetch all pages from an F&O OData entity set (follows @odata.nextLink)."""
        url = f"{FNO_DATA_URL}/{entity}"
        results = []
        resp = self._request("GET", url, params=params)
        data = resp.json()
        results.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
        while next_link:
            resp = self._request("GET", next_link)
            data = resp.json()
            results.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink")
        return results

    # ------------------------------------------------------------------
    # READ: SALES INVOICES
    # ------------------------------------------------------------------

    def get_sales_invoices(self, date_from=None, date_to=None):
        """
        GET posted sales invoice headers for this legal entity.
        Filters on InvoiceDate range + dataAreaId. [VERIFY field: InvoiceDate]
        """
        filters = [f"dataAreaId eq '{self._data_area_id}'"]
        if date_from:
            filters.append(f"InvoiceDate ge {date_from}T00:00:00Z")
        if date_to:
            filters.append(f"InvoiceDate le {date_to}T23:59:59Z")
        params = {
            "$filter": " and ".join(filters),
            "cross-company": "true",
            "$top": 1000,
        }
        return self._fetch_all(HEADER_ENTITY, params=params)

    def get_invoice_lines(self, invoice_number):
        """GET line items for one invoice. [VERIFY line entity + key field]"""
        params = {
            "$filter": (f"dataAreaId eq '{self._data_area_id}' and "
                        f"InvoiceNumber eq '{invoice_number}'"),
            "cross-company": "true",
        }
        return self._fetch_all(LINE_ENTITY, params=params)

    # ------------------------------------------------------------------
    # WRITE-BACK: NRS custom fields
    # ------------------------------------------------------------------

    def write_back(self, invoice_number, irn=None, qr_code_url=None, status=None,
                   invoice_date=None):
        """
        PATCH the NRS_* custom fields onto the invoice header after NRS submission.

        F&O OData PATCH needs the full entity key in the URL. For
        SalesInvoiceHeadersV2 the key is typically (dataAreaId, InvoiceNumber)
        [VERIFY — some builds include InvoiceDate]. We build a key predicate and
        fall back gracefully if the key shape differs.

        Returns (ok: bool, detail: str).
        """
        body = {}
        if irn is not None:
            body[NRS_IRN_FIELD] = irn
        if qr_code_url is not None:
            body[NRS_QR_FIELD] = qr_code_url
        if status is not None:
            body[NRS_STATUS_FIELD] = status
        if not body:
            return False, "nothing to write"

        key = f"dataAreaId='{self._data_area_id}',InvoiceNumber='{invoice_number}'"
        if invoice_date:
            key += f",InvoiceDate={invoice_date}"
        url = f"{FNO_DATA_URL}/{HEADER_ENTITY}({key})"
        try:
            resp = self._request("PATCH", url, payload=body)
            return True, f"HTTP {resp.status_code}"
        except requests.exceptions.HTTPError as e:
            detail = getattr(e, "response", None)
            msg = detail.text[:300] if detail is not None else str(e)
            logger.error(f"F&O write_back failed for {invoice_number}: {msg}")
            return False, msg

    # ------------------------------------------------------------------
    # CONNECTION TEST
    # ------------------------------------------------------------------

    def test_connection(self):
        """Read one invoice header to confirm token + entity + legal entity access."""
        try:
            params = {
                "$filter": f"dataAreaId eq '{self._data_area_id}'",
                "cross-company": "true",
                "$top": 1,
            }
            resp = self._request("GET", f"{FNO_DATA_URL}/{HEADER_ENTITY}", params=params)
            rows = resp.json().get("value", [])
            nrs_present = bool(rows) and any("NRS" in k.upper() for k in rows[0])
            return {
                "ok": True,
                "rows": len(rows),
                "nrs_fields_present": nrs_present,
                "sample_fields": sorted(rows[0].keys()) if rows else [],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
