/* PacDown 前端逻辑：单页应用，无构建依赖 */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PLATFORM_NAME = {
  bilibili: "哔哩哔哩", douyin: "抖音", kuaishou: "快手",
  xiaohongshu: "小红书", generic: "通用",
};
const STATUS_TEXT = {
  pending: "排队中", parsing: "解析中", downloading: "下载中",
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
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `请求失败 (${r.status})`);
  return data;
}

function toast(msg, type = "ok", ms = 3400) {
  const icons = {
    ok: '<svg class="t-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12.5l5 5L20 7"/></svg>',
    err: '<svg class="t-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    warn: '<svg class="t-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 8v5m0 3h.01M10.3 3.8L1.8 18.3A2 2 0 0 0 3.5 21h17a2 2 0 0 0 1.7-2.7L13.7 3.8a2 2 0 0 0-3.4 0z"/></svg>',
  };
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `${icons[type] || icons.ok}<div>${esc(msg)}</div>`;
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
    parseItems = results.map((r) => ({ ...r, checked: r.ok }));
    renderParseResults();
    const fails = results.filter((r) => !r.ok).length;
    toast(`解析完成：${results.length - fails} 个成功${fails ? `，${fails} 个失败` : ""}`, fails ? "warn" : "ok");
  } catch (e) { toast(e.message, "err"); }
  finally {
    btn.disabled = false;
    btn.innerHTML = `解析链接`;
  }
});

