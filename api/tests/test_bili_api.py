from datetime import datetime

from blivedm_api.bili_api import normalize_sessdata, parse_bili_live_time


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
