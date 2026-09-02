"""
pdf_gen_fno.py
==============
Render a Karishma invoice PDF that mirrors the D365 F&O invoice print layout,
with the NRS IRN and QR code added. Uses reportlab only (no extra deps).
"""

import io
import os
import logging
import requests
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from fno_config import SUPPLIER_PROFILE

logger = logging.getLogger(__name__)

W, H = A4                      # 595 x 842 pt
L, R = 40, W - 40              # left / right margins
GREY = (0.55, 0.55, 0.55)
DARK = (0.10, 0.10, 0.10)
RED  = (0.78, 0.09, 0.11)
LINE = (0.80, 0.80, 0.80)


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
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d %B %Y")
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


def _logo(c, x, top_y, height=26.0, max_w=170.0):
    """Draw the logo so its TOP edge sits at top_y, scaled to `height` keeping aspect."""
    p = SUPPLIER_PROFILE.get("logo_path")
    if p and os.path.exists(p):
        sz = _png_size(p)
        w = height * (sz[0] / sz[1]) if sz and sz[1] else 110.0
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
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(x + height / 2, top_y - height + 6, "K")
    c.setFillColorRGB(*DARK)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(x + height + 6, top_y - height + 6, "KARISHMA")


def _field(c, x, y, label, value, val_x, val_bold=False, val_right=None):
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*GREY)
    c.drawString(x, y, label)
    c.setFillColorRGB(*DARK)
    c.setFont("Helvetica-Bold" if val_bold else "Helvetica", 8)
    if val_right is not None:
        c.drawRightString(val_right, y, str(value or ""))
    else:
        c.drawString(val_x, y, str(value or ""))


