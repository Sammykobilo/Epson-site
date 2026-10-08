import base64
import hashlib
import hmac
import json
import os
import re
import smtplib
import sqlite3
import ssl
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()
ZIP_DIR = Path(os.environ.get("PRODUCT_ZIP_DIR", ROOT / "private-products")).resolve()
DB_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "data" / "orders.sqlite3")).resolve()
PORT = int(os.environ.get("PORT", "8000"))
SITE_URL = os.environ.get("SITE_URL", f"http://localhost:{PORT}").rstrip("/")
USD_PRICE_CENTS = 1000
MAX_EMAIL_ATTACHMENTS = 18 * 1024 * 1024
MPESA_ENV = os.environ.get("MPESA_ENV", "sandbox").lower()
MPESA_BASE = (
    "https://api.safaricom.co.ke"
    if MPESA_ENV == "production"
    else "https://sandbox.safaricom.co.ke"
)

PRODUCTS = {
    "epson-tx550w-adjustment-program": "TX550W",
    "epson-sx510w-adjustment-program": "SX510W",
    "epson-l3111-adjustment-program": "L3111",
    "epson-l3110-adjustment-program": "L3110",
    "epson-l3150-adjustment-program": "L3150",
    "epson-l3115-adjustment-program": "L3115",
    "epson-l3116-adjustment-program": "L3116",
    "epson-l130-adjustment-program": "L130",
    "epson-l220-adjustment-program": "L220",
    "epson-l310-adjustment-program": "L310",
    "epson-l360-adjustment-program": "L360",
    "epson-l365-adjustment-program": "L365",
    "epson-l375-adjustment-program": "L375",
    "epson-l200-adjustment-program": "L200",
    "epson-l380-adjustment-program": "L380",
    "epson-l381-adjustment-program": "L381",
    "epson-l382-adjustment-program": "L382",
    "epson-l383-adjustment-program": "L383",
    "epson-l385-adjustment-program": "L385",
    "epson-l485-adjustment-program": "L485",
    "epson-px660-adjustment-program": "PX660",
}

PRODUCT_ARCHIVES = {
    "epson-tx550w-adjustment-program": "tx550w-sx510w.rar",
    "epson-sx510w-adjustment-program": "tx550w-sx510w.rar",
    "epson-l3111-adjustment-program": "l3110-l3111.zip",
    "epson-l3110-adjustment-program": "l3110-l3111.zip",
    "epson-l130-adjustment-program": "l130-l220-l310-l360-l365.rar",
    "epson-l220-adjustment-program": "l130-l220-l310-l360-l365.rar",
    "epson-l310-adjustment-program": "l130-l220-l310-l360-l365.rar",
    "epson-l360-adjustment-program": "l130-l220-l310-l360-l365.rar",
    "epson-l365-adjustment-program": "l130-l220-l310-l360-l365.rar",
    "epson-l375-adjustment-program": "l375-l475.rar",
    "epson-l200-adjustment-program": "l200.zip",
}

_db_lock = threading.Lock()
_oauth_token = None
_oauth_expiry = 0


def db_connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def product_archive(slug):
    filename = PRODUCT_ARCHIVES.get(slug, f"{slug}.zip")
    archive = (ZIP_DIR / filename).resolve()
    if archive.parent != ZIP_DIR:
        raise ValueError("The configured product archive path is invalid.")
    return archive


def is_supported_archive(archive):
    if zipfile.is_zipfile(archive):
        return True
    try:
        with archive.open("rb") as source:
            return source.read(6) == b"Rar!\x1a\x07"
    except OSError:
        return False


@contextmanager
def db_session():
    connection = db_connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    with db_session() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                method TEXT NOT NULL,
                status TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'not_sent',
                items_json TEXT NOT NULL,
                total_usd_cents INTEGER NOT NULL,
                total_kes INTEGER,
                usd_kes_rate REAL,
                checkout_request_id TEXT UNIQUE,
                provider_payment_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def provider_json(url, method="GET", payload=None, headers=None, timeout=20):
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"Payment provider returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Payment provider request failed: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Payment provider returned an invalid response.") from exc


def get_fx_rate():
    response = provider_json("https://open.er-api.com/v6/latest/USD")
    rate = response.get("rates", {}).get("KES")
    if response.get("result") != "success" or not isinstance(rate, (int, float)) or rate <= 0:
        raise RuntimeError("The USD-to-KES exchange-rate service did not return a valid rate.")
    return float(rate)


