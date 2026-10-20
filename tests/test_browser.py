from types import SimpleNamespace

from app import browser


def test_volume_range_and_non_windows(monkeypatch):
    monkeypatch.setattr(browser.sys, "platform", "linux")
    assert not browser.set_system_volume(101)
    assert not browser.set_system_volume(-1)
    assert not browser.set_system_volume(True)
    assert not browser.set_system_volume(40)


def test_volume_missing_dependency_fails_gracefully(monkeypatch):
    monkeypatch.setattr(browser.sys, "platform", "win32")
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name.startswith("pycaw"):
            raise ImportError("mock missing pycaw")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    assert not browser.set_system_volume(40)
    assert not browser.set_system_mute(True)


def test_volume_api_failure_returns_false(monkeypatch):
    monkeypatch.setattr(browser.sys, "platform", "win32")
    endpoint = SimpleNamespace(SetMasterVolumeLevelScalar=lambda *_: (_ for _ in ()).throw(OSError("device unavailable")), SetMute=lambda *_: (_ for _ in ()).throw(OSError("device unavailable")))
    fake = SimpleNamespace(AudioUtilities=SimpleNamespace(GetSpeakers=lambda: SimpleNamespace(EndpointVolume=endpoint)))
    import sys
    monkeypatch.setitem(sys.modules, "pycaw", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake)
    assert not browser.set_system_volume(40)
    assert not browser.set_system_mute(False)
