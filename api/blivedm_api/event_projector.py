from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from .config import Settings
from .db import Database
from .events import command_data, event_time_from_unix, first_value, maybe_int, maybe_text, nested_value, normalize_cmd

logger = logging.getLogger(__name__)

PROJECTION_NAME = "known_event_projector"
PROJECTOR_LOCK_NAME = "blivedm_event_projector"
KNOWN_EVENT_TABLES = {
    "danmaku": "danmaku_events",
    "enter_room": "enter_room_events",
    "follow": "follow_events",
    "gift": "gift_events",
    "guard": "guard_events",
    "super_chat": "super_chat_events",
}
GUARD_LEVEL_NAMES = {
    1: "总督",
    2: "提督",
    3: "舰长",
}


@dataclass(frozen=True)
class ProjectionStats:
    read_count: int
    projected_count: int
    skipped_count: int
    last_event_id: int


@dataclass(frozen=True)
class ProjectionRows:
    table_name: str
    fact_row: Dict[str, Any]
    time_row: Dict[str, Any]


def seq_get(value: Any, index: int) -> Any:
    if isinstance(value, (list, tuple)) and 0 <= index < len(value):
        return value[index]
    return None


def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_raw_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def safe_datetime(value: Any, fallback: Optional[datetime] = None) -> datetime:
    if isinstance(value, datetime):
        return value
    return fallback or datetime.now()


def maybe_decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def medal_from_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {
            "medal_name": maybe_text(first_value(value.get("name"), value.get("medal_name"), value.get("medalName"))),
            "medal_level": maybe_int(first_value(value.get("level"), value.get("medal_level"), value.get("medalLevel"))),
            "medal_anchor_uid": maybe_int(
                first_value(value.get("anchor_uid"), value.get("anchorUid"), value.get("target_id"), value.get("ruid"))
            ),
            "guard_level": maybe_int(first_value(value.get("guard_level"), value.get("guardLevel"))),
        }
    if isinstance(value, (list, tuple)):
        return {
            "medal_name": maybe_text(seq_get(value, 1)),
            "medal_level": maybe_int(seq_get(value, 0)),
            "medal_anchor_uid": maybe_int(first_value(seq_get(value, 12), seq_get(value, 13))),
            "guard_level": maybe_int(seq_get(value, 10)),
        }
    return {
        "medal_name": "",
        "medal_level": None,
        "medal_anchor_uid": None,
        "guard_level": None,
    }


