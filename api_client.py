# api_client.py
"""
api_client.py
=============
Cryptware Systems E-Invoicing API client.
Based on official API docs: x-api-key auth, /invoice/* endpoints.
"""

import requests
import logging
import json

logger = logging.getLogger(__name__)


class EInvoiceAPIClient:
    """
    Client for Cryptware Systems E-Invoicing API.
    Auth: x-api-key header (not Bearer token).
    Endpoints: /invoice/generate, /invoice/{id}/status, etc.
    """

    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _request(self, method, endpoint, payload=None):
        """Make an authenticated request to the API."""
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.request(method, url, json=payload, timeout=60)

            # Try to parse JSON response
            try:
                data = resp.json()
            except json.JSONDecodeError:
                data = {"raw_response": resp.text}

            if resp.status_code in (200, 201):
                return {"success": True, "data": data, "status": resp.status_code}
            else:
                return {
                    "success": False,
                    "error": data.get("message") or data.get("error") or resp.text,
                    "data": data,
                    "status": resp.status_code,
                }

        except requests.exceptions.Timeout:
            logger.error(f"API request timeout: {url}")
            return {"success": False, "error": "Request timeout", "status": 408}
        except requests.exceptions.ConnectionError as e:
            logger.error(f"API connection error: {e}")
            return {"success": False, "error": f"Connection error: {e}", "status": 0}
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return {"success": False, "error": str(e), "status": 0}

    # ------------------------------------------------------------------
    # INVOICE APIs
    # ------------------------------------------------------------------

    def generate_invoice(self, payload):
        """POST /invoice/generate - Create and validate e-invoice."""
        logger.info(f"Submitting invoice: {payload.get('document_identifier', 'unknown')}")
        result = self._request("POST", "/invoice/generate", payload)

        if result["success"]:
            irn = (result["data"].get("data") or {}).get("irn") or result["data"].get("irn", "N/A")
            logger.info(f"Invoice submitted successfully: {irn}")
        else:
            logger.error(f"Invoice submission failed: {result.get('error', 'Unknown error')}")

        return result

    def get_invoice(self, invoice_id):
        """GET /invoice/{id} - Get invoice details."""
        return self._request("GET", f"/invoice/{invoice_id}")

    def get_invoice_status(self, invoice_id):
        """GET /invoice/{id}/status - Get invoice status."""
        return self._request("GET", f"/invoice/{invoice_id}/status")

    def get_qr_code(self, invoice_id):
        """GET /invoice/{id}/qrcode - Download QR code."""
        return self._request("GET", f"/invoice/{invoice_id}/qrcode")

    def transmit_invoice(self, irn):
        """POST /invoice/transmit/{irn} - Transmit signed invoice to NRS."""
        logger.info(f"Transmitting invoice to NRS: {irn}")
        return self._request("POST", f"/invoice/transmit/{irn}")

    def retry_invoice(self, invoice_id):
        """POST /invoice/{id}/retry - Retry failed invoice."""
        return self._request("POST", f"/invoice/{invoice_id}/retry")

    def update_payment_status(self, irn, payload):
        """PATCH /invoice/{irn} - Update payment status."""
        return self._request("PATCH", f"/invoice/{irn}", payload)

    def search_invoices(self, params=None):
        """GET /invoice - Search invoices."""
        return self._request("GET", "/invoice")

    def get_statistics(self):
        """GET /invoice/statistics - Invoice statistics."""
        return self._request("GET", "/invoice/statistics")

    # ------------------------------------------------------------------
    # REFERENCE DATA
    # ------------------------------------------------------------------

    def get_tax_categories(self):
        """GET /reference-data/tax-categories"""
        return self._request("GET", "/reference-data/tax-categories")

    def get_countries(self):
        """GET /reference-data/countries"""
        return self._request("GET", "/reference-data/countries")

    def get_currencies(self):
        """GET /reference-data/currencies"""
        return self._request("GET", "/reference-data/currencies")

    def get_invoice_types(self):
        """GET /reference-data/invoice-types"""
        return self._request("GET", "/reference-data/invoice-types")

    def get_hsn_codes(self):
        """GET /reference-data/hsn-codes"""
        return self._request("GET", "/reference-data/hsn-codes")

    def get_isic_codes(self):
        """GET /reference-data/isic-codes"""
        return self._request("GET", "/reference-data/isic-codes")

    # ------------------------------------------------------------------
    # CONNECTION TEST
    # ------------------------------------------------------------------

    def test_connection(self):
        """Test API connectivity using statistics endpoint."""
        try:
            result = self._request("GET", "/invoice/statistics")
            return {
                "ok": result["success"],
                "status": result.get("status"),
                "message": "Connected" if result["success"] else result.get("error", "Failed"),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
