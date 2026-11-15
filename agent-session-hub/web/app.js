const $ = (s) => document.querySelector(s);
$("#current-year").textContent = new Date().getFullYear();
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function icon(name) {
  return `<span class="icon icon--${name}" aria-hidden="true"></span>`;
}
const state = {
  rows: [],
  view: "all",
  provider: "all",
  project: "",
  selected: null,
  detail: null,
  tab: "conversation",
  q: "",
  searchMatches: null,
};
let toastTimer,
  searchTimer,
  searchSeq = 0,
  detailSeq = 0;
function toast(s) {
  $("#toast").textContent = s;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 3500);
}
async function api(path, data, method = "POST") {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method,
          headers: { "Content-Type": "application/json", "X-ASH": "1" },
          body: JSON.stringify(data),
        },
  );
  if (!response.ok) {
    let error = await response.json();
    throw Error(error.error || "请求失败");
  }
  return response.json();
}
function projectName(p) {
  return (
    p.replaceAll("\\", "/").split("/").filter(Boolean).at(-1) || "未识别项目"
  );
}
function date(t) {
  return new Date(t * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function badge(p) {
  return `<span class="provider ${p}">${icon(p === "codex" ? "codex" : "claude")} ${p === "codex" ? "Codex" : "Claude Code"}</span>`;
}
function title() {
  return state.project
    ? projectName(state.project)
    : { all: "全部会话", favorite: "我的收藏", archived: "已归档" }[state.view];
}
function render() {
  const projects = [...new Set(state.rows.map((r) => r.cwd))].sort();
  $("#all-count").textContent = state.rows.filter((r) => !r.archived).length;
  $("#metric-total").textContent = state.rows.length;
  $("#metric-projects").textContent = projects.length;
  $("#project-count").textContent = projects.length;
  $("#metric-recent").textContent = state.rows.filter(
    (r) => Date.now() / 1000 - r.updated < 604800,
  ).length;
  $("#projects").innerHTML = projects
    .map(
      (p) =>
        `<button class="project ${state.project === p ? "active" : ""}" data-project="${esc(p)}" title="${esc(p)}"><span class="folder">${icon("folder")}</span><span class="project-name">${esc(projectName(p))}</span><b>${state.rows.filter((r) => r.cwd === p).length}</b></button>`,
    )
    .join("");
  document
    .querySelectorAll("[data-view]")
    .forEach((b) =>
      b.classList.toggle(
        "active",
        !state.project && b.dataset.view === state.view,
      ),
    );
  document
    .querySelectorAll("[data-provider]")
    .forEach((b) =>
      b.classList.toggle("active", b.dataset.provider === state.provider),
    );
  $("#breadcrumb").textContent = title();
  $("#list-title").innerHTML =
    esc(title()) + ' <span id="visible-count">0</span>';
  let rows = state.rows.filter(
    (r) =>
      (state.view === "archived" ? r.archived : !r.archived) &&
      (state.view !== "favorite" || r.favorite) &&
      (state.provider === "all" || r.provider === state.provider) &&
      (!state.project || r.cwd === state.project) &&
      (!state.q || state.searchMatches?.has(r.id)),
  );
  if ($("#sort").value === "old")
    rows = rows.toReversed ? rows.toReversed() : [...rows].reverse();
  $("#visible-count").textContent = rows.length;
  $("#sessions").innerHTML = rows.length
    ? rows
        .map(
          (r) =>
            `<button class="session-row ${r.id === state.selected ? "selected" : ""}" data-id="${r.id}">${badge(r.provider)}<div class="row-meta"><span>${esc(r.model || "模型未记录")}</span><time class="row-time">${date(r.updated)}</time></div><div class="row-title">${r.favorite ? icon("star-filled") : ""}${esc(r.title)}</div><div class="row-foot"><span>${icon("folder")} ${esc(projectName(r.cwd))}</span><span>${r.count} 条记录</span></div></button>`,
        )
        .join("")
    : `<div class="empty-list">${state.q ? "没有匹配的会话，试试其他关键词。" : state.rows.length ? "这里还没有会话。" : "尚未发现会话文件。<br>请在「数据源与设置」检查目录，或用 --demo 启动示例。"}</div>`;
}
async function load() {
  state.rows = await api("/api/sessions");
  render();
  const s = await api("/api/status");
  $("#scan-time").textContent = s.last_scan
    ? "更新于 " +
      new Date(s.last_scan).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "尚未扫描";
  $("#source-list").innerHTML = s.sources
    .map(
      (r) =>
        `<div class="source"><strong>${r.provider === "codex" ? "Codex CLI" : "Claude Code"}</strong><span>${r.exists ? "目录已连接" : "目录不存在"}</span><code>${esc(r.path)}</code></div>`,
    )
    .join("");
  $("#warnings").textContent = s.warnings.join("\n");
  return s;
}
async function select(id) {
  const sequence = ++detailSeq;
  state.selected = id;
  render();
  const row = await api("/api/sessions/" + id);
  if (sequence !== detailSeq) return;
  state.detail = row;
  state.tab = "conversation";
  renderDetail();
}
function renderDetail() {
  const r = state.detail;
  if (!r) return;
  const tabs = [
    ["conversation", "对话记录"],
    ["tools", "工具调用"],
    ["info", "会话信息"],
  ];
  const tools = r.messages.filter((m) => m.role === "tool").length;
  $("#detail").innerHTML =
    `<div class="detail-head"><div class="detail-top">${badge(r.provider)}<div class="detail-actions"><button class="icon-button ${r.favorite ? "on" : ""}" data-action="favorite" title="${r.favorite ? "取消收藏" : "收藏会话"}" aria-label="${r.favorite ? "取消收藏" : "收藏会话"}">${icon(r.favorite ? "star-filled" : "star")}</button><button class="icon-button" data-action="archived" title="${r.archived ? "取消归档" : "归档会话"}" aria-label="${r.archived ? "取消归档" : "归档会话"}">${icon(r.archived ? "unarchive" : "archive")}</button><a class="icon-button" href="/api/sessions/${r.id}/export?format=md" title="导出 Markdown" aria-label="导出 Markdown">${icon("download")}</a><button class="icon-button danger" data-action="delete" title="永久删除会话文件" aria-label="永久删除会话文件">${icon("trash")}</button></div></div><h2 class="detail-title">${esc(r.title)}</h2><div class="path" title="${esc(r.cwd)}">${icon("folder")} ${esc(r.cwd || "工作目录未记录")}</div><div class="detail-info"><span>${icon("model")} ${esc(r.model || "模型未记录")}</span><span>${icon("clock")} ${date(r.updated)}</span><span>${r.messages.length} 条记录</span></div></div><div class="detail-tabs">${tabs.map(([k, n]) => `<button data-tab="${k}" class="${state.tab === k ? "active" : ""}">${n}${k === "tools" ? " " + tools : ""}</button>`).join("")}<small>会话记录</small></div><div class="messages">${body(r)}</div><div class="detail-bottom"><span>${r.errors ? `${r.errors} 行未能解析` : "本地会话文件"}</span><button class="text-button" data-action="copy">复制会话 ID</button></div>`;
}
function body(r) {
  if (state.tab === "info")
    return `<pre class="raw">${esc(JSON.stringify({ 会话ID: r.sid, 来源文件: r.path, 模型: r.model || null, token总量: r.tokens, 归档: !!r.archived, 收藏: !!r.favorite }, null, 2))}</pre><p class="empty-list">未记录的用量显示为空；不估算费用。</p><a class="text-button" href="/api/sessions/${r.id}/export">下载 JSON</a>`;
  const messages =
    state.tab === "tools"
      ? r.messages.filter((m) => m.role === "tool")
      : r.messages;
  if (!messages.length)
    return '<div class="empty-list">此会话没有对应记录。</div>';
  return messages
    .map((m) =>
      m.role === "tool"
        ? `<details class="tool-block" ${state.tab === "tools" ? "open" : ""}><summary>${icon("terminal")} ${esc(m.name || "工具调用")}<span> · 展开查看</span></summary><pre>${esc(m.text)}</pre></details>`
        : `<article class="message ${esc(m.role)}"><div class="message-heading"><span class="role-icon">${icon(m.role === "user" ? "user" : m.role === "assistant" ? (r.provider === "codex" ? "codex" : "claude") : "system")}</span>${m.role === "user" ? "你" : m.role === "assistant" ? (r.provider === "codex" ? "Codex" : "Claude Code") : "系统"}<time>${m.time ? esc(new Date(m.time).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })) : ""}</time></div><div class="message-body">${esc(m.text)}</div></article>`,
    )
    .join("");
}
$("#navigation").addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (!b) return;
  state.view = b.dataset.view;
  state.project = "";
  render();
});
$("#projects").addEventListener("click", (e) => {
  const b = e.target.closest("[data-project]");
  if (!b) return;
  state.project = b.dataset.project;
  state.view = "all";
  render();
});
$(".filters").addEventListener("click", (e) => {
  const b = e.target.closest("[data-provider]");
  if (b) {
    state.provider = b.dataset.provider;
    render();
  }
});
$("#sessions").addEventListener("click", (e) => {
  const b = e.target.closest("[data-id]");
  if (b) select(b.dataset.id).catch((e) => toast(e.message));
});
$("#sort").onchange = render;
async function search() {
  const q = state.q,
    sequence = ++searchSeq;
  if (!q) {
    state.searchMatches = null;
    render();
    return;
  }
  const rows = await api("/api/sessions?q=" + encodeURIComponent(q));
  if (sequence === searchSeq && q === state.q) {
    state.searchMatches = new Set(rows.map((r) => r.id));
    render();
  }
}
$("#search").oninput = () => {
  state.q = $("#search").value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => search().catch((e) => toast(e.message)), 180);
};
document.addEventListener("keydown", (e) => {
  if (
    e.key === "/" &&
    !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName) &&
    !$("#settings").open
  ) {
    e.preventDefault();
    $("#search").focus();
  }
});
$("#refresh").onclick = async () => {
  const b = $("#refresh");
  b.disabled = true;
  try {
    const r = await api("/api/scan", {});
    await load();
    await search();
    if (state.selected && state.rows.some((r) => r.id === state.selected))
      await select(state.selected);
    else {
      state.selected = null;
      state.detail = null;
      $("#detail").innerHTML =
        '<div class="empty"><h2>选择一条会话查看详情</h2></div>';
    }
    toast(
      `扫描完成，更新 ${r.indexed} 个会话${r.warnings.length ? "；请检查数据源提示" : ""}`,
    );
  } catch (e) {
    toast(e.message);
  } finally {
    b.disabled = false;
  }
};
$("#sources").onclick = () => $("#settings").showModal();
$("#close-settings").onclick = () => $("#settings").close();
$("#detail").onclick = async (e) => {
  try {
    const tab = e.target.closest("[data-tab]");
    if (tab) {
      state.tab = tab.dataset.tab;
      renderDetail();
      return;
    }
    const a = e.target.closest("[data-action]");
    if (!a) return;
    const action = a.dataset.action,
      r = state.detail;
    if (action === "copy") {
      await navigator.clipboard.writeText(r.sid);
      toast("已复制会话 ID");
      return;
    }
    if (action === "delete") {
      deleteTarget = { id: r.id, revision: r.revision };
      $("#delete-title").textContent = r.title;
      $("#delete-path").textContent = r.path;
      $("#delete-error").hidden = true;
      $("#delete-dialog").showModal();
      return;
    }
    const value = !r[action];
    await api("/api/sessions/" + r.id, { [action]: value });
    r[action] = value;
    await load();
    renderDetail();
    toast(
      action === "favorite"
        ? value
          ? "已收藏"
          : "已取消收藏"
        : value
          ? "已归档，可在「已归档」中查看"
          : "已取消归档",
    );
  } catch (e) {
    toast(e.message);
  }
};
load()
  .then(() => {
    if (state.rows.length) return select(state.rows[0].id);
  })
  .catch((e) => toast("无法加载会话：" + e.message));

