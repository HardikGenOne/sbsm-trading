"""Package the exact committed source for a cloud host; excludes ignored secrets/data."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def main():
    for command in (["git", "diff", "--quiet"], ["git", "diff", "--cached", "--quiet"]):
        if subprocess.run(command, cwd=ROOT).returncode:
            raise SystemExit("Commit source changes before packaging an immutable deployment.")
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for name in filter(None, paths):
        path = Path(name)
        if path.name.startswith(".env") and path.name != ".env.example" or path.suffix in (".db", ".sqlite", ".sqlite3", ".pem", ".key") or name.startswith(("data/", "work/", ".venv/")):
            raise SystemExit("Private runtime file is tracked; refusing to package.")
    revision = subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, text=True).strip()
    folder = ROOT / "work" / "deploy"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"sbsm-{revision}.tar.gz"
    subprocess.run(["git", "archive", "--format=tar.gz", "--output", str(target), "HEAD"], cwd=ROOT, check=True)
    print("Created committed source archive:", target)
    print("Private configuration and trading databases must be transferred separately over your authenticated SSH connection.")


if __name__ == "__main__":
    main()
