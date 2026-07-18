"""Encrypt/decrypt MT5 credentials stored in DB."""
import base64, hashlib, os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    secret = os.getenv("SESSION_SECRET", "fallback-secret-change-me")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
