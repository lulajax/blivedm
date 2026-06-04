const roomsBody = document.querySelector("#roomsBody");
const eventsList = document.querySelector("#eventsList");
const statusText = document.querySelector("#statusText");
const toast = document.querySelector("#toast");
const roomForm = document.querySelector("#roomForm");
const roomInput = document.querySelector("#roomInput");
const remarkInput = document.querySelector("#remarkInput");
const collectorClientForm = document.querySelector("#collectorClientForm");
const collectorClientsBody = document.querySelector("#collectorClientsBody");
const collectorClientIdInput = document.querySelector("#collectorClientIdInput");
const collectorClientRemarkInput = document.querySelector("#collectorClientRemarkInput");
const collectorMaxRoomsInput = document.querySelector("#collectorMaxRoomsInput");
const collectorEnabledInput = document.querySelector("#collectorEnabledInput");
const collectorSessdataInput = document.querySelector("#collectorSessdataInput");
const clientConfigBtn = document.querySelector("#clientConfigBtn");
const clientConfigCloseBtn = document.querySelector("#clientConfigCloseBtn");
const clientConfigModal = document.querySelector("#clientConfigModal");
const refreshBtn = document.querySelector("#refreshBtn");
const analyticsRefreshBtn = document.querySelector("#analyticsRefreshBtn");
const eventRoomFilter = document.querySelector("#eventRoomFilter");
const eventTypeFilter = document.querySelector("#eventTypeFilter");
const streamSummary = document.querySelector("#streamSummary");
const analyticsSummary = document.querySelector("#analyticsSummary");
const analyticsCharts = document.querySelector("#analyticsCharts");