function renderParseResults() {
  const box = $("#parse-results");
  box.hidden = false;
  $("#check-all").checked = parseItems.every((p) => p.checked);
  $("#parse-list").innerHTML = parseItems.map((p, i) => {
    if (!p.ok) {
      return `<div class="parse-item error">
        <input type="checkbox" class="pcheck" data-i="${i}" ${p.checked ? "" : "disabled"}>
        <div class="parse-info">
          <div class="parse-title" style="color:var(--text-3)">${esc(p.url.slice(0, 70))}</div>
          <div class="parse-err">解析失败：${esc(p.error)}</div>
        </div>
        ${statusBadge("failed")}
      </div>`;
    }
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
      </div>
    </div>`;
  }).join("");
}

function fmtCount(n) {
  n = +n;
  if (!isFinite(n)) return "";
  if (n >= 10000) return (n / 10000).toFixed(1) + "万";
  return String(n);
}

$("#parse-list").addEventListener("change", (e) => {
  const cb = e.target.closest(".pcheck");
  if (cb) {
    parseItems[+cb.dataset.i].checked = cb.checked;
    $("#check-all").checked = parseItems.filter((p) => p.ok).every((p) => p.checked);
  }
});
$("#check-all").addEventListener("change", (e) => {
  parseItems.forEach((p) => { if (p.ok) p.checked = e.target.checked; });
  renderParseResults();
});

$("#btn-download-selected").addEventListener("click", async () => {
  const text = parseItems.filter((p) => p.ok && p.checked).map((p) => p.url).join("\n");
  if (!text) { toast("请至少勾选一个视频", "warn"); return; }
  const options = {
    quality: "best",
    extract_audio: $("#opt-audio").checked,
    download_danmaku: $("#opt-danmaku").checked,
    fetch_comments: $("#opt-comments").checked,
  };
  try {
    const { results } = await api("/api/download", { method: "POST", body: { text, options } });
    const created = results.filter((r) => r.status === "created").length;
    const dup = results.filter((r) => r.status === "duplicate").length;
    const failed = results.filter((r) => r.status === "failed").length;
    if (created) toast(`已加入下载队列：${created} 个`);
    if (dup) toast(`${dup} 个视频已下载过，可在历史中查看`, "warn");
    if (failed) toast(`${failed} 个创建失败`, "err");
    $("#parse-results").hidden = true;
    pollTasks(true);
  } catch (e) { toast(e.message, "err"); }
});

/* ---------- 任务轮询 ---------- */

let lastTaskIds = new Set();

function taskSummaryHTML(tasks) {
  if (!tasks.length) return "";
  const active = tasks.filter((t) => ["downloading", "processing", "parsing"].includes(t.status)).length;
  const done = tasks.filter((t) => t.status === "done").length;
  const failed = tasks.filter((t) => t.status === "failed").length;
  return `<span class="num">${active} 进行中 · ${done} 完成${failed ? ` · <span style="color:var(--err)">${failed} 失败</span>` : ""}</span>`;
}

async function pollTasks(force = false) {
  try {
    const { tasks } = await api("/api/tasks");
    const hasActive = tasks.length > 0;
    const head = $("#tasks-head");
    head.hidden = !hasActive;
    if (!hasActive) {
      $("#task-list").innerHTML = "";
      if (lastTaskIds.size) { // 队列刚清空：刷新历史统计
        lastTaskIds.clear();
        if ($("#page-history").classList.contains("active")) loadHistory();
      }
      return;
    }
    $("#task-summary").innerHTML = taskSummaryHTML(tasks);
    $("#task-list").innerHTML = tasks.map((t) => {
      const pct = t.status === "pending" ? 0 : t.progress || 0;
      const barCls = t.status === "pending" ? "indeterminate"
        : t.status === "downloading" ? "striped"
        : t.status === "processing" ? "striped" : "";
      const progCls = t.status === "done" ? "ok" : t.status === "failed" ? "err" : "";
      return `<div class="task-item">
        <div class="task-top">
          ${badge(t.platform)}
          <span class="task-title">${esc(t.title || t.file_path || "…")}</span>
          ${t.author ? `<span class="task-author">${esc(t.author)}</span>` : ""}
          ${t.status === "downloading" && t.speed ? `<span class="task-speed num">${esc(t.speed)}</span>` : ""}
          ${statusBadge(t.status)}
          ${t.status === "failed" ? `<button class="btn btn-ghost btn-sm" onclick="retryTask(${t.id})">重试</button>` : ""}
        </div>
        <div class="progress ${progCls}"><div class="progress-bar ${barCls}" style="width:${t.status === "failed" ? 100 : pct}%"></div></div>
        ${t.error ? `<div class="task-err">${esc(t.error)}</div>` : ""}
      </div>`;
    }).join("");
    lastTaskIds = new Set(tasks.map((t) => t.id));
  } catch (e) { console.error(e); }
}

window.retryTask = async (id) => {
  try {
    await api(`/api/tasks/${id}/retry`, { method: "POST" });
    toast("已重新排队");
    pollTasks();
  } catch (e) { toast(e.message, "err"); }
};

/* ---------- 历史页 ---------- */

let historyState = { page: 1, platform: "", status: "", keyword: "" };

async function loadHistory() {
  const grid = $("#history-grid");
  grid.innerHTML = Array(6).fill(`<div class="skeleton"><div class="sk-cover"></div><div class="sk-line" style="width:80%"></div><div class="sk-line" style="width:55%"></div></div>`).join("");
  loadStats();
  const s = historyState;
  const params = new URLSearchParams({
    page: s.page, size: 24, platform: s.platform, status: s.status, keyword: s.keyword,
  });
  try {
    const { items, total, page, size } = await api(`/api/history?${params}`);
    renderHistory(items, total, page, size);
  } catch (e) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><p>${esc(e.message)}</p></div>`;
  }
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
    $("#stat-row").innerHTML = `
      <div class="stat-card glass"><div class="stat-label">${icons.total}已保存视频</div>
        <div class="stat-value num">${st.total}</div><div class="stat-sub">${platChips}</div></div>
      <div class="stat-card glass"><div class="stat-label">${icons.size}占用空间</div>
        <div class="stat-value num">${st.total_size >= 1024 ** 3 ? (st.total_size / 1024 ** 3).toFixed(2) + " GB" : (st.total_size / 1024 ** 2).toFixed(1) + " MB"}</div><div class="stat-sub">含视频与附件</div></div>
      <div class="stat-card glass"><div class="stat-label">${icons.today}今日新增</div>
        <div class="stat-value num">${st.today}</div><div class="stat-sub">最近 24 小时内完成</div></div>
      <div class="stat-card glass"><div class="stat-label">${icons.failed}失败任务</div>
        <div class="stat-value num" style="${st.failed ? "color:var(--err)" : ""}">${st.failed}</div><div class="stat-sub">可在任务列表重试</div></div>`;
  } catch (e) { console.error(e); }
}

