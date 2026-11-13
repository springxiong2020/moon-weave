"use strict";
const $ = (id) => document.getElementById(id);
const number = (value) => new Intl.NumberFormat("zh-CN").format(value || 0);
const normalize = (value) =>
  value.replace(
    /壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区|省|市/g,
    "",
  );
const escapeHTML = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const percent = (value, total) =>
  total ? ((value / total) * 100).toFixed(1) + "%" : "—";
const localTime = (value) =>
  value
    ? value.endsWith("Z")
      ? new Date(value).toLocaleString("zh-CN", { hour12: false })
      : value.slice(0, 19).replace("T", " ")
    : "暂无记录";
const compactTime = (value) =>
  value
    ? !value.endsWith("Z")
      ? value.slice(5, 16).replace("T", " ")
      : new Date(value).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "—";
const chartColors = [
  "#79b5f5",
  "#84d8ce",
  "#ba9be7",
  "#edbe76",
  "#dc8b95",
  "#8298b9",
];
const statusColors = {
  200: "#79b5f5",
  206: "#dc8b95",
  301: "#8298b9",
  302: "#a0b5d2",
  304: "#edbe76",
  400: "#6b9fdf",
  403: "#ba9be7",
  404: "#84d8ce",
  444: "#74b8be",
  500: "#ef929c",
  502: "#df9a8c",
  503: "#e8ae77",
  504: "#d9a1c9",
};
const chartText = "#93abc9";
const reducedMotion = window.matchMedia(
  "(prefers-reduced-motion: reduce)",
).matches;
const mapNames = new Set();
// Leave room for border shadows, labels and the controls at either edge.
function defaultMapView() {
  return {
    center: null,
    zoom: 1,
    layoutCenter: ["52%", "49%"],
    layoutSize: "88%",
  };
}
let map,
  trend,
  statuses,
  mapReady = false,
  lastData,
  rankingRegion = "all";
let auth = { authenticated: false, configured: false, csrf_token: "" };
let currentView = "public",
  adminPanel = "overview",
  autoRefresh = false;
let publicController,
  adminGeneration = 0,
  toastTimer;