def generate(detail, out_path):
    """detail: dict from fno_service.invoice_detail(); writes PDF to out_path."""
    c = canvas.Canvas(out_path, pagesize=A4)
    sup = SUPPLIER_PROFILE
    cust = detail["customer"]
    refs = detail["refs"]
    cur = detail["currency"]

    # ---- header band ----
    _logo(c, L, H - 32)   # logo top near page top; sized to 26pt tall
    # QR + IRN top-right
    if detail.get("irn"):
        _qr_flowable(c, detail["irn"], detail.get("qr_url"), R - 78, H - 92, 78)
        c.setFont("Helvetica", 6)
        c.setFillColorRGB(*GREY)
        c.drawRightString(R, H - 100, "IRN")
        c.setFont("Helvetica-Bold", 6.5)
        c.setFillColorRGB(*DARK)
        c.drawRightString(R, H - 109, detail["irn"])

    title = "Credit Note" if detail["is_credit_note"] else "Invoice"
    c.setFillColorRGB(*DARK)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(L, H - 105, title)
    c.setFont("Helvetica", 8.5)
    c.drawString(L, H - 120, detail["invoice_num"])
    c.drawString(L + 90, H - 120, _fmt_date(detail["invoice_date"]))

    # ---- two info columns ----
    ly = H - 150
    c.setFont("Helvetica", 7.5); c.setFillColorRGB(*GREY)
    c.drawString(L, ly, "Invoice for")
    c.drawString(320, ly, "References and Terms")
    ly -= 14
    c.setFont("Helvetica-Bold", 9); c.setFillColorRGB(*DARK)
    c.drawString(L, ly, cust["name"])

    left = [
        ("Contact person", ""),
        ("Enterprise number", cust.get("enterprise_number")),
        ("Tax registration number", cust.get("tin")),
    ]
    yy = ly - 14
    for lbl, val in left:
        _field(c, L, yy, lbl, val, L + 120); yy -= 12
    # address (wrapped so it never runs into the right column)
    c.setFont("Helvetica", 7.5); c.setFillColorRGB(*GREY); c.drawString(L, yy, "Address")
    c.setFillColorRGB(*DARK); c.setFont("Helvetica", 8)
    addr_wrapped = []
    for al in cust.get("address_lines", []):
        addr_wrapped += _wrap(al, 30)
    for al in addr_wrapped:
        c.drawString(L + 120, yy, al); yy -= 11
    yy -= 2
    _field(c, L, yy, "Email Address", cust.get("email"), L + 120); yy -= 12
    _field(c, L, yy, "Phone Number", cust.get("phone"), L + 120)

    # right column
    ry = ly - 14
    right = [
        ("Sales order", refs.get("sales_order")),
        ("Requisition", refs.get("requisition")),
        ("Your reference", refs.get("your_reference")),
        ("Our reference", ""),
        ("Delivery terms", refs.get("delivery_terms")),
        ("Invoice account", refs.get("invoice_account")),
        ("Payment", refs.get("payment")),
        ("Payment reference", ""),
    ]
    for lbl, val in right:
        _field(c, 320, ry, lbl, val, 430); ry -= 12
    ry -= 4
    _field(c, 320, ry, "Total amount due", _money(detail["total"]), 0, val_bold=True, val_right=R); ry -= 13
    _field(c, 320, ry, "Due date", _fmt_date(refs.get("due_date")), 430)

    # ---- line items table ----
    ty = min(yy, ry) - 26
    cols = {"desc": L, "qty": 256, "unit": 260, "price": 340,
            "disc": 388, "amt": 448, "vat": 500, "gross": R}
    c.setStrokeColorRGB(*LINE); c.setLineWidth(0.6)
    c.line(L, ty + 12, R, ty + 12)
    c.setFont("Helvetica-Bold", 6.5); c.setFillColorRGB(*DARK)
    c.drawString(cols["desc"], ty, "Description")
    c.drawRightString(cols["qty"], ty, "Quantity")
    c.drawString(cols["unit"], ty, "Unit")
    c.drawRightString(cols["price"], ty, "Unit price")
    c.drawRightString(cols["disc"], ty, "Discount")
    c.drawRightString(cols["amt"], ty, "Amount")
    c.drawRightString(cols["vat"], ty, "VAT amount")
    c.drawRightString(cols["gross"], ty, "Gross amount")
    c.line(L, ty - 4, R, ty - 4)
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
        # sub row: item (base) / category / HS-ISIC / tax rate
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 6)
        c.drawString(cols["desc"], sub_y - 9, f"Item  {str(ln['item']).split('-')[0]}")
        c.drawString(cols["desc"] + 90, sub_y - 9, (f"Category  {ln['category']}")[:44])
        c.drawRightString(cols["amt"], sub_y - 9, f"HS/ISIC  {ln['hs_isic']}")
        c.drawRightString(cols["gross"], sub_y - 9, f"Tax Rate  VAT {ln['tax_rate']}%")
        ty -= (24 if len(dlines) == 1 else 33)
        if ty < 210:  # page break guard
            c.showPage(); ty = H - 60

    c.setStrokeColorRGB(*LINE); c.line(L, ty + 6, R, ty + 6)
    ty -= 6

    # ---- totals block (right aligned) ----
    tot = [
        ("Sales subtotal amount", _money(detail["sub_total"])),
        ("Total discount", "-"),
        ("Total charges", "-"),
        ("Net amount", _money(detail["sub_total"])),
        ("Sales tax", _money(detail["tax_total"])),
        ("Round-off", "-"),
    ]
    ty2 = ty
    for lbl, val in tot:
        c.setFont("Helvetica", 7.5); c.setFillColorRGB(*GREY)
        c.drawRightString(R - 90, ty2, lbl)
        c.setFillColorRGB(*DARK); c.setFont("Helvetica", 8)
        c.drawRightString(R, ty2, val); ty2 -= 13
    ty2 -= 2
    c.setFont("Helvetica-Bold", 9); c.setFillColorRGB(*DARK)
    c.drawRightString(R - 90, ty2, "Total")
    c.drawRightString(R, ty2, f"{_money(detail['total'])}  {cur}")

    # ---- payment instructions (left) ----
    py = ty - 6
    c.setFont("Helvetica-Bold", 8.5); c.setFillColorRGB(*DARK)
    c.drawString(L, py, "Payment instructions"); py -= 13
    for lbl, val in [("Bank account number", sup["bank_account"]),
                     ("Account name", sup["bank_account_name"]),
                     ("Bank name", sup["bank_name"])]:
        _field(c, L, py, lbl, val, L + 110); py -= 12

    # ---- footer ----
    c.setStrokeColorRGB(*LINE); c.line(L, 42, R, 42)
    c.setFont("Helvetica", 7); c.setFillColorRGB(*GREY)
    footer = (f"{sup['name']}   RC {sup['rc']}   TIN {sup['tin']}   "
              f"{sup['email']}   {sup['address']}")
    c.drawString(L, 32, footer)
    c.drawRightString(R, 32, "1 of 1")

    c.showPage()
    c.save()
    return out_path
