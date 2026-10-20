import pytest
from app import actions


def test_strip_wake_word():
    assert actions.strip_wake_word("OK Cozy, open youtube") == "open youtube"


def test_open_website():
    a = actions.parse_command("ok cozy open youtube")
    assert a.kind == "open_website" and "youtube.com" in a.target


def test_open_app():
    assert actions.parse_command("open notepad").kind == "open_named"


def test_open_domain():
    a = actions.parse_command("open github.com")
    assert a.target == "https://github.com"


def test_risky_needs_approval():
    a = actions.parse_command("send a message to mom")
    assert a.kind == "chat" and a.needs_approval


def test_execute_blocks_unapproved():
    with pytest.raises(PermissionError):
        actions.execute(actions.parse_command("call dad"))


def test_execute_calls_launcher():
    seen = []
    actions.execute(actions.parse_command("open google"), open_url=seen.append)
    assert seen == ["https://www.google.com"]


@pytest.mark.parametrize("text, expected", [
    ("Cozy, set volume to 100", 100),
    ("set volume to 40 percent", 40),
    ("volume 0%", 0),
])
def test_exact_system_volume_parsing(text, expected):
    action = actions.parse_command(text)
    assert action.kind == "media" and action.target == "set_volume"
    assert action.value == expected


@pytest.mark.parametrize("text", ["set volume to 101", "set volume to 1000 percent", "set volume to -1"])
def test_exact_volume_rejects_out_of_range(text):
    action = actions.parse_command(text)
    assert action.kind == "reply"
    assert "between 0 and 100" in action.label


def test_youtube_play_and_search_parse_differently():
    play = actions.parse_command("play kalyani song")
    search = actions.parse_command("search cats on google")
    assert play.kind == "youtube_play" and play.query == "kalyani song"
    assert search.label.startswith("Searching Google for cats")


def test_video_volume_and_mute_stay_separate_from_windows_volume():
    assert actions.parse_command("increase YouTube volume").target == "video_volume_up"
    assert actions.parse_command("decrease video volume").target == "video_volume_down"
    assert actions.parse_command("mute the video").target == "video_mute"
    assert actions.parse_command("unmute YouTube").target == "video_unmute"
    assert actions.parse_command("increase volume").target == "volume_up"