const pages = { logs: 1, locations: 1 };

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined && text !== null) result.textContent = String(text);
  if (className) result.className = className;
  return result;
}
// Keep native selects as the data source; draw popups inside the page so
// browser/OS select overlays cannot drift outside the mobile viewport.
const selectWidgets = new Map();
function enhanceSelect(id, label) {
  const select = $(id),
    wrapper = node("div", null, "custom-select");
  select.parentNode.insertBefore(wrapper, select);
  wrapper.append(select);
  const trigger = node("button", "", "select-trigger"),
    menu = node("div", null, "select-menu");
  trigger.type = "button";
  trigger.id = id + "-trigger";
  trigger.setAttribute("role", "combobox");
  trigger.setAttribute("aria-label", label);
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-controls", id + "-menu");
  trigger.setAttribute("aria-expanded", "false");
  menu.id = id + "-menu";
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", label);
  menu.hidden = true;
  wrapper.append(trigger, menu);
  for (const labelElement of Array.from(select.labels || []))
    labelElement.htmlFor = trigger.id;
  select.hidden = true;
  let active = -1,
    signature = "",
    typed = "",
    typedAt = 0;
  const items = () => Array.from(menu.children);
  function mark(index) {
    const options = items();
    active = index;
    options.forEach((item, i) => item.classList.toggle("active", i === index));
    if (options[index]) {
      trigger.setAttribute("aria-activedescendant", options[index].id);
      options[index].scrollIntoView({ block: "nearest" });
    }
  }
  function close() {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    trigger.removeAttribute("aria-activedescendant");
  }
  function sync() {
    const options = Array.from(select.options),
      selected = options.find((option) => option.value === select.value);
    trigger.textContent = selected ? selected.textContent : label;
    trigger.title = trigger.textContent;
    trigger.disabled = select.disabled;
    const next = JSON.stringify(
      options.map((option) => [
        option.value,
        option.textContent,
        option.disabled,
      ]),
    );
    if (next !== signature) {
      signature = next;
      menu.replaceChildren(
        ...options.map((option, index) => {
          const item = node("button", option.textContent, "select-option");
          item.type = "button";
          item.tabIndex = -1;
          item.id = id + "-option-" + index;
          item.setAttribute("role", "option");
          item.title = option.textContent;
          item.disabled = option.disabled;
          item.dataset.value = option.value;
          item.onpointerdown = (event) => event.preventDefault();
          item.onclick = () => choose(index);
          return item;
        }),
      );
    }
    items().forEach((item) =>
      item.setAttribute(
        "aria-selected",
        String(item.dataset.value === select.value),
      ),
    );
    if (!menu.hidden)
      mark(
        Math.max(
          0,
          options.findIndex((option) => option.value === select.value),
        ),
      );
  }
  function open() {
    for (const widget of selectWidgets.values()) widget.close();
    sync();
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    const rect = trigger.getBoundingClientRect();
    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    const below = viewportHeight - rect.bottom - 12,
      above = rect.top - 12;
    const upwards = below < Math.min(menu.scrollHeight, 288) && above > below;
    wrapper.classList.toggle("opens-up", upwards);
    menu.style.maxHeight =
      Math.max(48, Math.min(288, upwards ? above : below)) + "px";
    mark(
      Math.max(
        0,
        Array.from(select.options).findIndex(
          (option) => option.value === select.value,
        ),
      ),
    );
  }
  function choose(index) {
    const option = select.options[index];
    if (!option || option.disabled) return;
    const changed = select.value !== option.value;
    select.value = option.value;
    close();
    sync();
    trigger.focus();
    if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
  }
  trigger.onclick = () => (menu.hidden ? open() : close());
  trigger.onkeydown = (event) => {
    const options = Array.from(select.options);
    if (event.key === "Escape" || event.key === "Tab") {
      close();
      return;
    }
    if (["Enter", " "].includes(event.key)) {
      event.preventDefault();
      if (menu.hidden) open();
      else choose(active);
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const wasClosed = menu.hidden;
      if (wasClosed) open();
      let next = active;
      if (event.key === "Home") next = 0;
      else if (event.key === "End") next = options.length - 1;
      else if (!wasClosed)
        next = Math.max(
          0,
          Math.min(
            options.length - 1,
            active + (event.key === "ArrowDown" ? 1 : -1),
          ),
        );
      mark(next);
      return;
    }
    if (
      event.key.length === 1 &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey
    ) {
      event.preventDefault();
      if (menu.hidden) open();
      typed = Date.now() - typedAt < 700 ? typed + event.key : event.key;
      typedAt = Date.now();
      const found = options.findIndex((option) =>
        option.textContent.toLowerCase().startsWith(typed.toLowerCase()),
      );
      if (found >= 0) mark(found);
    }
  };
  document.addEventListener("pointerdown", (event) => {
    if (!wrapper.contains(event.target)) close();
  });
  select.addEventListener("change", sync);
  const widget = { sync, close, trigger, menu, isOpen: () => !menu.hidden };
  selectWidgets.set(id, widget);
  sync();
  return widget;
}
function syncSelects() {
  for (const widget of selectWidgets.values()) widget.sync();
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 4500);
}
function empty(container, message) {
  container.replaceChildren(node("div", message, "empty-state"));
}
function tableEmpty(container, columns, message = "当前范围暂无数据") {
  const row = node("tr"),
    cell = node("td", message, "empty-state");
  cell.colSpan = columns;
  row.append(cell);
  container.replaceChildren(row);
}
function addCell(row, content, className) {
  const cell = node("td", null, className);
  if (content instanceof Node) cell.append(content);
  else cell.textContent = content ?? "—";
  row.append(cell);
  return cell;
}
function bytes(value) {
  if (value < 1024) return [number(value), "B"];
  for (const [unit, scale] of [
    ["TB", 2 ** 40],
    ["GB", 2 ** 30],
    ["MB", 2 ** 20],
    ["KB", 2 ** 10],
  ]) {
    if (value >= scale) return [(value / scale).toFixed(1), unit];
  }
  return ["0", "B"];
}
function metric(id, value, unit) {
  const element = $(id);
  element.replaceChildren(document.createTextNode(value));
  if (unit) element.append(node("small", unit));
}
function filters() {
  return { site: $("site").value, hours: $("hours").value };
}
async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { ...options.headers };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = auth.csrf_token;
  }
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), 20000);
  const abort = () => timeout.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) timeout.abort();
  try {
    const response = await fetch(path.replace(/^\//, ""), {
      ...options,
      method,
      headers,
      signal: timeout.signal,
      credentials: "same-origin",
    });
    const data = await response
      .json()
      .catch(() => ({ error: "服务响应格式异常" }));
    if (!response.ok) {
      if (response.status === 401 && path.startsWith("/api/admin/")) {
        applyAuth({
          authenticated: false,
          configured: auth.configured,
          csrf_token: auth.csrf_token,
        });
        showView("public");
      }
      if (
        response.status === 403 &&
        (path.startsWith("/api/admin/") || path === "/api/auth/logout")
      ) {
        await loadAuth();
        if (!auth.authenticated) showView("public");
      }
      throw new Error(data.error || `请求失败（${response.status}）`);
    }
    return data;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}
