const roomsBody = document.querySelector("#roomsBody");
const eventsList = document.querySelector("#eventsList");
const statusText = document.querySelector("#statusText");
const toast = document.querySelector("#toast");
const roomForm = document.querySelector("#roomForm");
const roomInput = document.querySelector("#roomInput");
const remarkInput = document.querySelector("#remarkInput");
const refreshBtn = document.querySelector("#refreshBtn");
const eventRoomFilter = document.querySelector("#eventRoomFilter");
const eventTypeFilter = document.querySelector("#eventTypeFilter");

let refreshTimer = null;
const EVENT_PAGE_SIZE = 50;
const EVENT_SCROLL_THRESHOLD = 120;
let eventState = {
  items: [],
  hasMore: true,
  beforeId: null,
  loading: false,
  filtersKey: "",
};

const EVENT_TYPE_LABELS = {
  danmaku: "弹幕",
  enter_room: "进房",
  gift: "礼物",
  guard: "上舰",
  super_chat: "醒目留言",
  super_chat_delete: "删除醒目留言",
  interact_word: "互动",
  like_guide: "点赞引导",
  like_notice: "点赞通知",
  shopping_cart: "购物车",
  rank_change: "排名变化",
  voice_join: "连麦列表",
  voice_join_count: "连麦人数",
  gift_combo: "礼物连击",
  unknown: "未知",
  parse_error: "解析错误",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // keep response status text
    }
    throw new Error(message);
  }
  return response.json();
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function eventTypeLabel(value) {
  return EVENT_TYPE_LABELS[value] || value || "-";
}

function parseJson(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function safeImageUrl(value) {
  const url = String(value || "").trim();
  if (!url) return "";
  try {
    const parsed = new URL(url, window.location.href);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "";
    }
    if (parsed.protocol === "http:") {
      parsed.protocol = "https:";
    }
    return parsed.href;
  } catch {
    return "";
  }
}

function addEmote(emotes, seenUrls, item, fallbackAlt = "表情") {
  if (!item || typeof item !== "object") return;
  const url = safeImageUrl(item.url);
  if (!url || seenUrls.has(url)) return;
  seenUrls.add(url);
  emotes.push({
    url,
    alt: item.descript || item.emoji || item.emoticon_unique || fallbackAlt,
  });
}

function extractDanmakuEmotes(event) {
  if (event.event_type !== "danmaku") return [];
  const raw = parseJson(event.raw_json);
  const emotes = [];
  const seenUrls = new Set();

  const modeInfo = raw?.info?.[0]?.[13];
  addEmote(emotes, seenUrls, modeInfo, event.content || "表情");

  const extra = parseJson(raw?.info?.[0]?.[15]?.extra);
  const extraEmotes = extra?.emots;
  if (extraEmotes && typeof extraEmotes === "object") {
    Object.values(extraEmotes).forEach((item) => addEmote(emotes, seenUrls, item, event.content || "表情"));
  }

  return emotes;
}

