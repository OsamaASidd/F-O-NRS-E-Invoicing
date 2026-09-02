# app_fno.py — Karishma D365 F&O -> NRS e-invoicing dashboard
import os
import logging
from flask import Flask, render_template, jsonify, request
import fno_service as svc

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


if __name__ == "__main__":
    print("\n  Karishma (D365 F&O) NRS E-Invoicing Dashboard")
    print("  http://localhost:5001\n")
    app.run(debug=False, host="0.0.0.0", port=5001)