function clearAdminData() {
  adminGeneration++;
  for (const id of [
    "admin-metrics",
    "queue-summary",
    "run-details",
    "settings",
    "cursor-rows",
    "log-results",
    "location-rows",
    "path-rows",
    "ip-rows",
    "slow-summary",
  ])
    $(id).replaceChildren();
  $("admin-user").textContent = "";
  $("admin-notice").textContent = "";
  $("log-query").value = "";
  $("location-query").value = "";
  pages.logs = pages.locations = 1;
}
function applyAuth(data) {
  const wasAdmin = auth.authenticated;
  auth = { ...auth, ...data };
  $("auth-button").querySelector("span").textContent = auth.authenticated
    ? "退出登录"
    : "管理员登录";
  $("role-label").textContent = auth.authenticated
    ? "管理员数据空间"
    : "公开数据空间";
  $("admin-lock").textContent = auth.authenticated ? "已登录" : "登录";
  if (auth.authenticated) $("admin-user").textContent = auth.username;
  else if (wasAdmin) clearAdminData();
}
async function loadAuth() {
  try {
    applyAuth(await api("/api/auth"));
  } catch (error) {
    $("notice").textContent = "暂时无法读取登录状态：" + error.message;
  }
}
function updateFilterContext() {
  const globalView =
    currentView === "admin" && ["overview", "locations"].includes(adminPanel);
  $("filter-note").textContent = globalView
    ? "筛选用于日志与流量分析；当前视图为全库状态"
    : "统计按日志发生时间计算";
}
function showView(view) {
  if (view === "admin" && !auth.authenticated) {
    openLogin();
    return;
  }
  currentView = view;
  updateFilterContext();
  $("public-view").hidden = view !== "public";
  $("admin-view").hidden = view !== "admin";
  $("public-nav").classList.toggle("active", view === "public");
  $("admin-nav").classList.toggle("active", view === "admin");
  $("view-title").textContent = view === "admin" ? "管理工作台" : "流量全景";
  $("page-eyebrow").textContent =
    view === "admin" ? "在数据背后，掌握每一个细节" : "来自每一个角落的连接";
  $("page-title").replaceChildren(
    document.createTextNode(
      view === "admin" ? "掌控网站的每一刻" : "看见流量的脉搏",
    ),
    node("span", ".", "heading-point"),
  );
  $("page-description").textContent =
    view === "admin"
      ? "从访问明细到定位队列，让运行状态清晰可见。"
      : "从一次请求，到一张地图。探索网站与世界的连接。";
  if (view === "admin") refreshAdmin();
  else {
    adminGeneration++;
    requestAnimationFrame(resizeCharts);
  }
}
function openLogin() {
  $("login-error").textContent = auth.configured
    ? ""
    : "请先在服务器配置 DASHBOARD_USER、DASHBOARD_PASSWORD 和 SESSION_SECRET。";
  $("password").value = "";
  $("login-dialog").showModal();
}
function renderRanking() {
  if (!lastData) return;
  const all = lastData.provinces.slice().sort((a, b) => b.value - a.value);
  const isForeign = (p) => p.name.startsWith("境外");
  const isDomestic = (p) =>
    !isForeign(p) && !["未知", "内网/保留地址"].includes(p.name);
  const items = all.filter(
    (p) =>
      rankingRegion === "all" ||
      (rankingRegion === "overseas" ? isForeign(p) : isDomestic(p)),
  );
  const max = Math.max(1, ...items.map((p) => p.value));
  $("ranking").replaceChildren();
  $("region-count").textContent = `${all.length} 个来源地区`;
  $("coverage").textContent =
    `占当前窗口 ${number(lastData.total)} 次请求的比例`;
  items.forEach((p, index) => {
    const item = node(
      "li",
      null,
      "ranking-item" + (isForeign(p) ? " foreign" : ""),
    );
    const row = node("div", null, "ranking-row");
    const name = node("span", p.name, "rank-name");
    name.title = p.name;
    row.append(
      node("span", String(index + 1).padStart(2, "0"), "rank-number"),
      name,
      node("span", number(p.value), "rank-value"),
      node("span", percent(p.value, lastData.total), "rank-percent"),
    );
    const bar = node("div", null, "bar"),
      fill = node("span");
    fill.style.width = (p.value / max) * 100 + "%";
    bar.append(fill);
    item.append(row, bar);
    $("ranking").append(item);
  });
  if (!items.length) empty($("ranking"), "当前范围暂无此类来源");
}
function renderSpark(data) {
  const values = data.trend.map((p) => p.value),
    max = Math.max(1, ...values);
  const points = values
    .map(
      (v, i) =>
        `${(i / Math.max(1, values.length - 1)) * 180},${45 - (v / max) * 38}`,
    )
    .join(" ");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 180 50");
  svg.setAttribute("preserveAspectRatio", "none");
  const line = document.createElementNS(svg.namespaceURI, "polyline");
  line.setAttribute("points", points);
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", "#9ed5ff");
  line.setAttribute("stroke-width", "2");
  svg.append(line);
  $("spark").replaceChildren(svg);
}
function renderStats(data) {
  lastData = data;
  metric("total", number(data.total));
  metric("uv", number(data.unique_ips));
  metric(
    "success-rate",
    data.success_rate === null ? "—" : String(data.success_rate),
    data.success_rate === null ? "" : "%",
  );
  metric("latency", String(data.average_ms), "ms");
  metric("traffic", ...bytes(data.traffic_bytes));
  $("failures").textContent = number(data.failures);
  $("growth").textContent =
    data.growth === null
      ? data.total
        ? "上一时段无请求记录"
        : "等待第一条访问请求"
      : `${data.growth >= 0 ? "+" : ""}${data.growth}% 较上一等长时段`;
  $("growth").className =
    "metric-foot" +
    (data.growth > 0 ? " positive" : data.growth < 0 ? " negative" : "");
  $("updated").textContent =
    "更新于 " +
    data.updated_at.slice(11, 19);
  $("notice").textContent = data.total
    ? ""
    : "当前站点与时间窗口暂无访问记录，可调整筛选条件。";
  const selected = $("site").value;
  $("site").replaceChildren(
    new Option("全部站点", ""),
    ...data.sites.map((s) => new Option(s, s)),
  );
  if (selected && !data.sites.includes(selected))
    $("site").append(new Option(selected, selected));
  $("site").value = selected;
  selectWidgets.get("site")?.sync();
  for (const key of ["domestic", "overseas", "unknown"])
    $(key + "-share").textContent = percent(data.regions[key], data.total);
  renderRanking();
  renderSpark(data);
  if (mapReady) {
    const mapData = data.provinces
      .filter((p) => mapNames.has(normalize(p.name)))
      .map((p) => ({ name: normalize(p.name), value: p.value }));
    map.setOption({
      visualMap: { max: Math.max(1, ...mapData.map((p) => p.value)) },
      series: [{ data: mapData }],
    });
  }
  const peak = data.trend.reduce(
    (best, item) => (item.value > best.value ? item : best),
    { value: 0, time: null },
  );
  $("peak-time").textContent = compactTime(peak.time);
  $("peak-count").textContent = number(peak.value);
  if (trend)
    trend.setOption({
      series: [{ data: data.trend.map((p) => [p.time, p.value]) }],
    });
  if (statuses) {
    const statusData = data.statuses
      .slice()
      .sort((a, b) => b.value - a.value || Number(a.status) - Number(b.status))
      .map((item) => ({
        name: String(item.status),
        value: item.value,
        itemStyle: {
          color:
            statusColors[item.status] ||
            chartColors[Number(item.status) % chartColors.length],
        },
      }));
    statuses.setOption({
      legend: { data: statusData.map((item) => item.name) },
      graphic: [
        {
          type: "text",
          left: "center",
          top: "35%",
          style: {
            text: data.total ? number(data.total) : "暂无数据",
            fill: "#e2edff",
            fontSize: 23,
            fontWeight: 500,
          },
        },
        {
          type: "text",
          left: "center",
          top: "48%",
          style: { text: "请求总量", fill: chartText, fontSize: 10 },
        },
      ],
      series: [{ data: statusData }],
    });
  }
  $("site-rows").replaceChildren();
  data.site_stats.forEach((s) => {
    const row = node("tr"),
      button = node("button", s.site, "site-link");
    button.onclick = () => {
      $("site").value = s.site;
      filterChanged();
    };
    const siteCell = node("span");
    siteCell.append(node("i", null, "site-dot"), button);
    addCell(row, siteCell);
    addCell(row, number(s.requests));
    const share = node("div", null, "inline-bar"),
      bar = node("span"),
      fill = node("i");
    fill.style.width = (data.total ? (s.requests / data.total) * 100 : 0) + "%";
    bar.append(fill);
    share.append(bar, node("b", percent(s.requests, data.total)));
    addCell(row, share);
    addCell(row, s.average_ms + " ms");
    addCell(
      row,
      node(
        "span",
        number(s.server_errors),
        "status-pill" + (s.server_errors ? " warn" : ""),
      ),
    );
    $("site-rows").append(row);
  });
  if (!data.site_stats.length) tableEmpty($("site-rows"), 5);
}
async function refresh() {
  publicController?.abort();
  const controller = new AbortController();
  publicController = controller;
  $("refresh").disabled = true;
  try {
    const data = await api("/api/stats?" + new URLSearchParams(filters()), {
      signal: controller.signal,
    });
    if (controller.signal.aborted) return;
    renderStats(data);
    $("connection").textContent = autoRefresh ? "实时观测" : "刷新已暂停";
    document.querySelector(".live-indicator").classList.remove("offline");
  } catch (error) {
    if (error.name === "AbortError" && controller.signal.aborted) return;
    $("notice").textContent =
      error.name === "AbortError" ? "请求超时，请稍后刷新。" : error.message;
    $("connection").textContent = "连接异常";
    document.querySelector(".live-indicator").classList.add("offline");
  } finally {
    if (publicController === controller) {
      $("refresh").disabled = false;
      publicController = null;
    }
  }
  if (currentView === "admin" && auth.authenticated) await refreshAdmin();
}
function pageControls(kind, data) {
  const max = Math.max(1, Math.ceil(data.total / data.page_size));
  $(kind + "-page").textContent =
    `第 ${data.page} / ${max} 页 · ${number(data.total)} 条`;
  $(kind + "-prev").disabled = data.page <= 1;
  $(kind + "-next").disabled = data.page >= max;
}
function detailsList(id, items) {
  $(id).replaceChildren(
    ...items.map(([label, value]) => {
      const row = node("div");
      row.append(node("dt", label), node("dd", value));
      return row;
    }),
  );
}
function renderOverview(data) {
  const states = data.states;
  $("admin-metrics").replaceChildren(
    ...[
      ["待处理 IP", states.pending || 0, "等待定位进程消费"],
      ["等待重试 IP", states.retry || 0, "失败及限流延期任务"],
      ["已解析 IP", states.resolved || 0, "包含境外与保留地址"],
      ["解析异常日志", data.malformed, "全库异常物理行数"],
    ].map(([label, value, hint]) => {
      const card = node("article", null, "metric");
      card.append(
        node("div", label, "metric-label"),
        node("strong", number(value)),
        node("div", hint, "metric-foot"),
      );
      return card;
    }),
  );
  $("queue-summary").replaceChildren(
    ...[
      ["待处理", states.pending || 0],
      ["重试", states.retry || 0],
      ["已解析", states.resolved || 0],
    ].map(([name, value]) => {
      const item = node("div", name);
      item.append(node("b", number(value)));
      return item;
    }),
  );
  detailsList("run-details", [
    ["当前到期任务", number(data.due)],
    ["最近定位更新", localTime(data.last_location_update)],
    ["最近日志发生时间", localTime(data.latest_event)],
    ["下次任务到期", localTime(data.next_location_due)],
  ]);
  detailsList("settings", [
    ["百度请求速率", data.settings.geo_qps + " 次 / 秒"],
    ["日志时间", data.settings.log_time],
    ["采集 IP 豁免", data.settings.whitelist || "未配置"],
  ]);
  $("cursor-rows").replaceChildren();
  data.cursors.forEach((c) => {
    const row = node("tr");
    addCell(row, c.path, "request-cell");
    addCell(
      row,
      bytes(c.offset).join(" ") + " / " + number(c.offset) + " bytes",
    );
    addCell(row, number(c.generation));
    $("cursor-rows").append(row);
  });
  if (!data.cursors.length) tableEmpty($("cursor-rows"), 3, "尚无采集检查点");
}
async function copyLog(raw) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(raw);
    return;
  }
  // Support dashboards served over HTTP where Clipboard API is unavailable.
  const textarea = node("textarea");
  textarea.value = raw;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  const focused = document.activeElement;
  document.body.append(textarea);
  try {
    textarea.select();
    if (!document.execCommand("copy")) throw new Error("copy failed");
  } finally {
    textarea.remove();
    focused?.focus();
  }
}
function renderLogs(data) {
  $("log-results").replaceChildren();
  data.items.forEach((item) => {
    const details = node("details", null, "log-entry"),
      summary = node("summary");
    summary.append(
      node("time", compactTime(item.time)),
      node(
        "span",
        item.level ?? "access",
        "status-pill" +
          (item.status >= 400 || item.kind === "error" ? " warn" : ""),
      ),
      node("span", item.site),
      node("span", item.raw, "log-preview"),
    );
    const copyButton = node("button", "复制原文", "log-copy");
    copyButton.type = "button";
    copyButton.onclick = async () => {
      copyButton.disabled = true;
      try {
        await copyLog(item.raw);
        toast("日志原文已复制");
      } catch {
        toast("复制失败，请选中原文手动复制");
      } finally {
        copyButton.disabled = false;
      }
    };
    const actions = node("div", null, "log-actions");
    actions.append(
      node(
        "div",
        `${item.ip || "无来源 IP"} / ${item.request_time === null ? "无耗时" : item.request_time + " s"}${item.parse_error ? " / " + item.parse_error : ""}`,
        "log-meta",
      ),
      copyButton,
    );
    details.append(summary, actions, node("pre", item.raw));
    $("log-results").append(details);
  });
  if (!data.items.length) empty($("log-results"), "当前筛选下没有日志记录");
  pageControls("logs", data);
}
function renderLocations(data) {
  $("location-rows").replaceChildren();
  data.items.forEach((item) => {
    const row = node("tr"),
      origin = node("span", item.ip);
    origin.append(
      node(
        "span",
        item.province + (item.city ? " / " + item.city : ""),
        "table-secondary",
      ),
    );
    addCell(row, origin);
    const labels = { resolved: "已解析", retry: "等待重试", pending: "待处理" };
    addCell(
      row,
      node(
        "span",
        labels[item.state] || item.state,
        "status-pill" +
          (item.state === "retry"
            ? " warn"
            : item.state === "pending"
              ? " pending"
              : ""),
      ),
    );
    addCell(row, number(item.attempts));
    addCell(row, compactTime(item.retry_at));
    if (item.state === "retry") {
      const retry = node("button", "重新调度", "text-button");
      retry.onclick = () => retryIP(item.ip, retry);
      addCell(row, retry);
    } else addCell(row, "—");
    $("location-rows").append(row);
  });
  if (!data.items.length)
    tableEmpty($("location-rows"), 5, "当前筛选下没有定位任务");
  pageControls("locations", data);
}
function renderTraffic(data) {
  $("slow-summary").textContent =
    `当前窗口有 ${number(data.slow_count)} 次耗时 ≥ 1 秒的请求，可在“日志检索 / 慢请求”中查看原文。`;
  $("path-rows").replaceChildren();
  $("ip-rows").replaceChildren();
  data.paths.forEach((p) => {
    const row = node("tr");
    addCell(row, p.request, "request-cell");
    addCell(row, number(p.count));
    addCell(row, p.average_ms + " ms");
    $("path-rows").append(row);
  });
  data.ips.forEach((p) => {
    const row = node("tr");
    addCell(row, p.ip);
    addCell(row, number(p.count));
    addCell(row, number(p.errors));
    $("ip-rows").append(row);
  });
  if (!data.paths.length) tableEmpty($("path-rows"), 3);
  if (!data.ips.length) tableEmpty($("ip-rows"), 3);
}
async function refreshAdmin() {
  if (!auth.authenticated || currentView !== "admin") return;
  const generation = ++adminGeneration,
    panel = adminPanel;
  const params = new URLSearchParams(filters());
  if (panel === "logs") {
    params.set("page", pages.logs);
    params.set("kind", $("log-kind").value);
    params.set("q", $("log-query").value.trim());
  }
  if (panel === "locations") {
    params.set("page", pages.locations);
    params.set("state", $("location-state").value);
    params.set("q", $("location-query").value.trim());
  }
  const path =
    panel === "overview"
      ? "/api/admin/overview"
      : `/api/admin/${panel}?${params}`;
  const container = $("admin-" + panel);
  container.setAttribute("aria-busy", "true");
  try {
    const data = await api(path);
    if (
      generation !== adminGeneration ||
      !auth.authenticated ||
      currentView !== "admin"
    )
      return;
    if (
      ["logs", "locations"].includes(panel) &&
      data.page > Math.max(1, Math.ceil(data.total / data.page_size))
    ) {
      pages[panel] = Math.max(1, Math.ceil(data.total / data.page_size));
      await refreshAdmin();
      return;
    }
    $("admin-notice").textContent = "";
    ({
      overview: renderOverview,
      logs: renderLogs,
      locations: renderLocations,
      traffic: renderTraffic,
    })[panel](data);
  } catch (error) {
    if (generation === adminGeneration)
      $("admin-notice").textContent = error.message;
  } finally {
    container.removeAttribute("aria-busy");
  }
}
async function retryIP(ip, button) {
  button.disabled = true;
  try {
    const result = await api("/api/admin/locations/retry", {
      method: "POST",
      body: JSON.stringify({ ip }),
    });
    toast(`已重新调度 ${number(result.affected)} 个 IP，等待定位进程处理`);
    await refreshAdmin();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}
function resizeCharts() {
  map?.resize();
  trend?.resize();
  statuses?.resize();
}
function filterChanged() {
  syncSelects();
  pages.logs = pages.locations = 1;
  const query = new URLSearchParams(filters());
  history.replaceState(null, "", "?" + query);
  refresh();
}
async function initCharts() {
  if (!window.echarts) {
    for (const id of ["map", "trend", "statuses"])
      empty($(id), "图表资源未就绪，统计数据仍可查看。");
    return;
  }
  const common = {
    animation: !reducedMotion,
    textStyle: {
      fontFamily: '"Noto Sans SC","Microsoft YaHei",sans-serif',
      color: chartText,
    },
    backgroundColor: "transparent",
  };
  trend = echarts.init($("trend"));
  statuses = echarts.init($("statuses"));
  trend.setOption({
    ...common,
    tooltip: {
      trigger: "axis",
      backgroundColor: "#203752",
      borderColor: "#486689",
      textStyle: { color: "#dcecff" },
      axisPointer: { type: "line", lineStyle: { color: "#6a9bd0" } },
    },
    grid: { left: 44, right: 12, top: 15, bottom: 35 },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: "#354b68" } },
      axisLabel: { color: chartText, fontSize: 9, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { fontSize: 9, color: chartText },
      splitLine: { lineStyle: { color: "#253a55", type: "dashed" } },
    },
    series: [
      {
        type: "line",
        showSymbol: false,
        smooth: 0.2,
        lineStyle: { color: "#7dbbf7", width: 2 },
        itemStyle: { color: "#a4d7ff" },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "#608ed64d" },
            { offset: 1, color: "#608ed602" },
          ]),
        },
        data: [],
      },
    ],
  });
  statuses.setOption({
    ...common,
    color: chartColors,
    tooltip: {
      trigger: "item",
      backgroundColor: "#203752",
      borderColor: "#486689",
      textStyle: { color: "#dcecff" },
      formatter: "{b} : {c} ({d}%)",
    },
    legend: {
      bottom: 7,
      itemWidth: 7,
      itemHeight: 7,
      icon: "circle",
      textStyle: { color: chartText, fontSize: 10 },
    },
    series: [
      {
        type: "pie",
        radius: ["58%", "73%"],
        center: ["50%", "43%"],
        label: { show: false },
        itemStyle: { borderColor: "#14223a", borderWidth: 4, borderRadius: 4 },
        emphasis: { scaleSize: 5 },
        data: [],
      },
    ],
  });
  try {
    const response = await fetch("static/china.json");
    if (!response.ok) throw Error("地图边界资源未就绪");
    const geo = await response.json();
    if (!Array.isArray(geo.features)) throw Error("地图边界格式异常");
    geo.features = geo.features.filter((f) => f && f.geometry);
    geo.features.forEach((f) => {
      f.properties = f.properties || {};
      f.properties.name =
        typeof f.properties.name === "string"
          ? normalize(f.properties.name)
          : "";
      if (f.properties.name) mapNames.add(f.properties.name);
    });
    echarts.registerMap("china", geo);
    map = echarts.init($("map"));
    map.setOption({
      ...common,
      tooltip: {
        trigger: "item",
        backgroundColor: "#203752",
        borderColor: "#486689",
        textStyle: { color: "#e0eeff" },
        formatter: (p) =>
          `${escapeHTML(p.name)}<br>访问请求：${number(Number.isFinite(p.value) ? p.value : 0)}`,
      },
      visualMap: {
        min: 0,
        max: 1,
        left: 0,
        bottom: 28,
        itemWidth: 7,
        itemHeight: 75,
        text: ["多", "少"],
        textGap: 8,
        textStyle: { color: chartText, fontSize: 8 },
        inRange: { color: ["#253f65", "#386696", "#5b94c6", "#8cd9e3"] },
        calculable: false,
      },
      series: [
        {
          type: "map",
          map: "china",
          roam: true,
          scaleLimit: { min: 0.8, max: 5 },
          ...defaultMapView(),
          label: { show: true, color: "#b6cce4", fontSize: 9 },
          itemStyle: {
            borderColor: "#91b9d66b",
            borderWidth: 0.65,
            areaColor: "#253f65",
            shadowColor: "#09172b",
            shadowBlur: 8,
            shadowOffsetY: 3,
          },
          emphasis: {
            label: { color: "#fff" },
            itemStyle: {
              areaColor: "#b395db",
              borderColor: "#d4c0fa",
              borderWidth: 1,
            },
          },
          select: {
            label: { color: "#fff" },
            itemStyle: { areaColor: "#a08bce" },
          },
          selectedMode: "single",
          data: [],
        },
      ],
    });
    map.on("click", (event) => {
      $("map-selection").textContent =
        `${event.name} · ${number(Number.isFinite(event.value) ? event.value : 0)} 次请求`;
    });
    mapReady = true;
  } catch (error) {
    empty($("map"), error.message + "，可在右侧查看地域统计。");
  }
}
$("public-nav").onclick = () => showView("public");
$("admin-nav").onclick = () => showView("admin");
$("open-admin").onclick = () => showView("admin");
$("refresh").onclick = refresh;
$("site").onchange = filterChanged;
$("hours").onchange = filterChanged;
$("auto-toggle").onclick = () => {
  autoRefresh = !autoRefresh;
  $("auto-toggle").setAttribute("aria-pressed", String(autoRefresh));
  $("connection").textContent = autoRefresh ? "实时观测" : "刷新已暂停";
  if (autoRefresh) refresh();
};
$("ranking-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-region]");
  if (!button) return;
  rankingRegion = button.dataset.region;
  for (const item of $("ranking-tabs").querySelectorAll("button"))
    item.classList.toggle("selected", item === button);
  renderRanking();
});
$("map-reset").onclick = () => {
  if (!mapReady) return toast("地图尚未就绪");
  map.setOption({ series: [defaultMapView()] });
  map.dispatchAction({ type: "unselect", seriesIndex: 0 });
  $("map-selection").textContent = "拖动或滚轮探索地图 · 点击省份查看请求量";
};
$("map-export").onclick = () => {
  if (!mapReady) return toast("地图尚未就绪");
  try {
    const link = node("a");
    link.href = map.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: "#14223a",
    });
    link.download = "春天熊-访客地图.png";
    link.click();
  } catch (error) {
    toast("地图导出失败，请稍后重试");
  }
};
$("auth-button").onclick = async () => {
  if (!auth.authenticated) {
    openLogin();
    return;
  }
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
    applyAuth({ authenticated: false });
    showView("public");
    await loadAuth();
    toast("已退出管理员会话");
  } catch (error) {
    toast(error.message);
  }
};
$("close-login").onclick = () => $("login-dialog").close();
$("login-dialog").addEventListener("close", () => {
  $("password").value = "";
});
$("login-form").onsubmit = async (event) => {
  event.preventDefault();
  $("login-submit").disabled = true;
  $("login-error").textContent = "";
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("username").value,
        password: $("password").value,
      }),
    });
    applyAuth(data);
    $("password").value = "";
    $("login-dialog").close();
    showView("admin");
    toast("欢迎回来，已进入管理员工作台");
  } catch (error) {
    $("login-error").textContent = error.message;
  } finally {
    $("login-submit").disabled = false;
  }
};
document.querySelector(".workspace-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-panel]");
  if (!button) return;
  adminPanel = button.dataset.panel;
  updateFilterContext();
  for (const item of document.querySelectorAll(".workspace-tabs button"))
    item.classList.toggle("selected", item === button);
  for (const id of ["overview", "logs", "locations", "traffic"])
    $("admin-" + id).hidden = id !== adminPanel;
  refreshAdmin();
});
$("log-form").onsubmit = (event) => {
  event.preventDefault();
  pages.logs = 1;
  refreshAdmin();
};
$("location-form").onsubmit = (event) => {
  event.preventDefault();
  pages.locations = 1;
  refreshAdmin();
};
$("log-kind").onchange = () => {
  pages.logs = 1;
  refreshAdmin();
};
$("location-state").onchange = () => {
  pages.locations = 1;
  refreshAdmin();
};
for (const kind of ["logs", "locations"])
  for (const [direction, step] of [
    ["prev", -1],
    ["next", 1],
  ])
    $(kind + "-" + direction).onclick = () => {
      pages[kind] = Math.max(1, pages[kind] + step);
      refreshAdmin();
    };
