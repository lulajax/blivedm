import pytest

from blivedm_api.room_parser import parse_room_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123456", 123456),
        (123456, 123456),
        ("https://live.bilibili.com/21449083", 21449083),
        ("https://live.bilibili.com/21449083?spm_id_from=foo", 21449083),
    ],
)
def test_parse_room_id(value, expected):
    assert parse_room_id(value) == expected


@pytest.mark.parametrize("value", ["", "https://example.com/live", "abc", 0, -1])
def test_parse_room_id_invalid(value):
    with pytest.raises(ValueError):
        parse_room_id(value)
