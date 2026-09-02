# fno_config.example.py
# -------------------------------------------------------------------
# Copy this file to `fno_config.py` and fill in your real values.
#   cp fno_config.example.py fno_config.py   (macOS/Linux)
#   copy fno_config.example.py fno_config.py (Windows)
# `fno_config.py` is gitignored — never commit real secrets.
# -------------------------------------------------------------------

# --- Azure AD / Entra app registration (D365 F&O tenant) ---
FNO_TENANT_ID     = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"   # Directory (Tenant) ID
FNO_CLIENT_ID     = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"   # Application (Client) ID
FNO_CLIENT_SECRET = "your-client-secret-here"               # Client secret VALUE

# --- F&O environment ---
# Sandbox/UAT: https://<name>-test.sandbox.operations.dynamics.com
# Production:  https://<name>.operations.dynamics.com
FNO_BASE_URL   = "https://your-org-test.sandbox.operations.dynamics.com"
FNO_SCOPE      = [FNO_BASE_URL.rstrip("/") + "/.default"]
FNO_DATA_URL   = FNO_BASE_URL.rstrip("/") + "/data"
DATA_AREA_ID   = "KL"                                        # Legal entity / dataAreaId

# --- Cryptware NRS e-invoicing API ---
API_BASE_URL   = "https://preprod-api.cryptwaresystemsltd.com/"   # preprod; swap for prod at go-live

# --- Supplier (identified to Cryptware by api_key) ---
KARISHMA = {
    "api_key": "sk_live_your_cryptware_api_key_here",
    "name": "YOUR COMPANY LIMITED",
    "data_area_id": "KL",
}

# --- App / DB ---
DB_PATH = "einvoice_fno.db"
PDF_DIR = "invoices"
