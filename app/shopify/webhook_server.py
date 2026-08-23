from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.shopify.client import get_shopify_settings
from app.shopify.derived_inventory import process_webhook


MAX_BODY_BYTES = 5 * 1024 * 1024
PROCESSING_LOCK = threading.Lock()


def valid_hmac(body: bytes, supplied: str, secret: str) -> bool:
    if not supplied or not secret:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, supplied.strip())


class ShopifyWebhookHandler(BaseHTTPRequestHandler):
    server_version = "WeldingshopShopifyWebhook/1.0"

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._reply(200, {"status": "ok"})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/shopify/webhooks/inventory":
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._reply(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(413, {"error": "invalid body size"})
            return
        body = self.rfile.read(length)
        settings = get_shopify_settings(include_token=True)
        if not valid_hmac(
            body,
            self.headers.get("X-Shopify-Hmac-Sha256") or "",
            settings.get("webhook_secret") or "",
        ):
            self._reply(401, {"error": "invalid hmac"})
            return
        try:
            payload = json.loads(body)
            with PROCESSING_LOCK:
                result = process_webhook(
                    self.headers.get("X-Shopify-Topic") or "",
                    self.headers.get("X-Shopify-Webhook-Id") or "",
                    payload,
                )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._reply(400, {"error": str(exc)})
            return
        except Exception as exc:
            self.log_error("webhook processing failed: %s", exc)
            self._reply(500, {"error": "processing failed"})
            return
        self._reply(200, result)

    def log_message(self, format: str, *args: object) -> None:
        super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8502)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), ShopifyWebhookHandler).serve_forever()


if __name__ == "__main__":
    main()
