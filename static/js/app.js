/* PacDown 前端逻辑：单页应用，无构建依赖 */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PLATFORM_NAME = {
  bilibili: "哔哩哔哩", douyin: "抖音", kuaishou: "快手",
  xiaohongshu: "小红书", direct: "直链", generic: "通用",
};
const STATUS_TEXT = {
  pending: "排队中", parsing: "解析中", working: "解析中", downloading: "下载中",
  processing: "后处理", done: "已完成", failed: "失败", duplicate: "已存在",
};

/* ---------- 工具 ---------- */

function fmtDuration(sec) {
  sec = +sec || 0;
  if (!sec) return "";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtSize(bytes) {
  bytes = +bytes || 0;
  if (bytes < 1024) return "";
  if (bytes < 1024 ** 3) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  return (bytes / 1024 ** 3).toFixed(2) + " GB";
}

function fmtTime(t) { return (t || "").slice(5, 16); }

function coverSrc(url, platform) {
  if (!url) return "";
  return `/api/cover?url=${encodeURIComponent(url)}&platform=${platform || ""}`;
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) {
    showAuth();  // 会话失效 → 回到登录
    throw new Error("请先登录");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `请求失败 (${r.status})`);
  return data;
}

/* ---------- 认证 ---------- */

let me = null;             // {id, username, role}
let allowRegister = true;

function isAdmin() { return me?.role === "admin"; }

async function fetchMe() {
  try {
    const d = await fetch("/api/auth/me").then((r) => r.json());
    me = d.user;
    allowRegister = d.allow_register !== false;
    return me;
  } catch (e) { me = null; return null; }
}

function showAuth() {
  me = null;
  $("#auth-mask").hidden = false;
  $(".sidebar").style.visibility = "hidden";
  $(".main").style.visibility = "hidden";
  $("#auth-err").hidden = true;
}

function hideAuth() {
  $("#auth-mask").hidden = true;
  $(".sidebar").style.visibility = "";
  $(".main").style.visibility = "";
  applyRoleNav();
  const chip = $("#user-chip");
  chip.hidden = false;
  $("#user-name").textContent = me?.username || "";
}

function applyRoleNav() {
  // 设置 / 统计 仅 admin
  const isAdminUser = isAdmin();
  const settingsBtn = document.querySelector('.nav-item[data-page="settings"]');
  if (settingsBtn) settingsBtn.hidden = !isAdminUser;
  $("#nav-stats").hidden = !isAdminUser;
}

let authTab = "login";
$("#tab-login").addEventListener("click", () => setAuthTab("login"));
$("#tab-register").addEventListener("click", () => setAuthTab("register"));

function setAuthTab(t) {
  authTab = t;
  $("#tab-login").classList.toggle("active", t === "login");
  $("#tab-register").classList.toggle("active", t === "register");
  $("#btn-auth").textContent = t === "login" ? "登 录" : "注 册";
  $("#auth-hint").hidden = t === "login" || allowRegister;
  $("#auth-err").hidden = true;
}

$("#btn-auth").addEventListener("click", async () => {
  const username = $("#auth-username").value.trim();
  const password = $("#auth-password").value;
  const err = $("#auth-err");
  if (!username || !password) {
    err.textContent = "请输入用户名和密码"; err.hidden = false; return;
  }
  const btn = $("#btn-auth");
  btn.disabled = true;
  try {
    const path = authTab === "login" ? "/api/auth/login" : "/api/auth/register";
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      err.textContent = data.detail || "操作失败"; err.hidden = false; return;
    }
    me = data.user;
    hideAuth();
    if (authTab === "register" && me?.role === "admin") {
      toast(me.role === "admin" ? "注册成功，你已成为管理员（旧数据已并入你的账号）" : "注册成功");
    } else {
      toast(`欢迎回来，${me?.username || ""}`);
    }
    boot();
  } finally {
    btn.disabled = false;
  }
});

$("#auth-username").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#btn-auth").click(); });
$("#auth-password").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#btn-auth").click(); });

$("#btn-logout").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
  showAuth();
});

$("#btn-change-pwd").addEventListener("click", async () => {
  const oldPwd = $("#set-old-pwd").value;
  const newPwd = $("#set-new-pwd").value;
  if (!oldPwd || !newPwd) { toast("请填写原密码与新密码", "warn"); return; }
  try {
    await api("/api/auth/password", { method: "POST", body: { old_password: oldPwd, new_password: newPwd } });
    toast("密码已修改");
    $("#set-old-pwd").value = ""; $("#set-new-pwd").value = "";
  } catch (e) { toast(e.message, "err"); }
});

/* ---------- 备份 / 恢复（admin） ---------- */

$("#btn-backup").addEventListener("click", () => {
  window.location.href = "/api/backup/download";
});

$("#btn-restore").addEventListener("click", () => $("#backup-file").click());
$("#backup-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (!confirm("恢复将覆盖当前数据库与配置，确认继续？")) { e.target.value = ""; return; }
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/backup/restore", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "恢复失败");
    toast(data.message || "恢复成功", "ok", 6000);
    e.target.value = "";
  } catch (err) { toast(err.message, "err"); }
});

function toast(msg, type = "ok", ms = 3400, action = null) {
  const icons = {
    ok: '<svg class="t-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12.5l5 5L20 7"/></svg>',
    err: '<svg class="t-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    warn: '<svg class="t-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 8v5m0 3h.01M10.3 3.8L1.8 18.3A2 2 0 0 0 3.5 21h17a2 2 0 0 0 1.7-2.7L13.7 3.8a2 2 0 0 0-3.4 0z"/></svg>',
  };
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `${icons[type] || icons.ok}<div class="t-body">${esc(msg)}</div>` +
    (action ? `<button class="t-action">${esc(action.label)}</button>` : "");
  if (action) {
    el.querySelector(".t-action").addEventListener("click", () => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 320);
      action.onClick();
    });
  }
  $("#toast-root").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 320); }, ms);
}

function badge(platform) {
  return `<span class="badge badge-${esc(platform)}">${PLATFORM_NAME[platform] || platform}</span>`;
}

function statusBadge(status) {
  return `<span class="st st-${esc(status)}">${STATUS_TEXT[status] || status}</span>`;
}

