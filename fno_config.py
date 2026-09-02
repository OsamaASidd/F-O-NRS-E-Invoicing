# fno_config.py — Karishma F&O -> NRS configuration.
# Secrets are loaded from the environment / a local .env file (see .env.example).
# This file contains NO secrets and is safe to commit.

import os

try:
    from dotenv import load_dotenv
    load_dotenv()                       # load variables from a local .env if present
except ImportError:
    pass                                # dotenv optional; real env vars still work


def _env(name, default=""):
    v = os.getenv(name)
    return v if v not in (None, "") else default


# --- Azure AD / Entra app (from environment) ---
FNO_TENANT_ID     = _env("FNO_TENANT_ID")
FNO_CLIENT_ID     = _env("FNO_CLIENT_ID")
FNO_CLIENT_SECRET = _env("FNO_CLIENT_SECRET")

# --- F&O environment ---
FNO_BASE_URL = _env("FNO_BASE_URL",
                    "https://karishma-test.sandbox.operations.dynamics.com").rstrip("/")
FNO_SCOPE    = [FNO_BASE_URL + "/.default"]
FNO_DATA_URL = FNO_BASE_URL + "/data"
DATA_AREA_ID = _env("DATA_AREA_ID", "KL")

# --- Cryptware NRS API ---
API_BASE_URL = _env("CRYPTWARE_API_BASE_URL", "https://preprod-api.cryptwaresystemsltd.com/")

# --- Supplier (identified to Cryptware by api_key) ---
KARISHMA = {
    "api_key": _env("CRYPTWARE_API_KEY"),
    "name": _env("SUPPLIER_NAME", "KARISHMA CDK LIMITED"),
    "data_area_id": DATA_AREA_ID,
}

# --- Supplier profile (footer + payment instructions on the printed invoice) ---
# Not secret; safe to keep here. Override individual values via env if desired.
SUPPLIER_PROFILE = {
    "name": _env("SUPPLIER_NAME", "Karishma CDK Limited"),
    "rc": _env("SUPPLIER_RC", "778735"),
    "tin": _env("SUPPLIER_TIN", "17825683-0001"),
    "email": _env("SUPPLIER_EMAIL", "info@karishma-ng.com"),
    "address": _env("SUPPLIER_ADDRESS", "KM 38, Lagos Ibadan Expressway, Sagamu 110113, Ogun State, Nigeria"),
    "bank_account": _env("SUPPLIER_BANK_ACCOUNT", "0002424253"),
    "bank_account_name": _env("SUPPLIER_BANK_ACCOUNT_NAME", "Karishma CDK Limited"),
    "bank_name": _env("SUPPLIER_BANK_NAME", "Standard Chartered Bank Nigeria Limited"),
    "logo_path": _env("SUPPLIER_LOGO_PATH", "static/Karishma logo.png"),
}

# --- App / DB ---
DB_PATH = _env("DB_PATH", "einvoice_fno.db")
PDF_DIR = "invoices"
