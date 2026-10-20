"""Gives the Cozy extension a STABLE Chrome ID (so it stays the same on every machine and reinstall).

    python scripts/make_extension_key.py          # creates extension-key.pem (keep it private!) and edits extension/manifest.json

Chrome derives the extension ID from the "key" in manifest.json. Without it, an unpacked extension's ID depends on
where the folder lives. Run this once, commit the manifest change, and never share extension-key.pem.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extension_id(der_public_key: bytes) -> str:
    digest = hashlib.sha256(der_public_key).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def main(root: Path = ROOT) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem_path = root / "extension-key.pem"
    if pem_path.exists():
        key = serialization.load_pem_private_key(pem_path.read_bytes(), password=None)
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    manifest_path = root / "extension" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["key"] = base64.b64encode(der).decode()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return extension_id(der)


if __name__ == "__main__":
    print("Extension ID:", main())
    print("Keep extension-key.pem private (it is in .gitignore). Reload the extension in chrome://extensions.")
    sys.exit(0)