/* ---------- 主题 ---------- */

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("pacdown-theme", t);
}
applyTheme(localStorage.getItem("pacdown-theme") || "dark");
$("#theme-toggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

/* ---------- 导航 ---------- */

$("#nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-item");
  if (!btn) return;
  $$(".nav-item").forEach((n) => n.classList.toggle("active", n === btn));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${btn.dataset.page}`));
  if (btn.dataset.page === "history") loadHistory();
  if (btn.dataset.page === "subs") loadSubs();
  if (btn.dataset.page === "settings") loadSettings();
  if (btn.dataset.page === "repost") loadRepost();
  if (btn.dataset.page === "tools") loadToolbox();
  if (btn.dataset.page === "stats") loadStatsPage();
});

/* ---------- 下载页 ---------- */

let parseItems = []; // {url, ok, info, error, checked}

async function loadDirs() {
  try {
    const d = await api("/api/config/dirs");
    $("#dir-label").textContent = d.current;
    const sel = $("#dir-select");
    sel.innerHTML = `<option value="">切换目录…</option>` +
      [d.current, ...d.recent.filter((x) => x !== d.current)]
        .map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    sel.onchange = async () => {
      if (!sel.value) return;
      try {
        await api("/api/config/dirs", { method: "POST", body: { dir: sel.value } });
        toast(`下载目录已切换：${sel.value}`);
        sel.value = "";
        loadDirs();
      } catch (e) { toast(e.message, "err"); }
    };
  } catch (e) { console.error(e); }
}

$("#btn-clear").addEventListener("click", () => {
  $("#url-input").value = "";
  $("#parse-results").hidden = true;
  parseItems = [];
});

$("#btn-parse").addEventListener("click", async () => {
  const text = $("#url-input").value.trim();
  if (!text) { toast("请先粘贴链接", "warn"); return; }
  const btn = $("#btn-parse");
  btn.disabled = true;
  btn.textContent = "解析中…";
  try {
    const { results } = await api("/api/parse", { method: "POST", body: { text } });
    if (!results.length) { toast("未识别到链接", "warn"); return; }
    parseItems = results.map((r) => normalizeParseItem(r));
    renderParseResults();
    const fails = results.filter((r) => !r.ok).length;
    toast(`解析完成：${results.length - fails} 个成功${fails ? `，${fails} 个失败` : ""}`, fails ? "warn" : "ok");
  } catch (e) { toast(e.message, "err"); }
  finally {
    btn.disabled = false;
    btn.innerHTML = `解析链接`;
  }
});

/* 解析结果项归一化：主页/合集/分P 统一转成 sections 结构（可勾选子项） */
function normalizeParseItem(r) {
  const item = { ...r, checked: r.ok, expanded: false };
  const wrap = (entries) => entries.map((e) => ({ ...e, checked: true }));
  if (r.kind === "uploader" && r.entries) {
    item.sections = [{
      label: `${r.uploader?.name || "博主"} 的最新 ${r.entries.length} 个视频`,
      entries: wrap(r.entries),
    }];
  } else if (r.kind === "playlist" && r.entries) {
    item.sections = [{ label: r.title || "合集", entries: wrap(r.entries) }];
  } else if (r.kind === "playlist" && r.sections) {
    item.sections = r.sections.map((s) => ({
      label: s.label, entries: wrap(s.entries || []),
    }));
  }
  return item;
}

function renderParseResults() {
  const box = $("#parse-results");
  box.hidden = false;
  updateCheckAll();
  $("#parse-list").innerHTML = parseItems.map((p, i) => renderParseItem(p, i)).join("");
}

function renderParseItem(p, i) {
  if (!p.ok) {
    return `<div class="parse-item error">
      <input type="checkbox" class="pcheck" data-i="${i}" disabled>
      <div class="parse-info">
        <div class="parse-title" style="color:var(--text-3)">${esc(p.url.slice(0, 70))}</div>
        <div class="parse-err">解析失败：${esc(p.error)}</div>
      </div>
      ${statusBadge("failed")}
    </div>`;
  }

  // 批量型：博主主页 / 合集 / 分P
  if (p.sections) {
    const total = p.sections.reduce((n, s) => n + s.entries.length, 0);
    let head;
    if (p.uploader) {
      head = `<img class="parse-cover" src="${esc(coverSrc(p.uploader.avatar, p.uploader.platform))}" alt=""
                 onerror="this.style.visibility='hidden'">
        <div class="parse-info">
          <div class="parse-title">${esc(p.uploader.name || "博主")} 的主页</div>
          <div class="parse-meta">${badge(p.uploader.platform)}<span>批量下载最新视频</span></div>
        </div>`;
    } else {
      const info = p.info || {};
      head = `<img class="parse-cover" src="${esc(coverSrc(info.cover_url, info.platform))}" alt=""
                 onerror="this.style.visibility='hidden'">
        <div class="parse-info">
          <div class="parse-title">${esc(info.title || "(无标题)")}</div>
          <div class="parse-meta">${badge(info.platform)}<span>${esc(info.author || "")}</span></div>
        </div>`;
    }
    const sectionsBody = p.sections.map((s, si) => `
      <div class="child-section">
        <div class="child-label">${esc(s.label)}</div>
        ${s.entries.map((e, ci) => `
          <label class="child-item">
            <input type="checkbox" class="ccheck" data-i="${i}" data-s="${si}" data-c="${ci}" ${e.checked ? "checked" : ""}>
            ${e.cover ? `<img class="child-cover" loading="lazy" src="${esc(coverSrc(e.cover, p.uploader?.platform || p.info?.platform || ""))}" alt="" onerror="this.style.visibility='hidden'">` : ""}
            <span class="child-title">${e.index != null ? `<b class="num child-idx">${e.index}</b>` : ""}${esc(e.title || "(无标题)")}</span>
            ${e.duration ? `<span class="num">${fmtDuration(e.duration)}</span>` : ""}
            ${e.publish_time ? `<span class="num">${fmtTime(e.publish_time)}</span>` : ""}
          </label>`).join("")}
      </div>`).join("");
    return `<div class="parse-item batch">
      <div class="parse-row">
        ${head}
        <button class="btn btn-ghost btn-sm expand-btn" data-i="${i}">
          ${p.expanded ? "收起" : `展开批量下载（${total} 个）`}
        </button>
      </div>
      <div class="children" ${p.expanded ? "" : "hidden"}>${sectionsBody}</div>
    </div>`;
  }

  // 普通视频
  const info = p.info;
  const stats = [];
  if (info.stats?.play != null) stats.push(`播放 ${fmtCount(info.stats.play)}`);
  if (info.stats?.like != null) stats.push(`赞 ${fmtCount(info.stats.like)}`);
  return `<div class="parse-item" style="animation-delay:${Math.min(i * 45, 300)}ms">
    <input type="checkbox" class="pcheck" data-i="${i}" ${p.checked ? "checked" : ""}>
    <img class="parse-cover" src="${esc(coverSrc(info.cover_url, info.platform))}" alt=""
         onerror="this.style.visibility='hidden'">
    <div class="parse-info">
      <div class="parse-title">${esc(info.title || "(无标题)")}</div>
      <div class="parse-meta">
        ${badge(info.platform)}
        <span>${esc(info.author || "未知作者")}</span>
        ${info.duration ? `<span class="num">${fmtDuration(info.duration)}</span>` : ""}
        ${info.publish_time ? `<span class="num">${fmtTime(info.publish_time)}</span>` : ""}
        ${info.is_images ? `<span style="color:var(--cyan)">图集 · ${info.image_count} 张</span>` : ""}
        ${stats.map((s) => `<span>${s}</span>`).join("")}
      </div>
      ${p.collection ? `<button class="btn btn-ghost btn-sm coll-btn" data-i="${i}">所属合集《${esc(p.collection.name || "合集")}》· 点击展开下载全集</button>` : ""}
    </div>
  </div>`;
}

function fmtCount(n) {
  n = +n;
  if (!isFinite(n)) return "";
  if (n >= 10000) return (n / 10000).toFixed(1) + "万";
  return String(n);
}

function updateCheckAll() {
  const states = [];
  parseItems.forEach((p) => {
    if (!p.ok) return;
    if (p.sections) p.sections.forEach((s) => s.entries.forEach((e) => states.push(e.checked)));
    else states.push(p.checked);
  });
  $("#check-all").checked = states.length > 0 && states.every(Boolean);
}

$("#parse-list").addEventListener("change", (e) => {
  const cb = e.target.closest(".pcheck");
  if (cb) {
    parseItems[+cb.dataset.i].checked = cb.checked;
    updateCheckAll();
    return;
  }
  const cc = e.target.closest(".ccheck");
  if (cc) {
    parseItems[+cc.dataset.i].sections[+cc.dataset.s].entries[+cc.dataset.c].checked = cc.checked;
    updateCheckAll();
  }
});

$("#parse-list").addEventListener("click", async (e) => {
  const exp = e.target.closest(".expand-btn");
  if (exp) {
    const it = parseItems[+exp.dataset.i];
    it.expanded = !it.expanded;
    renderParseResults();
    return;
  }
  const collBtn = e.target.closest(".coll-btn");
  if (collBtn) {
    const i = +collBtn.dataset.i;
    const it = parseItems[i];
    collBtn.disabled = true;
    collBtn.textContent = "正在拉取合集列表…";
    try {
      const c = it.collection;
      const data = await api(`/api/collection?platform=${encodeURIComponent(c.platform)}&id=${encodeURIComponent(c.id)}`);
      it.sections = [{
        label: `合集《${data.name || "合集"}》· ${data.entries.length} 集`,
        entries: data.entries.map((x) => ({ ...x, checked: true })),
      }];
      it.expanded = true;
      delete it.collection;
      renderParseResults();
    } catch (err) {
      toast(err.message, "err", 5000);
      collBtn.disabled = false;
      collBtn.textContent = "合集拉取失败 · 点击重试";
    }
  }
});

$("#check-all").addEventListener("change", (e) => {
  parseItems.forEach((p) => {
    if (!p.ok) return;
    if (p.sections) p.sections.forEach((s) => s.entries.forEach((en) => { en.checked = e.target.checked; }));
    else p.checked = e.target.checked;
  });
  renderParseResults();
});

$("#btn-download-selected").addEventListener("click", async () => {
  const urls = [];
  parseItems.forEach((p) => {
    if (!p.ok) return;
    if (p.sections) p.sections.forEach((s) => s.entries.forEach((en) => { if (en.checked) urls.push(en.url); }));
    else if (p.checked) urls.push(p.url);
  });
  if (!urls.length) { toast("请至少勾选一个视频", "warn"); return; }
  if (urls.length > 50) {
    toast(`一次最多下载 50 个，已截取前 50 个`, "warn");
    urls.length = 50;
  }
  const text = urls.join("\n");
  const options = {
    quality: "best",
    extract_audio: $("#opt-audio").checked,
    download_danmaku: $("#opt-danmaku").checked,
    download_subtitle: $("#opt-subtitle").checked,
    fetch_comments: $("#opt-comments").checked,
  };
  const btn = $("#btn-download-selected");
  btn.disabled = true;
  try {
    const { results } = await api("/api/download", { method: "POST", body: { text, options } });
    const queued = results.filter((r) => r.status === "queued").length;
    const failed = results.filter((r) => r.status === "failed").length;
    if (queued) toast(`已加入队列：${queued} 个任务，解析完成后自动开始下载`);
    if (failed) toast(`${failed} 个任务创建失败`, "err");
    $("#parse-results").hidden = true;
    pollTasks(true);
  } catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; }
});

/* ---------- 任务轮询（增量渲染：节点复用，进度平滑） ---------- */

const doneNotified = new Set();   // 已 toast 过完成提示的任务
const dupTimers = new Map();      // duplicate 提示的自动清理定时器

function buildTaskEl(t) {
  const el = document.createElement("div");
  el.className = "task-item";
  el.dataset.id = t.id;
  el.innerHTML = `
    <div class="task-top">
      <span data-f="badge"></span>
      <span class="task-title" data-f="title"></span>
      <span class="task-author" data-f="author"></span>
      <span class="task-speed num" data-f="speed"></span>
      <span data-f="status"></span>
      <button class="btn btn-ghost btn-sm" data-f="retry" hidden onclick="retryTask(${t.id})">重试</button>
    </div>
    <div class="progress" data-f="progressWrap">
      <div class="progress-bar" data-f="bar"></div>
    </div>
    <div class="task-err" data-f="err" hidden></div>`;
  el.querySelector('[data-f="retry"]').hidden = true;
  updateTaskEl(el, t, true);
  return el;
}

function updateTaskEl(el, t, fresh = false) {
  const q = (f) => el.querySelector(`[data-f="${f}"]`);
  const setText = (f, val) => {
    const node = q(f);
    const v = val || "";
    if (node.textContent !== v) node.textContent = v;
  };
  const setClass = (node, cls) => { if (node.className !== cls) node.className = cls; };
  setText("title", t.title || t.file_path || "正在解析…");
  setText("author", t.author);
  setText("speed", t.status === "downloading" ? (t.speed || "") : "");
  const badgeEl = q("badge");
  if (badgeEl.dataset.p !== t.platform) { badgeEl.innerHTML = badge(t.platform); badgeEl.dataset.p = t.platform; }
  const stEl = q("status");
  if (stEl.dataset.s !== t.status) { stEl.innerHTML = statusBadge(t.status); stEl.dataset.s = t.status; }

  const wrap = q("progressWrap");
  const bar = q("bar");
  setClass(wrap, "progress" + (t.status === "done" ? " ok" : t.status === "failed" ? " err" : ""));
  if (t.status === "parsing" || t.status === "working") {
    setClass(bar, "progress-bar indeterminate");
  } else if (t.status === "pending") {
    setClass(bar, "progress-bar");
    bar.style.width = "0%";
  } else if (t.status === "downloading" || t.status === "processing") {
    setClass(bar, "progress-bar striped");
    bar.style.width = `${t.status === "processing" ? 99 : (t.progress || 0)}%`;
  } else {
    setClass(bar, "progress-bar");
    bar.style.width = "100%";
  }

  const errEl = q("err");
  const errText = t.error || "";
  if (errText) { errEl.textContent = errText; errEl.hidden = false; }
  else errEl.hidden = true;
  q("retry").hidden = t.status !== "failed";

  if (t.status === "done" && !doneNotified.has(t.id)) {
    doneNotified.add(t.id);
    toast(`《${(t.title || "视频").slice(0, 30)}》下载完成`, "ok", 5200, {
      label: "好用？支持作者 ☕",
      onClick: () => { $("#donate-modal").hidden = false; },
    });
    if ($("#page-history").classList.contains("active")) loadHistory(true);
  }
  // duplicate 提示：8 秒后自动清理
  if (t.status === "duplicate" && !dupTimers.has(t.id)) {
    dupTimers.set(t.id, setTimeout(async () => {
      try { await api(`/api/tasks/${t.id}`, { method: "DELETE" }); } catch (e) { /* 忽略 */ }
      const node = document.querySelector(`.task-item[data-id="${t.id}"]`);
      if (node) { node.classList.add("task-out"); setTimeout(() => node.remove(), 400); }
      pollTasks();
    }, 8000));
  }
}

function renderTasks(tasks) {
  const list = $("#task-list");
  const existing = new Map([...list.children].map((el) => [String(el.dataset.id), el]));
  const seen = new Set();
  for (const t of tasks) {
    seen.add(String(t.id));
    let el = existing.get(String(t.id));
    if (el) { updateTaskEl(el, t); }
    else { list.appendChild(buildTaskEl(t)); }
  }
  // 消失的任务：平滑淡出
  existing.forEach((el, id) => {
    if (!seen.has(id) && !el.classList.contains("task-out")) {
      el.classList.add("task-out");
      setTimeout(() => el.remove(), 450);
    }
  });
}

async function pollTasks(force = false) {
  try {
    const { tasks } = await api("/api/tasks");
    const hasVisible = tasks.length > 0;
    if (!hasVisible) {
      // 完全无任务：仅一次性清理，之后不再碰 DOM
      if (!$("#tasks-head").hidden || $("#task-list").children.length) {
        $("#tasks-head").hidden = true;
        $("#task-list").innerHTML = "";
      }
      $("#playhead").classList.remove("on");
      $("#nav-active-dot").hidden = true;
      return;
    }
    const active = tasks.filter((t) => !["done", "duplicate"].includes(t.status)).length;
    const done = tasks.filter((t) => t.status === "done").length;
    const failed = tasks.filter((t) => t.status === "failed").length;
    const summaryHtml =
      `<span class="num">${active} 进行中` +
      (done ? ` · ${done} 完成` : "") +
      (failed ? ` · <span style="color:var(--err)">${failed} 失败</span>` : "") +
      `</span>`;
    if ($("#tasks-head").hidden) $("#tasks-head").hidden = false;
    if ($("#task-summary").innerHTML !== summaryHtml) $("#task-summary").innerHTML = summaryHtml;
    $("#btn-retry-all").hidden = !failed;
    $("#btn-clear-tasks").hidden = !(failed || tasks.some((t) => t.status === "duplicate"));
    renderTasks(tasks);
    // 播放头：有任务在跑时顶部的"时间线指针"亮起
    const running = tasks.some((t) => ["parsing", "pending", "downloading", "processing"].includes(t.status));
    $("#playhead").classList.toggle("on", running);
    $("#nav-active-dot").hidden = !running;
  } catch (e) { console.error(e); }
}

window.retryTask = async (id) => {
  try {
    await api(`/api/tasks/${id}/retry`, { method: "POST" });
    toast("已重新排队");
    pollTasks();
  } catch (e) { toast(e.message, "err"); }
};

$("#btn-retry-all").addEventListener("click", async () => {
  try {
    const r = await api("/api/tasks/retry_all", { method: "POST" });
    toast(r.retried ? `已重新入队 ${r.retried} 个任务` : "没有失败任务");
    pollTasks(true);
  } catch (e) { toast(e.message, "err"); }
});

$("#btn-clear-tasks").addEventListener("click", async () => {
  try {
    const r = await api("/api/tasks/clear", { method: "POST" });
    toast(r.removed ? `已清理 ${r.removed} 条记录` : "没有可清理的记录");
    pollTasks(true);
  } catch (e) { toast(e.message, "err"); }
});

/* ---------- 历史页 ---------- */

let historyState = { page: 1, platform: "", status: "", keyword: "", group: "date", favorite: 0, tag: "", view: localStorage.getItem("pacdown-view") || "grid" };
let historyLoaded = false;   // 首次加载后才显示骨架屏，静默刷新不闪
let manageMode = false;      // 批量管理模式
let manageSel = new Set();   // 选中的记录 id
let lastRenderedIds = [];    // 当前列表/分组渲染的记录 id（全选用）

const GROUP_LABEL = { date: "按日期", platform: "按平台", author: "按作者" };

function groupLabel(g) {
  if (historyState.group === "date") return g.label;
  if (historyState.group === "platform") return PLATFORM_NAME[g.key] || g.key;
  if (historyState.group === "author") return g.label || "未知作者";
  return g.label;
}

async function loadHistory(silent = false) {
  const grid = $("#history-grid");
  if (!silent || !historyLoaded) {
    grid.innerHTML = Array(6).fill(`<div class="skeleton"><div class="sk-cover"></div><div class="sk-line" style="width:80%"></div><div class="sk-line" style="width:55%"></div></div>`).join("");
  }
  loadStats();
  loadTagFilter();
  const s = historyState;
  try {
    if (s.group === "none") {
      const params = new URLSearchParams({
        page: s.page, size: 24, platform: s.platform, status: s.status, keyword: s.keyword,
        favorite: s.favorite, tag: s.tag,
      });
      const { items, total, page, size } = await api(`/api/history?${params}`);
      if (grid.querySelector(".skeleton")) delete grid.dataset.sig;
      renderHistory(items, total, page, size);
    } else {
      const params = new URLSearchParams({
        platform: s.platform, status: s.status, keyword: s.keyword, group_by: s.group,
        favorite: s.favorite, tag: s.tag,
      });
      const { groups } = await api(`/api/history/groups?${params}`);
      if (grid.querySelector(".skeleton")) delete grid.dataset.sig;
      renderGroups(groups);
    }
    historyLoaded = true;
  } catch (e) {
    if (!silent) grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><p>${esc(e.message)}</p></div>`;
  }
}

