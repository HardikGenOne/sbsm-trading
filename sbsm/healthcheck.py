"""Container health check, independent of proxy environment variables and broker access."""
import os

import httpx


def main():
    try:
        port = int(os.environ.get("PORT", "8000"))
        with httpx.Client(timeout=3, trust_env=False) as client:
            return 0 if client.get(f"http://127.0.0.1:{port}/health/ready").status_code == 200 else 1
    except (httpx.HTTPError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
