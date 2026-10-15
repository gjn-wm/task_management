"""Settings read from the environment, with `.env` next to this file as a fallback.

Imported first by every entry point (server.py, database.py, auth.py), so
secrets never need to live in the source. Real environment variables always win
over `.env`, so deployment settings are never silently overridden by a stale
local file.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path: str):
    # Hand-rolled rather than python-dotenv so this adds no dependency.
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


_load_dotenv(os.path.join(BASE_DIR, ".env"))


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to the .env file next to server.py "
            "(see .env.example), or set it in the environment.")
    return value


def get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        raise RuntimeError(f"{name} must be an integer.")
