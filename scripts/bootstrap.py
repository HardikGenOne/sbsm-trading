"""Generate local secrets without printing them. Run from any working directory."""
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / ".env"
if path.exists():
    print("Existing .env preserved.")
else:
    body = (ROOT / ".env.example").read_text()
    body = body.replace("replace-with-a-long-random-admin-secret", secrets.token_urlsafe(40))
    body = body.replace("replace-with-a-different-random-webhook-secret", secrets.token_urlsafe(40))
    body = body.replace("sqlite:///data/paper.db", "sqlite:///" + str(ROOT / "data/paper.db"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(body)
    print("Created private .env with generated credentials. Values were not printed.")
