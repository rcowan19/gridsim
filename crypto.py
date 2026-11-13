"""Encrypt small secrets (a user's ESPN cookies) before they touch the database. Key: GRIDSIM_SECRET_KEY (a Fernet key)."""
import json
import os

from cryptography.fernet import Fernet, InvalidToken

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, "data", ".secret_key")


def _fernet():
    k = os.environ.get("GRIDSIM_SECRET_KEY")
    if not k:
        if os.environ.get("GRIDSIM_ENV") == "production":
            raise RuntimeError("GRIDSIM_SECRET_KEY is required in production (python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())').")
        if not os.path.exists(KEY_FILE):                      # local dev: make a key once and reuse it
            os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
            with open(KEY_FILE, "wb") as f:
                f.write(Fernet.generate_key())
            os.chmod(KEY_FILE, 0o600)
        k = open(KEY_FILE, "rb").read().decode()
    return Fernet(k.encode() if isinstance(k, str) else k)


def seal(d):
    return _fernet().encrypt(json.dumps(d, separators=(",", ":")).encode()).decode() if d else None


def unseal(s):
    if not s:
        return {}
    try:
        return json.loads(_fernet().decrypt(s.encode()).decode())
    except InvalidToken:
        return {}          # key rotated or data damaged: treat as "no saved credentials", the user re-enters them