function renderGroups(groups) {
  const grid = $("#history-grid");
  const sig = JSON.stringify([historyState, groups.map((g) => g.key + ":" + g.count + ":" + g.size), manageMode]);
  if (grid.dataset.sig === sig) return;
  const isRefresh = Boolean(grid.dataset.sig);
  grid.dataset.sig = sig;
  grid.classList.toggle("no-anim", isRefresh);
  $("#pagination").innerHTML = "";
  if (!groups.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 15l5-5 4 4 3-3 6 6"/></svg>
      <p>还没有下载记录<br>回到「下载」页，粘贴一个链接开始</p></div>`;
    lastRenderedIds = [];
    return;
  }
  lastRenderedIds = groups.flatMap((g) => g.items.map((v) => v.id));
  grid.innerHTML = groups.map((g) => {
    const cards = g.items.map((v) => hCardHTML(v, 0)).join("");
    return `<div class="group-block" style="grid-column:1/-1">
      <div class="group-head">
        <span class="group-label">${esc(groupLabel(g))}</span>
        <span class="group-meta num">${g.count} 个 · ${fmtSize(g.size) || "0 MB"}</span>
      </div>
      <div class="group-grid">${cards}</div>
    </div>`;
  }).join("");
}

async function loadStats() {
  try {
    const st = await api("/api/history/stats");
    const platChips = st.by_platform.slice(0, 4)
      .map((p) => `<span style="color:var(--text-3)">${PLATFORM_NAME[p.platform] || p.platform} ${p.n}</span>`).join(" · ");
    const icons = {
      total: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 15l5-5 4 4 3-3 6 6"/></svg>',
      size: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 3v18M8 7h6.5a2.5 2.5 0 0 1 0 5H9a2.5 2.5 0 0 0 0 5h7"/></svg>',
      today: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
      failed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 8v5m0 3h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"/></svg>',
    };
    const html = `
      <div class="stat-card glass"><div class="stat-label">${icons.total}已保存视频</div>
        <div class="stat-value num">${st.total}</div><div class="stat-sub">${platChips}</div></div>
      <div class="stat-card glass"><div class="stat-label">${icons.size}占用空间</div>
        <div class="stat-value num">${st.total_size >= 1024 ** 3 ? (st.total_size / 1024 ** 3).toFixed(2) + " GB" : (st.total_size / 1024 ** 2).toFixed(1) + " MB"}</div><div class="stat-sub">含视频与附件</div></div>
      <div class="stat-card glass"><div class="stat-label">${icons.today}今日新增</div>
        <div class="stat-value num">${st.today}</div><div class="stat-sub">最近 24 小时内完成</div></div>
      <div class="stat-card glass"><div class="stat-label">${icons.failed}失败任务</div>
        <div class="stat-value num" style="${st.failed ? "color:var(--err)" : ""}">${st.failed}</div><div class="stat-sub">可在任务列表重试</div></div>`;
    // 内容无变化时不重绘，避免数字块闪烁
    if ($("#stat-row").dataset.sig !== st.total + "_" + st.total_size + "_" + st.today + "_" + st.failed) {
      $("#stat-row").innerHTML = html;
      $("#stat-row").dataset.sig = st.total + "_" + st.total_size + "_" + st.today + "_" + st.failed;
    }
  } catch (e) { console.error(e); }
}

function hCardHTML(v, i) {
  const imageCount = JSON.parse(v.images || "[]").length;
  // 封面：优先平台封面 → 图集首图 → 本地截帧封面
  const coverSrcVal = v.cover_url
    ? coverSrc(v.cover_url, v.platform)
    : (imageCount ? `/api/file?id=${v.id}&type=image&index=0`
       : (v.cover_path ? `/api/file?id=${v.id}&type=cover` : ""));
  const cover = coverSrcVal
    ? `<img src="${esc(coverSrcVal)}" loading="lazy" alt="" onerror="this.style.display='none'">` : "";
  const durOrCount = imageCount
    ? `<span class="h-dur" style="display:inline-flex;align-items:center;gap:3px">
         <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.8"/><path d="M21 15.5l-5-5L5 21"/></svg>${imageCount} 图</span>`
    : (v.duration ? `<span class="h-dur">${fmtDuration(v.duration)}</span>` : "");
  const sel = manageSel.has(v.id);
  const tags = (v.tags || []).slice(0, 3).map((t) => `<span class="h-tag">#${esc(t)}</span>`).join("");
  return `<div class="h-card ${manageMode ? "managing" : ""} ${sel ? "sel" : ""}" style="animation-delay:${Math.min(i * 30, 240)}ms" data-vid="${v.id}" onclick="cardClick(event, ${v.id})">
    <div class="h-cover">
      ${cover}
      ${durOrCount}
      <span class="h-status">${statusBadge(v.status)}</span>
      ${v.favorite ? `<span class="h-fav" title="已收藏">★</span>` : ""}
      ${manageMode ? `<span class="h-check ${sel ? "on" : ""}">${sel ? "✓" : ""}</span>` : ""}
    </div>
    <div class="h-body">
      <div class="h-title">${esc(v.title || "(无标题)")}</div>
      <div class="h-meta">${badge(v.platform)}<span class="author">${esc(v.author || "")}</span>
        <span class="size">${fmtSize(v.file_size)}</span></div>
      ${tags ? `<div class="h-tags">${tags}</div>` : ""}
    </div>
  </div>`;
}

/* 列表视图行 */
function hRowHTML(v) {
  const imageCount = JSON.parse(v.images || "[]").length;
  const coverSrcVal = v.cover_url
    ? coverSrc(v.cover_url, v.platform)
    : (imageCount ? `/api/file?id=${v.id}&type=image&index=0`
       : (v.cover_path ? `/api/file?id=${v.id}&type=cover` : ""));
  const sel = manageSel.has(v.id);
  const tags = (v.tags || []).map((t) => `<span class="h-tag">#${esc(t)}</span>`).join("");
  return `<div class="h-row ${manageMode ? "managing" : ""} ${sel ? "sel" : ""}" data-vid="${v.id}" onclick="cardClick(event, ${v.id})">
    ${manageMode ? `<span class="h-check-static ${sel ? "on" : ""}">${sel ? "✓" : ""}</span>` : ""}
    ${coverSrcVal ? `<img class="h-row-cover" loading="lazy" src="${esc(coverSrcVal)}" alt="" onerror="this.style.visibility='hidden'">` : `<div class="h-row-cover"></div>`}
    <div class="h-row-main">
      <div class="h-row-title">${esc(v.title || "(无标题)")}${v.favorite ? ` <span class="h-fav-inline">★</span>` : ""}</div>
      <div class="h-row-sub">${badge(v.platform)}<span>${esc(v.author || "")}</span>${tags}</div>
    </div>
    <div class="h-row-side num">
      <span>${fmtSize(v.file_size)}</span>
      <span>${imageCount ? `${imageCount} 图` : (v.duration ? fmtDuration(v.duration) : "")}</span>
      <span>${fmtTime(v.downloaded_at)}</span>
    </div>
    ${statusBadge(v.status)}
  </div>`;
}

const VIEW_ICONS = {
  grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/></svg>',
  list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M9 6h12M9 12h12M9 18h12M4 6h.5M4 12h.5M4 18h.5"/></svg>',
};

function updateViewBtn() {
  $("#btn-view-toggle").innerHTML = (historyState.view === "list" ? VIEW_ICONS.grid : VIEW_ICONS.list) +
    (historyState.view === "list" ? "网格" : "列表");
}

$("#btn-view-toggle").addEventListener("click", () => {
  historyState.view = historyState.view === "list" ? "grid" : "list";
  localStorage.setItem("pacdown-view", historyState.view);
  updateViewBtn();
  if (historyState.group !== "none") {
    toast("列表视图在分组为「不分组」时生效", "warn");
    return;
  }
  delete $("#history-grid").dataset.sig;
  loadHistory(true);
});

function renderHistory(items, total, page, size) {  const grid = $("#history-grid");
  // 数据未变化时不重绘（避免轮询刷新时整页闪烁）
  const sig = JSON.stringify([historyState, total, items.map((v) => v.id + ":" + v.status + ":" + v.file_size), manageMode]);
  if (grid.dataset.sig === sig) return;
  const isRefresh = Boolean(grid.dataset.sig);   // 静默刷新时卡片不再重播入场动画
  grid.dataset.sig = sig;
  grid.classList.toggle("no-anim", isRefresh);
  lastRenderedIds = items.map((v) => v.id);
  if (!items.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 15l5-5 4 4 3-3 6 6"/></svg>
      <p>还没有下载记录<br>回到「下载」页，粘贴一个链接开始</p></div>`;
    $("#pagination").innerHTML = "";
    return;
  }
  grid.innerHTML = historyState.view === "list"
    ? `<div class="h-list" style="grid-column:1/-1">` + items.map((v) => hRowHTML(v)).join("") + `</div>`
    : items.map((v, i) => hCardHTML(v, i)).join("");
  // 分页
  const pages = Math.ceil(total / size);
  if (pages <= 1) { $("#pagination").innerHTML = ""; return; }
  const btns = [];
  const add = (p, label, cur, dis) =>
    btns.push(`<button class="page-btn ${cur ? "cur" : ""}" ${dis ? "disabled" : ""} onclick="goPage(${p})">${label}</button>`);
  add(page - 1, "‹", false, page <= 1);
  const start = Math.max(1, page - 2), end = Math.min(pages, page + 2);
  if (start > 1) { add(1, 1); if (start > 2) btns.push(`<span class="page-btn" style="border:0;background:none">…</span>`); }
  for (let p = start; p <= end; p++) add(p, p, p === page);
  if (end < pages) { if (end < pages - 1) btns.push(`<span class="page-btn" style="border:0;background:none">…</span>`); add(pages, pages); }
  add(page + 1, "›", false, page >= pages);
  $("#pagination").innerHTML = btns.join("");
}

window.goPage = (p) => { historyState.page = p; loadHistory(); };

let searchDebounce;
$("#history-search").addEventListener("input", (e) => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    historyState.keyword = e.target.value.trim();
    historyState.page = 1;
    loadHistory();
  }, 350);
});
$("#history-platform").addEventListener("change", (e) => {
  historyState.platform = e.target.value; historyState.page = 1; loadHistory();
});
$("#history-status").addEventListener("change", (e) => {
  historyState.status = e.target.value; historyState.page = 1; loadHistory();
});
$("#history-group").addEventListener("change", (e) => {
  historyState.group = e.target.value; historyState.page = 1; loadHistory();
});
$("#btn-export").addEventListener("click", () => {
  window.location.href = "/api/history/export";
});

/* ---------- 片库：收藏 / 标签筛选 ---------- */

$("#btn-fav-filter").addEventListener("click", () => {
  historyState.favorite = historyState.favorite ? 0 : 1;
  $("#btn-fav-filter").classList.toggle("on", !!historyState.favorite);
  historyState.page = 1;
  loadHistory();
});

async function loadTagFilter() {
  try {
    const { tags } = await api("/api/history/tags");
    const sel = $("#history-tag");
    const cur = sel.value;
    sel.innerHTML = `<option value="">全部标签</option>` +
      tags.map((t) => `<option value="${esc(t)}">#${esc(t)}</option>`).join("");
    sel.value = cur;
  } catch (e) { /* 忽略 */ }
}

$("#history-tag").addEventListener("change", (e) => {
  historyState.tag = e.target.value; historyState.page = 1; loadHistory();
});

/* ---------- 片库：批量管理模式 ---------- */

function setManageMode(on) {
  manageMode = on;
  manageSel.clear();
  updateManageBar();
  $("#manage-bar").hidden = !on;
  $("#btn-manage").classList.toggle("on", on);
  delete $("#history-grid").dataset.sig;  // 强制重绘（卡片要挂复选框）
  loadHistory(true);
}

window.cardClick = (e, id) => {
  if (!manageMode) { openDetail(id); return; }
  if (manageSel.has(id)) manageSel.delete(id);
  else manageSel.add(id);
  const card = $(`.h-card[data-vid="${id}"]`);
  if (card) {
    const sel = manageSel.has(id);
    card.classList.toggle("sel", sel);
    const chk = card.querySelector(".h-check");
    if (chk) { chk.classList.toggle("on", sel); chk.textContent = sel ? "✓" : ""; }
  }
  updateManageBar();
};

function updateManageBar() {
  $("#manage-count").textContent = `已选 ${manageSel.size} 项`;
}

$("#btn-manage").addEventListener("click", () => setManageMode(!manageMode));
$("#manage-exit").addEventListener("click", () => setManageMode(false));

$("#manage-select-all").addEventListener("click", () => {
  const allSelected = lastRenderedIds.length > 0 &&
    lastRenderedIds.every((id) => manageSel.has(id));
  manageSel = allSelected ? new Set() : new Set(lastRenderedIds);
  $$("#history-grid .h-card").forEach((card) => {
    const sel = manageSel.has(+card.dataset.vid);
    card.classList.toggle("sel", sel);
    const chk = card.querySelector(".h-check");
    if (chk) { chk.classList.toggle("on", sel); chk.textContent = sel ? "✓" : ""; }
  });
  updateManageBar();
});

$("#manage-export").addEventListener("click", () => {
  if (!manageSel.size) { toast("请先勾选记录", "warn"); return; }
  window.location.href = `/api/history/export?ids=${[...manageSel].join(",")}`;
});

$("#manage-delete").addEventListener("click", async () => {
  if (!manageSel.size) { toast("请先勾选记录", "warn"); return; }
  if (!confirm(`删除选中的 ${manageSel.size} 条记录？`)) return;
  const delFiles = confirm("同时删除磁盘上的文件？\n「确定」删除文件，「取消」仅删除记录");
  try {
    const r = await api("/api/history/batch_delete", {
      method: "POST",
      body: { ids: [...manageSel], keep_files: !delFiles },
    });
    toast(`已删除 ${r.deleted} 条记录${r.files_removed ? `，清理 ${r.files_removed} 个文件` : ""}`);
    setManageMode(false);
    loadHistory();
    loadStats();
  } catch (e) { toast(e.message, "err"); }
});

/* ---------- 详情弹窗 ---------- */

window.openDetail = async (id) => {
  try {
    const v = await api(`/api/history/${id}`);
    const stats = Object.entries(v.stats || {})
      .filter(([, val]) => val != null)
      .map(([k, val]) => `<div class="kv"><b>${{ play: "播放", like: "点赞", comment: "评论", share: "分享", collect: "收藏", view: "播放", liked: "点赞", collected: "收藏" }[k] || k}</b><span class="num">${fmtCount(val) || val}</span></div>`)
      .join("");
    const kv = (label, val) => val ? `<div class="kv"><b>${label}</b><span>${esc(val)}</span></div>` : "";

    // 预览区：视频直接播放 / 图集网格 / 否则封面（可点开放大）
    const images = JSON.parse(v.images || "[]");
    let preview = "";
    if (v.status === "done" && v.file_path && !images.length) {
      preview = `<video class="preview-video" controls preload="metadata"
        src="/api/file?id=${v.id}&type=video" poster="${esc(coverSrc(v.cover_url, v.platform))}"></video>
        <div class="preview-hint">在线预览 · 拖动进度条播放</div>`;
    } else if (v.status === "done" && images.length) {
      preview = `<div class="gallery-grid">` + images.map((_, i) =>
        `<div class="gallery-item" onclick="openGallery(${v.id}, ${images.length}, ${i})">
           <img src="/api/file?id=${v.id}&type=image&index=${i}" loading="lazy" alt=""
                onerror="this.parentElement.style.display='none'">
           <span class="g-idx">${i + 1}/${images.length}</span>
         </div>`).join("") + `</div>
        <div class="preview-hint">点击图片查看大图</div>`;
    } else if (v.cover_url) {
      preview = `<img class="modal-cover" src="${esc(coverSrc(v.cover_url, v.platform))}" alt=""
        onclick='openLightbox([{type:"image",src:${JSON.stringify(coverSrc(v.cover_url, v.platform))},caption:"封面"}], 0)'>`;
    }

    let audio = "";
    if (v.status === "done" && v.audio_path) {
      audio = `<audio class="preview-audio" controls preload="none"
        src="/api/file?id=${v.id}&type=audio"></audio>
        <div class="preview-hint">MP3 试听</div>`;
    }

    // 作者完整文案（超出标题部分）
    let descBlock = "";
    if (v.description && v.description.trim() && v.description.trim() !== v.title) {
      descBlock = `<div class="desc-block">
        <div class="desc-label">作者文案</div>
        <div class="desc-text">${esc(v.description.trim())}</div>
      </div>`;
    }

    // 评论列表
    let commentsBlock = "";
    if (Array.isArray(v.comments) && v.comments.length) {
      commentsBlock = `<details class="comments-block">
        <summary>评论 · ${v.comments.length} 条热评</summary>
        <div class="comment-list">` + v.comments.map((c) => `
          <div class="comment-item">
            <div class="comment-head">
              <span class="comment-user">${esc(c.user || "匿名")}</span>
              <span class="comment-meta num">👍 ${fmtCount(c.like) || 0}${c.time ? " · " + c.time : ""}</span>
            </div>
            <div class="comment-text">${esc(c.content || "")}</div>
          </div>`).join("") + `</div></details>`;
    }

    $("#modal-body").innerHTML = `
      <button class="modal-close" onclick="closeModal()">✕</button>
      ${preview}
      <div class="modal-title-row">
        <h3 id="detail-title">${esc(v.title || "(无标题)")}</h3>
        <button class="fav-btn ${v.favorite ? "on" : ""}" id="detail-fav" title="收藏 / 取消收藏">★</button>
        <button class="btn btn-ghost btn-sm" id="btn-edit-meta" onclick="toggleEditMeta()">编辑</button>
      </div>
      <div id="detail-edit" hidden>
        <div class="field"><label>标题</label><input id="edit-title"></div>
        <div class="field"><label>作者</label><input id="edit-author"></div>
        <div class="field"><label>描述</label><textarea id="edit-desc" rows="3"></textarea></div>
        <p class="hint">仅修改库内元数据，磁盘文件名不变</p>
        <div class="gen-actions">
          <button class="btn btn-primary btn-sm" onclick="saveEditMeta()">保存</button>
          <button class="btn btn-ghost btn-sm" onclick="toggleEditMeta()">取消</button>
        </div>
      </div>
      <div class="h-meta" style="margin-top:8px">${badge(v.platform)}<span>${esc(v.author)}</span>${statusBadge(v.status)}</div>
      <div class="detail-tags" id="detail-tags"></div>
      <div class="modal-grid">
        ${kv("发布时间", v.publish_time)}
        ${kv("时长", fmtDuration(v.duration))}
        ${kv("清晰度", v.quality === "best" ? "最高可用" : v.quality)}
        ${kv("文件大小", fmtSize(v.file_size))}
        ${kv("下载时间", v.downloaded_at)}
        ${v.danmaku_path ? kv("弹幕", "已保存 XML") : ""}
        ${v.subtitle_path ? kv("字幕", "已保存 SRT") : ""}
        ${images.length ? kv("图集", images.length + " 张") : ""}
        ${stats}
      </div>
      ${v.file_path ? `<div class="kv" style="margin-top:10px"><b>文件位置</b><span style="font-size:12px">${esc(v.file_path)}</span></div>` : ""}
      ${descBlock}
      ${commentsBlock}
      ${v.error ? `<div class="task-err" style="margin-top:10px">${esc(v.error)}</div>` : ""}
      <div class="modal-actions">
        ${v.status === "done" && v.file_path && !images.length ? `<button class="btn btn-primary btn-sm" onclick="saveToLocal(${v.id}, 'file')">保存视频到本机</button>` : ""}
        ${v.status === "done" ? `<button class="btn btn-primary btn-sm" onclick="saveToLocal(${v.id}, 'zip')">打包下载 ZIP</button>` : ""}
        ${v.file_path && v.status === "done" ? `<button class="btn btn-ghost btn-sm" onclick="openFolder(${v.id})">打开所在目录</button>` : ""}
        <button class="btn btn-ghost btn-sm" onclick="redownload(${v.id}, '${esc(v.source_url)}')">重新下载</button>
        <button class="btn btn-danger btn-sm" onclick="deleteVideo(${v.id})">删除记录</button>
      </div>
      <details class="modal-raw"><summary>查看原始数据 JSON</summary>
        <pre>${esc(JSON.stringify(v.raw, null, 2))}</pre></details>
    `;
    detailVid = v.id;
    editMetaVid = v.id;
    detailTags = [...(v.tags || [])];
    renderDetailTags();
    $("#detail-fav").addEventListener("click", async (e) => {
      const on = !e.currentTarget.classList.contains("on");
      try {
        await api(`/api/history/${v.id}`, { method: "PATCH", body: { favorite: on } });
        e.currentTarget.classList.toggle("on", on);
        loadHistory(true);
      } catch (err) { toast(err.message, "err"); }
    });
    $("#edit-title").value = v.title || "";
    $("#edit-author").value = v.author || "";
    $("#edit-desc").value = v.description || "";
    $("#detail-edit").hidden = true;
    $("#btn-edit-meta").textContent = "编辑";
    $("#modal").hidden = false;
  } catch (e) { toast(e.message, "err"); }
};

/* 详情弹窗元数据编辑 */
let editMetaVid = 0;
window.toggleEditMeta = () => {
  const box = $("#detail-edit");
  box.hidden = !box.hidden;
  $("#btn-edit-meta").textContent = box.hidden ? "编辑" : "收起";
};

window.saveEditMeta = async () => {
  try {
    await api(`/api/history/${editMetaVid}`, {
      method: "PATCH",
      body: {
        title: $("#edit-title").value.trim(),
        author: $("#edit-author").value.trim(),
        description: $("#edit-desc").value.trim(),
      },
    });
    toast("已保存");
    toggleEditMeta();
    openDetail(editMetaVid);   // 重开刷新显示
    loadHistory(true);
  } catch (e) { toast(e.message, "err"); }
};

/* 详情弹窗的标签编辑 */
let detailTags = [];
let detailVid = 0;

function renderDetailTags() {
  const box = $("#detail-tags");
  box.innerHTML = `<span class="detail-tags-label">标签</span>` +
    detailTags.map((t, i) =>
      `<span class="tag-chip">#${esc(t)}<span class="tag-x" data-i="${i}">✕</span></span>`).join("") +
    `<button class="tag-add" id="detail-tag-add">+ 标签</button>`;
}

$("#modal-body").addEventListener("click", async (e) => {
  if (!detailVid) return;
  const x = e.target.closest(".tag-x");
  if (x && e.target.closest("#detail-tags")) {
    detailTags.splice(+x.dataset.i, 1);
    renderDetailTags();
    await saveDetailTags();
    return;
  }
  if (e.target.id === "detail-tag-add") {
    const t = prompt("新标签（不用带 #）");
    if (t && t.trim()) {
      detailTags.push(t.trim().replace(/^#+/, ""));
      renderDetailTags();
      await saveDetailTags();
    }
  }
});

async function saveDetailTags() {
  try {
    await api(`/api/history/${detailVid}`, { method: "PATCH", body: { tags: detailTags } });
    loadHistory(true);
    loadTagFilter();
  } catch (e) { toast(e.message, "err"); }
}

window.closeModal = () => {
  $$("#modal-body video, #modal-body audio").forEach((el) => el.pause());
  $("#modal").hidden = true;
};
$("#modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") closeModal();
});

/* ---------- 灯箱：全屏预览图片 / 播放视频 ---------- */

let lbItems = [], lbIndex = 0;

window.openLightbox = (items, index = 0) => {
  lbItems = items.filter((it) => it && it.src);
  if (!lbItems.length) return;
  lbIndex = Math.max(0, Math.min(index, lbItems.length - 1));
  $("#lightbox").hidden = false;
  renderLightbox();
};

window.openGallery = (vid, count, index) => {
  const items = Array.from({ length: count }, (_, i) => ({
    type: "image",
    src: `/api/file?id=${vid}&type=image&index=${i}`,
    caption: `图集 · ${i + 1} / ${count}`,
  }));
  openLightbox(items, index);
};

function renderLightbox() {
  const it = lbItems[lbIndex];
  const multi = lbItems.length > 1;
  $("#lb-prev").style.display = multi ? "" : "none";
  $("#lb-next").style.display = multi ? "" : "none";
  $("#lb-stage").innerHTML = it.type === "video"
    ? `<video controls autoplay src="${esc(it.src)}"></video>`
    : `<img src="${esc(it.src)}" alt="">`;
  $("#lb-caption").textContent = it.caption || "";
}

function lbMove(step) {
  if (!lbItems.length) return;
  lbIndex = (lbIndex + step + lbItems.length) % lbItems.length;
  renderLightbox();
}

$("#lb-prev").addEventListener("click", () => lbMove(-1));
$("#lb-next").addEventListener("click", () => lbMove(1));
$("#lb-close").addEventListener("click", () => closeLightbox());
$("#lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") closeLightbox();
});
function closeLightbox() {
  $("#lightbox").hidden = true;
  $("#lb-stage").innerHTML = "";  // 停止视频播放
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("#lightbox").hidden) closeLightbox();
    else if (!$("#modal").hidden) closeModal();
    else if (!$("#donate-modal").hidden) closeDonate();
  }
  if (!$("#lightbox").hidden) {
    if (e.key === "ArrowLeft") lbMove(-1);
    if (e.key === "ArrowRight") lbMove(1);
  }
});