let deleteTarget = null;
let deleting = false;
$("#cancel-delete").onclick = () => $("#delete-dialog").close();
$("#delete-dialog").addEventListener("cancel", (e) => {
  if (deleting) e.preventDefault();
});
$("#delete-dialog").addEventListener("close", () => {
  deleteTarget = null;
});
$("#confirm-delete").onclick = async () => {
  if (deleting || !deleteTarget) return;
  const target = deleteTarget;
  deleting = true;
  $("#confirm-delete").disabled = true;
  $("#cancel-delete").disabled = true;
  $("#confirm-delete").textContent = "正在删除…";
  $("#delete-error").hidden = true;
  try {
    await api(
      "/api/sessions/" + target.id,
      { confirm: target.id, revision: target.revision },
      "DELETE",
    );
  } catch (e) {
    $("#delete-error").textContent = e.message;
    $("#delete-error").hidden = false;
    return;
  } finally {
    deleting = false;
    $("#confirm-delete").disabled = false;
    $("#cancel-delete").disabled = false;
    $("#confirm-delete").textContent = "永久删除文件";
  }
  $("#delete-dialog").close();
  if (state.selected === target.id) {
    ++detailSeq;
    state.selected = null;
    state.detail = null;
    $("#detail").innerHTML =
      '<div class="empty"><h2>会话已删除</h2><p>选择其他会话继续查看。</p></div>';
  }
  state.rows = state.rows.filter((r) => r.id !== target.id);
  if (state.project && !state.rows.some((r) => r.cwd === state.project))
    state.project = "";
  render();
  toast("已永久删除会话文件");
  try {
    await load();
    await search();
  } catch (e) {
    toast("文件已删除，但列表刷新失败：" + e.message);
  }
};
