"""Atomic updates to explicitly selected local configuration files; never logs values."""
import json
import os
from pathlib import Path
import tempfile


def update_private_env(path: Path, values: dict[str, str]):
    original = path.read_text() if path.exists() else ""
    remaining = dict(values)
    lines = []
    for line in original.splitlines():
        key = line.split("=", 1)[0]
        if key in remaining:
            line = key + "=" + json.dumps(remaining.pop(key))
        lines.append(line)
    lines.extend(key + "=" + json.dumps(value) for key, value in remaining.items())
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".env.setup-", suffix=".tmp", text=True)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write("\n".join(lines) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