window.saveToLocal = (id, kind) => {
  // 域名/远程访问时把文件拉回本机：file=单个视频，zip=全部产物打包
  window.location.href = kind === "zip"
    ? `/api/history/${id}/zip`
    : `/api/file?id=${id}&type=video&download=1`;
};

window.openFolder = async (id) => {
  try { await api(`/api/history/${id}/open`, { method: "POST" }); }
  catch (e) { toast(e.message, "err"); }
};

window.redownload = async (id, url) => {
  try {
    const { results } = await api("/api/download", {
      method: "POST", body: { text: url, options: { quality: "best" }, force: true },
    });
    toast(results[0]?.status === "created" ? "已重新加入队列" : "已加入队列");
    closeModal();
    pollTasks(true);
  } catch (e) { toast(e.message, "err"); }
};

window.deleteVideo = async (id) => {
  if (!confirm("删除这条记录？")) return;
  const delFiles = confirm("同时删除磁盘上的文件？\n「确定」删除文件，「取消」仅删除记录");
  try {
    await api(`/api/history/${id}?keep_files=${!delFiles}`, { method: "DELETE" });
    toast(delFiles ? "记录与文件已删除" : "记录已删除，文件保留");
    closeModal();
    loadHistory();
  } catch (e) { toast(e.message, "err"); }
};