function renderHistory(items, total, page, size) {
  const grid = $("#history-grid");
  if (!items.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 15l5-5 4 4 3-3 6 6"/></svg>
      <p>还没有下载记录<br>回到「下载」页，粘贴一个链接开始</p></div>`;
    $("#pagination").innerHTML = "";
    return;
  }
  grid.innerHTML = items.map((v, i) => {
    const imageCount = JSON.parse(v.images || "[]").length;
    // 封面：优先平台封面，图集无封面时回退到本地第一张图
    const coverSrcVal = v.cover_url
      ? coverSrc(v.cover_url, v.platform)
      : (imageCount ? `/api/file?id=${v.id}&type=image&index=0` : "");
    const cover = coverSrcVal
      ? `<img src="${esc(coverSrcVal)}" loading="lazy" alt="" onerror="this.style.display='none'">` : "";
    const durOrCount = imageCount
      ? `<span class="h-dur" style="display:inline-flex;align-items:center;gap:3px">
           <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.8"/><path d="M21 15.5l-5-5L5 21"/></svg>${imageCount} 图</span>`
      : (v.duration ? `<span class="h-dur">${fmtDuration(v.duration)}</span>` : "");
    return `<div class="h-card" style="animation-delay:${Math.min(i * 30, 240)}ms" onclick="openDetail(${v.id})">
      <div class="h-cover">
        ${cover}
        ${durOrCount}
        <span class="h-status">${statusBadge(v.status)}</span>
      </div>
      <div class="h-body">
        <div class="h-title">${esc(v.title || "(无标题)")}</div>
        <div class="h-meta">${badge(v.platform)}<span class="author">${esc(v.author || "")}</span>
          <span class="size">${fmtSize(v.file_size)}</span></div>
      </div>
    </div>`;
  }).join("");
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
$("#btn-export").addEventListener("click", () => {
  window.location.href = "/api/history/export";
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
      <h3>${esc(v.title || "(无标题)")}</h3>
      <div class="h-meta" style="margin-top:8px">${badge(v.platform)}<span>${esc(v.author)}</span>${statusBadge(v.status)}</div>
      <div class="modal-grid">
        ${kv("发布时间", v.publish_time)}
        ${kv("时长", fmtDuration(v.duration))}
        ${kv("清晰度", v.quality === "best" ? "最高可用" : v.quality)}
        ${kv("文件大小", fmtSize(v.file_size))}
        ${kv("下载时间", v.downloaded_at)}
        ${v.danmaku_path ? kv("弹幕", "已保存 XML") : ""}
        ${images.length ? kv("图集", images.length + " 张") : ""}
        ${stats}
      </div>
      ${v.file_path ? `<div class="kv" style="margin-top:10px"><b>文件位置</b><span style="font-size:12px">${esc(v.file_path)}</span></div>` : ""}
      ${descBlock}
      ${commentsBlock}
      ${v.error ? `<div class="task-err" style="margin-top:10px">${esc(v.error)}</div>` : ""}
      <div class="modal-actions">
        ${v.file_path && v.status === "done" ? `<button class="btn btn-primary btn-sm" onclick="openFolder(${v.id})">打开所在目录</button>` : ""}
        <button class="btn btn-ghost btn-sm" onclick="redownload(${v.id}, '${esc(v.source_url)}')">重新下载</button>
        <button class="btn btn-danger btn-sm" onclick="deleteVideo(${v.id})">删除记录</button>
      </div>
      <details class="modal-raw"><summary>查看原始数据 JSON</summary>
        <pre>${esc(JSON.stringify(v.raw, null, 2))}</pre></details>
    `;
    $("#modal").hidden = false;
  } catch (e) { toast(e.message, "err"); }
};

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
    else closeModal();
  }
  if (!$("#lightbox").hidden) {
    if (e.key === "ArrowLeft") lbMove(-1);
    if (e.key === "ArrowRight") lbMove(1);
  }
});

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
  if (!confirm("删除这条记录？文件会保留在磁盘上。")) return;
  try {
    await api(`/api/history/${id}?keep_files=true`, { method: "DELETE" });
    toast("记录已删除");
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
    box.innerHTML = items.map((s) => `
      <div class="sub-card glass ${s.enabled ? "" : "sub-paused"}">
        <img class="sub-avatar" src="${esc(coverSrc(s.avatar_url, s.platform))}" alt=""
             onerror="this.style.visibility='hidden'">
        <div class="sub-info">
          <div class="sub-name">${esc(s.uploader_name)} ${badge(s.platform)}</div>
          <div class="sub-meta">上次检查：${fmtTime(s.last_checked) || "从未"} · 已抓取 ${s.new_count} 个</div>
          ${s.last_error ? `<div class="sub-err" title="${esc(s.last_error)}">${esc(s.last_error)}</div>` : ""}
        </div>
        <div class="sub-actions">
          <button class="btn btn-ghost btn-sm" onclick="checkSub(${s.id})">检查</button>
          <button class="btn btn-ghost btn-sm" onclick="toggleSub(${s.id}, ${s.enabled ? 0 : 1})">${s.enabled ? "暂停" : "恢复"}</button>
          <button class="btn btn-danger btn-sm" onclick="removeSub(${s.id})">删除</button>
        </div>
      </div>`).join("");
  } catch (e) { box.innerHTML = `<div class="empty" style="grid-column:1/-1"><p>${esc(e.message)}</p></div>`; }
}

$("#btn-add-sub").addEventListener("click", async () => {
  const url = $("#sub-url").value.trim();
  if (!url) { toast("请输入博主主页链接", "warn"); return; }
  try {
    await api("/api/subscriptions", { method: "POST", body: { url } });
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

/* ---------- 设置页 ---------- */

const COOKIE_FIELDS = [
  ["set-bili-cookie", "bilibili_cookie"],
  ["set-dy-cookie", "douyin_cookie"],
  ["set-ks-cookie", "kuaishou_cookie"],
  ["set-xhs-cookie", "xiaohongshu_cookie"],
];

async function loadSettings() {
  try {
    const cfg = await api("/api/config");
    $("#set-download-dir").value = cfg.download_dir || "";
    $("#set-concurrency").value = cfg.max_concurrency;
    $("#set-quality").value = cfg.default_quality;
    $("#set-sub-interval").value = cfg.subscription_interval;
    $("#set-proxy").value = cfg.http_proxy || "";
    COOKIE_FIELDS.forEach(([id, key]) => {
      const input = $(`#${id}`);
      input.value = "";
      input.placeholder = cfg[key] === "__SET__" ? "已配置（留空保持不变）" : input.dataset.ph || input.placeholder;
      input.dataset.set = cfg[key] === "__SET__" ? "1" : "";
    });
  } catch (e) { toast(e.message, "err"); }
}

$("#btn-save-settings").addEventListener("click", async () => {
  const body = {
    download_dir: $("#set-download-dir").value.trim(),
    max_concurrency: +$("#set-concurrency").value || 3,
    default_quality: $("#set-quality").value.trim() || "best",
    subscription_interval: +$("#set-sub-interval").value || 30,
    http_proxy: $("#set-proxy").value.trim(),
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
  loadDirs();
  pollTasks();
  setInterval(() => {
    if (!document.hidden) pollTasks();
  }, 1500);
  loadHistory(); // 预热统计
}

boot();
