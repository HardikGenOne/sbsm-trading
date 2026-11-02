"""Safe local diagnostics; does not submit signals or place any orders."""
import argparse
import json

import httpx

from sbsm.config import Settings
from sbsm.engine import now_ms
from sbsm.market import KiteQuotes


def probe_market_data(settings, clock=now_ms, feed_factory=KiteQuotes):
    feed = feed_factory(settings)
    try:
        if not feed.configured:
            return {"status": "BLOCKED", "reason": "MISSING_CREDENTIALS", "next_step": "Run scripts/kite_login.py locally; do not share credentials in chat."}
        quotes = feed.fetch(["ICICIBANK"])
        if not quotes:
            return {"status": "WAITING", "reason": "NO_TWO_SIDED_QUOTE", "next_step": "Check market hours and market-data permissions."}
        quote = quotes[0]
        age = clock() - quote.asof_ms
        fresh = 0 <= age <= settings.quote_max_age_seconds * 1000
        return {"status": "PASS" if fresh else "WAITING", "reason": "FRESH_QUOTE" if fresh else "STALE_OR_CLOSED_MARKET_QUOTE", "symbol": quote.symbol, "quote_asof_ms": quote.asof_ms, "age_ms": age, "tick_size_rupees": str(quote.tick_size)}
    except httpx.HTTPStatusError as exc:
        # Never print request/response bodies, URLs, headers, or credentials.
        code = exc.response.status_code
        reason = "AUTHENTICATION_OR_PERMISSION_FAILURE" if code in (401, 403) else "BROKER_HTTP_ERROR"
        return {"status": "BLOCKED", "reason": reason, "http_status": code}
    except httpx.HTTPError:
        return {"status": "BLOCKED", "reason": "MARKET_DATA_NETWORK_ERROR"}
    except (KeyError, ValueError, TypeError):
        return {"status": "BLOCKED", "reason": "UNEXPECTED_MARKET_DATA_FORMAT"}
    finally:
        feed.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-market-data", action="store_true", help="Make read-only Kite quote requests using local credentials")
    args = parser.parse_args()
    settings = Settings()
    # Admin credentials are sent only to this fixed local address.
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=5, follow_redirects=False, trust_env=False) as client:
        try:
            response = client.get("/api/readiness", headers={"Authorization": "Bearer " + settings.admin_token.get_secret_value()})
            if response.status_code == 200:
                result = {"local_server": response.json()}
            else:
                result = {"local_server": {"status": "BLOCKED", "http_status": response.status_code, "next_step": "Restart API and worker with current local configuration."}}
        except (httpx.HTTPError, ValueError):
            result = {"local_server": {"status": "BLOCKED", "next_step": "Start .venv/bin/python scripts/run_local.py"}}
    if args.probe_market_data:
        result["market_data_probe"] = probe_market_data(settings)
    print(json.dumps(result, indent=2))
    return 0 if result["local_server"].get("ready_for_new_entries") and (not args.probe_market_data or result["market_data_probe"]["status"] == "PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
