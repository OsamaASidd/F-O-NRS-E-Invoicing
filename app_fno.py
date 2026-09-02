# app_fno.py — Karishma D365 F&O -> NRS e-invoicing dashboard
import os
import logging
from flask import Flask, render_template, jsonify, request, send_file, abort
import fno_service as svc
import pdf_gen_fno
from fno_config import PDF_DIR

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("logs/fno.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("fno_dashboard.html")


@app.route("/api/invoices")
def api_invoices():
    try:
        rows = svc.list_invoices()
        stats = {
            "total": len(rows),
            "posted": sum(1 for r in rows if r["status"] == "posted"),
            "pending": sum(1 for r in rows if r["status"] == "pending"),
            "failed": sum(1 for r in rows if r["status"] == "failed"),
        }
        return jsonify({"ok": True, "invoices": rows, "stats": stats})
    except Exception as e:
        logger.exception("list invoices failed")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/preview/<path:voucher>")
def api_preview(voucher):
    try:
        payload, warnings = svc.build_payload(voucher)
        return jsonify({"ok": payload is not None, "payload": payload, "warnings": warnings})
    except Exception as e:
        logger.exception("preview failed")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/post/<path:voucher>", methods=["POST"])
def api_post(voucher):
    try:
        return jsonify(svc.post_invoice(voucher))
    except Exception as e:
        logger.exception("post failed")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/download/<path:voucher>")
def download_pdf(voucher):
    try:
        detail = svc.invoice_detail(voucher)
        if not detail:
            abort(404, "Invoice not found")
        if not detail.get("irn"):
            abort(400, "Invoice not yet posted to NRS (no IRN/QR) - post it first")
        os.makedirs(PDF_DIR, exist_ok=True)
        safe = voucher.replace("/", "_").replace(" ", "_")
        path = os.path.join(PDF_DIR, f"{safe}.pdf")
        pdf_gen_fno.generate(detail, path)
        return send_file(path, as_attachment=True, download_name=f"{detail['invoice_num']}.pdf")
    except Exception as e:
        logger.exception("pdf generation failed")
        abort(500, str(e))


if __name__ == "__main__":
    print("\n  Karishma (D365 F&O) NRS E-Invoicing Dashboard")
    print("  http://localhost:5001\n")
    app.run(debug=False, host="0.0.0.0", port=5001)