def get_mpesa_token():
    global _oauth_token, _oauth_expiry
    if _oauth_token and time.time() < _oauth_expiry:
        return _oauth_token
    consumer_key = os.environ.get("MPESA_CONSUMER_KEY", "")
    consumer_secret = os.environ.get("MPESA_CONSUMER_SECRET", "")
    if not consumer_key or not consumer_secret:
        raise RuntimeError("M-PESA is not configured. Set the Daraja consumer key and secret.")
    credentials = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()
    response = provider_json(
        f"{MPESA_BASE}/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": f"Basic {credentials}"},
    )
    token = response.get("access_token")
    if not token:
        raise RuntimeError("Daraja did not return an access token.")
    _oauth_token = token
    _oauth_expiry = time.time() + max(30, int(response.get("expires_in", 3599)) - 60)
    return token


def mpesa_password(timestamp):
    shortcode = os.environ.get("MPESA_SHORTCODE", "")
    passkey = os.environ.get("MPESA_PASSKEY", "")
    if not shortcode or not passkey:
        raise RuntimeError("M-PESA is not configured. Set the Daraja shortcode and passkey.")
    value = f"{shortcode}{passkey}{timestamp}".encode()
    return base64.b64encode(value).decode()


def mpesa_post(path, payload):
    token = get_mpesa_token()
    return provider_json(
        f"{MPESA_BASE}{path}",
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def send_delivery_email(order):
    host = os.environ.get("SMTP_HOST", "")
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("EMAIL_FROM", "")
    if not all((host, username, password, sender)):
        raise RuntimeError("Email delivery is not configured. Set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, and EMAIL_FROM.")

    message = EmailMessage()
    message["Subject"] = "Your Epson Centre digital product"
    message["From"] = sender
    message["To"] = order["email"]
    message.set_content(
        "Thank you for your Epson Centre order. Your purchased digital product archive(s) "
        "are attached to this email.\n\n"
        "Keep this email for your records. If you need assistance, contact the support address "
        "published on the Epson Centre website."
    )

    items = json.loads(order["items_json"])
    archives = {}
    for item in items:
        slug = item["slug"]
        archive = product_archive(slug)
        if not archive.is_file():
            raise RuntimeError(f"The purchased product archive is not available for {slug}.")
        if not is_supported_archive(archive):
            raise RuntimeError(f"The purchased archive for {slug} is not a supported ZIP or RAR file.")
        archives[archive.name] = archive
    if sum(archive.stat().st_size for archive in archives.values()) > MAX_EMAIL_ATTACHMENTS:
        raise RuntimeError("The product archives exceed the email attachment size limit.")
    for archive_name, archive in archives.items():
        subtype = "vnd.rar" if archive.suffix.lower() == ".rar" else "zip"
        message.add_attachment(archive.read_bytes(), maintype="application", subtype=subtype, filename=archive_name)

    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        if os.environ.get("SMTP_USE_TLS", "true").lower() == "true":
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        smtp.login(username, password)
        smtp.send_message(message)


def deliver_paid_order(order_id):
    with _db_lock, db_session() as db:
        row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row or row["status"] != "paid" or row["delivery_status"] == "sent":
            return
        order = dict(row)
        db.execute(
            "UPDATE orders SET delivery_status = 'sending', updated_at = ? WHERE id = ?",
            (now_iso(), order_id),
        )
    try:
        send_delivery_email(order)
    except Exception as exc:
        print(f"Email delivery failed for order {order_id}: {exc}", flush=True)
        with _db_lock, db_session() as db:
            db.execute(
                "UPDATE orders SET delivery_status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (str(exc)[:1000], now_iso(), order_id),
            )
        return
    with _db_lock, db_session() as db:
        db.execute(
            "UPDATE orders SET delivery_status = 'sent', error = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), order_id),
        )


def mark_order_paid(order_id, provider_payment_id=None):
    with _db_lock, db_session() as db:
        row = db.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise RuntimeError("The confirmed payment references an unknown order.")
        if row["status"] == "paid":
            return
        db.execute(
            "UPDATE orders SET status = 'paid', provider_payment_id = ?, updated_at = ? WHERE id = ?",
            (str(provider_payment_id or ""), now_iso(), order_id),
        )
    deliver_paid_order(order_id)


def mark_order_failed(order_id, reason):
    with _db_lock, db_session() as db:
        db.execute(
            """
            UPDATE orders
            SET status = 'failed', error = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (str(reason)[:1000], now_iso(), order_id),
        )


def canonical_nowpayments_payload(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_nowpayments_signature(payload, received_signature):
    secret = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
    if not secret or not received_signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical_nowpayments_payload(payload),
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, received_signature.lower())


def validate_nowpayments_payment(payload):
    payment_id = payload.get("payment_id")
    api_key = os.environ.get("NOWPAYMENTS_API_KEY", "")
    if not payment_id or not api_key:
        raise RuntimeError("The payment notification is missing required provider configuration.")
    remote = provider_json(
        f"https://api.nowpayments.io/v1/payment/{payment_id}",
        headers={"x-api-key": api_key},
    )
    order_id = str(payload.get("order_id") or "")
    if (
        str(remote.get("order_id") or "") != order_id
        or remote.get("payment_status") != "finished"
    ):
        raise RuntimeError("The NOWPayments payment could not be confirmed.")
    try:
        remote_amount = round(float(remote.get("price_amount")) * 100)
        expected_amount = int(payload.get("price_amount") and round(float(payload["price_amount"]) * 100))
    except (TypeError, ValueError):
        raise RuntimeError("The NOWPayments payment amount is invalid.")
    with db_session() as db:
        order = db.execute("SELECT total_usd_cents FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order or remote_amount != order["total_usd_cents"] or expected_amount != order["total_usd_cents"]:
        raise RuntimeError("The NOWPayments amount does not match the order total.")
    if str(remote.get("price_currency", "")).lower() != "usd":
        raise RuntimeError("The NOWPayments invoice currency does not match USD.")


def normalized_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif digits.startswith("7") or digits.startswith("1"):
        digits = "254" + digits
    if not re.fullmatch(r"254[17]\d{8}", digits):
        raise ValueError("Enter a valid Kenyan M-PESA number, such as 0712345678 or 254712345678.")
    return digits


class EpsonCentreHandler(BaseHTTPRequestHandler):
    server_version = "EpsonCentre/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request length.")
        if length <= 0 or length > 64 * 1024:
            raise ValueError("Request body is empty or too large.")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            try:
                body = (ROOT / "index.html").read_bytes()
            except OSError:
                self.send_error(500, "Website page is unavailable.")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path in ("/sitemap.xml", "/robots.txt"):
            try:
                body = (ROOT / parsed.path.lstrip("/")).read_bytes()
            except OSError:
                self.send_error(404)
                return
            content_type = "application/xml; charset=utf-8" if parsed.path.endswith(".xml") else "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/blog" or re.fullmatch(r"/blog/[a-z0-9-]+", parsed.path):
            self.send_response(301)
            self.send_header("Location", parsed.path + "/" + (f"?{parsed.query}" if parsed.query else ""))
            self.end_headers()
            return
        if parsed.path.startswith("/blog/"):
            blog_root = (ROOT / "blog").resolve()
            page_path = (ROOT / parsed.path.lstrip("/")).resolve()
            if parsed.path.endswith("/"):
                page_path = page_path / "index.html"
            if blog_root not in page_path.parents or page_path.suffix != ".html":
                self.send_error(404)
                return
            try:
                body = page_path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        match = re.fullmatch(r"/api/orders/([0-9a-f-]{36})", parsed.path)
        if match:
            with db_session() as db:
                row = db.execute(
                    "SELECT id, status, delivery_status, method, error FROM orders WHERE id = ?",
                    (match.group(1),),
                ).fetchone()
            if not row:
                self.send_json(404, {"error": "Order not found."})
                return
            self.send_json(
                200,
                {
                    "orderId": row["id"],
                    "status": row["status"],
                    "deliveryStatus": row["delivery_status"],
                    "method": row["method"],
                    "message": (
                        "Payment confirmed and the product archive has been emailed."
                        if row["status"] == "paid" and row["delivery_status"] == "sent"
                        else "Payment confirmed. Email delivery is being completed."
                        if row["status"] == "paid"
                        else "The payment was not completed. Return to checkout to try again."
                        if row["status"] == "failed"
                        else "Waiting for payment confirmation."
                    ),
                },
            )
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/checkout":
                self.create_checkout()
            elif parsed.path == "/api/webhooks/nowpayments":
                self.handle_nowpayments_webhook()
            elif parsed.path == "/api/webhooks/mpesa":
                self.handle_mpesa_callback()
            else:
                self.send_json(404, {"error": "Endpoint not found."})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            print(f"Request failed on {parsed.path}: {exc}", flush=True)
            self.send_json(502, {"error": str(exc)})

    def create_checkout(self):
        body = self.read_json()
        email = str(body.get("email", "")).strip()
        name = str(body.get("name", "")).strip()
        method = str(body.get("method", "")).lower()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address for ZIP delivery.")
        if len(name) > 120 or not name:
            raise ValueError("Enter your name.")
        if method not in ("crypto", "mpesa"):
            raise ValueError("Choose a supported payment method.")
        raw_items = body.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("Your cart is empty.")
        items = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                raise ValueError("The cart contains an invalid item.")
            slug = str(entry.get("slug", ""))
            quantity = entry.get("quantity")
            if slug not in PRODUCTS or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10:
                raise ValueError("The cart contains an invalid product or quantity.")
            archive = product_archive(slug)
            if not archive.is_file():
                raise ValueError(f"The product archive for Epson {PRODUCTS[slug]} is not installed yet. No payment has been started.")
            if not is_supported_archive(archive):
                raise ValueError(f"The archive for Epson {PRODUCTS[slug]} is invalid. No payment has been started.")
            items.append({"slug": slug, "model": PRODUCTS[slug], "quantity": quantity})
        archive_sizes = {
            archive.name: archive.stat().st_size
            for archive in (product_archive(slug) for slug in {item["slug"] for item in items})
        }
        if sum(archive_sizes.values()) > MAX_EMAIL_ATTACHMENTS:
            raise ValueError("The selected product archives are too large to deliver by email.")
        total_cents = sum(item["quantity"] * USD_PRICE_CENTS for item in items)
        if total_cents > 100000:
            raise ValueError("The maximum order total is $1,000.")

        order_id = str(uuid.uuid4())
        timestamp = now_iso()
        rate = None
        total_kes = None
        if method == "mpesa":
            phone = normalized_phone(str(body.get("phone", "")))
            rate = get_fx_rate()
            total_kes = int((total_cents / 100 * rate) + 0.5)
        else:
            phone = None

        with _db_lock, db_session() as db:
            db.execute(
                """
                INSERT INTO orders (
                    id, email, customer_name, method, status, items_json,
                    total_usd_cents, total_kes, usd_kes_rate, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, email, name, method, json.dumps(items),
                    total_cents, total_kes, rate, timestamp, timestamp,
                ),
            )

        if method == "crypto":
            api_key = os.environ.get("NOWPAYMENTS_API_KEY", "")
            if not api_key or not os.environ.get("NOWPAYMENTS_IPN_SECRET", ""):
                mark_order_failed(order_id, "NOWPayments is not configured.")
                raise RuntimeError("Crypto checkout is not configured. Set the NOWPayments API key and IPN secret.")
            try:
                invoice = provider_json(
                    "https://api.nowpayments.io/v1/invoice",
                    method="POST",
                    payload={
                        "price_amount": total_cents / 100,
                        "price_currency": "usd",
                        "order_id": order_id,
                        "order_description": f"Epson Centre digital product order {order_id}",
                        "ipn_callback_url": f"{SITE_URL}/api/webhooks/nowpayments",
                        "success_url": f"{SITE_URL}/#/order?order_id={order_id}",
                        "cancel_url": f"{SITE_URL}/#/checkout",
                    },
                    headers={"x-api-key": api_key},
                )
            except Exception as exc:
                mark_order_failed(order_id, exc)
                raise
            invoice_url = invoice.get("invoice_url")
            if not invoice_url:
                mark_order_failed(order_id, "NOWPayments did not return a checkout URL.")
                raise RuntimeError("NOWPayments did not return a checkout URL.")
            self.send_json(200, {"orderId": order_id, "method": method, "paymentUrl": invoice_url})
            return

        shortcode = os.environ.get("MPESA_SHORTCODE", "")
        callback_url = os.environ.get("MPESA_CALLBACK_URL", f"{SITE_URL}/api/webhooks/mpesa")
        if not shortcode:
            mark_order_failed(order_id, "M-PESA shortcode is not configured.")
            raise RuntimeError("M-PESA is not configured. Set MPESA_SHORTCODE.")
        transaction_type = os.environ.get("MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        try:
            response = mpesa_post(
                "/mpesa/stkpush/v1/processrequest",
                {
                    "BusinessShortCode": shortcode,
                    "Password": mpesa_password(stamp),
                    "Timestamp": stamp,
                    "TransactionType": transaction_type,
                    "Amount": total_kes,
                    "PartyA": phone,
                    "PartyB": os.environ.get("MPESA_PARTY_B", shortcode),
                    "PhoneNumber": phone,
                    "CallBackURL": callback_url,
                    "AccountReference": "EC" + order_id.replace("-", "")[:10],
                    "TransactionDesc": "Epson Centre digital product",
                },
            )
        except Exception as exc:
            mark_order_failed(order_id, exc)
            raise
        checkout_request_id = response.get("CheckoutRequestID")
        if response.get("ResponseCode") != "0" or not checkout_request_id:
            with _db_lock, db_session() as db:
                db.execute(
                    "UPDATE orders SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                    (str(response.get("ResponseDescription") or response.get("errorMessage") or "STK push failed")[:1000], now_iso(), order_id),
                )
            raise RuntimeError(str(response.get("ResponseDescription") or response.get("errorMessage") or "M-PESA could not start the payment."))
        with _db_lock, db_session() as db:
            db.execute(
                "UPDATE orders SET checkout_request_id = ?, updated_at = ? WHERE id = ?",
                (checkout_request_id, now_iso(), order_id),
            )
        self.send_json(
            200,
            {
                "orderId": order_id,
                "method": method,
                "amountKes": total_kes,
                "fxRate": rate,
                "message": "Check your phone and approve the M-PESA payment prompt.",
            },
        )

    def handle_nowpayments_webhook(self):
        body = self.read_json()
        if not verify_nowpayments_signature(body, self.headers.get("x-nowpayments-sig", "")):
            self.send_json(401, {"error": "Invalid NOWPayments signature."})
            return
        if body.get("payment_status") == "finished":
            validate_nowpayments_payment(body)
            mark_order_paid(str(body.get("order_id")), body.get("payment_id"))
        self.send_json(200, {"ok": True})

    def handle_mpesa_callback(self):
        body = self.read_json()
        callback = body.get("Body", {}).get("stkCallback", {})
        checkout_id = str(callback.get("CheckoutRequestID") or "")
        if not checkout_id:
            self.send_json(400, {"ResultCode": 1, "ResultDesc": "Missing checkout request ID"})
            return
        with db_session() as db:
            row = db.execute(
                "SELECT id, total_kes FROM orders WHERE checkout_request_id = ? AND method = 'mpesa'",
                (checkout_id,),
            ).fetchone()
        if not row:
            self.send_json(404, {"ResultCode": 1, "ResultDesc": "Order not found"})
            return
        if callback.get("ResultCode") == 0:
            self.verify_mpesa_transaction(checkout_id, row["total_kes"])
            mark_order_paid(row["id"], checkout_id)
        else:
            mark_order_failed(row["id"], callback.get("ResultDesc") or "M-PESA payment was cancelled or not completed.")
        self.send_json(200, {"ResultCode": 0, "ResultDesc": "Accepted"})

    def verify_mpesa_transaction(self, checkout_id, expected_amount):
        shortcode = os.environ.get("MPESA_SHORTCODE", "")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        response = mpesa_post(
            "/mpesa/stkpushquery/v1/query",
            {
                "BusinessShortCode": shortcode,
                "Password": mpesa_password(stamp),
                "Timestamp": stamp,
                "CheckoutRequestID": checkout_id,
            },
        )
        if str(response.get("ResultCode")) != "0":
            raise RuntimeError("M-PESA transaction verification did not return a successful result.")
        amount = response.get("Amount")
        if amount is not None and int(float(amount)) != int(expected_amount):
            raise RuntimeError("M-PESA transaction amount does not match the order.")


def main():
    initialize_database()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), EpsonCentreHandler)
    print(f"Epson Centre listening at http://localhost:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