let refreshTimer = null;
const EVENT_PAGE_SIZE = 50;
const EVENT_SCROLL_THRESHOLD = 120;
const ANALYTICS_MINUTES = 60;
const ANALYTICS_BUCKET_MINUTES = 10;
const roomSessions = new Map();
const expandedRooms = new Set();
let roomsCache = [];
let selectedRoomId = "";
let selectedSessionId = "";
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
  follow: "关注",
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
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(startValue, endValue) {
  if (!startValue) return "-";
  const start = new Date(startValue);
  const end = endValue ? new Date(endValue) : new Date();
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "-";
  const seconds = Math.max(0, Math.floor((end.getTime() - start.getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours}小时${minutes}分`;
  return `${minutes}分`;
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

function firstPositiveNumber(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number) && number > 0) {
      return number;
    }
  }
  return 0;
}

function formatCompactNumber(value) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
}

function formatShortNumber(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  if (number >= 10000) return `${(number / 10000).toFixed(number >= 100000 ? 0 : 1)}万`;
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function giftCoinLabel(value) {
  const type = String(value || "").toLowerCase();
  if (type === "gold") return "金瓜子";
  if (type === "silver") return "银瓜子";
  return value || "瓜子";
}

function extractGiftValue(event) {
  if (event.event_type !== "gift") return null;
  const raw = parseJson(event.raw_json);
  const data = raw?.data || {};
  const num = firstPositiveNumber(
    data.num,
    data.gift_num,
    data.giftNum,
    data.combo_send?.gift_num,
    data.batch_combo_send?.gift_num
  );
  const unitPrice = firstPositiveNumber(data.discount_price, data.price);
  const totalCoin = firstPositiveNumber(data.total_coin, data.totalCoin, unitPrice && num ? unitPrice * num : 0);
  if (!totalCoin) return null;

  const label = giftCoinLabel(data.coin_type || data.coinType);
  const title = unitPrice && num ? `单价 ${formatCompactNumber(unitPrice)} ${label} x ${formatCompactNumber(num)}` : "";
  return {
    label,
    title,
    totalCoin,
  };
}

function renderGiftValue(event) {
  const value = extractGiftValue(event);
  if (!value) return "";
  return `
    <span class="gift-value" title="${escapeHtml(value.title)}">
      价值 ${escapeHtml(formatCompactNumber(value.totalCoin))} ${escapeHtml(value.label)}
    </span>
  `;
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
  const giftValue = renderGiftValue(event);
  if (!emotes.length) {
    return `<span class="event-text">${text}</span>${giftValue}`;
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
  return `<span class="event-text">${text}</span>${imageHtml}${giftValue}`;
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

function sessionCell(room) {
  if (!room.current_session_id || !room.current_session_started_at) {
    return "-";
  }
  return `
    <div class="session-cell">
      <strong>#${escapeHtml(room.current_session_id)}</strong>
      <div class="muted">${escapeHtml(formatTime(room.current_session_started_at))}</div>
    </div>
  `;
}

function sessdataCell(client) {
  if (!client.sessdata_configured) {
    return '<span class="badge off">未配置</span>';
  }
  return `
    <div class="secret-cell">
      <span class="badge on">已配置</span>
      <span class="muted">${escapeHtml(client.sessdata_preview || "")}</span>
    </div>
  `;
}

function renderSessionList(roomNo) {
  if (!expandedRooms.has(String(roomNo))) return "";
  const sessions = roomSessions.get(String(roomNo));
  if (!sessions) {
    return '<div class="session-list"><div class="session-list-state">场次加载中...</div></div>';
  }
  if (!sessions.length) {
    return '<div class="session-list"><div class="session-list-state">暂无场次记录</div></div>';
  }
  return `
    <div class="session-list">
      ${sessions
        .map((session) => {
          const selected = selectedSessionId === String(session.id);
          const status = session.status === "live" ? "直播中" : "已结束";
          return `
            <button
              class="session-item${selected ? " selected" : ""}"
              type="button"
              data-action="select-session"
              data-room-filter="${session.room_id}"
              data-session-id="${session.id}"
            >
              <span>
                <strong>#${escapeHtml(session.id)}</strong>
                <em>${escapeHtml(status)}</em>
              </span>
              <small>${escapeHtml(formatTime(session.started_at))}</small>
              <small>时长 ${escapeHtml(formatDuration(session.started_at, session.ended_at))}</small>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderRooms(rooms) {
  if (!rooms.length) {
    roomsBody.innerHTML = '<div class="empty-state">暂无房间</div>';
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
      const expanded = expandedRooms.has(String(roomNo));
      return `
        <article class="room-card${selected ? " selected" : ""}" data-room-filter="${roomNo}" aria-selected="${selected ? "true" : "false"}">
          <div class="room-card-main">
            <div class="room-avatar">${escapeHtml(String(title || roomNo).slice(0, 1).toUpperCase())}</div>
            <div class="room-card-content">
              <div class="room-card-title">
                <strong title="${title}">${title}</strong>
                ${statusBadge(room)}
              </div>
              <div class="room-meta">
                <span>${roomNo}</span>
                <span>录入 ${room.room_id}</span>
                ${listeningBadge(room)}
              </div>
              ${room.current_session_id ? `<div class="room-session">${sessionCell(room)}</div>` : ""}
              <div class="room-remark">${remark || "暂无备注"}</div>
            </div>
          </div>
          <div class="room-actions">
            <button class="secondary" data-action="toggle-sessions" data-room-filter="${roomNo}">${expanded ? "收起" : "场次"}</button>
            <button class="secondary" data-action="toggle" data-id="${room.id}" data-enabled="${room.enabled ? "0" : "1"}">${enabledText}</button>
            <button class="danger" data-action="delete" data-id="${room.id}">删除</button>
          </div>
          ${renderSessionList(roomNo)}
        </article>
      `;
    })
    .join("");
}

function renderCollectorClients(clients) {
  if (!clients.length) {
    collectorClientsBody.innerHTML = '<tr><td colspan="7">暂无客户端</td></tr>';
    return;
  }

  collectorClientsBody.innerHTML = clients
    .map((client) => {
      const clientId = escapeHtml(client.client_id);
      const remark = escapeHtml(client.remark || "");
      const enabled = client.enabled !== false;
      const maxActiveRooms = Number(client.max_active_rooms || 50);
      return `
        <tr>
          <td>
            <strong>${clientId}</strong>
            <div class="muted">创建 ${escapeHtml(formatTime(client.created_at))}</div>
          </td>
          <td>${enabled ? '<span class="badge on">启用</span>' : '<span class="badge off">停用</span>'}</td>
          <td>${Number.isFinite(maxActiveRooms) ? maxActiveRooms : 50}</td>
          <td>${sessdataCell(client)}</td>
          <td>${escapeHtml(formatTime(client.last_seen_at))}</td>
          <td>${remark || "-"}</td>
          <td>
            <div class="actions">
              <button
                class="secondary"
                data-action="edit-client"
                data-client-id="${clientId}"
                data-remark="${remark}"
                data-enabled="${enabled ? "1" : "0"}"
                data-max-active-rooms="${Number.isFinite(maxActiveRooms) ? maxActiveRooms : 50}"
              >编辑</button>
              <button
                class="danger"
                data-action="clear-client-cookie"
                data-client-id="${clientId}"
                data-remark="${remark}"
                data-enabled="${enabled ? "1" : "0"}"
                data-max-active-rooms="${Number.isFinite(maxActiveRooms) ? maxActiveRooms : 50}"
              >清空Cookie</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function updateSelectedRoomRows() {
  const activeRoomFilter = eventRoomFilter.value.trim();
  selectedRoomId = activeRoomFilter;
  roomsBody.querySelectorAll(".room-card[data-room-filter]").forEach((row) => {
    const selected = activeRoomFilter && row.dataset.roomFilter === activeRoomFilter;
    row.classList.toggle("selected", Boolean(selected));
    row.setAttribute("aria-selected", selected ? "true" : "false");
  });
  roomsBody.querySelectorAll(".session-item[data-session-id]").forEach((item) => {
    item.classList.toggle("selected", selectedSessionId && item.dataset.sessionId === selectedSessionId);
  });
}

async function loadRoomSessions(roomNo) {
  const key = String(roomNo);
  if (roomSessions.has(key)) return roomSessions.get(key);
  const payload = await requestJson(`/api/live-sessions?room_id=${encodeURIComponent(key)}&limit=10`);
  roomSessions.set(key, payload.items || []);
  return roomSessions.get(key);
}

async function applyRoomEventFilter(roomNo) {
  const key = String(roomNo);
  selectedRoomId = key;
  selectedSessionId = "";
  eventRoomFilter.value = roomNo;
  // 选中主播后自动展开场次，并重新拉取以包含刚开播的直播。
  expandedRooms.add(key);
  roomSessions.delete(key);
  renderRooms(roomsCache);
  updateSelectedRoomRows();

  let sessions = [];
  try {
    sessions = await loadRoomSessions(key);
  } catch (error) {
    showToast(error.message);
  }
  // 默认加载最近一个场次的消息（场次按开播时间倒序，取第一个）。
  if (sessions.length) {
    selectedSessionId = String(sessions[0].id);
  }
  renderRooms(roomsCache);
  updateSelectedRoomRows();
  await Promise.all([loadEvents({ reset: true }), loadAnalytics()]);
}

async function applySessionEventFilter(roomNo, sessionId) {
  selectedRoomId = String(roomNo);
  selectedSessionId = String(sessionId);
  eventRoomFilter.value = String(roomNo);
  updateSelectedRoomRows();
  await Promise.all([loadEvents({ reset: true }), loadAnalytics()]);
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
  roomsCache = payload.items || [];
  renderRooms(roomsCache);
  return roomsCache;
}

async function loadCollectorClients() {
  const payload = await requestJson("/api/collector-clients");
  renderCollectorClients(payload.items || []);
  return payload.items || [];
}

function currentEventFilters() {
  const roomId = eventRoomFilter.value.trim();
  const eventType = eventTypeFilter.value;
  return {
    roomId,
    liveSessionId: selectedSessionId,
    eventType,
    key: `${roomId}:${selectedSessionId}:${eventType}`,
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
  if (filters.liveSessionId) {
    params.set("live_session_id", filters.liveSessionId);
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

const CHART_W = 340;
const CHART_H = 130;
const PLOT_LEFT = 30;
const PLOT_RIGHT = 332;
const PLOT_TOP = 10;
const PLOT_BOTTOM = 100;
const CHART_Y_LEVELS = [1, 2 / 3, 1 / 3, 0];

function chartGeometry(series, keys) {
  let maxValue = 1;
  keys.forEach((key) => {
    series.forEach((item) => {
      maxValue = Math.max(maxValue, Number(item[key] || 0));
    });
  });
  const plotW = PLOT_RIGHT - PLOT_LEFT;
  const plotH = PLOT_BOTTOM - PLOT_TOP;
  return {
    maxValue,
    xFor: (index) =>
      series.length <= 1 ? PLOT_LEFT + plotW / 2 : PLOT_LEFT + (index / (series.length - 1)) * plotW,
    yFor: (value) => clamp(PLOT_BOTTOM - (value / maxValue) * plotH, PLOT_TOP, PLOT_BOTTOM),
  };
}

function seriesPoints(series, key, geo) {
  return series.map((item, index) => ({
    x: geo.xFor(index),
    y: geo.yFor(Number(item[key] || 0)),
    value: Number(item[key] || 0),
    label: item.label || "",
  }));
}

function pointsToPath(points) {
  return points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
}

function pointsToDots(points, cls) {
  return points
    .map(
      (p) =>
        `<circle class="chart-dot ${cls}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="2.6"><title>${escapeHtml(
          p.label
        )} · ${escapeHtml(formatShortNumber(p.value))}</title></circle>`
    )
    .join("");
}

function chartGridLines(geo) {
  return CHART_Y_LEVELS.map((frac) => {
    const y = geo.yFor(geo.maxValue * frac);
    return `<line x1="${PLOT_LEFT}" y1="${y.toFixed(1)}" x2="${PLOT_RIGHT}" y2="${y.toFixed(1)}"></line>`;
  }).join("");
}

function chartYAxis(geo) {
  return CHART_Y_LEVELS.map((frac) => {
    const value = geo.maxValue * frac;
    const y = geo.yFor(value);
    return `<text class="axis-y" x="${(PLOT_LEFT - 6).toFixed(1)}" y="${(y + 3).toFixed(1)}" text-anchor="end">${escapeHtml(
      formatShortNumber(Math.round(value))
    )}</text>`;
  }).join("");
}

function chartXAxis(series, geo) {
  if (!series.length) return "";
  const step = Math.max(1, Math.ceil(series.length / 7));
  return series
    .map((item, index) => {
      if (index % step !== 0 && index !== series.length - 1) return "";
      const anchor = index === 0 ? "start" : index === series.length - 1 ? "end" : "middle";
      return `<text class="axis-x" x="${geo.xFor(index).toFixed(1)}" y="${(PLOT_BOTTOM + 16).toFixed(
        1
      )}" text-anchor="${anchor}">${escapeHtml(item.label || "")}</text>`;
    })
    .join("");
}

function renderChart({ title, subtitle, series, primaryKey, secondaryKey = "", primaryLabel = "", secondaryLabel = "" }) {
  const keys = secondaryKey ? [primaryKey, secondaryKey] : [primaryKey];
  const geo = chartGeometry(series, keys);
  const primaryPoints = seriesPoints(series, primaryKey, geo);
  const secondaryPoints = secondaryKey ? seriesPoints(series, secondaryKey, geo) : [];
  return `
    <article class="chart-card">
      <div class="chart-head">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(subtitle)}</p>
        </div>
        <button class="mini-refresh" type="button" data-action="refresh-analytics">刷新</button>
      </div>
      <svg class="line-chart" viewBox="0 0 ${CHART_W} ${CHART_H}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(title)}趋势图">
        <g class="grid-lines">${chartGridLines(geo)}</g>
        <g class="axis-labels">${chartYAxis(geo)}${chartXAxis(series, geo)}</g>
        ${secondaryPoints.length ? `<path class="chart-line secondary-line" d="${pointsToPath(secondaryPoints)}"></path>` : ""}
        ${primaryPoints.length ? `<path class="chart-line primary-line" d="${pointsToPath(primaryPoints)}"></path>` : ""}
        ${secondaryPoints.length ? `<g class="chart-dots">${pointsToDots(secondaryPoints, "secondary")}</g>` : ""}
        ${primaryPoints.length ? `<g class="chart-dots">${pointsToDots(primaryPoints, "primary")}</g>` : ""}
      </svg>
      <div class="chart-legend">
        <span><i class="legend-dot primary"></i>${escapeHtml(primaryLabel || title)}</span>
        ${secondaryKey ? `<span><i class="legend-dot secondary"></i>${escapeHtml(secondaryLabel)}</span>` : ""}
      </div>
    </article>
  `;
}

function renderGiftRank(rows, sessionSelected) {
  const title = sessionSelected ? "本场次用户送礼榜" : "用户送礼榜";
  const content = rows.length
    ? rows
        .map(
          (row, index) => `
            <div class="rank-row">
              <span class="rank-index">${index + 1}</span>
              <div class="rank-user">
                <strong title="${escapeHtml(row.username || row.uid || "-")}">${escapeHtml(row.username || `UID ${row.uid || "-"}`)}</strong>
                <small>${escapeHtml(row.uid || "-")} · ${escapeHtml(formatTime(row.last_gift_at))}</small>
              </div>
              <div class="rank-value">
                <strong>${escapeHtml(formatShortNumber(row.gift_total_coin))}</strong>
                <small>${escapeHtml(formatShortNumber(row.gift_count))} 次 / ${escapeHtml(formatShortNumber(row.gift_num))} 件</small>
              </div>
            </div>
          `
        )
        .join("")
    : '<div class="rank-empty">暂无送礼数据</div>';
  return `
    <article class="rank-card">
      <div class="chart-head">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>按瓜子总数排序</p>
        </div>
      </div>
      <div class="rank-list">${content}</div>
    </article>
  `;
}

function renderAnalytics(payload) {
  const summary = payload.summary || {};
  const byType = summary.by_type || {};
  const series = payload.series || [];
  const enterRoom = byType.enter_room || {};
  const follow = byType.follow || {};
  const danmaku = byType.danmaku || {};
  const gift = byType.gift || {};
  const guard = byType.guard || {};
  const superChat = byType.super_chat || {};
  const session = payload.range?.session;
  const roomText = session
    ? `房间 ${payload.range.room_id || session.room_id} · 场次 #${session.id}`
    : payload.range?.room_id
      ? `房间 ${payload.range.room_id}`
      : "全部房间";

  streamSummary.textContent = `${roomText} · ${formatShortNumber(summary.total_events)} 条事件 · ${formatShortNumber(summary.unique_users)} 位用户`;
  analyticsSummary.innerHTML = `
    <div class="metric-tile">
      <span>总事件</span>
      <strong>${formatShortNumber(summary.total_events)}</strong>
    </div>
    <div class="metric-tile">
      <span>去重用户</span>
      <strong>${formatShortNumber(summary.unique_users)}</strong>
    </div>
    <div class="metric-tile">
      <span>进房人数</span>
      <strong>${formatShortNumber(enterRoom.unique_users)}</strong>
    </div>
    <div class="metric-tile">
      <span>礼物价值</span>
      <strong>${formatShortNumber(summary.gift_total_coin)}</strong>
    </div>
  `;

  if (!series.length) {
    analyticsCharts.innerHTML = '<div class="empty-state">暂无统计数据</div>';
    return;
  }

  analyticsCharts.innerHTML = [
    renderChart({
      title: "进房间人数",
      subtitle: `总人次 ${formatShortNumber(enterRoom.count)} / 去重人数 ${formatShortNumber(enterRoom.unique_users)}`,
      series,
      primaryKey: "enter_room_count",
      secondaryKey: "enter_room_users",
      primaryLabel: "总人次",
      secondaryLabel: "去重人数",
    }),
    renderChart({
      title: "关注人数",
      subtitle: `关注总数 ${formatShortNumber(follow.unique_users)}`,
      series,
      primaryKey: "follow_count",
      primaryLabel: "关注人数",
    }),
    renderChart({
      title: "聊天消息",
      subtitle: `弹幕总数 ${formatShortNumber(danmaku.count)}`,
      series,
      primaryKey: "danmaku_count",
      primaryLabel: "弹幕",
    }),
    renderChart({
      title: "送礼人数",
      subtitle: `送礼 ${formatShortNumber(gift.count)} 次 / 去重 ${formatShortNumber(gift.unique_users)} 人`,
      series,
      primaryKey: "gift_count",
      secondaryKey: "gift_users",
      primaryLabel: "送礼次数",
      secondaryLabel: "送礼人数",
    }),
    renderChart({
      title: "送礼金额",
      subtitle: `瓜子总数 ${formatShortNumber(summary.gift_total_coin)} · 上舰 ${formatShortNumber(guard.count)} · 醒目 ${formatShortNumber(superChat.count)}`,
      series,
      primaryKey: "gift_total_coin",
      primaryLabel: "瓜子总数",
    }),
    renderChart({
      title: "上舰与醒目留言",
      subtitle: `上舰 ${formatShortNumber(guard.count)} · 醒目留言 ${formatShortNumber(superChat.count)}`,
      series,
      primaryKey: "guard_count",
      secondaryKey: "super_chat_count",
      primaryLabel: "上舰",
      secondaryLabel: "醒目留言",
    }),
    renderGiftRank(payload.gift_rank || [], Boolean(session)),
  ].join("");
}

async function loadAnalytics() {
  const filters = currentEventFilters();
  const params = new URLSearchParams({
    minutes: String(ANALYTICS_MINUTES),
    bucket_minutes: String(ANALYTICS_BUCKET_MINUTES),
  });
  if (filters.roomId) {
    params.set("room_id", filters.roomId);
  }
  if (filters.liveSessionId) {
    params.set("live_session_id", filters.liveSessionId);
  }
  const payload = await requestJson(`/api/analytics/overview?${params}`);
  renderAnalytics(payload);
  return payload;
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
      loadCollectorClients(),
      resetEvents ? loadEvents({ reset: true }) : Promise.resolve(),
      loadAnalytics(),
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

collectorClientForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const clientId = collectorClientIdInput.value.trim();
  const sessdata = collectorSessdataInput.value.trim();
  if (!clientId) {
    showToast("client_id 不能为空");
    return;
  }
  const body = {
    client_id: clientId,
    remark: collectorClientRemarkInput.value.trim(),
    enabled: collectorEnabledInput.checked,
    max_active_rooms: Math.max(1, Number.parseInt(collectorMaxRoomsInput.value || "50", 10)),
  };
  if (sessdata) {
    body.bili_sessdata = sessdata;
  }

  try {
    await requestJson("/api/collector-clients", {
      method: "POST",
      body: JSON.stringify(body),
    });
    collectorSessdataInput.value = "";
    showToast("客户端配置已保存");
    await loadCollectorClients();
  } catch (error) {
    showToast(error.message);
  }
});

collectorClientsBody.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  const clientId = target.dataset.clientId || "";
  const remark = target.dataset.remark || "";
  const enabled = target.dataset.enabled !== "0";
  const maxActiveRooms = target.dataset.maxActiveRooms || "50";

  if (target.dataset.action === "edit-client") {
    collectorClientIdInput.value = clientId;
    collectorClientRemarkInput.value = remark;
    collectorEnabledInput.checked = enabled;
    collectorMaxRoomsInput.value = maxActiveRooms;
    collectorSessdataInput.value = "";
    collectorSessdataInput.focus();
    return;
  }

  if (target.dataset.action !== "clear-client-cookie") return;
  try {
    await requestJson("/api/collector-clients", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        remark,
        enabled,
        max_active_rooms: Math.max(1, Number.parseInt(maxActiveRooms || "50", 10)),
        bili_sessdata: "",
      }),
    });
    showToast("客户端 Cookie 已清空");
    await loadCollectorClients();
  } catch (error) {
    showToast(error.message);
  }
});

function openClientConfig() {
  clientConfigModal.hidden = false;
  collectorClientIdInput.focus();
}

function closeClientConfig() {
  clientConfigModal.hidden = true;
}

clientConfigBtn.addEventListener("click", openClientConfig);
clientConfigCloseBtn.addEventListener("click", closeClientConfig);
clientConfigModal.addEventListener("click", (event) => {
  if (event.target === clientConfigModal) {
    closeClientConfig();
  }
});

roomsBody.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  try {
    if (target) {
      if (target.dataset.action === "toggle-sessions") {
        const roomNo = target.dataset.roomFilter;
        const key = String(roomNo);
        if (expandedRooms.has(key)) {
          expandedRooms.delete(key);
        } else {
          expandedRooms.add(key);
          renderRooms(roomsCache);
          await loadRoomSessions(key);
        }
        renderRooms(roomsCache);
        updateSelectedRoomRows();
        return;
      }
      if (target.dataset.action === "select-session") {
        await applySessionEventFilter(target.dataset.roomFilter, target.dataset.sessionId);
        return;
      }
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

    const row = event.target.closest(".room-card[data-room-filter]");
    if (!row) return;
    await applyRoomEventFilter(row.dataset.roomFilter);
  } catch (error) {
    showToast(error.message);
  }
});

refreshBtn.addEventListener("click", () => refreshAll({ resetEvents: true }));
analyticsRefreshBtn.addEventListener("click", () => loadAnalytics().catch((error) => showToast(error.message)));
analyticsCharts.addEventListener("click", (event) => {
  if (!event.target.closest("[data-action='refresh-analytics']")) return;
  loadAnalytics().catch((error) => showToast(error.message));
});
eventRoomFilter.addEventListener("input", () => {
  selectedSessionId = "";
  updateSelectedRoomRows();
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    Promise.all([loadEvents({ reset: true }), loadAnalytics()]).catch((error) => showToast(error.message));
  }, 250);
});
eventTypeFilter.addEventListener("change", () => {
  Promise.all([loadEvents({ reset: true }), loadAnalytics()]).catch((error) => showToast(error.message));
});
eventsList.addEventListener("scroll", maybeLoadMoreEvents);

refreshAll({ resetEvents: true });
setInterval(() => {
  refreshAll({ resetEvents: eventsList.scrollTop <= 40 });
}, 10000);
