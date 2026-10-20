import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("make_extension_key", ROOT / "scripts" / "make_extension_key.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extension_key_script_is_stable_and_writes_the_manifest(tmp_path):
    mod = load()
    (tmp_path / "extension").mkdir()
    (tmp_path / "extension" / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "x", "version": "1"}))
    first = mod.main(tmp_path)
    second = mod.main(tmp_path)                                         # reuses the saved key => same ID
    assert first == second and re.fullmatch(r"[a-p]{32}", first)
    manifest = json.loads((tmp_path / "extension" / "manifest.json").read_text())
    assert manifest["key"] and manifest["name"] == "x"
    assert (tmp_path / "extension-key.pem").exists()


def test_extension_id_algorithm_matches_chromes_documented_scheme():
    mod = load()
    assert mod.extension_id(b"\x00" * 10) == "".join(chr(97 + int(c, 16)) for c in __import__("hashlib").sha256(b"\x00" * 10).hexdigest()[:32])


def test_shipped_project_has_no_secrets_or_runtime_files():
    assert not (ROOT / "extension-key.pem").exists()
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text())
    assert "key" not in manifest                                         # users generate their own
