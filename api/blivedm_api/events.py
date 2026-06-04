from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import blivedm.models.web as web_models

from .db import EventRecord

logger = logging.getLogger(__name__)

IGNORED_COMMANDS = frozenset(
    {
        # 仍不需要在管理页展示的高频展示、排行和状态类命令。
        "COMBO_SEND",
        "COMMON_ANIMATION",
        "COMMON_NOTICE_DANMAKU",
        "DM_INTERACTION",
        "ENTRY_EFFECT",
        "GIFT_COMBO",
        "HOT_RANK_CHANGED",
        "HOT_RANK_CHANGED_V2",
        "LIKE_GUIDE_USER",
        "LIKE_INFO_V3_CLICK",
        "LIKE_INFO_V3_NOTICE",
        "LIKE_INFO_V3_UPDATE",
        "LIVE",
        "LIVE_INTERACTIVE_GAME",
        "LOG_IN_NOTICE",
        "NOTICE_MSG",
        "ONLINE_RANK_COUNT",
        "ONLINE_RANK_TOP3",
        "ONLINE_RANK_V2",
        "ONLINE_RANK_V3",
        "PK_BATTLE_END",
        "PK_BATTLE_FINAL_PROCESS",
        "PK_BATTLE_PROCESS",
        "PK_BATTLE_PROCESS_NEW",
        "PK_BATTLE_SETTLE",
        "PK_BATTLE_SETTLE_USER",
        "PK_BATTLE_SETTLE_V2",
        "PK_INFO",
        "PLAYURL_RELOAD",
        "POPULARITY_RED_POCKET_V2_NEW",
        "POPULARITY_RED_POCKET_V2_START",
        "POPULARITY_RED_POCKET_V2_WINNER_LIST",
        "POPULAR_RANK_CHANGED",
        "PREPARING",
        "RANK_CHANGED",
        "RANK_CHANGED_V2",
        "ROOM_SKIN_MSG",
        "ROOM_REAL_TIME_MESSAGE_UPDATE",
        "SHOPPING_CART_SHOW",
        "STOP_LIVE_ROOM_LIST",
        "SUPER_CHAT_MESSAGE_DELETE",
        "SUPER_CHAT_MESSAGE_JPN",
        "UNIVERSAL_EVENT_GIFT",
        "UNIVERSAL_EVENT_GIFT_V2",
        "USER_TOAST_MSG",
        "VOICE_JOIN_LIST",
        "VOICE_JOIN_ROOM_COUNT_INFO",
        "WATCHED_CHANGE",
        "WIDGET_BANNER",
        "WIDGET_GIFT_STAR_PROCESS_V2",
    }
)

INTERACT_WORD_LABELS = {
    1: "进入房间",
    2: "关注主播",
    3: "分享直播间",
    4: "特别关注主播",
    5: "与主播互粉",
    6: "为主播点赞",
}

def normalize_cmd(command: Dict[str, Any]) -> str:
    cmd = str(command.get("cmd", ""))
    pos = cmd.find(":")
    if pos != -1:
        cmd = cmd[:pos]
    return cmd


