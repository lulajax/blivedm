import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

import pytest

from blivedm_api.events import (
    IGNORED_COMMANDS,
    enter_room_event_key,
    event_time_from_seconds,
    event_from_command,
    stable_event_key,
)


@dataclass
class FakeInteractWord:
    timestamp: int
    uid: int


def test_stable_event_key_truncates_long_values():
    key = stable_event_key("event", "x" * 500)

    assert len(key) <= 190
    assert key.startswith("event:")


def test_enter_room_event_key_uses_timestamp_and_uid():
    message = FakeInteractWord(timestamp=1710000000, uid=42)

    assert enter_room_event_key(message) == "enter_room:1710000000:42"


def test_event_time_from_seconds_uses_local_timezone():
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        assert event_time_from_seconds(0) is None
        assert event_time_from_seconds(1710000000) == datetime(2024, 3, 10, 0, 0, 0)
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        if hasattr(time, "tzset"):
            time.tzset()


def test_ignored_display_commands_are_not_persisted():
    for cmd in IGNORED_COMMANDS:
        assert event_from_command(1, {"cmd": cmd}) is None


def command(cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"cmd": cmd, "data": data}


def test_interact_word_enter_room_is_parsed_as_enter_room():
    event = event_from_command(
        1,
        command(
            "INTERACT_WORD",
            {
                "uid": 42,
                "uname": "alice",
                "timestamp": 1710000000,
                "msg_type": 1,
            },
        ),
    )

    assert event is not None
    assert event.event_type == "enter_room"
    assert event.event_key == "enter_room:1710000000:42"
    assert event.uid == 42
    assert event.username == "alice"
    assert event.content == "进入房间"


def test_interact_word_non_enter_is_ignored():
    event = event_from_command(
        1,
        command(
            "INTERACT_WORD",
            {
                "uid": 42,
                "uname": "alice",
                "timestamp": 1710000000,
                "msg_type": 2,
            },
        ),
    )

    assert event is None


@pytest.mark.parametrize(
    ("cmd", "data"),
    [
        ("LIKE_GUIDE_USER", {"uid": 1, "uname": "u1", "copy_writing": "给主播点个赞"}),
        ("SHOPPING_CART_SHOW", {"goods": [{"id": 1}, {"id": 2}]}),
        ("RANK_CHANGED", {"rank": 8}),
        ("VOICE_JOIN_LIST", {"list": [{"uid": 1}]}),
        ("VOICE_JOIN_ROOM_COUNT_INFO", {"room_count": 3}),
        ("LIKE_INFO_V3_NOTICE", {"count": 9}),
    ],
)
def test_known_auxiliary_commands_are_ignored(cmd: str, data: Dict[str, Any]):
    event = event_from_command(1, command(cmd, data))

    assert event is None


@pytest.mark.parametrize("cmd", ["GIFT_COMBO", "COMBO_SEND"])
def test_gift_combo_commands_are_ignored(cmd: str):
    event = event_from_command(
        1,
        command(
            cmd,
            {
                "uid": 7,
                "uname": "gift-user",
                "gift_name": "辣条",
                "batch_combo_num": 3,
                "batch_combo_id": "combo-1",
                "timestamp": 1710000000,
            },
        ),
    )

    assert event is None
