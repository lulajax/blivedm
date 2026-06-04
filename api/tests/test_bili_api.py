import asyncio
from datetime import datetime

from blivedm_api import bili_api
from blivedm_api.bili_api import fetch_collect_config, normalize_sessdata, parse_bili_live_time


def test_parse_bili_live_time_valid_value():
    assert parse_bili_live_time("2026-06-03 14:12:29") == datetime(2026, 6, 3, 14, 12, 29)


def test_parse_bili_live_time_empty_values():
    assert parse_bili_live_time("0000-00-00 00:00:00") is None
    assert parse_bili_live_time("") is None
    assert parse_bili_live_time(None) is None


def test_normalize_sessdata_accepts_plain_value():
    assert normalize_sessdata(" abc123 ") == "abc123"


def test_normalize_sessdata_extracts_from_cookie_header():
    assert normalize_sessdata("DedeUserID=42; SESSDATA=abc%2Cdef; bili_jct=token") == "abc%2Cdef"


def test_normalize_sessdata_empty_value():
    assert normalize_sessdata("") == ""
    assert normalize_sessdata(None) == ""


def test_fetch_collect_config_accepts_degraded_blivedm_fallback(monkeypatch):
    class FakeClient:
        def __init__(self, room_id, *, session):
            self.room_id = room_id
            self.room_owner_uid = 123
            self.uid = 456
            self._host_server_list = [{"host": "broadcastlv.chat.bilibili.com", "wss_port": 443}]
            self._host_server_token = None

        async def init_room(self):
            return False

        def _get_buvid(self):
            return "fake-buvid"

    monkeypatch.setattr(bili_api.blivedm, "BLiveClient", FakeClient)

    config = asyncio.run(fetch_collect_config(object(), 1001))

    assert config.room_id == 1001
    assert config.anchor_uid == 123
    assert config.uid == 456
    assert config.buvid == "fake-buvid"
    assert config.token is None
    assert config.host_list == [{"host": "broadcastlv.chat.bilibili.com", "wss_port": 443}]