def stats_from_user_info(user_info: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    base = as_dict(user_info.get("base"))
    medal = medal_from_value(
        first_value(
            user_info.get("medal"),
            base.get("medal"),
            data.get("medal_info"),
            data.get("fans_medal"),
        )
    )
    return {
        "user_level": maybe_int(
            first_value(
                user_info.get("level"),
                user_info.get("user_level"),
                base.get("level"),
                data.get("user_level"),
            )
        ),
        "wealth_level": maybe_int(
            first_value(
                nested_value(user_info, "wealth", "level"),
                nested_value(base, "wealth", "level"),
                data.get("wealth_level"),
            )
        ),
        "medal_name": medal["medal_name"],
        "medal_level": medal["medal_level"],
        "medal_anchor_uid": medal["medal_anchor_uid"],
        "guard_level": maybe_int(first_value(medal["guard_level"], data.get("guard_level"))),
    }


def extract_user_stats(raw: Dict[str, Any], event_type: str) -> Dict[str, Any]:
    if event_type == "danmaku":
        info = raw.get("info") if isinstance(raw.get("info"), list) else []
        mode_user = as_dict(as_dict(seq_get(seq_get(info, 0), 15)).get("user"))
        medal = medal_from_value(first_value(mode_user.get("medal"), seq_get(info, 3)))
        return {
            "user_level": maybe_int(seq_get(seq_get(info, 4), 0)),
            "wealth_level": maybe_int(first_value(nested_value(mode_user, "wealth", "level"), seq_get(seq_get(info, 16), 0))),
            "medal_name": medal["medal_name"],
            "medal_level": medal["medal_level"],
            "medal_anchor_uid": medal["medal_anchor_uid"],
            "guard_level": maybe_int(first_value(medal["guard_level"], seq_get(info, 7))),
        }

    data = command_data(raw)
    user_info = as_dict(
        first_value(
            data.get("user_info"),
            data.get("uinfo"),
            data.get("sender_uinfo"),
            data.get("user"),
        )
    )
    return stats_from_user_info(user_info, data)


def common_fact_row(event: Dict[str, Any], context: Dict[str, Any], event_at: datetime, raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": int(event["id"]),
        "room_id": int(event["room_id"]),
        "configured_room_id": context.get("configured_room_id"),
        "live_session_id": context.get("live_session_id"),
        "anchor_uid": context.get("anchor_uid"),
        "uid": event.get("uid"),
        "username": str(event.get("username") or ""),
        "event_at": event_at,
        "created_at": safe_datetime(event.get("created_at"), event_at),
        "source_cmd": normalize_cmd(raw),
    }


def build_time_detail(event: Dict[str, Any], context: Dict[str, Any], event_at: datetime, timezone: str) -> Dict[str, Any]:
    ingested_at = safe_datetime(event.get("created_at"), event_at)
    hour_start_at = event_at.replace(minute=0, second=0, microsecond=0)
    minute_start_at = event_at.replace(second=0, microsecond=0)
    session_started_at = context.get("session_started_at")
    session_elapsed_seconds = None
    if isinstance(session_started_at, datetime):
        session_elapsed_seconds = int((event_at - session_started_at).total_seconds())
    return {
        "event_id": int(event["id"]),
        "event_type": str(event["event_type"]),
        "room_id": int(event["room_id"]),
        "configured_room_id": context.get("configured_room_id"),
        "live_session_id": context.get("live_session_id"),
        "anchor_uid": context.get("anchor_uid"),
        "uid": event.get("uid"),
        "event_at": event_at,
        "ingested_at": ingested_at,
        "timezone": timezone,
        "date_key": int(event_at.strftime("%Y%m%d")),
        "hour_key": int(event_at.strftime("%Y%m%d%H")),
        "minute_key": int(event_at.strftime("%Y%m%d%H%M")),
        "event_date": event_at.date(),
        "hour_start_at": hour_start_at,
        "minute_start_at": minute_start_at,
        "year": event_at.year,
        "quarter": (event_at.month - 1) // 3 + 1,
        "month": event_at.month,
        "day": event_at.day,
        "hour": event_at.hour,
        "minute": event_at.minute,
        "second": event_at.second,
        "weekday": event_at.isoweekday(),
        "iso_week": event_at.isocalendar().week,
        "is_weekend": 1 if event_at.isoweekday() in (6, 7) else 0,
        "session_started_at": session_started_at,
        "session_elapsed_seconds": session_elapsed_seconds,
        "ingest_delay_seconds": int((ingested_at - event_at).total_seconds()),
    }


def build_projection_rows(event: Dict[str, Any], context: Dict[str, Any], timezone: str = "Asia/Shanghai") -> Optional[ProjectionRows]:
    event_type = str(event.get("event_type") or "")
    table_name = KNOWN_EVENT_TABLES.get(event_type)
    if table_name is None:
        return None

    raw = parse_raw_json(event.get("raw_json"))
    event_at = safe_datetime(first_value(event.get("event_time"), event.get("created_at")))
    fact = common_fact_row(event, context, event_at, raw)
    stats = extract_user_stats(raw, event_type)
    data = command_data(raw)

    if event_type == "danmaku":
        content = str(event.get("content") or "")
        fact.update(stats)
        fact.update({"content": content, "content_length": len(content)})
    elif event_type == "enter_room":
        fact.update(stats)
        fact.update({"action_text": str(event.get("content") or "进入房间")})
    elif event_type == "follow":
        fact.update(stats)
        fact.update({"action_text": str(event.get("content") or "关注主播")})
    elif event_type == "gift":
        gift_num = maybe_int(first_value(data.get("num"), data.get("gift_num"), data.get("giftNum"))) or 0
        gift_price = maybe_int(first_value(data.get("price"), data.get("gift_price"), data.get("discount_price")))
        total_price = maybe_int(first_value(data.get("total_coin"), data.get("total_price"), data.get("totalCoin")))
        if total_price is None and gift_price is not None and gift_num:
            total_price = gift_price * gift_num
        fact.update(stats)
        fact.update(
            {
                "gift_id": maybe_int(first_value(data.get("gift_id"), data.get("giftId"))),
                "gift_name": maybe_text(first_value(data.get("gift_name"), data.get("giftName"))),
                "gift_num": gift_num,
                "gift_price": gift_price,
                "gift_total_price": total_price,
                "coin_type": maybe_text(data.get("coin_type")),
                "batch_combo_id": maybe_text(data.get("batch_combo_id")),
                "combo_id": maybe_text(first_value(data.get("combo_id"), data.get("comboId"))),
            }
        )
    elif event_type == "guard":
        guard_level = maybe_int(first_value(data.get("guard_level"), data.get("guardLevel"), stats["guard_level"]))
        guard_num = maybe_int(first_value(data.get("num"), data.get("gift_num"), data.get("guard_num"))) or 1
        price = maybe_int(first_value(data.get("price"), data.get("gift_price")))
        total_price = maybe_int(first_value(data.get("total_price"), data.get("total_coin")))
        if total_price is None and price is not None and guard_num:
            total_price = price * guard_num
        fact.update(stats)
        fact.update(
            {
                "guard_level": guard_level,
                "guard_name": GUARD_LEVEL_NAMES.get(guard_level or 0, ""),
                "guard_num": guard_num,
                "gift_id": maybe_int(first_value(data.get("gift_id"), data.get("giftId"))),
                "gift_name": maybe_text(first_value(data.get("gift_name"), data.get("giftName"))),
                "price": price,
                "total_price": total_price,
            }
        )
    elif event_type == "super_chat":
        start_time = event_time_from_unix(data.get("start_time"))
        end_time = event_time_from_unix(data.get("end_time"))
        duration_seconds = maybe_int(first_value(data.get("time"), data.get("duration")))
        if duration_seconds is None and start_time is not None and end_time is not None:
            duration_seconds = int((end_time - start_time).total_seconds())
        message = str(event.get("content") or data.get("message") or "")
        fact.update(stats)
        fact.update(
            {
                "super_chat_id": maybe_int(data.get("id")),
                "message": message,
                "message_length": len(message),
                "price": maybe_decimal(first_value(data.get("price"), data.get("rmb"))),
                "start_time": start_time,
                "end_time": end_time,
                "duration_seconds": duration_seconds,
            }
        )

    return ProjectionRows(
        table_name=table_name,
        fact_row=fact,
        time_row=build_time_detail(event, context, event_at, timezone),
    )


class EventProjector:
    def __init__(self, settings: Settings, db: Database):
        self._settings = settings
        self._db = db

    async def process_once(self) -> ProjectionStats:
        last_event_id = await self._db.get_projection_last_event_id(PROJECTION_NAME)
        rows = await self._db.fetch_room_events_after(last_event_id, self._settings.event_projector_batch_size)
        if not rows:
            return ProjectionStats(read_count=0, projected_count=0, skipped_count=0, last_event_id=last_event_id)

        projected_count = 0
        skipped_count = 0
        max_event_id = last_event_id
        context_cache: Dict[Any, Dict[str, Any]] = {}
        fact_rows_by_table: Dict[str, list[Dict[str, Any]]] = {}
        time_rows: list[Dict[str, Any]] = []
        for event in rows:
            event_id = int(event["id"])
            max_event_id = max(max_event_id, event_id)
            if str(event.get("event_type") or "") not in KNOWN_EVENT_TABLES:
                skipped_count += 1
                continue

            event_at = safe_datetime(first_value(event.get("event_time"), event.get("created_at")))
            room_id = int(event["room_id"])
            live_session_id = event.get("live_session_id")
            if live_session_id is not None:
                context_key = ("session", int(live_session_id))
            else:
                context_key = ("room-minute", room_id, event_at.replace(second=0, microsecond=0))
            context = context_cache.get(context_key)
            if context is None:
                context = await self._db.resolve_event_projection_context(
                    room_id=room_id,
                    event_at=event_at,
                    live_session_id=live_session_id,
                )
                context_cache[context_key] = context
            projection = build_projection_rows(event, context, self._settings.event_projector_timezone)
            if projection is None:
                skipped_count += 1
                continue

            fact_rows_by_table.setdefault(projection.table_name, []).append(projection.fact_row)
            time_rows.append(projection.time_row)
            projected_count += 1

        for table_name, fact_rows in fact_rows_by_table.items():
            await self._db.upsert_analytic_events(table_name, fact_rows)
        await self._db.upsert_event_time_details(time_rows)
        await self._db.update_projection_last_event_id(PROJECTION_NAME, max_event_id)
        return ProjectionStats(
            read_count=len(rows),
            projected_count=projected_count,
            skipped_count=skipped_count,
            last_event_id=max_event_id,
        )

    async def run_forever(self) -> None:
        while True:
            async with self._db.named_lock(PROJECTOR_LOCK_NAME, timeout_seconds=0) as acquired:
                if not acquired:
                    logger.warning("event projector lock is held by another process")
                    await asyncio.sleep(self._settings.event_projector_poll_interval_seconds)
                    continue
                logger.info("event projector started")
                while True:
                    try:
                        stats = await self.process_once()
                        if stats.read_count:
                            logger.info(
                                "event projector batch read=%s projected=%s skipped=%s last_event_id=%s",
                                stats.read_count,
                                stats.projected_count,
                                stats.skipped_count,
                                stats.last_event_id,
                            )
                        else:
                            await asyncio.sleep(self._settings.event_projector_poll_interval_seconds)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        logger.exception("event projector batch failed")
                        await asyncio.sleep(self._settings.event_projector_poll_interval_seconds)


async def async_main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    settings.validate()
    db = Database(settings)
    await db.connect()
    try:
        await db.migrate()
        await EventProjector(settings, db).run_forever()
    finally:
        await db.close()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("event projector stopped")


if __name__ == "__main__":
    main()