def event_time_from_seconds(value: Optional[int]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromtimestamp(value)


def event_time_from_milliseconds(value: Optional[int]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000)


def event_time_from_unix(value: Any) -> Optional[datetime]:
    number = maybe_int(value)
    if not number:
        return None
    if number > 10_000_000_000:
        return event_time_from_milliseconds(number)
    return event_time_from_seconds(number)


def command_data(command: Dict[str, Any]) -> Dict[str, Any]:
    data = command.get("data")
    return data if isinstance(data, dict) else {}


def nested_value(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        return value
    return None


def maybe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None


def maybe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return ""
    return str(value).strip()


def extract_uid(data: Dict[str, Any]) -> Optional[int]:
    return maybe_int(
        first_value(
            data.get("uid"),
            data.get("mid"),
            data.get("user_id"),
            nested_value(data, "user_info", "uid"),
            nested_value(data, "user_info", "mid"),
            nested_value(data, "uinfo", "uid"),
            nested_value(data, "uinfo", "base", "uid"),
            nested_value(data, "user", "uid"),
            nested_value(data, "user", "mid"),
            nested_value(data, "user", "base", "uid"),
            nested_value(data, "sender_uinfo", "uid"),
            nested_value(data, "sender_uinfo", "base", "uid"),
        )
    )


def extract_username(data: Dict[str, Any]) -> str:
    return maybe_text(
        first_value(
            data.get("uname"),
            data.get("username"),
            data.get("user_name"),
            data.get("name"),
            nested_value(data, "user_info", "uname"),
            nested_value(data, "user_info", "username"),
            nested_value(data, "uinfo", "uname"),
            nested_value(data, "uinfo", "base", "name"),
            nested_value(data, "user", "uname"),
            nested_value(data, "user", "name"),
            nested_value(data, "user", "base", "name"),
            nested_value(data, "sender_uinfo", "uname"),
            nested_value(data, "sender_uinfo", "base", "name"),
        )
    )


def common_timestamp(data: Dict[str, Any]) -> Any:
    return first_value(
        data.get("timestamp"),
        data.get("time"),
        data.get("ts"),
        data.get("start_time"),
        data.get("send_time"),
        data.get("ctime"),
        data.get("created_at"),
        data.get("trigger_time"),
    )


def event_time_from_common(data: Dict[str, Any]) -> Optional[datetime]:
    return event_time_from_unix(common_timestamp(data))


def content_from_common(data: Dict[str, Any], fallback: str) -> str:
    content = maybe_text(
        first_value(
            data.get("content"),
            data.get("message"),
            data.get("msg"),
            data.get("text"),
            data.get("copy_writing"),
            data.get("toast_msg"),
            data.get("desc"),
            data.get("title"),
            data.get("show_text"),
            data.get("rank_name"),
        )
    )
    return content or fallback


def stable_event_key(*parts: Any) -> str:
    text = ":".join("" if part is None else str(part) for part in parts)
    if len(text) <= 190:
        return text
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{text[:149]}:{digest}"


def danmaku_event_key(message: web_models.DanmakuMessage) -> str:
    id_str = message.extra_dict.get("id_str")
    if id_str:
        return stable_event_key("danmaku", id_str)
    return stable_event_key("danmaku", message.timestamp, message.uid, message.rnd, message.msg)


def gift_event_key(message: web_models.GiftMessage) -> str:
    return stable_event_key("gift", message.tid or message.rnd, message.uid, message.gift_id, message.num)


def guard_event_key(message: web_models.GuardBuyMessage) -> str:
    return stable_event_key("guard", message.start_time, message.uid, message.guard_level, message.gift_id, message.num)


def user_toast_event_key(message: web_models.UserToastV2Message) -> str:
    return stable_event_key("user_toast", message.start_time, message.uid, message.guard_level, message.num, message.source)


def super_chat_event_key(message: web_models.SuperChatMessage) -> str:
    return stable_event_key("super_chat", message.id)


def enter_room_event_key(message: web_models.InteractWordV2Message) -> str:
    return stable_event_key("enter_room", message.timestamp, message.uid)


def command_digest(command: Dict[str, Any]) -> str:
    payload = json.dumps(command, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def generic_command_event_key(event_type: str, command: Dict[str, Any], data: Dict[str, Any], uid: Optional[int]) -> str:
    identity = first_value(
        data.get("id"),
        data.get("event_id"),
        data.get("msg_id"),
        data.get("unique_id"),
        data.get("batch_combo_id"),
        data.get("combo_id"),
        data.get("tid"),
        data.get("rnd"),
        common_timestamp(data),
        uid,
        normalize_cmd(command),
    )
    return stable_event_key(event_type, identity, command_digest(command)[:16])


def interact_word_event(room_id: int, command: Dict[str, Any]) -> EventRecord:
    data = command_data(command)
    msg_type = maybe_int(first_value(data.get("msg_type"), data.get("msgType"), data.get("type"))) or 0
    uid = extract_uid(data)
    username = extract_username(data)
    timestamp = common_timestamp(data)
    event_time = event_time_from_unix(timestamp)
    label = INTERACT_WORD_LABELS.get(msg_type, content_from_common(data, "互动消息"))
    if msg_type in (2, 4):
        event_type = "follow"
    elif msg_type == 1:
        event_type = "enter_room"
    else:
        event_type = "interact_word"
    if event_type in ("enter_room", "follow") and timestamp and uid:
        event_key = stable_event_key(event_type, timestamp, uid)
    else:
        event_key = generic_command_event_key(event_type, command, data, uid)
    return EventRecord(
        room_id=room_id,
        event_type=event_type,
        event_key=event_key,
        uid=uid,
        username=username,
        content=label,
        raw=command,
        event_time=event_time,
    )


def gift_combo_event(room_id: int, command: Dict[str, Any]) -> EventRecord:
    data = command_data(command)
    uid = extract_uid(data)
    username = extract_username(data)
    gift_name = maybe_text(first_value(data.get("gift_name"), data.get("giftName"), nested_value(data, "gift_info", "name")))
    combo_num = maybe_int(first_value(data.get("batch_combo_num"), data.get("combo_num"), data.get("num"), data.get("gift_num")))
    if gift_name and combo_num:
        content = f"{gift_name} 连击 x{combo_num}"
    elif gift_name:
        content = f"{gift_name} 连击"
    else:
        content = content_from_common(data, "礼物连击")
    return EventRecord(
        room_id=room_id,
        event_type="gift_combo",
        event_key=generic_command_event_key("gift_combo", command, data, uid),
        uid=uid,
        username=username,
        content=content,
        raw=command,
        event_time=event_time_from_common(data),
    )


def shopping_cart_content(data: Dict[str, Any]) -> str:
    content = content_from_common(data, "")
    if content:
        return content
    goods = first_value(data.get("goods"), data.get("goods_list"), data.get("items"))
    if isinstance(goods, list):
        return f"购物车展示 {len(goods)} 件商品"
    return "购物车展示"


def voice_join_list_content(data: Dict[str, Any]) -> str:
    users = first_value(data.get("list"), data.get("users"), data.get("items"))
    if isinstance(users, list):
        return f"连麦列表 {len(users)} 人"
    return content_from_common(data, "连麦列表")


def voice_join_count_content(data: Dict[str, Any]) -> str:
    count = maybe_int(first_value(data.get("room_count"), data.get("count"), data.get("total"), data.get("join_count")))
    if count is not None:
        return f"连麦人数 {count}"
    return content_from_common(data, "连麦人数")


def rank_change_content(data: Dict[str, Any]) -> str:
    content = content_from_common(data, "")
    rank = maybe_int(first_value(data.get("rank"), data.get("rank_value"), data.get("pos"), data.get("position")))
    if content and rank:
        return f"{content} #{rank}"
    if content:
        return content
    if rank:
        return f"排名变化 #{rank}"
    return "排名变化"


def like_notice_content(data: Dict[str, Any]) -> str:
    content = content_from_common(data, "")
    count = maybe_int(first_value(data.get("like_count"), data.get("count"), data.get("click_count")))
    if content and count:
        return f"{content} x{count}"
    if content:
        return content
    if count:
        return f"点赞通知 x{count}"
    return "点赞通知"


def generic_known_event(room_id: int, command: Dict[str, Any], event_type: str, fallback: str) -> EventRecord:
    data = command_data(command)
    uid = extract_uid(data)
    content = {
        "shopping_cart": shopping_cart_content,
        "voice_join": voice_join_list_content,
        "voice_join_count": voice_join_count_content,
        "rank_change": rank_change_content,
        "like_notice": like_notice_content,
    }.get(event_type, lambda item: content_from_common(item, fallback))(data)
    return EventRecord(
        room_id=room_id,
        event_type=event_type,
        event_key=generic_command_event_key(event_type, command, data, uid),
        uid=uid,
        username=extract_username(data),
        content=content,
        raw=command,
        event_time=event_time_from_common(data),
    )


def event_from_command(room_id: int, command: Dict[str, Any]) -> Optional[EventRecord]:
    # 这里只保留管理页需要查询的已知业务事件。未知命令直接忽略，
    # 避免高频协议噪声进入数据库和筛选列表。
    cmd = normalize_cmd(command)

    if cmd == "_HEARTBEAT" or cmd in IGNORED_COMMANDS:
        return None

    if cmd in ("DANMU_MSG", "DANMU_MSG_MIRROR"):
        message = web_models.DanmakuMessage.from_command(command["info"])
        return EventRecord(
            room_id=room_id,
            event_type="danmaku",
            event_key=danmaku_event_key(message),
            uid=message.uid,
            username=message.uname,
            content=message.msg,
            raw=command,
            event_time=event_time_from_milliseconds(message.timestamp),
        )

    if cmd == "INTERACT_WORD":
        event = interact_word_event(room_id, command)
        return event if event.event_type in ("enter_room", "follow") else None

    if cmd == "INTERACT_WORD_V2":
        message = web_models.InteractWordV2Message.from_command(command["data"])
        # msg_type=1 进入房间；msg_type=2/4 关注/特别关注主播。其他 interaction
        # word 多是展示效果或无关互动提示，暂不作为业务事件入库。
        if message.msg_type == 1:
            event_type, content = "enter_room", "进入房间"
        elif message.msg_type in (2, 4):
            event_type = "follow"
            content = INTERACT_WORD_LABELS.get(message.msg_type, "关注主播")
        else:
            return None
        return EventRecord(
            room_id=room_id,
            event_type=event_type,
            event_key=stable_event_key(event_type, message.timestamp, message.uid),
            uid=message.uid,
            username=message.username,
            content=content,
            raw=command,
            event_time=event_time_from_seconds(message.timestamp),
        )

    if cmd == "SEND_GIFT":
        message = web_models.GiftMessage.from_command(command["data"])
        return EventRecord(
            room_id=room_id,
            event_type="gift",
            event_key=gift_event_key(message),
            uid=message.uid,
            username=message.uname,
            content=f"{message.gift_name} x{message.num}",
            raw=command,
            event_time=event_time_from_seconds(message.timestamp),
        )

    if cmd == "GUARD_BUY":
        message = web_models.GuardBuyMessage.from_command(command["data"])
        return EventRecord(
            room_id=room_id,
            event_type="guard",
            event_key=guard_event_key(message),
            uid=message.uid,
            username=message.username,
            content=f"{message.gift_name} x{message.num}",
            raw=command,
            event_time=event_time_from_seconds(message.start_time),
        )

    if cmd == "USER_TOAST_MSG_V2":
        message = web_models.UserToastV2Message.from_command(command["data"])
        if message.source == 2:
            return None
        return EventRecord(
            room_id=room_id,
            event_type="guard",
            event_key=user_toast_event_key(message),
            uid=message.uid,
            username=message.username,
            content=message.toast_msg,
            raw=command,
            event_time=event_time_from_seconds(message.start_time),
        )

    if cmd == "SUPER_CHAT_MESSAGE":
        message = web_models.SuperChatMessage.from_command(command["data"])
        return EventRecord(
            room_id=room_id,
            event_type="super_chat",
            event_key=super_chat_event_key(message),
            uid=message.uid,
            username=message.uname,
            content=message.message,
            raw=command,
            event_time=event_time_from_seconds(message.start_time),
        )

    return None


def parse_event_or_error(room_id: int, command: Dict[str, Any]) -> Optional[EventRecord]:
    cmd = normalize_cmd(command)
    try:
        return event_from_command(room_id, command)
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to parse room=%s cmd=%s", room_id, cmd)
        return None
