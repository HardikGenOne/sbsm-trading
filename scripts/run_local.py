"""Run the supervised paper stack locally; no cloud or broker credentials required."""
import os
from pathlib import Path

from sbsm.config import Settings
from sbsm.service import supervise

if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    raise SystemExit(supervise(Settings(), "127.0.0.1", 8000))
