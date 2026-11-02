"""Create a private, reviewable production configuration; does not deploy anything."""
import argparse
from pathlib import Path
import re
import secrets

from sbsm.private_config import update_private_env

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, help="Public domain/subdomain only, without https://")
    args = parser.parse_args()
    domain = args.domain.lower().strip()
    if len(domain) > 253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        raise SystemExit("Provide a valid public domain, for example paper.yourdomain.com.")
    path = ROOT / ".env.cloud"
    if path.exists():
        raise SystemExit("Existing .env.cloud preserved. Edit it locally instead of overwriting credentials.")
    template = (ROOT / "deploy/production.env.example").read_text().replace("paper.example.com", domain)
    path.touch(mode=0o600, exist_ok=False)
    path.write_text(template)
    update_private_env(path, {"SBSM_ADMIN_TOKEN": secrets.token_urlsafe(40), "SBSM_WEBHOOK_TOKEN": secrets.token_urlsafe(40)})
    print("Created private .env.cloud with new production credentials. No values were printed.")
    print("Review it locally. This command creates no paid resources and starts no deployment.")


if __name__ == "__main__":
    main()
