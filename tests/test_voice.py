from app.voice import VoiceSession


def test_voice_speaks_backend_message_once():
    spoken = []
    session = VoiceSession(lambda _: {"status": "done", "message": "Paused."}, lambda *_: {}, spoken.append)
    session.handle("ok cozy pause")
    assert spoken == ["Paused."]


def test_voice_failure_message_is_spoken_once():
    spoken = []
    session = VoiceSession(lambda _: {"status": "error", "message": "I couldn't connect to the Chrome extension."}, lambda *_: {}, spoken.append)
    session.handle("ok cozy pause")
    assert spoken == ["I couldn't connect to the Chrome extension."]
