"""
Vercel serverless entrypoint for Telegram webhook.

Telegram pushes each update here via POST. We dispatch to the existing
command handler in webhook_handler.handle_command and return 200.

Vercel Python handlers use the BaseHTTPRequestHandler convention:
the module must export a class named `handler`.
"""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

# Make the project root importable (Vercel puts the function in api/ but
# we need to reach the modules at the repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webhook_handler import handle_command  # noqa: E402

# Optional: protect the endpoint with a shared secret. When set, Telegram
# is configured (via setWebhook) to send it as a header; any request
# missing it is rejected. Without this anyone who learns the URL could
# inject fake updates.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")


class handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: str = "ok"):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        # Useful for a smoke check from a browser.
        self._respond(200, "telegram webhook online")

    def do_POST(self):
        # Verify shared secret if configured
        if WEBHOOK_SECRET:
            got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if got != WEBHOOK_SECRET:
                self._respond(401, "unauthorized")
                return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            update = json.loads(body) if body else {}
        except (ValueError, json.JSONDecodeError) as e:
            print(f"⚠️  Bad request body: {e}")
            self._respond(400, "bad request")
            return

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            # Telegram also sends callback_query, inline_query, etc. — ignore.
            self._respond(200, "ignored")
            return

        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "")

        if chat_id and text.startswith("/"):
            try:
                handle_command(chat_id, text)
            except Exception as e:
                # Always return 200 to Telegram even on internal errors —
                # otherwise Telegram retries forever and you get duplicate runs.
                print(f"❌ Error handling '{text}': {e}")
                traceback.print_exc()
                try:
                    from telegram_bot import send_message
                    send_message(chat_id, "❌ Internal error processing command")
                except Exception:
                    pass

        self._respond(200, "ok")