/* ---------- 订阅页 ---------- */

async function loadSubs() {
  const box = $("#sub-list");
  box.innerHTML = Array(2).fill(`<div class="skeleton" style="height:78px;border-radius:15px"></div>`).join("");
  try {
    const { items } = await api("/api/subscriptions");
    if (!items.length) {
      box.innerHTML = `<div class="empty" style="grid-column:1/-1">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3"/></svg>
        <p>还没有订阅<br>粘贴 B站 UP主空间或抖音博主主页链接，新视频自动下载</p></div>`;
      return;
    }
    box.innerHTML = items.map((s) => {
      let opts = {};
      try { opts = JSON.parse(s.options || "{}"); } catch (e) { /* 忽略 */ }
      const optChips = [
        opts.extract_audio ? '<span class="h-tag">自动MP3</span>' : "",
        opts.download_danmaku ? '<span class="h-tag">自动弹幕</span>' : "",
      ].join("");
      return `
      <div class="sub-card glass ${s.enabled ? "" : "sub-paused"}">
        <img class="sub-avatar" src="${esc(coverSrc(s.avatar_url, s.platform))}" alt=""
             onerror="this.style.visibility='hidden'">
        <div class="sub-info">
          <div class="sub-name">${esc(s.uploader_name)} ${badge(s.platform)} ${optChips}</div>
          <div class="sub-meta">上次检查：${fmtTime(s.last_checked) || "从未"} · 已抓取 ${s.new_count} 个</div>
          ${s.last_error ? `<div class="sub-err" title="${esc(s.last_error)}">${esc(s.last_error)}</div>` : ""}
        </div>
        <div class="sub-actions">
          <button class="btn btn-ghost btn-sm" onclick="checkSub(${s.id})">检查</button>
          <button class="btn btn-ghost btn-sm" onclick="toggleSub(${s.id}, ${s.enabled ? 0 : 1})">${s.enabled ? "暂停" : "恢复"}</button>
          <button class="btn btn-danger btn-sm" onclick="removeSub(${s.id})">删除</button>
        </div>
      </div>`;
    }).join("");
  } catch (e) { box.innerHTML = `<div class="empty" style="grid-column:1/-1"><p>${esc(e.message)}</p></div>`; }
}

$("#btn-add-sub").addEventListener("click", async () => {
  const url = $("#sub-url").value.trim();
  if (!url) { toast("请输入博主主页链接", "warn"); return; }
  try {
    await api("/api/subscriptions", {
      method: "POST",
      body: { url, extract_audio: $("#sub-opt-audio").checked,
              download_danmaku: $("#sub-opt-danmaku").checked },
    });
    toast("订阅已添加，将按设置间隔自动检查");
    $("#sub-url").value = "";
    loadSubs();
  } catch (e) { toast(e.message, "err"); }
});

window.checkSub = async (id) => {
  toast("正在检查更新…", "warn", 1500);
  try {
    const { new_count } = await api(`/api/subscriptions/${id}/check`, { method: "POST" });
    toast(new_count ? `发现 ${new_count} 个新视频，已加入队列` : "暂无新视频");
    loadSubs(); pollTasks(true);
  } catch (e) { toast(e.message, "err"); loadSubs(); }
};

window.toggleSub = async (id, enabled) => {
  try {
    await api(`/api/subscriptions/${id}`, { method: "PATCH", body: { enabled: !!enabled } });
    loadSubs();
  } catch (e) { toast(e.message, "err"); }
};

window.removeSub = async (id) => {
  if (!confirm("删除该订阅？已下载的视频不受影响。")) return;
  try { await api(`/api/subscriptions/${id}`, { method: "DELETE" }); loadSubs(); }
  catch (e) { toast(e.message, "err"); }
};

/* ---------- 搬运工作台 ---------- */

let repostSel = null;    // 当前选中视频
let repostTags = [];     // 可编辑标签
let currentRepostId = 0;

async function loadRepost() {
  try {
    const st = await api("/api/repost/status");
    $("#ai-notice").hidden = !!st.ai_ready;
  } catch (e) { /* 忽略 */ }
  loadRepostVideos("");
  loadRepostHistory();
}

async function loadRepostVideos(keyword) {
  const box = $("#repost-video-list");
  try {
    const { items } = await api(`/api/repost/videos?keyword=${encodeURIComponent(keyword)}`);
    if (!items.length) {
      box.innerHTML = `<div class="empty" style="padding:26px 10px"><p>${keyword ? "没有匹配的视频" : "还没有已完成的下载"}</p></div>`;
      return;
    }
    box.innerHTML = items.map((v) => `
      <div class="rp-item ${repostSel?.id === v.id ? "sel" : ""}" data-vid="${v.id}">
        <img class="rp-thumb" src="${v.cover_url ? esc(coverSrc(v.cover_url, v.platform)) : ""}"
             onerror="this.style.visibility='hidden'">
        <div class="rp-info">
          <div class="rp-title">${esc(v.title || "(无标题)")}</div>
          <div class="rp-meta">${PLATFORM_NAME[v.platform] || v.platform} · ${esc(v.author || "")}</div>
        </div>
      </div>`).join("");
  } catch (e) {
    box.innerHTML = `<div class="empty" style="padding:26px 10px"><p>${esc(e.message)}</p></div>`;
  }
}

$("#repost-search").addEventListener("input", (e) => {
  clearTimeout($("#repost-search")._t);
  $("#repost-search")._t = setTimeout(() => loadRepostVideos(e.target.value.trim()), 350);
});

$("#repost-video-list").addEventListener("click", async (e) => {
  const item = e.target.closest(".rp-item");
  if (!item) return;
  const vid = +item.dataset.vid;
  $$(".rp-item").forEach((el) => el.classList.toggle("sel", el === item));
  try {
    const v = await api(`/api/history/${vid}`);
    repostSel = v;
    $("#repost-empty").hidden = true;
    $("#repost-video-card").hidden = false;
    $("#repost-gen").hidden = false;
    $("#repost-video-card").innerHTML = `
      <div class="rv-title">${esc(v.title)}</div>
      <div class="h-meta" style="margin-top:6px">${badge(v.platform)}
        <span>${esc(v.author)}</span>${statusBadge(v.status)}</div>
      ${v.description ? `<div class="rv-desc">${esc(v.description)}</div>` : ""}`;
    $("#gen-result").hidden = true;
  } catch (err) { toast(err.message, "err"); }
});

$("#btn-generate").addEventListener("click", async () => {
  if (!repostSel) return;
  const btn = $("#btn-generate");
  btn.disabled = true;
  btn.innerHTML = "AI 生成中…";
  try {
    const r = await api("/api/repost/generate", {
      method: "POST",
      body: { video_id: repostSel.id, style: $("#gen-style").value,
              credit: $("#gen-credit").checked },
    });
    currentRepostId = r.id;
    $("#gen-title").value = r.title;
    $("#gen-desc").value = r.description;
    repostTags = r.tags || [];
    renderTags();
    $("#gen-result").hidden = false;
    loadRepostHistory();
  } catch (e) { toast(e.message, "err", 5000); }
  finally { btn.disabled = false; btn.innerHTML = "AI 生成文案"; }
});

function renderTags() {
  const box = $("#gen-tags");
  box.innerHTML = repostTags.map((t, i) =>
    `<span class="tag-chip">#${esc(t)}<span class="tag-x" data-i="${i}">✕</span></span>`).join("") +
    `<button class="tag-add" id="tag-add">+ 标签</button>`;
}

$("#gen-tags").addEventListener("click", (e) => {
  const x = e.target.closest(".tag-x");
  if (x) { repostTags.splice(+x.dataset.i, 1); renderTags(); return; }
  if (e.target.id === "tag-add") {
    const t = prompt("新标签（不用带 #）");
    if (t && t.trim()) { repostTags.push(t.trim()); renderTags(); }
  }
});

