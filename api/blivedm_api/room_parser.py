from __future__ import annotations

import re
from typing import Union


ROOM_ID_PATTERN = re.compile(r"(?:live\.bilibili\.com/)?(\d+)")


def parse_room_id(value: Union[str, int]) -> int:
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("room id must be positive")
        return value

    text = value.strip()
    if not text:
        raise ValueError("room id is required")

    match = ROOM_ID_PATTERN.search(text)
    if match is None:
        raise ValueError(f"cannot parse Bilibili room id from: {value!r}")

    room_id = int(match.group(1))
    if room_id <= 0:
        raise ValueError("room id must be positive")
    return room_id