$("retry-all").onclick = () => $("confirm-dialog").showModal();
$("cancel-retry").onclick = () => $("confirm-dialog").close();
$("confirm-retry").onclick = async () => {
  $("confirm-retry").disabled = true;
  try {
    const result = await api("/api/admin/locations/retry", {
      method: "POST",
      body: JSON.stringify({ scope: "failed" }),
    });
    $("confirm-dialog").close();
    toast(`已重新调度 ${number(result.affected)} 个失败 IP`);
    pages.locations = 1;
    await refreshAdmin();
  } catch (error) {
    toast(error.message);
  } finally {
    $("confirm-retry").disabled = false;
  }
};
window.addEventListener("resize", () => {
  resizeCharts();
  for (const widget of selectWidgets.values()) widget.close();
});
window.addEventListener("pageshow", async (event) => {
  if (event.persisted) {
    clearAdminData();
    showView("public");
    await loadAuth();
    await refresh();
  }
});
async function start() {
  const initial = new URLSearchParams(location.search);
  if (["1", "24", "168", "720"].includes(initial.get("hours")))
    $("hours").value = initial.get("hours");
  if (initial.get("site")) {
    $("site").append(new Option(initial.get("site"), initial.get("site")));
    $("site").value = initial.get("site");
  }
  for (const [id, label] of [
    ["site", "观测站点"],
    ["hours", "时间窗口"],
    ["log-kind", "日志类型"],
    ["location-state", "定位状态"],
  ])
    enhanceSelect(id, label);
  await loadAuth();
  await initCharts();
  await refresh();
  setInterval(() => {
    if (
      autoRefresh &&
      !document.hidden &&
      !$("login-dialog").open &&
      !$("confirm-dialog").open &&
      ![...selectWidgets.values()].some((widget) => widget.isOpen())
    )
      refresh();
  }, 10000);
}
start();
