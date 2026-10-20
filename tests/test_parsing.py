from datetime import datetime

import pytest

from cozy.parser import normalize, split_steps
from cozy.registry import match_rules
from cozy.timeparse import extract_when, next_due
from cozy.tools._util import parse_duration, parse_timestamp


def parse(text):
    return match_rules(normalize(text))


@pytest.mark.parametrize("text, tool, args", [
    ("OK Cozy, open youtube", "open_target", {"name": "youtube"}),
    ("open github in a new tab", "open_target", {"name": "github", "new_tab": True}),
    ("open github in edge", "open_target", {"name": "github", "browser": "edge"}),
    ("go to downloads", "open_target", {"name": "downloads"}),
    ("play tenu leke mai jaunga", "youtube_play", {"query": "tenu leke mai jaunga"}),
    ("play lofi on youtube", "youtube_play", {"query": "lofi"}),
    ("play kalyani song please", "youtube_play", {"query": "kalyani song"}),
    ("play lofi on spotify", "music_search", {"query": "lofi", "service": "spotify"}),
    ("search cats on google", "web_search", {"query": "cats"}),
    ("set volume to 40 percent", "volume_set", {"percent": 40}),
    ("decrease volume to 25", "volume_set", {"percent": 25}),
    ("increase thr volume to 70", "volume_set", {"percent": 70}),
    ("increase volume", "volume_step", {"direction": "up"}),
    ("mute", "volume_mute", {"muted": True}),
    ("set video volume to 25", "media_control", {"action": "set_volume", "value": 25}),
    ("increase YouTube volume", "media_control", {"action": "volume_up"}),
    ("decrease video volume", "media_control", {"action": "volume_down"}),
    ("mute the video", "media_control", {"action": "mute"}),
    ("forward that video to 1min", "media_control", {"action": "seek_to", "value": 60}),
    ("forward it to 1 min", "media_control", {"action": "seek_to", "value": 60}),
    ("fast forward to 1:30", "media_control", {"action": "seek_to", "value": 90}),
    ("skip forward to 2 minutes", "media_control", {"action": "seek_to", "value": 120}),
    ("go to 2 minutes 10 seconds", "media_control", {"action": "seek_to", "value": 130}),
    ("forward by 1 minute", "media_control", {"action": "forward", "value": 60}),
    ("rewind 15 seconds", "media_control", {"action": "rewind", "value": 15}),
    ("forward", "media_control", {"action": "forward"}),
    ("pause", "media_control", {"action": "pause"}),
    ("skip ad", "media_control", {"action": "skip_ad"}),
    ("play at 1.5x", "playback_speed", {"value": 1.5}),
    ("pause in 20 minutes", "sleep_timer", {"value": 1200}),
    ("what song is this", "now_playing", {}),
    ("lyrics", "lyrics", {}),
    ("add kesariya to queue", "queue_add", {"query": "kesariya"}),
    ("remind me to call mom in 20 minutes", "set_reminder", {"text": "call mom in 20 minutes"}),
    ("timer for 10 minutes", "set_timer", {"text": "10 minutes"}),
    ("alarm at 6 am", "set_alarm", {"text": "6 am"}),
    ("5 km to miles", "convert", {"amount": 5.0, "src": "km", "dst": "miles"}),
    ("what is 12 percent of 450", "calculate", {"expr": "12 percent of 450"}),
    ("weather in Pune", "weather", {"city": "Pune"}),
    ("good morning", "good_morning", {}),
    ("what changed today", "git_changes_today", {}),
    ("commit with message fix tab recovery", "git_commit", {"message": "fix tab recovery"}),
    ("run the tests", "run_tests", {}),
    ("open my chalkmind project", "project_open", {"name": "chalkmind"}),
    ("shut down", "shutdown_pc", {}),
    ("undo that", "undo", {}),
    ("find my resume pdf", "find_file", {"query": "resume pdf"}),
    ("open my resume file", "open_file", {"query": "resume"}),
    ("summarize this page", "page_summary", {}),
    ("go back", "browser_back", {}),
    ("snap window to the left", "window_action", {"action": "snap_left"}),
    ("roll 2 dice", None, None),                            # only with the example plugin loaded
])
def test_rule_table(text, tool, args):
    found = parse(text)
    if tool is None:
        assert found is None or found[0] != "roll_dice"
        return
    assert found is not None, text
    assert found[0] == tool
    for k, v in args.items():
        assert found[1][k] == v, (text, found)


