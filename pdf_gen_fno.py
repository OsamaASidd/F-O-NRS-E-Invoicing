"""
pdf_gen_fno.py
==============
Render a Karishma invoice PDF that mirrors the D365 F&O invoice print layout
(per the client's SalesModTemplate Excel export: fonts, colors, borders),
with the NRS IRN and QR code added at the bottom of the page.
Uses reportlab only (no extra deps).
"""

import io
import os
import logging
import requests
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from fno_config import SUPPLIER_PROFILE, BANK_ACCOUNTS, VAT_CODE, INVOICE_NOTICE, INVOICE_THANKS

logger = logging.getLogger(__name__)

W, H = A4                      # 595 x 842 pt
L, R = 40, W - 40              # left / right margins

# Colors lifted from the client's Excel template (SalesModTemplate):
GREY_LIGHT = (0x76 / 255, 0x76 / 255, 0x76 / 255)   # section headers, sub-row detail, VAT details
LABEL_GREY = (0x59 / 255, 0x59 / 255, 0x59 / 255)   # field labels (Invoice details, totals, payment)
DARK       = (0x1A / 255, 0x1A / 255, 0x1A / 255)   # values / body text
BLACK      = (0x00, 0x00, 0x00)                     # bold supplier name in footer
RED        = (0.78, 0.09, 0.11)                     # logo-fallback tile only
LINE       = (0x76 / 255, 0x76 / 255, 0x76 / 255)   # thin rule color (matches template's grey borders)


def _money(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "-"


def _wrap(text, maxchars):
    """Greedy word-wrap into lines of at most maxchars."""
    words, lines, cur = str(text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= maxchars:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _fmt_date(iso):
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return iso or ""


def _qr_flowable(c, data, url, x, y, size):
    """Draw the NRS QR: prefer the official PNG from `url`, else render `data`."""
    if url:
        try:
            img = requests.get(url, timeout=20).content
            c.drawImage(ImageReader(io.BytesIO(img)), x, y, size, size,
                        preserveAspectRatio=True, mask="auto")
            return
        except Exception as e:
            logger.warning(f"QR image download failed: {e}")
    if data:
        w = QrCodeWidget(data)
        b = w.getBounds()
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0,
                                           size / (b[3] - b[1]), 0, 0])
        d.add(w)
        renderPDF.draw(d, c, x, y)


def _png_size(path):
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
    except Exception:
        pass
    return None


