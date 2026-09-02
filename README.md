# Karishma — D365 Finance & Operations → NRS E-Invoicing

Middleware that reads posted sales invoices (and credit notes) from **Microsoft Dynamics 365
Finance & Operations** over OData, maps them to the **Cryptware NRS e-invoicing** payload with
real F&O data, submits them to NRS, and returns the **IRN + QR code**. Ships with a small web
dashboard to review and submit invoices.

```
D365 F&O (OData)  ──▶  this middleware  ──▶  Cryptware NRS API  ──▶  IRN + QR
   invoices              map fields              /invoice/generate
```

---

## 1. Prerequisites

### On the machine that runs this app
- **Python 3.9+** (3.11+ recommended) and `pip`
- Network access to your F&O environment and to the Cryptware NRS API
- (Optional) `git` to clone the repo

### On the Dynamics 365 F&O side (done by the F&O/IT admin)
1. **Azure AD (Entra) app registration** in the F&O tenant, with a **client secret**.
2. **Admin consent** granted for the app against the F&O environment.
3. A **service user** in F&O linked to that app under
   *System administration → Setup → Microsoft Entra ID applications* (map the Application/Client ID
   to the service user), given **high priority** to avoid API throttling.
4. A **security role** assigned to that user granting **read** access to these OData data entities:
   - `SalesInvoiceJournalHeaders` (posted invoice headers)
   - `SalesInvoiceV4Lines` (invoice lines)
   - `CustomersV3` (customer details)
   - `TaxServiceTaxRegistrationNumberCustomers` (customer TIN / registration numbers)
   - `ProductCategoryAssignments` (HSN commodity code + sales category)
   - `ReleasedProductMastersV2` (product master, for commodity code fallback)
5. In the customer & product master, populate the fields the payload needs (see **Field mapping**).

### On the Cryptware side
- A **Cryptware NRS API key** (`x-api-key`) for the supplier/taxpayer.

---

## 2. Install

```bash
# clone
git clone https://github.com/OsamaASidd/F-O-NRS-E-Invoicing.git
cd F-O-NRS-E-Invoicing

# create & activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# install dependencies
pip install -r requirements.txt
```

`requirements.txt`: **Flask**, **requests**, **msal**.

---

## 3. Configure

Copy the template and fill in your real values:

```bash
# Windows
copy fno_config.example.py fno_config.py
# macOS/Linux
cp fno_config.example.py fno_config.py
```

Edit `fno_config.py`:

| Setting | What it is |
|---|---|
| `FNO_TENANT_ID` | Azure AD Directory (Tenant) ID |
| `FNO_CLIENT_ID` | Azure AD Application (Client) ID |
| `FNO_CLIENT_SECRET` | Client secret **value** |
| `FNO_BASE_URL` | F&O environment URL (sandbox or production) |
| `DATA_AREA_ID` | Legal entity / `dataAreaId` (e.g. `KL`) |
| `API_BASE_URL` | Cryptware NRS API base URL (preprod vs prod) |
| `KARISHMA["api_key"]` | Cryptware `x-api-key` for the supplier |

> ⚠️ **`fno_config.py` holds live secrets and is gitignored — never commit it.** Manage the secret
> via a vault or environment injection in production.

---

## 4. Run

```bash
python app_fno.py
```

Open the dashboard: **http://localhost:5001**

- **Refresh from F&O** — pull posted KL invoices/credit notes
- **Preview** — see the exact Cryptware payload (and any data warnings) before sending
- **Post** — submit to NRS; the IRN, status and QR link are stored and shown

### Verify connectivity first (optional)
```bash
python diag_fno.py        # read-only: token + entity access probe
```

---

## 5. Field mapping (F&O → NRS)

| NRS field | F&O source |
|---|---|
| Invoice header / totals | `SalesInvoiceJournalHeaders` |
| Line items | `SalesInvoiceV4Lines` |
| Customer name / email / address | `CustomersV3` |
| **TIN** | `TaxServiceTaxRegistrationNumberCustomers` where `TaxRegstrationType = 'TIN'` |
| **HSN code** | `ProductCategoryAssignments` (Commodity Code Hierarchy), formatted `0000.00` |
| **Product category** | `ProductCategoryAssignments` (Sales Hierarchy) |
| Unit of measure | line `SalesUnitSymbol` |
| Phone | customer phone, normalized to E.164 (`+234…`) |

If TIN/HSN/phone are missing or malformed in F&O, **Preview** flags a warning and the payload falls
back to a placeholder — populate the master data in F&O for a clean, compliant invoice.

---

## 6. Project structure

```
app_fno.py                  Flask dashboard (port 5001)
fno_service.py              read F&O + map fields + build payload + submit
fno_client.py               F&O OData client (token, reads, optional writeback)
api_client.py               Cryptware NRS API client
db.py                       local SQLite (submission status / IRN cache)
fno_config.example.py       config template  → copy to fno_config.py
diag_fno.py                 read-only connectivity probe
fno_post_*.py               standalone test scripts (single invoice / credit note)
templates/fno_dashboard.html  dashboard UI
requirements.txt
```

---

## 7. Notes

- **Credit notes** (voucher `SCN-*`) require a `cancel_references` block pointing at the original
  invoice's IRN, and the original must already be on NRS. Post the original invoice first.
- **Writeback** of IRN/QR back into F&O is **not** enabled in this phase (invoices are downloaded
  from the middleware). The standard posted-invoice entity is read-only; a custom writable entity or
  OData action is required to enable writeback later.
- **Production**: run behind a proper WSGI server (e.g. `waitress`/`gunicorn`) rather than Flask's
  dev server, over HTTPS, and switch `API_BASE_URL` to the Cryptware production endpoint and
  `FNO_BASE_URL` to the production F&O environment.

### Run in production (example, Windows-friendly)
```bash
pip install waitress
waitress-serve --port=5001 app_fno:app
```
