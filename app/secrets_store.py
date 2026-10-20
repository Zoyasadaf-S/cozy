"""Secrets live in the OS credential store (Windows Credential Manager), never in files."""
import keyring
from cryptography.fernet import Fernet

SERVICE = "cozy-assistant"
MASTER = "master_key"


def get_or_create_master_key() -> bytes:
    key = keyring.get_password(SERVICE, MASTER)
    if not key:
        key = Fernet.generate_key().decode()
        keyring.set_password(SERVICE, MASTER, key)
    return key.encode()


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_secret(name: str):
    return keyring.get_password(SERVICE, name)