function positiveInteger(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function guardName(level) {
  return {
    1: "总督",
    2: "提督",
    3: "舰长",
  }[positiveInteger(level)] || "";
}

function pushUserBadge(badges, seenLabels, type, label, title = "") {
  if (!label || seenLabels.has(label)) return;
  seenLabels.add(label);
  badges.push({ type, label, title });
}

function medalFromArray(value) {
  if (!Array.isArray(value) || value.length < 2) return null;
  return {
    level: value[0],
    name: value[1],
    guard_level: 0,
  };
}

function medalFromGiftInfo(value) {
  if (!value || typeof value !== "object") return null;
  return {
    level: value.medal_level,
    name: value.medal_name,
    guard_level: value.guard_level,
  };
}

function extractUserBadges(event) {
  const raw = parseJson(event.raw_json);
  const cmd = raw?.cmd || "";
  const badges = [];
  const seenLabels = new Set();

  let userLevel = 0;
  let wealthLevel = 0;
  let medal = null;
  let guardLevel = 0;

  if (cmd === "DANMU_MSG" || cmd === "DANMU_MSG_MIRROR") {
    const info = raw?.info || [];
    const modeUser = info?.[0]?.[15]?.user || {};
    userLevel = positiveInteger(info?.[4]?.[0]);
    wealthLevel = positiveInteger(modeUser?.wealth?.level || info?.[16]?.[0]);
    medal = modeUser?.medal || medalFromArray(info?.[3]);
    guardLevel = positiveInteger(medal?.guard_level || info?.[7]);
  } else if (cmd === "SEND_GIFT") {
    const data = raw?.data || {};
    const sender = data.sender_uinfo || {};
    wealthLevel = positiveInteger(sender?.wealth?.level || data.wealth_level);
    medal = sender?.medal || medalFromGiftInfo(data.medal_info);
    guardLevel = positiveInteger(medal?.guard_level || data.guard_level);
  } else if (cmd === "GUARD_BUY" || cmd === "USER_TOAST_MSG_V2") {
    const data = raw?.data || {};
    wealthLevel = positiveInteger(data.wealth_level);
    medal = medalFromGiftInfo(data.medal_info);
    guardLevel = positiveInteger(data.guard_level || medal?.guard_level);
  } else if (cmd === "SUPER_CHAT_MESSAGE") {
    const data = raw?.data || {};
    const userInfo = data.user_info || data.uinfo || {};
    wealthLevel = positiveInteger(userInfo?.wealth?.level || data.wealth_level);
    medal = userInfo?.medal || medalFromGiftInfo(data.medal_info);
    guardLevel = positiveInteger(medal?.guard_level || data.guard_level);
  } else if (
    cmd === "INTERACT_WORD" ||
    cmd === "LIKE_GUIDE_USER" ||
    cmd === "LIKE_INFO_V3_NOTICE" ||
    cmd === "GIFT_COMBO" ||
    cmd === "COMBO_SEND"
  ) {
    const data = raw?.data || {};
    const userInfo = data.user_info || data.uinfo || data.sender_uinfo || data.user || {};
    const base = userInfo.base || {};
    wealthLevel = positiveInteger(userInfo?.wealth?.level || base?.wealth?.level || data.wealth_level);
    medal = userInfo?.medal || base?.medal || medalFromGiftInfo(data.medal_info || data.fans_medal);
    guardLevel = positiveInteger(medal?.guard_level || data.guard_level);
  }

  if (wealthLevel) {
    pushUserBadge(badges, seenLabels, "wealth", `荣 ${wealthLevel}`, "荣耀等级");
  }
  if (userLevel) {
    pushUserBadge(badges, seenLabels, "level", `Lv ${userLevel}`, "直播用户等级");
  }
  const medalLevel = positiveInteger(medal?.level || medal?.medal_level);
  const medalName = String(medal?.name || medal?.medal_name || "").trim();
  if (medalLevel && medalName) {
    pushUserBadge(badges, seenLabels, "medal", `${medalName} ${medalLevel}`, "粉丝牌");
  }
  const guard = guardName(guardLevel);
  if (guard) {
    pushUserBadge(badges, seenLabels, "guard", guard, "舰队等级");
  }

  return badges;
}

function renderUserBadges(event) {
  const badges = extractUserBadges(event);
  if (!badges.length) return "";
  return `
    <span class="user-badges">
      ${badges
        .map(
          (badge) => `
            <span class="user-badge ${escapeHtml(badge.type)}" title="${escapeHtml(badge.title)}">
              ${escapeHtml(badge.label)}
            </span>
          `
        )
        .join("")}
    </span>
  `;
}

function renderEventContent(event) {
  const text = escapeHtml(event.content || "");
  const emotes = extractDanmakuEmotes(event);
  if (!emotes.length) {
    return `<span class="event-text">${text}</span>`;
  }
  const imageHtml = emotes
    .map(
      (emote) => `
        <img
          class="danmaku-emote"
          src="${escapeHtml(emote.url)}"
          alt="${escapeHtml(emote.alt)}"
          title="${escapeHtml(emote.alt)}"
          loading="lazy"
          referrerpolicy="no-referrer"
        />
      `
    )
    .join("");
  return `<span class="event-text">${text}</span>${imageHtml}`;
}

function statusBadge(room) {
  if (room.live_status === 1) {
    return '<span class="badge live">开播</span>';
  }
  if (room.last_checked_at) {
    return '<span class="badge off">未开播</span>';
  }
  return '<span class="badge wait">待检查</span>';
}

function listeningBadge(room) {
  return room.listening
    ? '<span class="badge on">监听中</span>'
    : '<span class="badge off">未监听</span>';
}

function renderRooms(rooms) {
  if (!rooms.length) {
    roomsBody.innerHTML = '<tr><td colspan="6">暂无房间</td></tr>';
    return;
  }
  const activeRoomFilter = eventRoomFilter.value.trim();
  roomsBody.innerHTML = rooms
    .map((room) => {
      const title = escapeHtml(room.room_title || "-");
      const remark = escapeHtml(room.remark || "");
      const roomNo = room.real_room_id || room.room_id;
      const enabledText = room.enabled ? "停用" : "启用";
      const selected = activeRoomFilter === String(roomNo);
      return `
        <tr class="room-row${selected ? " selected" : ""}" data-room-filter="${roomNo}" aria-selected="${selected ? "true" : "false"}">
          <td>
            <strong>${roomNo}</strong>
            <div class="muted">录入 ${room.room_id}</div>
          </td>
          <td><div class="room-title" title="${title}">${title}</div></td>
          <td>${statusBadge(room)}</td>
          <td>${listeningBadge(room)}</td>
          <td>${remark}</td>
          <td>
            <div class="actions">
              <button class="secondary" data-action="toggle" data-id="${room.id}" data-enabled="${room.enabled ? "0" : "1"}">${enabledText}</button>
              <button class="danger" data-action="delete" data-id="${room.id}">删除</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function updateSelectedRoomRows() {
  const activeRoomFilter = eventRoomFilter.value.trim();
  roomsBody.querySelectorAll("tr[data-room-filter]").forEach((row) => {
    const selected = activeRoomFilter && row.dataset.roomFilter === activeRoomFilter;
    row.classList.toggle("selected", Boolean(selected));
    row.setAttribute("aria-selected", selected ? "true" : "false");
  });
}

async function applyRoomEventFilter(roomNo) {
  eventRoomFilter.value = roomNo;
  updateSelectedRoomRows();
  await loadEvents({ reset: true });
}

function renderEventRows(events) {
  return events
    .map((event) => `
      <article class="event-row" data-event-id="${event.id}">
        <div class="event-meta">
          <span>${event.room_id}</span>
          <span title="${escapeHtml(event.event_type)}">${escapeHtml(eventTypeLabel(event.event_type))}</span>
          <span class="event-username">${escapeHtml(event.username || "-")}</span>
          ${renderUserBadges(event)}
          <span>${formatTime(event.event_time || event.created_at)}</span>
        </div>
        <div class="event-content">${renderEventContent(event)}</div>
      </article>
    `)
    .join("");
}

function renderEventLoadState() {
  let message = "向下滚动加载更多";
  if (eventState.loading && !eventState.items.length) {
    message = "加载中...";
  } else if (!eventState.items.length) {
    message = "暂无事件";
  } else if (eventState.loading) {
    message = "加载更多...";
  } else if (!eventState.hasMore) {
    message = "没有更多事件";
  }
  return `<div class="event-load-state">${message}</div>`;
}

function renderEvents() {
  eventsList.innerHTML = `${renderEventRows(eventState.items)}${renderEventLoadState()}`;
}

async function loadRooms() {
  const payload = await requestJson("/api/rooms");
  renderRooms(payload.items);
  return payload.items;
}

function currentEventFilters() {
  const roomId = eventRoomFilter.value.trim();
  const eventType = eventTypeFilter.value;
  return {
    roomId,
    eventType,
    key: `${roomId}:${eventType}`,
  };
}

function buildEventParams(filters, beforeId) {
  const params = new URLSearchParams({ limit: String(EVENT_PAGE_SIZE) });
  if (filters.roomId) {
    params.set("room_id", filters.roomId);
  }
  if (filters.eventType) {
    params.set("event_type", filters.eventType);
  }
  if (beforeId) {
    params.set("before_id", String(beforeId));
  }
  return params;
}

function lowestEventId(events) {
  return events.reduce((lowest, event) => {
    const id = Number(event.id);
    if (!Number.isFinite(id)) return lowest;
    return lowest === null || id < lowest ? id : lowest;
  }, null);
}

async function loadEvents({ reset = false } = {}) {
  if (eventState.loading) return;
  const filters = currentEventFilters();
  const shouldReset = reset || eventState.filtersKey !== filters.key;
  if (!shouldReset && !eventState.hasMore) return;

  if (shouldReset) {
    eventState.items = [];
    eventState.hasMore = true;
    eventState.beforeId = null;
    eventState.filtersKey = filters.key;
    eventsList.scrollTop = 0;
  }

  const params = buildEventParams(filters, shouldReset ? null : eventState.beforeId);
  eventState.loading = true;
  renderEvents();
  try {
    const payload = await requestJson(`/api/events?${params}`);
    const incoming = payload.items || [];
    if (shouldReset) {
      eventState.items = incoming;
    } else {
      const existingIds = new Set(eventState.items.map((event) => Number(event.id)));
      eventState.items = eventState.items.concat(incoming.filter((event) => !existingIds.has(Number(event.id))));
    }
    eventState.hasMore = payload.has_more !== undefined ? Boolean(payload.has_more) : incoming.length === EVENT_PAGE_SIZE;
    eventState.beforeId = payload.next_before_id || lowestEventId(eventState.items);
  } finally {
    eventState.loading = false;
    renderEvents();
  }
}

async function refreshLatestEvents() {
  if (eventsList.scrollTop > 40) return;
  await loadEvents({ reset: true });
}

function maybeLoadMoreEvents() {
  const remaining = eventsList.scrollHeight - eventsList.scrollTop - eventsList.clientHeight;
  if (remaining > EVENT_SCROLL_THRESHOLD) return;
  loadEvents().catch((error) => showToast(error.message));
}

async function loadStatus() {
  const payload = await requestJson("/api/status");
  const mode = payload.mode === "api-coordinator" ? "API协调服务运行" : "服务运行";
  statusText.textContent = `${mode} · ${payload.active_rooms.length} 个房间监听中`;
}

async function refreshAll({ resetEvents = true } = {}) {
  try {
    await Promise.all([
      loadRooms(),
      resetEvents ? loadEvents({ reset: true }) : Promise.resolve(),
      loadStatus(),
    ]);
  } catch (error) {
    showToast(error.message);
  }
}

roomForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await requestJson("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        room: roomInput.value,
        remark: remarkInput.value,
        enabled: true,
      }),
    });
    roomInput.value = "";
    remarkInput.value = "";
    showToast("房间已添加");
    await refreshAll();
  } catch (error) {
    showToast(error.message);
  }
});

roomsBody.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  try {
    if (target) {
      const id = target.dataset.id;
      if (target.dataset.action === "toggle") {
        await requestJson(`/api/rooms/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ enabled: target.dataset.enabled === "1" }),
        });
        showToast("房间状态已更新");
      }
      if (target.dataset.action === "delete") {
        await requestJson(`/api/rooms/${id}`, { method: "DELETE" });
        showToast("房间已删除");
      }
      await refreshAll();
      return;
    }

    const row = event.target.closest("tr[data-room-filter]");
    if (!row) return;
    await applyRoomEventFilter(row.dataset.roomFilter);
  } catch (error) {
    showToast(error.message);
  }
});

refreshBtn.addEventListener("click", () => refreshAll({ resetEvents: true }));
eventRoomFilter.addEventListener("input", () => {
  updateSelectedRoomRows();
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    loadEvents({ reset: true }).catch((error) => showToast(error.message));
  }, 250);
});
eventTypeFilter.addEventListener("change", () => {
  loadEvents({ reset: true }).catch((error) => showToast(error.message));
});
eventsList.addEventListener("scroll", maybeLoadMoreEvents);

refreshAll({ resetEvents: true });
setInterval(() => {
  refreshAll({ resetEvents: eventsList.scrollTop <= 40 });
}, 10000);
