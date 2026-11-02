"""Bind a dedicated Telegram bot to your private chat without exposing its token."""
import argparse
import getpass
from pathlib import Path
import re
import secrets

import httpx

from sbsm.notifications import DeliveryError, TelegramClient
from sbsm.private_config import update_private_env

ROOT = Path(__file__).resolve().parent.parent


def call(client, token, method, body=None):
    try:
        response = client.post(f"https://api.telegram.org/bot{token}/{method}", json=body or {})
        payload = response.json()
        if not response.is_success or not isinstance(payload, dict) or payload.get("ok") is not True:
            raise SystemExit("Telegram rejected the request. Check the bot/token; response details were not printed.")
        return payload["result"]
    except (httpx.HTTPError, ValueError, KeyError):
        raise SystemExit("Telegram request failed; no token, URL or response body was printed.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", choices=[".env", ".env.cloud"], default=".env")
    args = parser.parse_args()
    path = ROOT / args.env_file
    if not path.exists():
        raise SystemExit("Create the selected local/cloud configuration first.")
    print("Create a dedicated bot with @BotFather in Telegram using /newbot.")
    token = getpass.getpass("Bot token (hidden; never paste it into chat): ").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        raise SystemExit("Token format is invalid; nothing was saved.")
    with httpx.Client(timeout=10) as client:
        bot = call(client, token, "getMe")
        info = call(client, token, "getWebhookInfo")
        if info.get("url"):
            raise SystemExit("This bot already has a webhook. Use a dedicated bot; its existing webhook was not changed.")
        challenge = "sbsm_" + secrets.token_hex(6)
        print(f"Open your bot @{bot['username']} and send this exact private message:")
        print("/start " + challenge)
        input("Press Enter after sending it: ")
        updates = call(client, token, "getUpdates", {"limit": 100, "timeout": 0, "allowed_updates": ["message"]})
        matching = [u["message"] for u in updates if "message" in u and u["message"].get("text") == "/start " + challenge and u["message"].get("chat", {}).get("type") == "private" and u["message"].get("from", {}).get("id") == u["message"].get("chat", {}).get("id")]
        if not matching:
            raise SystemExit("Private-chat verification not found. Run again with a dedicated bot; no updates were acknowledged or deleted.")
        chat_id = str(matching[-1]["chat"]["id"])
        print("Verified the private chat that sent your one-time challenge.")
        print("It will receive paper trade details, allocation decisions and system errors.")
        if input("Type ENABLE to bind this chat and enable notifications: ").strip() != "ENABLE":
            raise SystemExit("No configuration changed.")
        update_private_env(path, {"SBSM_TELEGRAM_ENABLED": "true", "SBSM_TELEGRAM_BOT_TOKEN": token, "SBSM_TELEGRAM_CHAT_ID": chat_id, "SBSM_TELEGRAM_ALLOW_GROUPS": "false"})
    print("Telegram configuration saved privately. Restart the paper stack to load it.")
    if input("Send a non-financial connection test to this verified chat now? [y/N] ").strip().lower() == "y":
        sender = TelegramClient(token)
        try:
            sender.send(chat_id, "SBSM PAPER · CONNECTION TEST\nYour private notification chat is connected. No order was placed.")
            print("Telegram accepted the connection test.")
        except DeliveryError as exc:
            print("Connection test failed:", exc.code)
        finally:
            sender.close()


if __name__ == "__main__":
    main()