def _logo(c, x, top_y, height=49.0, max_w=230.0):
    """Draw the logo so its TOP edge sits at top_y, scaled to `height` keeping aspect
    (~38-39% of the A4 page width, per client feedback)."""
    p = SUPPLIER_PROFILE.get("logo_path")
    if p and os.path.exists(p):
        sz = _png_size(p)
        w = height * (sz[0] / sz[1]) if sz and sz[1] else 178.0
        w = min(w, max_w)
        try:
            c.drawImage(p, x, top_y - height, width=w, height=height, mask="auto")
            return
        except Exception:
            pass
    # fallback: red "K" tile + KARISHMA wordmark
    c.setFillColorRGB(*RED)
    c.rect(x, top_y - height, height, height, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(x + height / 2, top_y - height + 10, "K")
    c.setFillColorRGB(*DARK)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(x + height + 8, top_y - height + 10, "KARISHMA")


def _field(c, x, y, label, value, val_x, val_bold=False, label_color=LABEL_GREY, val_color=DARK, size=7.5):
    c.setFont("Helvetica", size)
    c.setFillColorRGB(*label_color)
    c.drawString(x, y, label)
    c.setFillColorRGB(*val_color)
    c.setFont("Helvetica-Bold" if val_bold else "Helvetica", size + 0.5)
    c.drawString(val_x, y, str(value or ""))


def generate(detail, out_path):
    """detail: dict from fno_service.invoice_detail(); writes PDF to out_path."""
    c = canvas.Canvas(out_path, pagesize=A4)
    sup = SUPPLIER_PROFILE
    cust = detail["customer"]
    refs = detail["refs"]
    cur = detail["currency"]

    # ---- header band ----
    _logo(c, L, H - 24)
    title = "Credit Note" if detail["is_credit_note"] else "Invoice"
    c.setFillColorRGB(*DARK)
    c.setFont("Helvetica", 22)
    c.drawString(L, H - 108, title)

    # ---- two info columns ----
    ly = H - 148
    c.setFont("Helvetica", 7.5); c.setFillColorRGB(*GREY_LIGHT)
    c.drawString(L, ly, "Invoice for")
    c.drawString(320, ly, "Invoice details")
    ly -= 14
    c.setFont("Helvetica-Bold", 9); c.setFillColorRGB(*DARK)
    c.drawString(L, ly, cust["name"])

    # left column: address directly under the name, then registration + contact
    yy = ly - 13
    c.setFont("Helvetica", 8); c.setFillColorRGB(*DARK)
    for al in cust.get("address_lines", []):
        for line in _wrap(al, 62):
            c.drawString(L, yy, line); yy -= 11
    yy -= 2
    left = [
        ("Enterprise number", cust.get("enterprise_number")),
        ("Tax registration number", cust.get("tin")),
        ("Contact", cust.get("contact")),
    ]
    for lbl, val in left:
        _field(c, L, yy, lbl, val, L + 130); yy -= 12

    # right column: invoice details — values LEFT-aligned under the labels
    ry = ly - 13
    right = [
        ("Invoice number", detail["invoice_num"]),
        ("Invoice date", _fmt_date(detail["invoice_date"])),
        ("Sales order", refs.get("sales_order")),
        ("Delivery terms", refs.get("delivery_terms")),
        ("Payment terms", refs.get("payment")),
        ("Invoice account", refs.get("invoice_account")),
        ("Requisition", refs.get("requisition")),
        ("Your reference", refs.get("your_reference")),
        ("Our reference", ""),
        ("Payment reference", ""),
    ]
    for lbl, val in right:
        _field(c, 320, ry, lbl, val, 420); ry -= 12

    # ---- line items table ----
    ty = min(yy, ry) - 24
    cols = {"desc": L, "qty": 256, "unit": 260, "price": 340,
            "disc": 388, "amt": 448, "vat": 500, "gross": R}
    c.setStrokeColorRGB(*LINE); c.setLineWidth(0.6)
    c.setFont("Helvetica-Bold", 6.5); c.setFillColorRGB(*DARK)
    c.drawString(cols["desc"], ty, "Description")
    c.drawRightString(cols["qty"], ty, "Quantity")
    c.drawString(cols["unit"], ty, "Unit")
    c.drawRightString(cols["price"], ty, "Unit price")
    c.drawRightString(cols["disc"], ty, "Discount")
    c.drawRightString(cols["amt"], ty, "Amount")
    c.drawRightString(cols["vat"], ty, "VAT amount")
    c.drawRightString(cols["gross"], ty, "Gross amount")
    c.line(L, ty - 4, R, ty - 4)      # only a bottom rule under the header (matches template)
    ty -= 18

    for ln in detail["lines"]:
        dlines = _wrap(ln["description"], 46)[:2]
        c.setFillColorRGB(*DARK); c.setFont("Helvetica", 7)
        c.drawString(cols["desc"], ty, dlines[0])
        c.setFont("Helvetica", 6.8)
        c.drawRightString(cols["qty"], ty, f"{ln['quantity']:,.2f}")
        c.drawString(cols["unit"], ty, ln["unit"])
        c.drawRightString(cols["price"], ty, _money(ln["unit_price"]))
        c.drawRightString(cols["disc"], ty, "-" if not ln["discount"] else _money(ln["discount"]))
        c.drawRightString(cols["amt"], ty, _money(ln["amount"]))
        c.drawRightString(cols["vat"], ty, _money(ln["vat"]))
        c.drawRightString(cols["gross"], ty, _money(ln["gross"]))
        sub_y = ty
        if len(dlines) > 1:
            c.setFillColorRGB(*DARK); c.setFont("Helvetica", 7)
            c.drawString(cols["desc"], ty - 9, dlines[1])
            sub_y = ty - 9
        # sub row 1: item number / HS-ISIC
        c.setFillColorRGB(*GREY_LIGHT); c.setFont("Helvetica", 6)
        c.drawString(cols["desc"], sub_y - 9, f"Item number  {str(ln['item']).split('-')[0]}")
        c.drawString(cols["price"], sub_y - 9, f"HS/ISIC  {ln['hs_isic']}")
        # sub row 2: category / tax rate / currency
        c.drawString(cols["desc"], sub_y - 18, (f"Category  {ln['category']}")[:50])
        c.drawRightString(cols["vat"], sub_y - 18, f"Tax rate  VAT {ln['tax_rate']}%")
        c.drawRightString(cols["gross"], sub_y - 18, f"Currency  {cur}")
        ty = sub_y - 18 - 10
        if ty < 240:  # page break guard (leaves room for totals/VAT/footer below)
            c.showPage(); ty = H - 60

    ty -= 4

    # ---- payment instructions (left) + totals (right) ----
    py = ty
    c.setFont("Helvetica", 8.5); c.setFillColorRGB(*LABEL_GREY)
    c.drawString(L, py, "Payment instructions"); py -= 13
    c.setFont("Helvetica", 7); c.setFillColorRGB(*LABEL_GREY)
    c.drawString(L, py, "Bank name")
    c.drawString(L + 130, py, "Account number")
    c.drawString(L + 220, py, "Account name")
    py -= 12
    c.setFillColorRGB(*DARK); c.setFont("Helvetica", 7.5)
    for b in BANK_ACCOUNTS:
        c.drawString(L, py, b["bank_name"])
        c.drawString(L + 130, py, b["account_number"])
        c.drawString(L + 220, py, b["account_name"])
        py -= 12

    ty2 = ty
    tot = [
        ("Subtotal", _money(detail["sub_total"])),
        ("Total charges", "-"),
        ("Net amount", _money(detail["sub_total"])),
        ("VAT amount", _money(detail["tax_total"])),
        ("Rounding", "-"),
    ]
    for lbl, val in tot:
        c.setFont("Helvetica", 7.5); c.setFillColorRGB(*LABEL_GREY)
        c.drawRightString(R - 90, ty2, lbl)
        c.setFillColorRGB(*DARK); c.setFont("Helvetica", 8)
        c.drawRightString(R, ty2, val); ty2 -= 13
    c.setStrokeColorRGB(*LINE); c.line(R - 110, ty2 + 7, R, ty2 + 7)   # rule between Rounding and Total
    ty2 -= 3
    c.setFont("Helvetica-Bold", 9); c.setFillColorRGB(*DARK)
    c.drawRightString(R - 90, ty2, "Total")
    c.drawRightString(R, ty2, _money(detail["total"])); ty2 -= 13
    c.setFont("Helvetica", 7.5); c.setFillColorRGB(*LABEL_GREY)
    c.drawRightString(R - 90, ty2, "Currency")
    c.setFillColorRGB(*DARK); c.setFont("Helvetica", 8)
    c.drawRightString(R, ty2, cur); ty2 -= 13
    c.setFont("Helvetica", 7.5); c.setFillColorRGB(*LABEL_GREY)
    c.drawRightString(R - 90, ty2, "Due date")
    c.setFillColorRGB(*DARK); c.setFont("Helvetica", 8)
    c.drawRightString(R, ty2, _fmt_date(refs.get("due_date")))

    by = min(py, ty2) - 16

    # ---- VAT details ----
    blended_rate = (round(detail["tax_total"] / detail["sub_total"] * 100, 1)
                    if detail["sub_total"] else 0.0)
    c.setFont("Helvetica", 8.5); c.setFillColorRGB(*GREY_LIGHT)
    c.drawString(L, by, "VAT details"); by -= 13

    vcols = [L, L + 110, L + 220, L + 350, R - 50]
    c.setFont("Helvetica", 6.8); c.setFillColorRGB(*GREY_LIGHT)
    for x, lbl in zip(vcols, ["Non-taxable", "Taxable", "Reverse charge balance",
                              "Reverse charge amount", "Currency"]):
        c.drawString(x, by, lbl)
    c.setStrokeColorRGB(*LINE); c.line(L, by - 3, R, by - 3)
    by -= 14
    c.setFillColorRGB(*GREY_LIGHT); c.setFont("Helvetica", 7.5)
    for x, val in zip(vcols, ["-", _money(detail["sub_total"]), "-", "-", cur]):
        c.drawString(x, by, val)
    by -= 15

    c.setFont("Helvetica", 6.8); c.setFillColorRGB(*GREY_LIGHT)
    for x, lbl in zip(vcols, ["Print code", "VAT code", "Amount origin", "VAT amount", "Currency"]):
        c.drawString(x, by, lbl)
    c.setStrokeColorRGB(*LINE); c.line(L, by - 3, R, by - 3)
    by -= 14
    c.setFillColorRGB(*GREY_LIGHT); c.setFont("Helvetica", 7.5)
    for x, val in zip(vcols, [f"VAT {blended_rate}%", VAT_CODE, _money(detail["sub_total"]),
                              _money(detail["tax_total"]), cur]):
        c.drawString(x, by, val)
    by -= 16

    # Invoice reference number + IRN on the same row
    c.setFont("Helvetica", 7.5); c.setFillColorRGB(*GREY_LIGHT)
    c.drawString(L, by, "Invoice reference number")
    c.setFillColorRGB(*DARK); c.setFont("Helvetica", 8)
    c.drawString(L + 150, by, str(refs.get("invoice_reference_number") or ""))
    if detail.get("irn"):
        c.setFont("Helvetica", 7.5); c.setFillColorRGB(*GREY_LIGHT)
        c.drawString(L + 260, by, "IRN")
        c.setFont("Helvetica-Bold", 6.5); c.setFillColorRGB(*DARK)
        c.drawString(L + 282, by, detail["irn"])
    by -= 20

    # ---- notice / thank-you ----
    c.setFont("Helvetica", 7.5); c.setFillColorRGB(*DARK)
    c.drawString(L, by, INVOICE_NOTICE); by -= 12
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(L, by, INVOICE_THANKS)

    # ---- QR (bottom-right; no footer rule — template has none) ----
    if detail.get("irn"):
        _qr_flowable(c, detail["irn"], detail.get("qr_url"), R - 56, 55, 56)

    # ---- footer (no divider line; Aptos-matched colors/sizes/weights) ----
    c.setFont("Helvetica-Bold", 8); c.setFillColorRGB(*BLACK)
    c.drawString(L, 20, sup["name"])
    name_w = c.stringWidth(sup["name"], "Helvetica-Bold", 8)
    c.setFont("Helvetica", 8); c.setFillColorRGB(*DARK)
    c.drawString(L + name_w + 14, 20, f"RC {sup['rc']}     TIN {sup['tin']}")
    c.setFillColorRGB(*GREY_LIGHT)
    c.drawRightString(R, 20, "1 of 1")
    c.drawString(L, 9, f"{sup['address']}     {sup['email']}")

    c.showPage()
    c.save()
    return out_path