$("#btn-copy-desc").addEventListener("click", async () => {
  const text = `${$("#gen-title").value}\n\n${$("#gen-desc").value}\n\n${repostTags.map((t) => "#" + t).join(" ")}`;
  try {
    await navigator.clipboard.writeText(text.trim());
    toast("文案已复制到剪贴板");
  } catch (e) {
    // 非安全上下文回退
    const ta = document.createElement("textarea");
    ta.value = text.trim();
    document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
    toast("文案已复制");
  }
});

$("#btn-save-repost").addEventListener("click", async () => {
  if (!currentRepostId) return;
  try {
    await api(`/api/repost/${currentRepostId}/save`, {
      method: "POST",
      body: { title: $("#gen-title").value,
              description: $("#gen-desc").value, tags: repostTags },
    });
    const tip = $("#gen-saved-tip");
    tip.textContent = "已保存 ✓"; tip.classList.add("show");
    setTimeout(() => tip.classList.remove("show"), 2000);
    loadRepostHistory();
  } catch (e) { toast(e.message, "err"); }
});

$("#btn-open-folder").addEventListener("click", async () => {
  if (!repostSel) return;
  try { await api(`/api/history/${repostSel.id}/open`, { method: "POST" }); }
  catch (e) { toast(e.message, "err"); }
});

async function loadRepostHistory() {
  const box = $("#repost-history");
  try {
    const { items } = await api("/api/repost/list");
    if (!items.length) {
      box.innerHTML = `<div class="empty"><p>还没有生成记录</p></div>`;
      return;
    }
    box.innerHTML = items.map((r) => `
      <div class="rh-item">
        <span class="rh-title">${esc(r.new_title)}</span>
        <span class="rh-desc">${esc(r.new_desc.replace(/\\n/g, " "))}</span>
        <span class="rh-time num">${(r.created_at || "").slice(5, 16)}</span>
        <button class="btn btn-ghost btn-sm rh-copy" data-cid="${r.id}">复制</button>
      </div>`).join("");
  } catch (e) { box.innerHTML = ""; }
}

$("#repost-history").addEventListener("click", async (e) => {
  const btn = e.target.closest(".rh-copy");
  if (!btn) return;
  try {
    const { items } = await api(`/api/repost/list?video_id=`);
    const row = items.find((r) => r.id === +btn.dataset.cid);
    if (row) {
      await navigator.clipboard.writeText(
        `${row.new_title}\n\n${row.new_desc}\n\n${row.tags.map((t) => "#" + t).join(" ")}`);
      toast("已复制该条文案");
    }
  } catch (err) { toast("复制失败", "err"); }
});

/* ---------- 工具箱 ---------- */

let toolsMeta = null;
let toolSrc = null;          // {video_id, title} | {upload, media, title}
let toolKind = "transcode";
let sourceTab = "video";     // video | images
let toolSources = [];

const TOOL_PARAM_DEFS = {
  transcode: [
    { k: "vcodec", label: "编码", type: "select", def: "h264",
      options: [["h264", "H.264 兼容好"], ["h265", "H.265 更省空间"], ["vp9", "VP9"], ["copy", "仅换封装"]] },
    { k: "resolution", label: "分辨率", type: "select", def: "source",
      options: [["source", "保持原分辨率"], ["1080", "1080p"], ["720", "720p"], ["480", "480p"]] },
    { k: "crf", label: "CRF（越小越清晰）", type: "number", def: 23, min: 15, max: 40 },
  ],
  compress: [
    { k: "crf", label: "CRF（越大文件越小）", type: "number", def: 28, min: 18, max: 40 },
    { k: "preset", label: "速度档位", type: "select", def: "medium",
      options: [["fast", "快"], ["medium", "均衡"], ["slow", "慢（更小）"]] },
  ],
  trim: [
    { k: "start", label: "开始时间", type: "text", def: "00:00:00", ph: "如 00:01:30" },
    { k: "end", label: "结束时间", type: "text", def: "", ph: "留空表示到结尾" },
  ],
  gif: [
    { k: "start", label: "开始时间", type: "text", def: "0", ph: "秒或 00:00:05" },
    { k: "duration", label: "时长（秒）", type: "number", def: 5, min: 1, max: 30 },
    { k: "fps", label: "帧率", type: "number", def: 12, min: 5, max: 30 },
    { k: "width", label: "宽度（像素）", type: "number", def: 480, min: 120, max: 1280 },
  ],
  watermark: [
    { k: "text", label: "水印文字", type: "text", def: "", ph: "如 @我的账号" },
    { k: "position", label: "位置", type: "select", def: "br",
      options: [["br", "右下"], ["bl", "左下"], ["tr", "右上"], ["tl", "左上"], ["center", "居中"]] },
    { k: "fontsize", label: "字号", type: "number", def: 32, min: 12, max: 120 },
    { k: "opacity", label: "不透明度（0-1）", type: "number", def: 0.7, min: 0.1, max: 1, step: 0.1 },
  ],
  frame: [
    { k: "at", label: "截取位置", type: "text", def: "20%", ph: "百分比或秒数，如 50% / 12.5" },
  ],
  danmaku2ass: [],
  burn_sub: [
    { k: "hint", label: "字幕文件", type: "text", def: "自动查找同目录 .ass/.srt", ph: "", disabled: true },
  ],
  img_convert: [
    { k: "format", label: "目标格式", type: "select", def: "webp",
      options: [["webp", "WebP（最小）"], ["jpg", "JPG"], ["png", "PNG"]] },
    { k: "quality", label: "质量（10-95）", type: "number", def: 85, min: 10, max: 95 },
    { k: "max_width", label: "最大宽度（0=原尺寸）", type: "number", def: 0, min: 0, max: 4096 },
  ],
  img_join: [
    { k: "max_width", label: "拼接宽度（像素）", type: "number", def: 1080, min: 200, max: 4096 },
  ],
  img_zip: [],
};

async function loadToolbox() {
  if (!toolsMeta) {
    try {
      toolsMeta = await api("/api/toolbox/tools");
      $("#tools-ffmpeg-notice").hidden = !!toolsMeta.ffmpeg;
      renderToolKinds();
      renderToolParams();
    } catch (e) { toast(e.message, "err"); return; }
  }
  loadToolSources("");
  pollToolJobs();
}

function isVideoTool() { return toolsMeta.video_tools.includes(toolKind); }

function renderToolKinds() {
  const row = $("#tool-kind-row");
  const mk = (k) => `<button class="tool-chip ${k === toolKind ? "active" : ""}" data-k="${k}">${toolsMeta.tools[k]}</button>`;
  row.innerHTML =
    `<span class="tool-group-label">视频</span>` + toolsMeta.video_tools.map(mk).join("") +
    `<span class="tool-group-label">图片</span>` + toolsMeta.image_tools.map(mk).join("");
}

$("#tool-kind-row").addEventListener("click", (e) => {
  const chip = e.target.closest(".tool-chip");
  if (!chip) return;
  toolKind = chip.dataset.k;
  renderToolKinds();
  renderToolParams();
  // 工具族与素材Tab联动
  const wantTab = isVideoTool() ? "video" : "images";
  if (wantTab !== sourceTab) {
    sourceTab = wantTab;
    $$(".picker-tab").forEach((t) => t.classList.toggle("active", t.dataset.kind === sourceTab));
    loadToolSources($("#tools-search").value.trim());
  }
});

function renderToolParams() {
  const defs = TOOL_PARAM_DEFS[toolKind] || [];
  const box = $("#tool-params");
  if (!defs.length) {
    box.innerHTML = `<p class="hint" style="margin:4px 0 0">该工具无参数，选好素材后直接开始</p>`;
    return;
  }
  box.innerHTML = defs.map((d) => {
    if (d.type === "select") {
      return `<label class="field-inline">${d.label}
        <select class="select" data-k="${d.k}" ${d.disabled ? "disabled" : ""}>${d.options.map(([v, t]) =>
          `<option value="${v}" ${v === d.def ? "selected" : ""}>${t}</option>`).join("")}</select>
      </label>`;
    }
    return `<label class="field-inline">${d.label}
      <input type="${d.type}" data-k="${d.k}" value="${esc(d.def)}" placeholder="${esc(d.ph || "")}"
             ${d.min != null ? `min="${d.min}"` : ""} ${d.max != null ? `max="${d.max}"` : ""} ${d.step ? `step="${d.step}"` : ""} ${d.disabled ? "disabled" : ""}>
    </label>`;
  }).join("");
}

async function loadToolSources(keyword) {
  const box = $("#tools-source-list");
  try {
    const { items } = await api(`/api/toolbox/sources?keyword=${encodeURIComponent(keyword)}`);
    toolSources = items;
    const filtered = items.filter((v) => v.kind === sourceTab);
    if (!filtered.length) {
      box.innerHTML = `<div class="empty" style="padding:26px 10px"><p>${sourceTab === "video" ? "没有已完成视频" : "没有图集"}${keyword ? "（无匹配）" : ""}</p></div>`;
      return;
    }
    box.innerHTML = filtered.map((v) => `
      <div class="rp-item ${toolSrc?.video_id === v.id ? "sel" : ""}" data-vid="${v.id}">
        <img class="rp-thumb" src="${v.cover_url ? esc(coverSrc(v.cover_url, v.platform)) : (v.cover_path ? `/api/file?id=${v.id}&type=cover` : "")}"
             onerror="this.style.visibility='hidden'">
        <div class="rp-info">
          <div class="rp-title">${esc(v.title || "(无标题)")}</div>
          <div class="rp-meta">${PLATFORM_NAME[v.platform] || v.platform} · ${v.kind === "images" ? `${v.image_count} 张图` : esc(v.author || "")}</div>
        </div>
      </div>`).join("");
  } catch (e) {
    box.innerHTML = `<div class="empty" style="padding:26px 10px"><p>${esc(e.message)}</p></div>`;
  }
}

$$(".picker-tab").forEach((t) => t.addEventListener("click", () => {
  sourceTab = t.dataset.kind;
  $$(".picker-tab").forEach((x) => x.classList.toggle("active", x === t));
  // 自动切到匹配素材族的第一个工具
  if (sourceTab === "video" && !isVideoTool()) { toolKind = "transcode"; renderToolKinds(); renderToolParams(); }
  if (sourceTab === "images" && isVideoTool()) { toolKind = "img_convert"; renderToolKinds(); renderToolParams(); }
  loadToolSources($("#tools-search").value.trim());
}));

$("#tools-search").addEventListener("input", (e) => {
  clearTimeout($("#tools-search")._t);
  $("#tools-search")._t = setTimeout(() => loadToolSources(e.target.value.trim()), 350);
});

$("#tools-source-list").addEventListener("click", (e) => {
  const item = e.target.closest(".rp-item");
  if (!item) return;
  const vid = +item.dataset.vid;
  const v = toolSources.find((x) => x.id === vid);
  toolSrc = { video_id: vid, title: v?.title || `#${vid}` };
  $("#tools-upload-name").textContent = "";
  $$("#tools-source-list .rp-item").forEach((el) => el.classList.toggle("sel", el === item));
  updateToolSrcTip();
});

$("#btn-tools-upload").addEventListener("click", () => $("#tools-file").click());
$("#tools-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/toolbox/upload", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "上传失败");
    toolSrc = { upload: data.name, media: data.media, title: file.name };
    $("#tools-upload-name").textContent = `已上传：${file.name}`;
    $$("#tools-source-list .rp-item").forEach((el) => el.classList.remove("sel"));
    // 上传图片时自动切到图片工具
    if (data.media === "image" && isVideoTool()) {
      toolKind = "img_convert"; renderToolKinds(); renderToolParams();
      sourceTab = "images";
      $$(".picker-tab").forEach((t) => t.classList.toggle("active", t.dataset.kind === "images"));
    }
    updateToolSrcTip();
  } catch (err) { toast(err.message, "err"); }
  e.target.value = "";
});

function updateToolSrcTip() {
  $("#tool-src-tip").textContent = toolSrc ? `素材：${toolSrc.title}` : "未选择素材";
}

$("#btn-tool-run").addEventListener("click", async () => {
  if (!toolSrc) { toast("请先在左侧选择素材或上传文件", "warn"); return; }
  const params = {};
  $$("#tool-params [data-k]").forEach((el) => { if (!el.disabled) params[el.dataset.k] = el.value; });
  const body = { kind: toolKind, params };
  if (toolSrc.video_id) body.video_id = toolSrc.video_id;
  else body.upload = toolSrc.upload;
  const btn = $("#btn-tool-run");
  btn.disabled = true;
  try {
    await api("/api/toolbox/jobs", { method: "POST", body });
    toast("已加入处理队列");
    pollToolJobs();
  } catch (e) { toast(e.message, "err", 5000); }
  finally { btn.disabled = false; }
});

