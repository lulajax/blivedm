from __future__ import annotations

import http.cookies
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
import blivedm
from blivedm import utils as blivedm_utils


ROOM_INFO_URL = "https://api.live.bilibili.com/room/v1/Room/get_info"


@dataclass(frozen=True)
class BiliRoomInfo:
    requested_room_id: int
    real_room_id: int
    title: str
    anchor_uid: Optional[int]
    live_status: int


@dataclass(frozen=True)
class BiliCollectConfig:
    room_id: int
    anchor_uid: Optional[int]
    uid: int
    buvid: str
    token: Optional[str]
    host_list: List[Dict[str, Any]]


def apply_bili_cookie(session: aiohttp.ClientSession, sessdata: str) -> None:
    # SESSDATA 可选；配置后会随 B 站 HTTP 请求发送，用于更稳定地获取房间信息。
    if not sessdata:
        return
    cookies = http.cookies.SimpleCookie()
    cookies["SESSDATA"] = sessdata
    cookies["SESSDATA"]["domain"] = "bilibili.com"
    session.cookie_jar.update_cookies(cookies)


async def fetch_room_info(session: aiohttp.ClientSession, room_id: int) -> BiliRoomInfo:
    async with session.get(
        ROOM_INFO_URL,
        headers={"User-Agent": blivedm_utils.USER_AGENT},
        params={"room_id": room_id},
    ) as response:
        response.raise_for_status()
        payload = await response.json()

    if payload.get("code") != 0:
        message = payload.get("message", "unknown error")
        raise RuntimeError(f"failed to fetch room={room_id}: {message}")

    data = payload["data"]
    return BiliRoomInfo(
        requested_room_id=room_id,
        real_room_id=int(data.get("room_id") or room_id),
        title=str(data.get("title") or ""),
        anchor_uid=int(data["uid"]) if data.get("uid") is not None else None,
        live_status=int(data.get("live_status") or 0),
    )


async def fetch_collect_config(session: aiohttp.ClientSession, room_id: int) -> BiliCollectConfig:
    # 这里借用 blivedm 的初始化逻辑拿到 WebSocket host/token/buvid，
    # 然后把这些参数下发给 client，避免 client 再访问 B 站 HTTP 接口。
    client = blivedm.BLiveClient(room_id, session=session)
    ok = await client.init_room()
    if not ok:
        raise RuntimeError(f"failed to initialize danmaku config for room={room_id}")

    host_list = getattr(client, "_host_server_list", None) or []
    if not host_list:
        raise RuntimeError(f"empty danmaku host list for room={room_id}")

    return BiliCollectConfig(
        room_id=int(client.room_id or room_id),
        anchor_uid=client.room_owner_uid,
        uid=int(client.uid or 0),
        buvid=client._get_buvid(),  # noqa: SLF001 - API 侧负责持有 B 站 HTTP/cookie 状态。
        token=getattr(client, "_host_server_token", None),
        host_list=host_list,
    )
