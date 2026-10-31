"""Cassation package. Loads repo-root .env into the environment on first import
(hackathon-day convenience: the challenge API key lives in .env, valid for the
event day only). Existing environment variables always win."""
import os
from pathlib import Path

_env = Path(__file__).resolve().parents[1] / ".env"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