async function pollToolJobs() {
  try {
    const { items } = await api("/api/toolbox/jobs");
    const box = $("#tool-jobs");
    if (!items.length) {
      box.innerHTML = `<div class="empty"><p>还没有处理任务</p></div>`;
      return;
    }
    const KIND_NAME = toolsMeta?.tools || {};
    box.innerHTML = items.map((j) => `
      <div class="tool-job glass">
        <div class="tj-head">
          <span class="tj-kind">${KIND_NAME[j.kind] || j.kind}</span>
          ${statusBadge(j.status)}
          <span class="tj-time num">${fmtTime(j.created_at)}</span>
          <div class="tj-actions">
            ${j.status === "done" && j.has_output
              ? Array.from({ length: j.outputs }, (_, i) =>
                  `<a class="btn btn-ghost btn-sm" href="/api/toolbox/jobs/${j.id}/file?index=${i}" download>下载${j.outputs > 1 ? ` ${i + 1}` : ""}</a>`).join("")
              : ""}
            <button class="btn btn-ghost btn-sm" onclick="deleteToolJob(${j.id})">删除</button>
          </div>
        </div>
        ${j.status === "running" || j.status === "pending"
          ? `<div class="progress"><div class="progress-bar striped" style="width:${j.progress || 2}%"></div></div>` : ""}
        ${j.error ? `<div class="task-err">${esc(j.error)}</div>` : ""}
      </div>`).join("");
  } catch (e) { /* 忽略 */ }
}

window.deleteToolJob = async (id) => {
  const delFiles = confirm("删除该任务记录？\n「确定」同时删除产物文件，「取消」仅删记录");
  try {
    await api(`/api/toolbox/jobs/${id}?keep_files=${!delFiles}`, { method: "DELETE" });
    pollToolJobs();
  } catch (e) { toast(e.message, "err"); }
};

/* ---------- 通知中心 ---------- */

let notifFirst = true;
let lastUnread = 0;
let notifCache = [];

async function pollNotifications(first = false) {
  try {
    const d = await api("/api/notifications?limit=30");
    notifCache = d.items;
    const badge = $("#bell-badge");
    badge.hidden = !d.unread;
    badge.textContent = d.unread > 99 ? "99+" : d.unread;
    if ((first || notifFirst) === false && d.unread > lastUnread && d.items.length) {
      notifyDesktop(d.items[0]);
      if (!$("#notif-drawer").hidden) renderNotifList();
    }
    notifFirst = false;
    lastUnread = d.unread;
  } catch (e) { /* 忽略 */ }
}

function notifyDesktop(n) {
  toast(n.title, n.kind === "task" ? "err" : "ok");
  if (window.Notification && Notification.permission === "granted") {
    try { new Notification(`PacDown · ${n.title}`, { body: n.body || "" }); } catch (e) { /* 忽略 */ }
  }
}

function renderNotifList() {
  const box = $("#notif-list");
  if (!notifCache.length) {
    box.innerHTML = `<div class="empty" style="padding:30px 10px"><p>暂无通知</p></div>`;
    return;
  }
  const icons = { subscription: "🔔", task: "⚠️", system: "ℹ️" };
  box.innerHTML = notifCache.map((n) => `
    <div class="notif-item ${n.read ? "" : "unread"}">
      <span class="notif-icon">${icons[n.kind] || icons.system}</span>
      <div class="notif-body">
        <div class="notif-title">${esc(n.title)}</div>
        ${n.body ? `<div class="notif-text">${esc(n.body)}</div>` : ""}
        <div class="notif-time num">${esc(n.created_at)}</div>
      </div>
    </div>`).join("");
}

$("#bell").addEventListener("click", async () => {
  const drawer = $("#notif-drawer");
  const opening = drawer.hidden;
  drawer.hidden = !opening;
  $("#notif-mask").hidden = !opening;
  if (opening) {
    renderNotifList();
    if (window.Notification && Notification.permission === "default") {
      try { await Notification.requestPermission(); } catch (e) { /* 忽略 */ }
    }
    if (lastUnread) {
      await api("/api/notifications/read", { method: "POST", body: {} });
      lastUnread = 0;
      $("#bell-badge").hidden = true;
      notifCache = notifCache.map((n) => ({ ...n, read: 1 }));
      renderNotifList();
    }
  }
});
$("#notif-mask").addEventListener("click", () => {
  $("#notif-drawer").hidden = true;
  $("#notif-mask").hidden = true;
});
$("#notif-read-all").addEventListener("click", async () => {
  await api("/api/notifications/read", { method: "POST", body: {} });
  lastUnread = 0;
  $("#bell-badge").hidden = true;
  pollNotifications();
});

/* ---------- 管理统计面板（仅 admin） ---------- */

async function loadStatsPage() {
  loadAdminStats();
}

async function loadAdminStats() {
  try {
    const [visits, dls] = await Promise.all([
      api("/api/stats/visits"),
      api("/api/stats/downloads"),
    ]);
    $("#stats-unlock").hidden = true;
    $("#stats-dashboard").hidden = false;
    renderAdminStats(visits, dls);
  } catch (e) { toast(e.message, "err"); }
}

const DEVICE_NAME = { mobile: "手机", pc: "电脑" };
const OS_NAME = { windows: "Windows", android: "安卓", ios: "iOS", macos: "macOS", linux: "Linux", other: "其他" };
const BROWSER_NAME = { wechat: "微信内置", qq: "QQ", chrome: "Chrome", edge: "Edge", safari: "Safari", firefox: "Firefox", douyin: "抖音内嵌", other: "其他" };
  const KIND_CN = { mp3: "提取MP3", transcode: "转码", compress: "压缩", trim: "剪辑", gif: "GIF", watermark: "水印", frame: "截帧", danmaku2ass: "弹幕转字幕", burn_sub: "字幕压制", img_convert: "图片转换", img_join: "拼接长图", img_zip: "图集打包" };

function maskIp(ip) {
  if (!ip) return "";
  if (ip.includes(":")) return ip.split(":").slice(0, 3).join(":") + ":*";
  const p = ip.split(".");
  return p.length === 4 ? `${p[0]}.${p[1]}.*.*` : ip;
}

function renderAdminStats(v, d) {
  // 总览卡片
  const pvPerUv = v.total_uv ? (v.total_pv / v.total_uv).toFixed(1) : "0";
  const cards = [
    ["累计访问", v.total_pv, `${v.total_uv} 位访客 · 人均 ${pvPerUv} 次`],
    ["今日访问", v.today_pv, `${v.today_uv} 位访客 · 昨日 ${v.yesterday_pv}`],
    ["累计下载", d.status_counts.done || 0, `成功率 ${d.success_rate}%`],
    ["失败任务", d.status_counts.failed || 0, "详情见下方列表"],
  ];
  $("#admin-cards").innerHTML = cards.map(([label, val, sub], i) => `
    <div class="stat-card glass"><div class="stat-label">${label}</div>
      <div class="stat-value num">${val}</div><div class="stat-sub">${esc(sub)}</div></div>`).join("");

  // 图表
  $("#visit-trend-total").textContent = `累计 ${v.total_pv} 次`;
  $("#chart-visits").innerHTML = areaChart(v.by_day.map((x) => ({ k: x.d, n: x.pv })), "#7c6cf5");
  $("#chart-hours").innerHTML = barChart(v.by_hour, "#3fd0e0");
  renderDist("#chart-devices", [
    ...v.by_device.map((x) => [DEVICE_NAME[x.k] || x.k, x.n, "#7c6cf5"]),
    ...v.by_os.slice(0, 4).map((x) => [OS_NAME[x.k] || x.k, x.n, "#3b82f6"]),
    ...v.by_browser.slice(0, 4).map((x) => [BROWSER_NAME[x.k] || x.k, x.n, "#3fd0e0"]),
  ]);
  renderDist("#chart-referers", v.by_referer.map((x) => [x.k, x.n, "#f0b945"]));

  const dlDays = d.by_day.map((x) => ({ k: x.d, n: x.n }));
  $("#dl-trend-total").textContent = `近30天 ${dlDays.reduce((s, x) => s + x.n, 0)} 个`;
  $("#chart-downloads").innerHTML = areaChart(dlDays, "#3ecf8e");
  const PLAT_COLOR = { bilibili: "#fb7299", douyin: "#fe4d6f", kuaishou: "#ff9b3d", xiaohongshu: "#ff5170", direct: "#38bdf8", generic: "#8a95f8" };
  $("#chart-platform").innerHTML = donut(d.by_platform.map((x) => ({
    k: PLATFORM_NAME[x.platform] || x.platform, n: x.n, color: PLAT_COLOR[x.platform] || "#8a95f8",
  })));
  renderDist("#chart-authors", d.top_authors.map((x) => [x.k, x.n, "#7c6cf5"]));
  renderDist("#chart-tags", d.top_tags.map((x) => ["#" + x.k, x.n, "#f0b945"]));
  renderDist("#chart-subs", d.top_subs.map((x) => [`${x.uploader_name}（${PLATFORM_NAME[x.platform] || x.platform}）`, x.new_count, "#3ecf8e"]));
  renderDist("#chart-tools", d.tool_usage.map((x) => [KIND_CN[x.k] || x.k, x.n, "#3fd0e0"]));

  // 明细表
  $("#visit-recent-count").textContent = `最近 ${v.recent.length} 条`;
  $("#table-visits").innerHTML = `<thead><tr><th>时间</th><th>IP</th><th>设备</th><th>来源</th><th>浏览器</th></tr></thead><tbody>` +
    v.recent.map((r) => `<tr>
      <td class="num">${esc(r.created_at)}</td>
      <td class="num">${esc(maskIp(r.ip))}</td>
      <td>${DEVICE_NAME[r.device] || r.device} · ${OS_NAME[r.os] || r.os}</td>
      <td>${esc(r.referer || "直接访问")}</td>
      <td>${BROWSER_NAME[r.browser] || r.browser}</td>
    </tr>`).join("") + `</tbody>`;
  $("#table-failed").innerHTML = d.recent_failed.length
    ? `<thead><tr><th>时间</th><th>标题</th><th>错误</th></tr></thead><tbody>` +
      d.recent_failed.map((r) => `<tr>
        <td class="num">${esc(r.created_at)}</td>
        <td>${esc((r.title || "").slice(0, 30))}</td>
        <td class="err-cell" title="${esc(r.error || "")}">${esc((r.error || "").slice(0, 60))}</td>
      </tr>`).join("") + `</tbody>`
    : `<tbody><tr><td style="text-align:center;color:var(--text-3);padding:18px">暂无失败任务 🎉</td></tr></tbody>`;
}

