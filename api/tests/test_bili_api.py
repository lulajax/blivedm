from datetime import datetime

from blivedm_api.bili_api import parse_bili_live_time


def test_parse_bili_live_time_valid_value():
    assert parse_bili_live_time("2026-06-03 14:12:29") == datetime(2026, 6, 3, 14, 12, 29)


def test_parse_bili_live_time_empty_values():
    assert parse_bili_live_time("0000-00-00 00:00:00") is None
    assert parse_bili_live_time("") is None
    assert parse_bili_live_time(None) is None