def test_go_to_a_folder_is_not_swallowed_by_seek():
    assert parse("go to downloads")[0] == "open_target"
    assert parse("go to 1:30")[0] == "media_control"


def test_unrelated_text_does_not_match():
    assert parse("tell me a story about dragons") is None
    assert parse("skip to next") == ("media_control", {"action": "next"})


@pytest.mark.parametrize("text, expected", [
    ("YouTube kholo", "open youtube"),
    ("GitHub alag tab mein kholo", "open GitHub in a new tab"),
    ("YouTube pe Dil Chorey chalao", "play Dil Chorey on youtube"),
    ("Volume thoda kam karo", "decrease volume"),
    ("Video ki volume kam karo", "decrease video volume"),
    ("Isko pause karo", "pause"),
])
def test_hinglish(text, expected):
    assert normalize(text) == expected


def test_multi_step_splitting_keeps_song_names_and_teach_commands_intact():
    assert split_steps("set volume to 30 and then play lofi") == ["set volume to 30", "play lofi"]
    assert split_steps("open github and play tum hi ho") == ["open github", "play tum hi ho"]
    assert split_steps("play rock and roll") == ["play rock and roll"]
    assert split_steps("teach work mode: open github then play lofi") == ["teach work mode: open github then play lofi"]
    assert split_steps("remind me to call mom and then eat in 5 minutes")[0].startswith("remind me")


def test_timestamps_and_durations():
    assert parse_timestamp("1min") == 60 and parse_timestamp("1:30") == 90 and parse_timestamp("1:02:03") == 3723
    assert parse_timestamp("2 minutes 10 seconds") == 130
    for bad in ("downloads", "1:75", "90000"):
        with pytest.raises(ValueError):
            parse_timestamp(bad)
    assert parse_duration("30 seconds") == 30 and parse_duration("2 minutes") == 120
    with pytest.raises(ValueError):
        parse_duration("0 seconds")


NOW = datetime(2026, 10, 19, 9, 0)


@pytest.mark.parametrize("text, task, due, repeat", [
    ("call mom in 20 minutes", "call mom", datetime(2026, 10, 19, 9, 20), None),
    ("submit form tomorrow at 7:30 am", "submit form", datetime(2026, 10, 20, 7, 30), None),
    ("stretch at 6 pm", "stretch", datetime(2026, 10, 19, 18, 0), None),
    ("stretch at 8 am", "stretch", datetime(2026, 10, 20, 8, 0), None),          # already past -> tomorrow
    ("solve a problem every day at 8 pm", "solve a problem", datetime(2026, 10, 19, 20, 0), {"kind": "daily"}),
    ("check mail every 30 minutes", "check mail", datetime(2026, 10, 19, 9, 30), {"kind": "interval", "seconds": 1800}),
    ("water plants every weekday at 7 am", "water plants", datetime(2026, 10, 20, 7, 0), {"kind": "weekdays"}),
    ("tomorrow", "", datetime(2026, 10, 20, 9, 0), None),
])
def test_extract_when(text, task, due, repeat):
    assert extract_when(text, NOW) == (task, due, repeat)


def test_extract_when_rejects_text_without_a_time_and_bad_clock_values():
    assert extract_when("buy milk", NOW) is None
    assert extract_when("meet at 25", NOW) is None


def test_next_due_skips_missed_occurrences_and_weekends():
    friday = datetime(2026, 10, 23, 7, 0)
    assert next_due({"kind": "weekdays"}, friday, datetime(2026, 10, 23, 7, 1)) == datetime(2026, 10, 26, 7, 0)
    assert next_due({"kind": "daily"}, datetime(2026, 10, 1, 8, 0), NOW) == datetime(2026, 10, 19, 8, 0) + __import__("datetime").timedelta(days=1)
    assert next_due({"kind": "interval", "seconds": 600}, NOW, datetime(2026, 10, 19, 9, 25)) == datetime(2026, 10, 19, 9, 30)