/* 纯 SVG 图表（无依赖） */
function areaChart(data, color) {
  if (!data.length) return `<div class="chart-empty">暂无数据</div>`;
  const W = 640, H = 168, PAD_X = 12, PAD_T = 26, PAD_B = 18;
  const days = 30;
  const daysMap = new Map(data.map((x) => [x.k, x.n]));
  const keys = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now - i * 86400000);
    keys.push(d.toISOString().slice(0, 10));
  }
  const series = keys.map((k) => daysMap.get(k) || 0);
  const max = Math.max(...series, 1);
  const x = (i) => PAD_X + (i / (days - 1)) * (W - PAD_X * 2);
  const y = (n) => H - PAD_B - (n / max) * (H - PAD_T - PAD_B);
  const pts = series.map((n, i) => `${x(i).toFixed(1)},${y(n).toFixed(1)}`);
  const line = `M${pts.join("L")}`;
  const area = `${line}L${x(days - 1).toFixed(1)},${H - PAD_B}L${PAD_X},${H - PAD_B}Z`;
  const labels = [0, Math.floor(days / 2), days - 1].map((i) =>
    `<text x="${x(i)}" y="${H - 4}" text-anchor="${i === 0 ? "start" : i === days - 1 ? "end" : "middle"}" class="axis">${keys[i].slice(5)}</text>`).join("");
  // 峰值标注：文字位置向内收，防止贴边被裁
  const peak = series.indexOf(Math.max(...series));
  const peakX = Math.min(Math.max(x(peak), 26), W - 30);
  const peakAnchor = x(peak) > W - 60 ? "end" : x(peak) < 60 ? "start" : "middle";
  const gridMid = y(max / 2), gridTop = y(max);
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="chart">
    <line x1="${PAD_X}" x2="${W - PAD_X}" y1="${gridTop}" y2="${gridTop}" class="grid"/>
    <line x1="${PAD_X}" x2="${W - PAD_X}" y1="${gridMid}" y2="${gridMid}" class="grid"/>
    <line x1="${PAD_X}" x2="${W - PAD_X}" y1="${H - PAD_B}" y2="${H - PAD_B}" class="grid base"/>
    <path d="${area}" fill="${color}" opacity="0.15"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="${x(peak)}" cy="${y(series[peak])}" r="3.5" fill="${color}" stroke="var(--bg)" stroke-width="1.5"/>
    <text x="${peakX}" y="${y(series[peak]) - 9}" text-anchor="${peakAnchor}" class="axis peak">${series[peak]}</text>
    ${labels}</svg>`;
}

function barChart(data, color) {
  const counts = Array.from({ length: 24 }, (_, h) => ({ h, n: 0 }));
  data.forEach((x) => { if (counts[x.h]) counts[x.h].n = x.n; });
  const max = Math.max(...counts.map((c) => c.n), 1);
  return `<div class="hours-grid">` + counts.map((c) => `
    <div class="hour-col" title="${c.h}:00 · ${c.n} 次">
      <div class="hour-bar" style="height:${Math.max(4, c.n / max * 100)}%;background:${c.n ? color : "var(--surface-2)"}"></div>
      <span class="hour-label num">${c.h}</span>
    </div>`).join("") + `</div>`;
}

function donut(items) {
  const total = items.reduce((s, x) => s + x.n, 0);
  if (!total) return `<div class="chart-empty">暂无数据</div>`;
  const R = 15.915;
  let acc = 0;
  const segs = items.map((x) => {
    const pct = x.n / total * 100;
    const seg = `<circle r="${R}" cx="21" cy="21" fill="transparent" stroke="${x.color}"
      stroke-width="5.5" stroke-dasharray="${pct.toFixed(2)} ${(100 - pct).toFixed(2)}"
      stroke-dashoffset="${(25 - acc).toFixed(2)}"/>`;
    acc += pct;
    return seg;
  }).join("");
  const legend = items.slice(0, 6).map((x) => `
    <div class="legend-row"><i style="background:${x.color}"></i>
      <span>${esc(x.k)}</span><b class="num">${x.n}</b>
      <span class="num pct">${(x.n / total * 100).toFixed(0)}%</span></div>`).join("");
  return `<div class="donut-flex">
    <svg viewBox="0 0 42 42" class="donut-svg">${segs}
      <text x="21" y="20" text-anchor="middle" class="donut-num">${total}</text>
      <text x="21" y="26" text-anchor="middle" class="donut-sub">总计</text></svg>
    <div class="legend">${legend}</div></div>`;
}

function renderDist(sel, rows) {
  const total = rows.reduce((s, [, n]) => s + n, 0) || 1;
  $(sel).innerHTML = rows.length ? rows.map(([k, n, color]) => `
    <div class="dist-row">
      <span class="dist-k">${esc(String(k))}</span>
      <div class="dist-bar"><i style="width:${(n / total * 100).toFixed(1)}%;background:${color}"></i></div>
      <b class="num">${n}</b>
    </div>`).join("") : `<div class="chart-empty">暂无数据</div>`;
}

/* ---------- 自动规则 ---------- */

const MATCH_DESC = {
  all: () => "全部下载",
  platform: (v) => `平台 = ${PLATFORM_NAME[v] || v}`,
  subscription: (v) => `订阅 #${v}`,
  tag: (v) => `标签包含 #${v}`,
};
const ACTION_DESC = { mp3: "提取MP3", transcode: "转码H.264", compress: "压缩", gif: "生成GIF" };

async function loadRules() {
  const box = $("#rule-list");
  try {
    const { items } = await api("/api/rules");
    if (!items.length) {
      box.innerHTML = `<p class="hint">还没有规则：如「抖音下载完自动提取 MP3」「某订阅的视频自动压缩」</p>`;
      return;
    }
    box.innerHTML = items.map((r) => {
      let actions = [];
      try { actions = JSON.parse(r.actions || "[]"); } catch (e) { /* 忽略 */ }
      return `<div class="rule-card glass ${r.enabled ? "" : "off"}">
        <div class="rule-info">
          <div class="rule-name">${esc(r.name)}</div>
          <div class="rule-meta">
            <span class="h-tag">${esc((MATCH_DESC[r.match_type] || ((v) => v))(r.match_value))}</span>
            ${actions.map((a) => `<span class="h-tag act">${ACTION_DESC[a.kind] || a.kind}</span>`).join("")}
            <span class="num">已触发 ${r.run_count} 次</span>
          </div>
        </div>
        <div class="rule-ops">
          <button class="btn btn-ghost btn-sm" onclick="toggleRule(${r.id}, ${r.enabled ? 0 : 1})">${r.enabled ? "停用" : "启用"}</button>
          <button class="btn btn-danger btn-sm" onclick="removeRule(${r.id})">删除</button>
        </div>
      </div>`;
    }).join("");
  } catch (e) {
    box.innerHTML = `<p class="hint">${esc(e.message)}</p>`;
  }
}

$("#rule-match-type").addEventListener("change", (e) => {
  $("#rule-match-value").hidden = e.target.value === "all";
});

$("#btn-add-rule").addEventListener("click", async () => {
  const actions = $$(".rule-act:checked").map((el) => ({ kind: el.value, params: {} }));
  const body = {
    name: $("#rule-name").value.trim(),
    match_type: $("#rule-match-type").value,
    match_value: $("#rule-match-value").value.trim(),
    actions,
  };
  try {
    await api("/api/rules", { method: "POST", body });
    toast("规则已添加，下次下载完成时生效");
    $("#rule-name").value = "";
    $$(".rule-act:checked").forEach((el) => { el.checked = false; });
    loadRules();
  } catch (e) { toast(e.message, "err"); }
});

window.toggleRule = async (id, enabled) => {
  try {
    await api(`/api/rules/${id}`, { method: "PATCH", body: { enabled: !!enabled } });
    loadRules();
  } catch (e) { toast(e.message, "err"); }
};

window.removeRule = async (id) => {
  if (!confirm("删除该规则？")) return;
  try { await api(`/api/rules/${id}`, { method: "DELETE" }); loadRules(); }
  catch (e) { toast(e.message, "err"); }
};

/* ---------- 赞赏 ---------- */

window.closeDonate = () => { $("#donate-modal").hidden = true; };
$("#donate-btn").addEventListener("click", () => { $("#donate-modal").hidden = false; });
$("#donate-modal").addEventListener("click", (e) => {
  if (e.target.id === "donate-modal") closeDonate();
});

/* ---------- 教程页 ---------- */

window.goDownloadDemo = () => {
  $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.page === "download"));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-download"));
  const input = $("#url-input");
  if (!input.value.trim()) {
    input.value = "https://www.bilibili.com/video/BV1GJ411x7h7";
    toast("已填入一条示例链接，点「解析链接」试试");
  }
  input.focus();
};

/* ---------- 设置页 ---------- */

const COOKIE_FIELDS = [
  ["set-bili-cookie", "bilibili_cookie"],
  ["set-dy-cookie", "douyin_cookie"],
  ["set-ks-cookie", "kuaishou_cookie"],
  ["set-xhs-cookie", "xiaohongshu_cookie"],
  ["set-ai-key", "ai_api_key"],
];

async function loadSettings() {
  try {
    const cfg = await api("/api/config");
    loadRules();
    // 账号区
    $("#account-name").textContent = me?.username || "";
    $("#account-role-tag").textContent = isAdmin() ? "管理员" : "普通用户";
    $("#reg-toggle-wrap").hidden = !isAdmin();
    $("#set-allow-register").checked = cfg.allow_register !== false;
    $("#set-download-dir").value = cfg.download_dir || "";
    $("#set-concurrency").value = cfg.max_concurrency;
    $("#set-quality").value = cfg.default_quality;
    $("#set-name-template").value = cfg.name_template || "{date}_{title}";
    $("#set-sub-interval").value = cfg.subscription_interval;
    $("#set-speed-limit").value = cfg.speed_limit_mb || 0;
    $("#set-clean-enabled").checked = !!cfg.auto_clean_enabled;
    $("#set-clean-days").value = cfg.auto_clean_days || 30;
    $("#set-clean-fav").checked = cfg.auto_clean_keep_favorite !== false;
    $("#set-proxy").value = cfg.http_proxy || "";
    $("#set-ai-url").value = cfg.ai_base_url || "";
    $("#set-ai-model").value = cfg.ai_model || "";
    COOKIE_FIELDS.forEach(([id, key]) => {
      const input = $(`#${id}`);
      input.value = "";
      input.placeholder = cfg[key] === "__SET__" ? "已配置（留空保持不变）" : input.placeholder;
      input.dataset.set = cfg[key] === "__SET__" ? "1" : "";
    });
  } catch (e) { toast(e.message, "err"); }
}

$("#btn-save-settings").addEventListener("click", async () => {
  const body = {
    download_dir: $("#set-download-dir").value.trim(),
    max_concurrency: +$("#set-concurrency").value || 3,
    default_quality: $("#set-quality").value.trim() || "best",
    name_template: $("#set-name-template").value.trim() || "{date}_{title}",
    subscription_interval: +$("#set-sub-interval").value || 30,
    speed_limit_mb: +$("#set-speed-limit").value || 0,
    auto_clean_enabled: $("#set-clean-enabled").checked,
    auto_clean_days: +$("#set-clean-days").value || 30,
    auto_clean_keep_favorite: $("#set-clean-fav").checked,
    allow_register: $("#set-allow-register").checked,
    http_proxy: $("#set-proxy").value.trim(),
    ai_base_url: $("#set-ai-url").value.trim(),
    ai_model: $("#set-ai-model").value.trim(),
  };
  COOKIE_FIELDS.forEach(([id, key]) => {
    const input = $(`#${id}`);
    body[key] = input.value.trim() ? input.value.trim() : (input.dataset.set ? "__KEEP__" : "");
  });
  try {
    await api("/api/config", { method: "POST", body });
    const tip = $("#settings-saved");
    tip.textContent = "已保存 ✓（部分设置对新任务生效）";
    tip.classList.add("show");
    setTimeout(() => tip.classList.remove("show"), 2500);
    loadDirs();
  } catch (e) { toast(e.message, "err"); }
});

/* ---------- 启动 ---------- */

async function boot() {
  if (!(await fetchMe())) { showAuth(); return; }
  hideAuth();
  loadDirs();
  updateViewBtn();
  applyRoleNav();
  // 下载页快捷选项跟随设置里的默认值（admin 可读全局配置，普通用户默认关闭）
  if (isAdmin()) {
    api("/api/config").then((cfg) => {
      $("#opt-audio").checked = !!cfg.extract_audio;
      $("#opt-danmaku").checked = !!cfg.download_danmaku;
      $("#opt-subtitle").checked = !!cfg.download_subtitle;
    }).catch(() => { /* 忽略 */ });
  }
  // 服务器托管了 Windows 客户端时显示下载入口
  api("/api/app/status").then((s) => {
    if (s.available) {
      const a = $("#app-dl-link");
      a.hidden = false;
      a.title += `（v${s.updated_at ? s.updated_at.slice(0, 10) : ""} · ${fmtSize(s.size)}）`;
    }
  }).catch(() => { /* 忽略 */ });
  // PWA：HTTPS 或 localhost 下注册 Service Worker（挂根路径获得全站作用域）
  if ("serviceWorker" in navigator &&
      (location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname))) {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* 忽略 */ });
  }
  pollTasks();
  pollNotifications(true);
  setInterval(() => {
    if (!document.hidden) {
      pollTasks();
      pollNotifications();
      if ($("#page-tools").classList.contains("active")) pollToolJobs();
    }
  }, 1500);
  loadStats(); // 预热顶部统计（不预热列表，避免与切页加载竞态）
}

boot();
