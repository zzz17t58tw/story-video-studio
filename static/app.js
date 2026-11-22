/* Story Video Studio — M1 前端外殼
   規則：前端不直接寫檔，一律走後端 API；欄位變更 1.5s debounce 自動 PUT。 */
"use strict";

const API = "";
const SAVE_DEBOUNCE_MS = 1500;

const state = {
  projects: [],
  slug: null,
  project: null,      // 目前開啟的 project.json（記憶體副本）
  view: "story",
  saveTimer: null,
  saving: false,
  dirty: false,
  validateResult: null,  // M3d：最近一次 /validate 的結果（null＝面板收起）
  dialogMigrationNotes: [],  // M12a：舊台詞引用換算失敗的訊息（後端讀檔時附）
  dataDir: "",        // /api/health 回報的實際資料夾（frozen 時在 exe 旁，不能寫死 projects/）
};

// ── 小工具 ───────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
};
const esc = (s) => String(s ?? "");

function setSaveStatus(kind, text) {
  const box = $("#save-status");
  box.className = "save-status " + kind;
  box.textContent = text;
}
function clockText() {
  return new Date().toLocaleTimeString("zh-TW", { hour12: false });
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { /* 非 JSON */ }
  if (!res.ok) {
    const msg = (body && body.error) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return body;
}

// ── 自動儲存 ─────────────────────────────────────────────
function markDirty() {
  if (!state.slug) return;
  state.dirty = true;
  setSaveStatus("pending", "待儲存…");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveNow, SAVE_DEBOUNCE_MS);
}

async function saveNow() {
  if (!state.slug || !state.project) return;
  if (!state.dirty && !state.saving) return;
  state.saving = true;
  setSaveStatus("saving", "儲存中…");
  try {
    const res = await api(`/api/projects/${state.slug}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.project, null, 2),
    });
    state.dirty = false;
    if (res.updatedAt) state.project.meta.updatedAt = res.updatedAt;
    setSaveStatus("saved", `已儲存 ${clockText()}`);
    loadProjectList();
  } catch (e) {
    setSaveStatus("error", `儲存失敗：${e.message}`);
  } finally {
    state.saving = false;
  }
}

// ── 專案列表 ─────────────────────────────────────────────
// 資料夾實際位置由後端決定（frozen 時在 exe 旁、也可用 --data-dir 換），前端不要自己猜。
async function loadHealth() {
  try {
    const res = await api("/api/health");
    if (res && res.paths && res.paths.data) {
      state.dataDir = String(res.paths.data).replace(/\\/g, "/").replace(/\/+$/, "");
      renderMetaLine();
    }
  } catch (_) { /* health 拿不到不影響使用 */ }
}

function renderMetaLine() {
  const p = state.project;
  const line = $("#project-meta-line");
  if (!line) return;   // init 期間 DOM 還沒好時直接跳過，不要讓呼叫端炸掉
  line.textContent = p ? `資料：${state.dataDir ? state.dataDir + "/" : ""}${p.meta.slug}/project.json` : "";
}

async function loadProjectList() {
  try {
    const res = await api("/api/projects");
    state.projects = res.projects || [];
  } catch (e) {
    state.projects = [];
    setSaveStatus("error", `讀專案列表失敗：${e.message}`);
  }
  renderProjectList();
}

function renderProjectList() {
  const ul = $("#project-list");
  ul.textContent = "";
  $("#project-empty").classList.toggle("hidden", state.projects.length > 0);
  for (const p of state.projects) {
    const li = el("li", { class: "project-item" + (p.slug === state.slug ? " active" : "") });
    li.append(
      el("button", {
        class: "project-open", type: "button", title: `開啟 ${p.name}`,
        onclick: () => openProject(p.slug),
      },
        el("span", { class: "project-name", text: p.name }),
        el("span", { class: "project-meta", text: `${p.shotCount} 鏡 · ${(p.updatedAt || "").slice(0, 16).replace("T", " ")}` }),
      ),
      el("button", {
        class: "project-del", type: "button", title: "刪除（移到 _trash，不直接刪）",
        onclick: (ev) => { ev.stopPropagation(); deleteProject(p.slug, p.name); },
      }, "✕"),
    );
    ul.append(li);
  }
}

async function deleteProject(slug, name) {
  if (!confirm(`要刪除專案「${name}」嗎？\n（不會真的刪檔，會移到 projects/_trash/）`)) return;
  try {
    await api(`/api/projects/${slug}`, { method: "DELETE" });
  } catch (e) {
    setSaveStatus("error", `刪除失敗：${e.message}`);
    return;
  }
  if (state.slug === slug) {
    state.slug = null;
    state.project = null;
    renderAll();
    setSaveStatus("idle", "尚未載入");
  }
  await loadProjectList();
}

// ── 開專案 ───────────────────────────────────────────────
async function openProject(slug) {
  clearTimeout(state.saveTimer);
  try {
    const res = await api(`/api/projects/${slug}`);
    state.slug = slug;
    state.project = res.project;
    state.dirty = false;
    // M12a：後端讀檔時把舊的序號引用換算成 beat id，換算不到的句子會附訊息——要讓使用者看得到
    const mig = state.project._dialogMigration;
    if (mig && (mig.notes || []).length) {
      state.dialogMigrationNotes = mig.notes;
      setSaveStatus("pending", "台詞引用已自動換算成劇本句子，但有幾筆換算不到（詳見鏡頭卡或劇本頁）");
    } else {
      state.dialogMigrationNotes = [];
    }
    setSaveStatus("saved", `已載入 ${clockText()}`);
  } catch (e) {
    setSaveStatus("error", `開專案失敗：${e.message}`);
    return;
  }
  renderProjectList();
  renderAll();
  // 一鍵佈署：跑到一半或剛完成時接回進度
  reattachDeployJob();
  // M4b：把這個專案最近一次的批次進度接回來（跑到一半 F5 也不會斷）
  await reattachLatestBatch();
  if (state.view === "shots" || batchState.job) renderShots();
  // M5b/M6：影片批次進度與合併檢查、成品清單也一起接回
  mkState.picked = {}; mkState.order = null; mkState.assemble = null; mkState.asmError = "";
  await reattachLatestVideoBatch();
  await loadAssemblePlan();
  await loadOutputs();
  if (state.view === "generate") renderGenerate();
}

// ── 導覽 ─────────────────────────────────────────────────
function setView(view) {
  state.view = view;
  document.querySelectorAll(".nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === "view-" + view));
  renderViews();
  if (view === "generate" && state.slug) {   // 進頁面就刷新合併檢查與成品（不阻塞畫面）
    loadAssemblePlan();
    loadOutputs();
    if (mkState.vidJobId && mkState.vidJob && ["queued", "running"].includes(mkState.vidJob.status)) pollVideoBatch();
  }
}

// ── 渲染：佔位 ───────────────────────────────────────────
function placeholder(text, note) {
  return el("div", { class: "card placeholder-card" },
    el("h3", { text: text }),
    el("p", { class: "hint", text: note || "此區塊在後續里程碑實作。" }),
  );
}

// ── 渲染：故事 ───────────────────────────────────────────
function renderStory() {
  const box = $("#view-story");
  box.textContent = "";
  if (!state.project) { box.append(emptyHint()); return; }
  const story = state.project.story;

  box.append(el("h2", { class: "view-title", text: "1 · 故事" }));

  // 一鍵佈署：故事寫好後，一次建出角色／場景／劇本／鏡頭（只建文字，圖片留空）
  const dpCard = el("div", { class: "card deploy-card" },
    el("div", { class: "card-head" },
      el("h3", { text: "🚀 一鍵佈署到各頁面" }),
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn btn-primary", type: "button",
        title: "用大綱自動建出角色卡、場景卡、劇本（集／場／對白）與分鏡（六段式 prompt）；圖片一律留空等你補",
        onclick: () => openDeployModal(),
      }, "🚀 一鍵佈署到各頁面"),
    ),
    el("p", { class: "hint", text:
      "把這個故事一次佈署成：角色卡 → 場景卡 → 劇本（集／場／對白）→ 分鏡（每鏡六段式 prompt）。"
      + "只建文字，所有圖片欄位保持空白（之後到各頁上傳或按生成）。約需 2–5 分鐘。" }),
  );
  box.append(dpCard);

  box.append(textareaField("故事概念（premise）", "通則：一句話說明這個故事在講什麼",
    story, "premise", true));
  box.append(textareaField("故事大綱（synopsis）", "可請模型擴寫成完整大綱",
    story, "synopsis", true));

  const styleCard = el("div", { class: "card" }, el("h3", { text: "風格設定（style）" }));
  const style = story.style;
  for (const [key, label, ph] of [
    ["genre", "類型（genre）", "例如：童話改編／懸疑"],
    ["tone", "基調（tone）", "例如：溫暖、帶一點黑色幽默"],
    ["visualStyle", "視覺風格（visualStyle）", "例如：手繪水彩、柔和晨光"],
    ["negative", "負向提示（negative）", "不要出現的東西"],
  ]) {
    styleCard.append(field(label, style, key, ph, false));
  }
  box.append(styleCard);

  // 章回列表
  const chapCard = el("div", { class: "card" });
  const head = el("div", { class: "card-head" },
    el("h3", { text: `章回（chapters）· ${story.chapters.length}` }),
    el("span", { class: "spacer" }),
    chatButton("story"),
    el("button", {
      class: "btn btn-small btn-primary", type: "button",
      onclick: () => { addChapter(); },
    }, "＋ 新增章回"),
  );
  chapCard.append(head);
  if (story.chapters.length === 0) {
    chapCard.append(el("p", { class: "hint", text: "尚未有章回。按「＋ 新增章回」開始寫正文。" }));
  }
  story.chapters.forEach((ch, idx) => chapCard.append(chapterBlock(ch, idx)));
  box.append(chapCard);
}

function emptyHint() {
  return el("div", { class: "card placeholder-card" },
    el("h3", { text: "尚未選擇專案" }),
    el("p", { class: "hint", text: "請在左側選一個專案，或按「＋ 新專案」建立。" }));
}
// 佔位卡（含尚未選擇專案的提示）一律顯示，切換分頁才看得到
function placeholderOrHint(text, note) {
  return state.project ? placeholder(text, note) : emptyHint();
}

function chapterBlock(ch, idx) {
  const wrap = el("div", { class: "chapter" });
  const bar = el("div", { class: "chapter-bar" },
    el("span", { class: "chapter-id", text: `${ch.id} · 第 ${idx + 1} 回` }),
    el("span", { class: "spacer" }),
    el("button", {
      class: "btn btn-small", type: "button", title: "往上移",
      onclick: () => moveChapter(idx, -1),
    }, "↑"),
    el("button", {
      class: "btn btn-small", type: "button", title: "往下移",
      onclick: () => moveChapter(idx, 1),
    }, "↓"),
    el("button", {
      class: "btn btn-small btn-danger", type: "button", title: "刪除本章",
      onclick: () => removeChapter(idx),
    }, "刪除"),
  );
  wrap.append(bar);
  wrap.append(field("標題", ch, "title", "章回標題", false));
  wrap.append(field("摘要", ch, "summary", "這一回在做什麼（給模型看的摘要）", false));
  wrap.append(textareaField("正文", "章回正文（给人看，之後由模型拆成劇本與鏡頭）", ch, "content", true));
  return wrap;
}

function nextChapterId() {
  const list = state.project.story.chapters;
  let n = list.length + 1;
  const used = new Set(list.map((c) => c.id));
  while (used.has(`ch${String(n).padStart(2, "0")}`)) n += 1;
  return `ch${String(n).padStart(2, "0")}`;
}

function addChapter() {
  const list = state.project.story.chapters;
  list.push({ id: nextChapterId(), title: "", summary: "", content: "" });
  markDirty();
  renderStory();
}

function removeChapter(idx) {
  const ch = state.project.story.chapters[idx];
  if (!confirm(`要刪除 ${ch.id}「${ch.title || "（無標題）"}」嗎？`)) return;
  state.project.story.chapters.splice(idx, 1);
  markDirty();
  renderStory();
}

function moveChapter(idx, delta) {
  const list = state.project.story.chapters;
  const to = idx + delta;
  if (to < 0 || to >= list.length) return;
  const [item] = list.splice(idx, 1);
  list.splice(to, 0, item);
  markDirty();
  renderStory();
}

// ── 欄位產生器 ───────────────────────────────────────────
function bindInput(input, obj, key) {
  input.value = esc(obj[key] ?? "");
  input.addEventListener("input", () => { obj[key] = input.value; markDirty(); });
  return input;
}

function field(label, obj, key, placeholder, mono) {
  const input = el("input", { type: "text", class: mono ? "mono" : "", placeholder: placeholder || "" });
  bindInput(input, obj, key);
  return el("label", { class: "field" }, el("span", { text: label }), input);
}

function textareaField(label, placeholder, obj, key, tall) {
  const ta = el("textarea", { class: tall ? "tall" : "", placeholder: placeholder || "", rows: tall ? "8" : "3" });
  bindInput(ta, obj, key);
  return el("label", { class: "field" }, el("span", { text: label }), ta);
}

// ══════════════════════════════════════════════════════════
// M2a：人物卡 + 場景卡 + 上傳 UI + ID 產生器 + 引用檢查
// 資料只走 GET/PUT /api/projects/{slug} 與 /upload；不改資料格式。
// ══════════════════════════════════════════════════════════

const ID_PREFIX = { characters: "c", scenes: "sc" };

// ── 通用小工具 ───────────────────────────────────────────
function picUrl(path) {
  if (!path) return "";
  if (/^(https?:|data:|\/)/.test(path)) return path;
  return `/files/${state.slug}/${path}`;
}

// 沿用舊內容：只在同一個卡片內就地更新，避免打字時失去焦點
function liveText(labelText, obj, key, placeholder, opts = {}) {
  const ta = el("textarea", Object.assign({
    class: opts.tall ? "tall" : "", rows: opts.rows || "3",
    placeholder: placeholder || "",
  }, opts.attrs || {}));
  bindInput(ta, obj, key);
  return el("label", { class: "field" }, el("span", { text: labelText }), ta);
}

function ensure_obj(parent, key, fallback) {
  if (!parent[key] || typeof parent[key] !== "object") parent[key] = fallback;
  return parent[key];
}
// 保留未知欄位：只補缺，不覆寫既有值
function ensureDefaults(target, defaults) {
  for (const [k, v] of Object.entries(defaults)) {
    if (target[k] === undefined) target[k] = v;
  }
  return target;
}

// 多行文字 ↔ 陣列（每行一條，空白行忽略）
function linesField(labelText, obj, key, placeholder) {
  const ta = el("textarea", { rows: "3", placeholder: placeholder || "一行一條" });
  ta.value = Array.isArray(obj[key]) ? obj[key].join("\n") : "";
  ta.addEventListener("input", () => {
    obj[key] = ta.value.split("\n").map((s) => s.trim()).filter(Boolean);
    markDirty();
  });
  return el("label", { class: "field" }, el("span", { text: labelText }), ta);
}

function selectField(labelText, obj, key, options, allowEmpty = true) {
  const sel = el("select", {});
  if (allowEmpty) sel.append(el("option", { value: "", text: "（未設定）" }));
  // 舊資料／外部模型可能給非枚舉值（例如性別寫成「男性，喉結明顯」）→ 保留它顯示，
  // 不要讓 select 因為對不到選項而自己跳回空白（使用者會以為資料沒寫進去）。
  const cur = obj[key] == null ? "" : String(obj[key]);
  const opts = [...options];
  if (cur && !opts.includes(cur)) opts.push(cur);
  for (const o of opts) sel.append(el("option", { value: o, text: o }));
  sel.value = cur;
  sel.addEventListener("change", () => { obj[key] = sel.value; markDirty(); });
  return el("label", { class: "field" }, el("span", { text: labelText }), sel);
}

function checkField(labelText, obj, key, onChange) {
  const box = el("input", { type: "checkbox" });
  box.checked = !!obj[key];
  box.addEventListener("change", () => {
    obj[key] = box.checked;
    markDirty();
    if (onChange) onChange(box.checked);
  });
  return el("label", { class: "checkline" }, box, el("span", { text: labelText }));
}

// ── ID 產生器（前端；後端 storage.next_id 同規則）──────────
function nextId(listKey) {
  const pfx = ID_PREFIX[listKey];
  const list = state.project[listKey] || [];
  let max = 0;
  for (const it of list) {
    const m = /^([a-z]+)(\d{2,})$/.exec(String(it.id || ""));
    if (m && m[1] === pfx) max = Math.max(max, parseInt(m[2], 10));
  }
  let n = max + 1;
  let id = pfx + String(n).padStart(2, "0");
  const used = new Set(list.map((i) => i.id));
  while (used.has(id)) { n += 1; id = pfx + String(n).padStart(2, "0"); }
  return id;
}

// ── 引用檢查 ─────────────────────────────────────────────
function charRefs(cid) {
  const p = state.project;
  const out = [];
  (p.script?.episodes || []).forEach((ep) => {
    (ep.scenes || []).forEach((es) => {
      if ((es.characterIds || []).includes(cid)) {
        out.push(`${ep.id} 的場次「${es.title || es.id}」出場人物`);
      }
      (es.beats || []).forEach((b) => {
        if (b.characterId === cid) {
          out.push(`${ep.id}/${es.id} 對白 ${b.id}（${b.type === "dialogue" ? "台詞" : "動作"}）`);
        }
      });
    });
  });
  (p.shots || []).forEach((sh) => {
    if ((sh.characterIds || []).includes(cid)) out.push(`鏡頭 ${sh.id}（第 ${sh.no} 鏡）出場人物`);
    (sh.dialogue || []).forEach((d, i) => {
      if (d.characterId === cid) out.push(`鏡頭 ${sh.id} 對白第 ${i + 1} 句`);
    });
  });
  return out;
}

function sceneRefs(sid) {
  const p = state.project;
  const out = [];
  (p.script?.episodes || []).forEach((ep) => {
    (ep.scenes || []).forEach((es) => {
      if (es.sceneId === sid) out.push(`${ep.id} 的場次「${es.title || es.id}」場景`);
    });
  });
  (p.shots || []).forEach((sh) => {
    if (sh.sceneId === sid) out.push(`鏡頭 ${sh.id}（第 ${sh.no} 鏡）場景`);
  });
  return out;
}

function clearCharRefs(cid) {
  const p = state.project;
  (p.script?.episodes || []).forEach((ep) => {
    (ep.scenes || []).forEach((es) => {
      if (Array.isArray(es.characterIds)) es.characterIds = es.characterIds.filter((x) => x !== cid);
      (es.beats || []).forEach((b) => { if (b.characterId === cid) b.characterId = null; });
    });
  });
  (p.shots || []).forEach((sh) => {
    if (Array.isArray(sh.characterIds)) sh.characterIds = sh.characterIds.filter((x) => x !== cid);
    (sh.dialogue || []).forEach((d) => { if (d.characterId === cid) d.characterId = null; });
  });
}

function clearSceneRefs(sid) {
  const p = state.project;
  (p.script?.episodes || []).forEach((ep) => {
    (ep.scenes || []).forEach((es) => { if (es.sceneId === sid) es.sceneId = null; });
  });
  (p.shots || []).forEach((sh) => { if (sh.sceneId === sid) sh.sceneId = null; });
}

// 回 true＝可刪（無引用或使用者已確認），false＝取消
// 刪除確認一律走頁內對話框（Prompts）：原生 confirm 無法列出引用明細、也無法截圖佐證。
function confirmDeleteWithRefs(title, refs) {
  return window.SVSConfirm({ title, refs });
}

// ── 上傳 ─────────────────────────────────────────────────
let lightboxEl = null;
function bindLightbox() {
  // 只在真的取到節點時才快取；否則下次呼叫重試（DOM 尚未就緒時不留下 null 快取）
  if (lightboxEl && lightboxEl.isConnected) return lightboxEl;
  lightboxEl = document.getElementById("lightbox");
  return lightboxEl;
}

function openLightbox(src, caption) {
  // 快取可能失效（節點被重建）或從未取到，一律重試；bindLightbox 本身已處理空值
  const box = bindLightbox();
  if (!box) { window.open(src, "_blank"); return; }
  box.querySelector("img").src = src;
  box.querySelector(".lb-caption").textContent = caption || "";
  box.classList.remove("hidden");
}

async function uploadFiles(slug, kind, fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return [];
  const out = [];
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f, f.name);
    try {
      const res = await api(`/api/projects/${slug}/upload?kind=${encodeURIComponent(kind)}`, {
        method: "POST", body: fd,
      });
      out.push(res.path);
    } catch (e) {
      setSaveStatus("error", `上傳失敗（${f.name}）：${e.message}`);
      alert(`上傳「${f.name}」失敗：${e.message}`);
    }
  }
  return out;
}

// 上傳列：<input type=file multiple> + 標籤
function uploadRow(accept, onPicked, opts = {}) {
  const input = el("input", { type: "file", class: "file-input", multiple: "multiple" });
  if (accept) input.accept = accept;
  input.addEventListener("change", async () => {
    const files = input.files;
    await onPicked(files);
    input.value = "";
  });
  return el("div", { class: "upload-row" },
    opts.label ? el("span", { class: "upload-label", text: opts.label }) : null,
    input,
  );
}

// 圖片縮圖牆：點擊放大、可刪除；人物參考圖帶 kind 與鎖臉
function refImageGrid(list, opts = {}) {
  const wrap = el("div", { class: "thumb-grid" });
  if (!list.length) {
    wrap.append(el("p", { class: "hint", text: opts.empty || "尚無參考圖。" }));
    return wrap;
  }
  list.forEach((ref, idx) => {
    const src = picUrl(ref.path);
    const kindLabel = { face: "臉", full: "全身", outfit: "服裝", wide: "全景", detail: "細節" }[ref.kind] || ref.kind || "";
    const thumb = el("div", { class: "thumb" + (ref.locked ? " locked" : "") },
      el("img", {
        src, alt: kindLabel, loading: "lazy",
        onclick: () => openLightbox(src, `${opts.title || ""} · ${kindLabel} · ${ref.path.split("/").pop()}`),
      }),
      el("div", { class: "thumb-bar" },
        el("span", { class: "thumb-kind", text: kindLabel || "—" }),
        ref.locked ? el("span", { class: "thumb-lock", text: "🔒 鎖臉" }) : null,
        el("button", {
          class: "thumb-del", type: "button", title: "刪除這張參考圖",
          onclick: () => {
            if (!confirm("要刪除這張參考圖嗎？（只從卡片移除，檔案仍留在 media/）")) return;
            list.splice(idx, 1);
            if (opts.onChanged) opts.onChanged();
            markDirty();
            // 立刻落地：生圖任務可能幾分鐘後才寫回，不能讓它用「開始生成時」的舊快照
            // 把這次刪除蓋回來（實測：刪掉的圖會在生成完成後復活）。
            clearTimeout(state.saveTimer);
            saveNow();
            renderViews();
          },
        }, "✕"),
      ),
    );
    if (opts.onKindChange) {
      const sel = el("select", { class: "thumb-select" },
        ...(opts.kinds || ["face", "full", "outfit"]).map((k) =>
          el("option", { value: k, text: { face: "臉", full: "全身", outfit: "服裝", wide: "全景", detail: "細節" }[k] || k })));
      sel.value = ref.kind || (opts.kinds || ["face"])[0];
      sel.addEventListener("change", () => { ref.kind = sel.value; markDirty(); renderViews(); });
      thumb.querySelector(".thumb-bar").insertBefore(sel, thumb.querySelector(".thumb-del"));
    }
    wrap.append(thumb);
  });
  return wrap;
}

// 音檔：播放器 + 刪除 + 重新上傳
function audioRow(path, onSet) {
  const wrap = el("div", { class: "audio-row" });
  if (path) {
    wrap.append(el("audio", { controls: "controls", src: picUrl(path), preload: "none" }));
    wrap.append(el("span", { class: "hint mono", text: path }));
    wrap.append(el("button", {
      class: "btn btn-small btn-danger", type: "button",
      onclick: () => { if (confirm("要移除聲音參照嗎？（檔案仍留在 media/）")) { onSet(""); renderViews(); } },
    }, "移除"));
  } else {
    wrap.append(el("span", { class: "hint", text: "尚無音檔" }));
  }
  wrap.append(uploadRow("audio/*", async (files) => {
    const paths = await uploadFiles(state.slug, "voices", files);
    if (paths.length) { onSet(paths[0]); markDirty(); renderViews(); }
  }, { label: path ? "換一個：" : "上傳音檔：" }));
  return wrap;
}

// 外型 → 英文 prompt（純字串拼接，不呼叫任何模型）
function composeAppearanceEn(ch) {
  const a = ch.appearance || {};
  const parts = [];
  const head = [a.age, a.gender].filter(Boolean).join(" ");
  if (head) parts.push(head);
  if (a.skin) parts.push(`${a.skin} skin`);
  if (a.face) parts.push(`${a.face} face`);
  if (a.hair) parts.push(`${a.hair} hair`);
  if (a.body) parts.push(`${a.body} body`);
  for (const f of a.features || []) parts.push(f);
  let out = [];
  if (parts.length) out.push(`${ch.name || "a character"}: ` + parts.join(", "));
  for (const o of ch.outfits || []) {
    const bits = [o.name, o.desc || o.promptEn].filter(Boolean).join(", ");
    if (bits) out.push(`${o.id} outfit — ${bits}`);
  }
  if ((ch.refImages || []).some((r) => r.locked) || ch.lockedFace) {
    out.push("keep the identical face from the reference image");
  }
  return out.join(". ").trim();
}

// ── 人物卡 ───────────────────────────────────────────────
const ROLE_OPTIONS = ["主角", "配角", "反派", "群眾", "旁白"];
const VOICE_ENGINES = ["fish_s2", "indextts2", "other"];

function charCard(ch) {
  const wrap = el("div", { class: "card char-card" });
  const head = el("div", { class: "card-head" });
  const avatar = el("img", {
    class: "card-avatar", alt: ch.name || ch.id,
    src: (ch.refImages && ch.refImages[0] && picUrl(ch.refImages[0].path)) || "",
    onclick: () => {
      const p = ch.refImages && ch.refImages[0] && picUrl(ch.refImages[0].path);
      if (p) openLightbox(p, ch.name || ch.id);
    },
  });
  if (!(ch.refImages && ch.refImages.length)) avatar.classList.add("empty");
  head.append(
    avatar,
    el("div", { class: "card-title" },
      el("h3", { text: `${ch.id} · ${ch.name || "（未命名人物）"}` }),
      el("span", { class: "hint", text: `${ch.role || "未設定定位"} · ${(ch.refImages || []).length} 張參考圖` }),
    ),
    el("span", { class: "spacer" }),
    chatButton("character", ch.id),
    el("button", {
      class: "btn btn-small btn-danger", type: "button", title: "刪除這個人物",
      onclick: () => removeCharacter(ch.id),
    }, "刪除"),
  );
  wrap.append(head);

  const grid = el("div", { class: "field-grid" });
  grid.append(
    field("姓名（name）", ch, "name", "例如：小紅帽", false),
    selectField("角色定位（role）", ch, "role", ROLE_OPTIONS),
  );
  wrap.append(grid);
  wrap.append(linesField("別名（aliases，一行一個）", ch, "aliases", "例如：奶奶口中的「紅斗篷」"));

  // 外型
  const ap = ensure_obj(ch, "appearance", {});
  const apCard = el("div", { class: "subcard" });
  apCard.append(el("div", { class: "subcard-head" }, el("h4", { text: "外型（appearance）" })));
  const apGrid = el("div", { class: "field-grid" });
  apGrid.append(
    field("年齡（age）", ap, "age", "例如：17 歲少女", false),
    selectField("性別（gender）", ap, "gender", ["女", "男", "其他"], false),
    field("膚色 / 膚質（skin）", ap, "skin", "例如：小麥色、臉頰有雀斑", false),
    field("臉型五官（face）", ap, "face", "例如：圓臉、大眼、單眼皮", false),
    field("髮型髮色（hair）", ap, "hair", "例如：黑色及腰直髮、瀏海", false),
    field("體型（body）", ap, "body", "例如：瘦高、肩膀窄", false),
  );
  apCard.append(apGrid);
  apCard.append(linesField("辨識特徵（features，一行一條）", ap, "features", "例如：左眉尾有疤"));
  apCard.append(liveText("英文外型 prompt（promptEn）", ap, "promptEn", "餵生圖用的英文外型描述", { rows: "3" }));
  const composeBtn = el("button", { class: "btn btn-small btn-primary", type: "button" }, "✦ 用外型欄位自動組英文 prompt");
  composeBtn.addEventListener("click", () => {
    const text = composeAppearanceEn(ch);
    if (!text) { alert("外型欄位都還是空的，先填幾個再按。"); return; }
    ap.promptEn = text;
    markDirty();
    renderViews();
    setSaveStatus("pending", "已填入英文 prompt，待儲存…");
  });
  apCard.append(el("div", { class: "row" }, composeBtn,
    el("span", { class: "hint", text: "純字串拼接，不會呼叫模型；要模型改寫請等 M3。" })));
  wrap.append(apCard);

  // 服裝
  const outfitCard = el("div", { class: "subcard" });
  outfitCard.append(el("div", { class: "subcard-head" },
    el("h4", { text: `服裝（outfits）· ${(ch.outfits || []).length}` }),
    el("span", { class: "spacer" }),
    el("button", {
      class: "btn btn-small", type: "button",
      onclick: () => {
        if (!Array.isArray(ch.outfits)) ch.outfits = [];
        let n = ch.outfits.length + 1;
        const used = new Set(ch.outfits.map((o) => o.id));
        let oid = "o" + String(n).padStart(2, "0");
        while (used.has(oid)) { n += 1; oid = "o" + String(n).padStart(2, "0"); }
        ch.outfits.push({ id: oid, name: "", desc: "", promptEn: "" });
        markDirty(); renderViews();
      },
    }, "＋ 新增服裝"),
  ));
  if (!(ch.outfits || []).length) {
    outfitCard.append(el("p", { class: "hint", text: "尚未新增服裝。" }));
  }
  (ch.outfits || []).forEach((o, i) => {
    const row = el("div", { class: "outfit" });
    row.append(el("div", { class: "chapter-bar" },
      el("span", { class: "chapter-id", text: o.id || `o${i + 1}` }),
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn btn-small btn-danger", type: "button",
        onclick: () => {
          if (!confirm(`要刪除服裝「${o.name || o.id}」嗎？`)) return;
          ch.outfits.splice(i, 1); markDirty(); renderViews();
        },
      }, "刪除"),
    ));
    row.append(field("名稱", o, "name", "例如：日常、宴會裝", false));
    row.append(liveText("說明（desc）", o, "desc", "中文描述", { rows: "2" }));
    row.append(liveText("英文 prompt（promptEn）", o, "promptEn", "英文服裝描述", { rows: "2" }));
    outfitCard.append(row);
  });
  wrap.append(outfitCard);

  // 背景（background）已移除：它不參與任何生成，只會讓使用者多填五個沒用的欄位。
  // 舊專案裡殘留的 background 資料不主動清除，讀進來也不再用它組提示詞。

  // M3d：身份錨點（identityAnchors）＋ 分階段造型（stages）
  wrap.append(anchorsCard(ch));
  wrap.append(stagesCard(ch));

  // 聲音
  const voice = ensure_obj(ch, "voice", {});
  const vCard = el("div", { class: "subcard" });
  vCard.append(el("h4", { text: "聲音（voice）" }));
  vCard.append(liveText("音色描述（desc）", voice, "desc", "例如：偏低、講話慢、帶鼻音", { rows: "2" }));
  vCard.append(el("label", { class: "field" },
    el("span", { text: "聲音參照音檔（refAudio）" }),
    audioRow(voice.refAudio || "", (v) => { voice.refAudio = v; })));
  vCard.append(selectField("配音引擎（engine）", voice, "engine", VOICE_ENGINES, false));
  vCard.append(field("備註（note）", voice, "note", "例如：情緒偏冷、語速 0.9 倍", false));
  wrap.append(vCard);

  // 參考圖
  const refCard = el("div", { class: "subcard" });
  refCard.append(el("h4", { text: `參考圖（refImages）· ${(ch.refImages || []).length}` }));
  if (!Array.isArray(ch.refImages)) ch.refImages = [];
  const kindSel = el("select", { class: "inline-select" },
    el("option", { value: "face", text: "臉（face）" }),
    el("option", { value: "full", text: "全身（full）" }),
    el("option", { value: "outfit", text: "服裝（outfit）" }),
  );
  const lockBox = el("input", { type: "checkbox", id: `lock-${ch.id}` });
  lockBox.checked = !!ch.lockedFace || (ch.refImages || []).some((r) => r.locked);
  lockBox.addEventListener("change", () => {
    ch.lockedFace = lockBox.checked;
    markDirty(); renderViews();
  });
  const lockLine = el("label", { class: "checkline" }, lockBox,
    el("span", { text: "鎖臉（這批參考圖用來固定同一個人的臉）" }));
  refCard.append(el("div", { class: "row wrap" }, el("span", { class: "hint", text: "上傳類型：" }), kindSel, lockLine));
  refCard.append(uploadRow("image/*", async (files) => {
    const paths = await uploadFiles(state.slug, "characters", files);
    if (!paths.length) return;
    const locked = lockBox.checked;
    for (const p of paths) ch.refImages.push({ path: p, kind: kindSel.value, locked });
    if (locked) ch.lockedFace = true;
    markDirty();
    renderViews();
  }, { label: "選擇參考圖（可多選）：" }));
  refCard.append(refImageGrid(ch.refImages, {
    title: ch.name || ch.id, kinds: ["face", "full", "outfit"],
    onKindChange: true,
    empty: "尚無參考圖。上傳後可選「臉／全身／服裝」並勾鎖臉。",
  }));
  wrap.append(refCard);

  // 第二層：H3 多角度設定表（把上面最新的 kind=full 立繪 → 多視角 i2i）
  wrap.append(h3SheetCard(ch));

  // 英文 prompt 一鍵組完後的提示
  const p = (ch.refImages || []).filter((r) => r.locked).slice(0, 3).map((r) => r.path);
  if (ch.lockedFace) {
    wrap.append(el("p", { class: "hint", text: `鎖臉已開啟${p.length ? "（" + p.join("、") + "）" : "（尚無已鎖定的參考圖）"}。生成時（M4）會把鎖定圖當參考圖送。` }));
  }
  return wrap;
}

// ── M3d：身份錨點（identityAnchors）────────────────────────────
const ANCHOR_TEXT_FIELDS = [
  ["hairStyle", "髮型（hairStyle）", "例如：及腰直髮、齊瀏海、低馬尾（英文）"],
  ["faceShape", "臉型（faceShape）", "例如：oval, narrow jaw（英文）"],
  ["facialFeatures", "五官（facialFeatures）", "例如：large pale-brown eyes, high nose bridge"],
  ["uniqueMarks", "辨識特徵（uniqueMarks）", "例如：small scar on left eyebrow"],
  ["skinTexture", "膚質（skinTexture）", "例如：fair with light freckles, matte finish"],
];
const COLOR_ANCHORS = [
  ["hair", "髮色（hair）"],
  ["eyes", "眼色（eyes）"],
  ["skin", "膚色（skin）"],
  ["primaryOutfit", "主服裝色（primaryOutfit）"],
];

function normalizeHex(v) {
  const s = String(v || "").trim();
  if (!s) return "";
  const m = s.match(/^#?([0-9a-fA-F]{6})$/);
  return m ? "#" + m[1].toUpperCase() : s;
}

function hexSyncRow(ca, key, label) {
  const swatch = el("input", { type: "color", class: "color-picker" });
  const hexIn = el("input", { type: "text", class: "mono color-hex", placeholder: "#RRGGBB", maxlength: "7" });
  const apply = (val, from) => {
    const v = normalizeHex(val);
    ca[key] = v;
    if (from !== "swatch") swatch.value = /^#[0-9A-Fa-f]{6}$/.test(v) ? v : "#000000";
    if (from !== "hex") hexIn.value = v;
    hexIn.classList.toggle("invalid", !!v && !/^#[0-9A-Fa-f]{6}$/.test(v));
    markDirty();
  };
  swatch.value = /^#[0-9A-Fa-f]{6}$/.test(ca[key] || "") ? ca[key] : "#000000";
  hexIn.value = ca[key] || "";
  swatch.addEventListener("input", () => apply(swatch.value, "swatch"));
  hexIn.addEventListener("input", () => apply(hexIn.value, "hex"));
  return el("label", { class: "field color-field" },
    el("span", { text: label }),
    el("div", { class: "color-row" }, swatch, hexIn));
}

function anchorsCard(ch) {
  const a = ensure_obj(ch, "identityAnchors", {});
  ensure_obj(a, "colorAnchors", { hair: "", eyes: "", skin: "", primaryOutfit: "" });
  const card = el("div", { class: "subcard" });
  const filled = ANCHOR_TEXT_FIELDS.filter(([k]) => a[k]).length +
    COLOR_ANCHORS.filter(([k]) => a.colorAnchors[k]).length;
  card.append(el("div", { class: "subcard-head" },
    el("h4", { text: `身份錨點（identityAnchors）· ${filled}/9` }),
    el("span", { class: "spacer" }),
    el("span", { class: "hint", text: "編譯 prompt 時逐字貼上，不改寫；顏色一律 Hex" }),
  ));
  const g = el("div", { class: "field-grid" });
  for (const [k, label, ph] of ANCHOR_TEXT_FIELDS) g.append(field(label, a, k, ph, true));
  card.append(g);
  const cg = el("div", { class: "field-grid color-grid" });
  for (const [k, label] of COLOR_ANCHORS) cg.append(hexSyncRow(a.colorAnchors, k, label));
  card.append(cg);
  const btn = el("button", { class: "btn btn-small", type: "button" },
    "✦ 從英文外型 prompt 抽色票（只認 #RRGGBB）");
  btn.addEventListener("click", () => {
    const text = String((ch.appearance && ch.appearance.promptEn) || "");
    const hits = text.match(/#[0-9A-Fa-f]{6}/g) || [];
    if (!hits.length) { alert("英文外型 prompt 裡沒有 #RRGGBB 形式的顏色；請直接填色票或 Hex 欄位。"); return; }
    COLOR_ANCHORS.forEach(([k], i) => { if (hits[i]) a.colorAnchors[k] = normalizeHex(hits[i]); });
    markDirty(); renderViews();
    setSaveStatus("pending", `已填入 ${Math.min(hits.length, 4)} 個顏色錨點，待儲存…`);
  });
  card.append(el("div", { class: "row wrap" }, btn,
    el("span", { class: "hint", text: "規則（LocalMiniDrama）：顏色只收 Hex、不寫 black/brown 這種色名；沒提到的留空。" })));
  return card;
}

// ── M3d：分階段造型（stages）──────────────────────────────────
function nextStageId(ch) {
  const used = new Set((ch.stages || []).map((s) => s.id));
  let n = 1;
  while (used.has("s" + n)) n += 1;
  return "s" + n;
}

function stagesCard(ch) {
  if (!Array.isArray(ch.stages)) ch.stages = [];
  const card = el("div", { class: "subcard" });
  card.append(el("div", { class: "subcard-head" },
    el("h4", { text: `分階段造型（stages）· ${ch.stages.length}` }),
    el("span", { class: "spacer" }),
    el("button", {
      class: "btn btn-small", type: "button",
      onclick: () => {
        ch.stages.push({
          id: nextStageId(ch), label: "", fromChapterId: "",
          characterSheet: "", outfitId: "", note: "",
        });
        markDirty(); renderViews();
      },
    }, "＋ 新增階段"),
  ));
  card.append(el("p", { class: "hint", text: "只有服裝或身上物件「真的變了」才新增階段（校服→病號服→戰損）；同名階段會出現在參考圖槽位與 prompt 的 Current look。stage id 存成 c01:s1。" }));
  if (!ch.stages.length) {
    card.append(el("p", { class: "hint", text: "尚未新增階段。" }));
    return card;
  }
  const chapters = ((state.project.story && state.project.story.chapters) || []);
  ch.stages.forEach((st, i) => {
    const row = el("div", { class: "outfit" });
    row.append(el("div", { class: "chapter-bar" },
      el("span", { class: "chapter-id", text: `${ch.id}:${st.id || `s${i + 1}`}` }),
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn btn-small btn-danger", type: "button",
        onclick: () => {
          if (!confirm(`要刪除階段「${st.label || st.id}」嗎？（已引用 ${ch.id}:${st.id} 的鏡頭會退回角色參考圖）`)) return;
          ch.stages.splice(i, 1);
          for (const sh of (state.project.shots || [])) {
            sh.stageIds = (sh.stageIds || []).filter((x) => x !== `${ch.id}:${st.id}`);
          }
          markDirty(); renderViews();
        },
      }, "刪除"),
    ));
    const lg = el("div", { class: "field-grid" });
    lg.append(field("階段名稱（label）", st, "label", "例如：入學、病號服、戰損", false));
    const chSel = el("select", {},
      el("option", { value: "", text: "（未指定章回）" }),
      ...chapters.map((c) => el("option", { value: c.id, text: `${c.id} · ${c.title || ""}` })));
    chSel.value = st.fromChapterId || "";
    chSel.addEventListener("change", () => { st.fromChapterId = chSel.value; markDirty(); });
    lg.append(el("label", { class: "field" }, el("span", { text: "起始章回（fromChapterId）" }), chSel));
    const oSel = el("select", {}, el("option", { value: "", text: "（未指定服裝）" }),
      ...(ch.outfits || []).map((o) => el("option", { value: o.id, text: `${o.id} · ${o.name || o.promptEn || ""}` })));
    oSel.value = st.outfitId || "";
    oSel.addEventListener("change", () => { st.outfitId = oSel.value; markDirty(); });
    lg.append(el("label", { class: "field" }, el("span", { text: "服裝（outfitId）" }), oSel));
    row.append(lg);
    const sheetLine = el("div", { class: "row wrap" },
      el("span", { class: "hint", text: "階段參考圖（characterSheet，kind=characters）：" }));
    if (st.characterSheet) {
      const src = picUrl(st.characterSheet);
      sheetLine.append(el("img", {
        class: "stage-sheet", src, alt: st.label || st.id,
        onclick: () => openLightbox(src, `${ch.name || ch.id} · ${st.label || st.id}`),
      }));
      sheetLine.append(el("span", { class: "hint mono path-line", text: st.characterSheet }));
      sheetLine.append(el("button", {
        class: "btn btn-small btn-danger", type: "button",
        onclick: () => { if (confirm("要移除這張階段圖嗎？（檔案仍留在 media/）")) { st.characterSheet = ""; markDirty(); renderViews(); } },
      }, "移除圖"));
    } else {
      sheetLine.append(el("span", { class: "hint", text: "尚無階段圖" }));
    }
    sheetLine.append(uploadRow("image/*", async (files) => {
      const paths = await uploadFiles(state.slug, "characters", files);
      if (!paths.length) return;
      st.characterSheet = paths[0];
      markDirty(); renderViews();
    }, { label: st.characterSheet ? "換一張：" : "上傳：" }));
    row.append(sheetLine);
    row.append(liveText("備註（note）", st, "note", "例如：第 3 集起換成病號服、外套破損", { rows: "2" }));
    card.append(row);
  });
  return card;
}

function charListCard() {
  const card = el("div", { class: "card" });
  const list = state.project.characters || [];
  card.append(el("div", { class: "card-head" },
    el("h3", { text: `人物列表 · ${list.length}` }),
    el("button", {
      class: "btn btn-small btn-primary", type: "button",
      onclick: () => {
        if (!Array.isArray(state.project.characters)) state.project.characters = [];
        const id = nextId("characters");
        state.project.characters.push({
          id, name: "", aliases: [], role: "配角",
          appearance: { age: "", gender: "", skin: "", face: "", hair: "", body: "", features: [], promptEn: "" },
          outfits: [],
          voice: { desc: "", refAudio: "", engine: "fish_s2", note: "" },
          refImages: [], lockedFace: false,
        });
        markDirty(); renderViews();
      },
    }, "＋ 新增人物"),
  ));
  if (!list.length) {
    card.append(el("p", { class: "hint", text: "尚未有角色。按「＋ 新增人物」，系統會自動編號 c01、c02…" }));
  }
  const nav = el("div", { class: "pill-list" });
  list.forEach((ch) => {
    nav.append(el("button", {
      class: "pill", type: "button",
      onclick: () => {
        const node = document.getElementById(`char-${ch.id}`);
        if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
      },
    },
      el("img", { src: (ch.refImages || [])[0] ? picUrl(ch.refImages[0].path) : "", alt: "" }),
      el("span", { text: `${ch.name || ch.id}（${ch.id}·${ch.role || "—"}）` }),
    ));
  });
  card.append(nav);
  return card;
}

function removeCharacter(cid) {
  const idx = (state.project.characters || []).findIndex((c) => c.id === cid);
  if (idx < 0) return;
  const ch = state.project.characters[idx];
  const refs = charRefs(cid);
  if (!confirmDeleteWithRefs(ch.name || cid, refs)) return;
  if (refs.length) clearCharRefs(cid);
  state.project.characters.splice(idx, 1);
  markDirty(); renderViews();
  setSaveStatus("pending", refs.length ? `已刪除 ${cid}，清掉 ${refs.length} 處引用，待儲存…` : "待儲存…");
}

function renderCharacters() {
  const box = $("#view-characters");
  box.textContent = "";
  if (!state.project) { box.append(emptyHint()); return; }
  if (!Array.isArray(state.project.characters)) state.project.characters = [];
  box.append(el("h2", { class: "view-title", text: "2 · 人物" }));
  box.append(characterSheetCard());
  box.append(charListCard());
  for (const ch of state.project.characters) {
    const card = charCard(ch);
    card.id = `char-${ch.id}`;
    box.append(card);
  }
}

// ── GPU 自動切換的共用顯示（生圖／生影片都用這個）──────────────
// 後端每次生成都回 gpu:{switched,stoppedLlama,freedMB,restored,restartWaitedS,busy,note,error}
function gpuBarText(g) {
  if (!g) return "";
  const gb = (mb) => `${(mb / 1024).toFixed(1)} GB`;
  const parts = [];
  if (g.stoppedLlama) parts.push(`已自動停止本機 LLM（釋放 ${gb(g.freedMB || 0)}）`);
  if (g.restored) parts.push(`已重新啟動本機 LLM（耗時 ${(g.restartWaitedS || 0).toFixed(1)}s）`);
  else if (g.stoppedLlama) parts.push(`⚠ 本機 LLM 尚未啟回${g.error ? "：" + g.error : ""}`);
  return parts.join(" → ");
}

// 紅色橫幅：LLM 正在運算被擋下（或 GPU 切換失敗）時用
function gpuBusyBanner(g, fallbackMsg) {
  const busy = g && g.busy;
  const box = el("div", { class: "gpu-banner gpu-banner-err" });
  box.append(el("div", { class: "gpu-banner-title", text: busy ? "⛔ 沒有送件：本機 LLM 正在運算中" : "⛔ GPU 切換失敗，沒有送件" }));
  box.append(el("div", { class: "gpu-banner-body", text: (g && g.error) || fallbackMsg || "" }));
  box.append(el("div", { class: "gpu-banner-hint", text: "建議：等本機 LLM 這一輪回覆結束（或到設定頁取消勾選「自動停止本機 LLM」自己手動切換）再按一次生成。" }));
  return box;
}

// 進度文字：已自動停止 → 載入模型 → 生成中 → 已重新啟動
function gpuProgressLine(g, extra) {
  const line = el("p", { class: "hint gpu-progress" });
  if (g && (g.stoppedLlama || g.switched)) {
    line.append(el("span", { class: "gpu-progress-on", text: gpuBarText(g) }));
    line.append(document.createTextNode(g.restored ? "" : "　"));
  }
  if (extra) line.append(el("span", { text: extra }));
  return line;
}

// ── 第二層：H3 多角度設定表（老臘肉的 H3 Character Sheet Designer，i2i）──────
// 流程：角色卡最新的 kind=full 立繪（第一層 MOSS 生的）→ H3 轉成多視角設定表。
const H3SHEET_VIEWS = [
  ["face_front", "正面頭像"], ["face_left", "左側臉"], ["face_right", "右側臉"],
  ["face_3q_left", "左四分之三臉"], ["body_front", "正面全身"], ["body_back", "背面全身"],
];
const H3SHEET_DEFAULT = ["face_front", "face_left", "face_right", "body_front", "body_back"];
const H3SHEET_STYLES = [
  ["none", "不指定（跟參考圖）"], ["photo", "實拍照片"], ["anime", "動畫"], ["semi_realistic_anime", "半寫實動畫"],
  ["3d", "3D"], ["realistic_painting", "寫實繪畫"], ["oil_painting", "油畫"], ["watercolor", "水彩"],
  ["gouache", "水粉"], ["colored_pencil", "色鉛筆"],
];

function h3SheetCard(ch) {
  const card = el("div", { class: "subcard h3sheet-card" });
  // 這張卡在 renderViews() 後會整個重建 → 進行中的 job 記在全域，重建後自動接回（否則進度會消失）
  window.__h3sheetJobs = window.__h3sheetJobs || {};
  const jobKey = `${state.slug}:${ch.id}`;
  const jobState = window.__h3sheetJobs[jobKey] || (window.__h3sheetJobs[jobKey] = { jobId: "", timer: null, last: null });
  card.append(el("div", { class: "subcard-head" },
    el("h4", { text: "🎭 多角度設定表（第二層：H3 image，i2i）" })));
  const fulls = (ch.refImages || []).filter((r) => (r.kind || "full") === "full" && r.path && r.source !== "h3sheet");
  const latest = fulls.length ? fulls[fulls.length - 1] : null;
  card.append(el("p", { class: "hint", text: latest
    ? `來源立繪：${latest.path.split("/").pop()}（kind=full 最新一張）。H3 會照它畫出多個角度；原圖保留。`
    : "這個角色還沒有全身立繪（kind=full）：請先用「生成與合併 → 角色立繪」生第一層立繪，再回來做多角度。" }));

  // 視角勾選（預設五個）
  const checks = el("div", { class: "row wrap h3sheet-views" });
  const boxes = {};
  for (const [key, label] of H3SHEET_VIEWS) {
    const cb = el("input", { type: "checkbox", value: key });
    cb.checked = H3SHEET_DEFAULT.includes(key);
    boxes[key] = cb;
    checks.append(el("label", { class: "checkline" }, cb, el("span", { text: label })));
  }
  card.append(el("label", { class: "field" }, el("span", { text: "要畫的視角（越少越快）" }), checks));
  card.append(el("p", { class: "hint", text: "預設五個視角。視角越多畫布越寬、越慢；16GB VRAM 底下第一次含載模型可能 10–25 分鐘。想先試就只勾 1–3 個。" }));

  const styleSel = el("select", {}, ...H3SHEET_STYLES.map(([v, t]) => el("option", { value: v, text: t })));
  const cropBox = el("input", { type: "checkbox" });
  cropBox.checked = true;
  const optsRow = el("div", { class: "row wrap" },
    el("span", { class: "hint", text: "風格：" }), styleSel,
    el("label", { class: "checkline" }, cropBox, el("span", { text: "完成後自動切成單張（拼版可切就切）" })));
  card.append(optsRow);

  const status = el("p", { class: "hint", text: "" });
  const list = el("div", { class: "gen-log hidden" });
  const outBox = el("div", { class: "thumb-grid" });
  const btn = el("button", { class: "btn btn-small btn-primary", type: "button", disabled: !latest },
    "🎭 生成多角度設定表（H3）");
  let timer = null;

  // 重建卡片後把「進行中／剛完成」的 job 接回來（進度與縮圖不因 renderViews 消失）
  if (jobState.jobId) { btn.disabled = true; }
  if (jobState.last && jobState.last.status === "done") { /* 由下面「已生成的多角度圖」呈現 */ }

  const paintResult = (j) => {
    outBox.textContent = "";
    const show = (path, label) => outBox.append(el("div", { class: "thumb" },
      el("img", {
        src: picUrl(path) + `?t=${Date.now()}`, alt: label, loading: "lazy",
        onclick: () => openLightbox(picUrl(path), `${ch.name || ch.id} · ${label}`),
      }),
      el("div", { class: "thumb-bar" }, el("span", { class: "thumb-kind", text: label })),
    ));
    if (j.path) show(j.path, `整張設定表 ${j.width}×${j.height}`);
    for (const c of j.crops || []) show(c.path, c.label || c.view);
  };

  const poll = async (jobId) => {
    let j = null;
    try {
      const r = await api(`/api/projects/${state.slug}/characters/${ch.id}/generate/sheet/${jobId}`);
      j = r.job;
    } catch (e) {
      status.className = "hint err-text";
      status.textContent = `✕ 讀進度失敗：${e.message}`;
      btn.disabled = false;
      return;
    }
    const mins = j.startedAt ? Math.max(0, Math.round((Date.now() - new Date(j.startedAt).getTime()) / 1000)) : 0;
    if (j.status === "queued" || j.status === "running") {
      status.className = "hint";
      const secs = j.elapsed || mins;
      status.textContent = `⏳ ${j.phase || "生成中…"}（已 ${Math.floor(secs / 60)} 分 ${secs % 60} 秒，jobId=${jobId}）`;
      timer = setTimeout(() => poll(jobId), 3000);
      return;
    }
    btn.disabled = false;
    jobState.jobId = "";
    if (j.status === "done") {
      jobState.last = j;
      status.className = "hint ok-text";
      status.textContent = `✓ 完成（${j.elapsed} 秒，pid=${j.pid}）：${j.path}｜${j.width}×${j.height}`
        + (j.crops && j.crops.length ? `｜已切成 ${j.crops.length} 張` : "");
      list.classList.remove("hidden");
      list.textContent = "";
      for (const w of j.warnings || []) list.append(el("p", { class: "hint", text: `⚠ ${w}` }));
      if (j.gpu && j.gpu.stoppedLlama) list.append(el("p", { class: "hint", text: `本機 LLM 已依設定還原${j.gpu.restored ? "（已啟回）" : ""}` }));
      paintResult(j);
      // 只刷新「這個角色」的參考圖。整批取代 characters 會把使用者這段時間的其他編輯洗掉，
      // 也會讓剛刪掉的圖（伺服器端已被生圖任務寫回）看起來又復活。
      try {
        const fresh = await api(`/api/projects/${state.slug}`);
        const srvCh = fresh && fresh.project
          && (fresh.project.characters || []).find((c) => c.id === ch.id);
        if (srvCh) { ch.refImages = srvCh.refImages; markDirty(); renderViews(); }
      } catch (_) { /* 拉不到就只顯示上面那組縮圖 */ }
    } else {
      status.className = "hint err-text";
      status.textContent = `✕ ${j.error || "失敗"}`;
    }
  };

  btn.addEventListener("click", async () => {
    const views = H3SHEET_VIEWS.map(([k]) => k).filter((k) => boxes[k].checked);
    if (!views.length) { alert("請至少勾一個視角"); return; }
    if (!confirm(`要用 ${latest.path.split("/").pop()} 生成 ${views.length} 個視角的多角度設定表嗎？\n`
      + "H3 image 很慢（16GB VRAM 可能 10–25 分鐘），期間請不要關頁面。原立繪不會被改。")) return;
    btn.disabled = true;
    list.classList.add("hidden");
    outBox.textContent = "";
    status.className = "hint";
    status.textContent = "送出中…";
    try {
      const res = await api(`/api/projects/${state.slug}/characters/${ch.id}/generate/sheet`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ views, style: styleSel.value, crop: cropBox.checked }),
      });
      if (!res.ok) throw new Error(res.error || "送件失敗");
      jobState.jobId = res.jobId;
      status.textContent = `已排入背景任務（jobId=${res.jobId}）…`;
      poll(res.jobId);
    } catch (e) {
      jobState.jobId = "";
      status.className = "hint err-text";
      status.textContent = `✕ ${e.message}`;
      btn.disabled = false;
    }
  });
  card.dataset.stopPoll = "1";
  const stop = () => { clearTimeout(timer); };
  card.addEventListener("DOMNodeRemovedFromDocument", stop);
  card.__stopPoll = stop;
  // 重建後若還有進行中的 job → 直接接回進度
  if (jobState.jobId) { setTimeout(() => poll(jobState.jobId), 300); }
  else if (jobState.last && jobState.last.status === "done" && !jobState.last.__painted) {
    jobState.last.__painted = true;
    paintResult(jobState.last);
  }

  card.append(el("div", { class: "row wrap" }, btn));

  // 目前卡上的多角度設定表（整張＋裁切單張，可點開放大）
  const done = (ch.refImages || []).filter((r) => r.source === "h3sheet");
  if (done.length) {
    card.append(el("h4", { text: `已生成的多角度圖（${done.length} 張）` }));
    card.append(refImageGrid(done, { title: ch.name || ch.id, empty: "" }));
  }
  card.append(status, list, outBox);
  return card;
}

// ── M6：角色立繪（正面全身／淺灰背景／完整服裝）────────────────
function characterSheetCard() {
  const card = el("div", { class: "card" }, el("h3", { text: "角色立繪（refImages，kind=full、鎖臉）" }));
  const eng = (state.project.settings && state.project.settings.imageEngine) || "qwen";
  const ids = (state.project.characters || []).map((c) => c.id);
  const sel = el("select", { multiple: true, size: String(Math.min(6, Math.max(2, ids.length))) },
    ...(state.project.characters || []).map((c) =>
      el("option", { value: c.id, text: `${c.id} · ${c.name || "（未命名）"}${(c.refImages || []).length ? `（已有 ${(c.refImages || []).length} 張）` : ""}` })));
  const status = el("p", { class: "hint", text: `引擎：${eng === "moss" ? "MOSS Image SFT" : "Qwen Image 2.1"}（可在設定頁切換）。每張約 ${eng === "moss" ? "60–180" : "10–40"} 秒，一位一位跑。` });
  const list = el("div", { class: "gen-log hidden" });
  const btn = el("button", { class: "btn btn-small btn-primary", type: "button" }, "生成立繪");
  btn.addEventListener("click", async () => {
    const picked = [...sel.selectedOptions].map((o) => o.value);
    if (!picked.length) { alert("請先選要生立繪的人物（可多選；不選＝全部）"); return; }
    if (!confirm(`要為 ${picked.join("、")} 生正面全身立繪嗎？\n會寫回人物卡的參考圖（kind=full、勾鎖臉），舊圖不會刪。`)) return;
    btn.disabled = true;
    status.className = "hint";
    status.textContent = `生成中…（${picked.length} 位依序跑，別關頁面）檢查 GPU：若本機 LLM 佔著 VRAM 會先自動停止它…`;
    list.classList.remove("hidden");
    list.textContent = "";
    try {
      const res = await api(`/api/projects/${state.slug}/generate/characters`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine: eng, ids: picked }),
      });
      const g = res.gpu;
      if (g && g.busy) {
        // LLM 正在運算 → 整批沒送件：紅色橫幅講清楚原因與建議
        status.className = "hint err-text";
        status.textContent = "";
        status.append(gpuBusyBanner(g, res.error));
      } else if (!res.ok && !res.items) {
        status.className = "hint err-text";
        status.textContent = `✕ ${res.error || "失敗"}`;
      } else {
        for (const it of res.items || []) {
          list.append(el("p", { class: "hint mono path-line", text: it.error
            ? `✕ ${it.characterId}：${it.error}`
            : `${it.characterId} ${it.name || ""}：${it.path}（${it.width}×${it.height}，${it.elapsed}s，pid=${it.pid}）` }));
        }
        status.className = "hint ok-text";
        status.textContent = `✓ 完成 ${res.okCount}/${res.count}｜已寫回 project.json`;
        if (g && g.stoppedLlama) list.append(gpuProgressLine(g, "（本機 LLM 已依設定自動還原）"));
        // 重讀專案，讓人物卡的參考圖縮圖出現
        const fresh = await api(`/api/projects/${state.slug}`);
        if (fresh && fresh.project) { state.project.characters = fresh.project.characters; renderCharacters(); }
      }
    } catch (e) {
      status.className = "hint err-text";
      status.textContent = `✕ 請求失敗：${e.message}`;
    } finally { btn.disabled = false; }
  });
  card.append(el("label", { class: "field" }, el("span", { text: "要生立繪的人物（可多選；Ctrl／Shift 多選）" }), sel));
  card.append(el("div", { class: "row wrap" }, btn, el("span", { class: "spacer" }), status));
  card.append(el("p", { class: "hint", text: "立繪＝正面全身、中性淺灰背景、完整服裝、臉部清楚（不要遮住臉或戴帽：H3 要靠這張臉鎖定角色）。寫入 media/characters/<id>_sheet_<時間>.png。" }));
  card.append(list);
  return card;
}

// ── 場景卡 ───────────────────────────────────────────────
const TIME_OF_DAY = ["清晨", "白天", "黃昏", "夜晚", "深夜"];

function sceneCard(sc) {
  const wrap = el("div", { class: "card scene-card" });
  const cover = (sc.refImages || [])[0];
  const head = el("div", { class: "card-head" },
    el("div", { class: "card-title" },
      el("h3", { text: `${sc.id} · ${sc.name || "（未命名場景）"}` }),
      el("span", { class: "hint", text: `${(sc.refImages || []).length} 張參考圖` }),
    ),
    el("span", { class: "spacer" }),
    chatButton("scene", sc.id),
    el("button", {
      class: "btn btn-small btn-danger", type: "button",
      onclick: () => removeScene(sc.id),
    }, "刪除"),
  );
  wrap.append(head);

  const grid = el("div", { class: "field-grid" });
  grid.append(
    field("場景名稱（name）", sc, "name", "例如：霧中森林", false),
    selectField("時間（timeOfDay）", sc, "timeOfDay", TIME_OF_DAY),
  );
  wrap.append(grid);
  wrap.append(liveText("場景描述（desc）", sc, "desc", "這個場景長什麼樣、有什麼東西", { rows: "3" }));
  const grid2 = el("div", { class: "field-grid" });
  grid2.append(
    field("光線（lighting）", sc, "lighting", "例如：側逆光、月光從樹縫灑下", false),
    field("氛圍（mood）", sc, "mood", "例如：安靜、帶壓迫感", false),
  );
  wrap.append(grid2);
  wrap.append(liveText("英文場景 prompt（promptEn）", sc, "promptEn", "英文場景描述（餵生圖用）", { rows: "3" }));

  const amb = ensure_obj(sc, "ambience", {});
  const ambCard = el("div", { class: "subcard" });
  ambCard.append(el("h4", { text: "環境音（ambience）" }));
  ambCard.append(liveText("環境音說明（note）", amb, "note", "例如：遠處蟲鳴、風吹樹葉", { rows: "2" }));
  ambCard.append(el("label", { class: "field" },
    el("span", { text: "環境音參照音檔（refAudio）" }),
    audioRow(amb.refAudio || "", (v) => { amb.refAudio = v; })));
  wrap.append(ambCard);

  const refCard = el("div", { class: "subcard" });
  if (!Array.isArray(sc.refImages)) sc.refImages = [];
  refCard.append(el("h4", { text: `參考圖（refImages）· ${sc.refImages.length}` }));
  const kindSel = el("select", { class: "inline-select" },
    el("option", { value: "wide", text: "全景（wide）" }),
    el("option", { value: "detail", text: "細節（detail）" }),
  );
  refCard.append(el("div", { class: "row wrap" }, el("span", { class: "hint", text: "上傳類型：" }), kindSel));
  refCard.append(uploadRow("image/*", async (files) => {
    const paths = await uploadFiles(state.slug, "scenes", files);
    if (!paths.length) return;
    for (const p of paths) sc.refImages.push({ path: p, kind: kindSel.value });
    markDirty(); renderViews();
  }, { label: "選擇場景參考圖（可多選）：" }));
  refCard.append(refImageGrid(sc.refImages, {
    title: sc.name || sc.id, kinds: ["wide", "detail"], onKindChange: true,
    empty: "尚無參考圖。可上傳「全景」定調、「細節」補材質。",
  }));
  wrap.append(refCard);
  if (cover) wrap.dataset.cover = cover.path;
  return wrap;
}

function sceneListCard() {
  const card = el("div", { class: "card" });
  const list = state.project.scenes || [];
  card.append(el("div", { class: "card-head" },
    el("h3", { text: `場景列表 · ${list.length}` }),
    el("button", {
      class: "btn btn-small btn-primary", type: "button",
      onclick: () => {
        if (!Array.isArray(state.project.scenes)) state.project.scenes = [];
        const id = nextId("scenes");
        state.project.scenes.push({
          id, name: "", desc: "", timeOfDay: "", lighting: "", mood: "", promptEn: "",
          refImages: [], ambience: { refAudio: "", note: "" },
        });
        markDirty(); renderViews();
      },
    }, "＋ 新增場景"),
  ));
  if (!list.length) {
    card.append(el("p", { class: "hint", text: "尚未有場景。按「＋ 新增場景」，系統會自動編號 sc01、sc02…" }));
  }
  const nav = el("div", { class: "pill-list" });
  list.forEach((sc) => {
    nav.append(el("button", {
      class: "pill", type: "button",
      onclick: () => {
        const node = document.getElementById(`scene-${sc.id}`);
        if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
      },
    },
      el("img", { src: (sc.refImages || [])[0] ? picUrl(sc.refImages[0].path) : "", alt: "" }),
      el("span", { text: `${sc.name || sc.id}（${sc.id}）` }),
    ));
  });
  card.append(nav);
  return card;
}

function removeScene(sid) {
  const list = state.project.scenes || [];
  const idx = list.findIndex((s) => s.id === sid);
  if (idx < 0) return;
  const sc = list[idx];
  const refs = sceneRefs(sid);
  if (!confirmDeleteWithRefs(sc.name || sid, refs)) return;
  if (refs.length) clearSceneRefs(sid);
  list.splice(idx, 1);
  markDirty(); renderViews();
  setSaveStatus("pending", refs.length ? `已刪除 ${sid}，清掉 ${refs.length} 處引用，待儲存…` : "待儲存…");
}

function renderScenes() {
  const box = $("#view-scenes");
  box.textContent = "";
  if (!state.project) { box.append(emptyHint()); return; }
  if (!Array.isArray(state.project.scenes)) state.project.scenes = [];
  box.append(el("h2", { class: "view-title", text: "3 · 場景" }));
  box.append(sceneListCard());
  for (const sc of state.project.scenes) {
    const card = sceneCard(sc);
    card.id = `scene-${sc.id}`;
    box.append(card);
  }
}


// ══════════════════════════════════════════════════════════
// M2b：劇本分頁（集 / 場次 / beats）＋ 鏡頭分頁（分鏡表）
// 規則照舊：資料只走 GET/PUT /api/projects/{slug} 與 /upload，
// 所有下拉都從同一份 project（characters / scenes / script）即時生成，
// 不呼叫任何模型，不改資料格式。
// ══════════════════════════════════════════════════════════

// ── 顯示輔助：代號一律翻成名字 ───────────────────────────
function charNameById(id) {
  const ch = (state.project.characters || []).find((c) => c.id === id);
  if (!ch) return id ? `（已刪除的人物 ${id}）` : "（未指定）";
  return ch.name || ch.id;
}
function sceneNameById(id) {
  const sc = (state.project.scenes || []).find((s) => s.id === id);
  if (!sc) return id ? `（已刪除的場景 ${id}）` : "（未指定場景）";
  return `${sc.name || sc.id}（${sc.id}）`;
}
function episodes() {
  if (!state.project.script || !Array.isArray(state.project.script.episodes)) {
    state.project.script = { episodes: [] };
  }
  return state.project.script.episodes;
}
function findEpisodeScene(esId) {
  if (!esId) return null;
  for (const ep of episodes()) {
    const es = (ep.scenes || []).find((s) => s.id === esId);
    if (es) return { ep, es };
  }
  return null;
}
// 「場次」的顯示名：標題 + 代號
function esLabel(es) {
  return `${es.title || "（未命名場次）"}（${es.id}）`;
}

// ── M14a：分鏡契約（docs/storyboard-contract.md v1）前端配套 ─────────
// role 六種（契約 §2）；顯示名與後端 app/storyboard.py 的 ROLE_LABEL 一致。
const SB_ROLE_LABEL = {
  establish: "建立鏡", dialogue: "對白鏡", reaction: "反應鏡",
  action: "動作鏡", insert: "插入鏡", closing: "收尾鏡",
};
// 幀 ↔ 秒（H3 合法格 17k+5、fps 24；合法域 124–362 幀＝5.17–15.08 秒）
function sbLegalFrames(sec) {
  const fps = 24;
  const raw = Math.round((Number(sec) || 0) * fps);
  const k = raw >= 5 ? Math.max(0, Math.round((raw - 5) / 17)) : 0;
  return 17 * k + 5;
}
// 這一鏡的 beats（以 beatIds 為準；舊資料退回 dialogBeatIds）
function shotBeatsOf(sh) {
  const found = findEpisodeScene(sh.episodeSceneId);
  const all = ((found && found.es && found.es.beats) || []).filter((b) => b && b.id && !b._skipped);
  let ids = Array.isArray(sh.beatIds) ? sh.beatIds : [];
  if (!ids.length) ids = shotDialogBeatIds(sh);
  const byId = new Map(all.map((b) => [b.id, b]));
  return { ids, beats: ids.map((id) => byId.get(id)).filter(Boolean), dialogue: esDialogueBeats(found && found.es) };
}
function sbFmtSec(v) {
  return `${Number(v || 0).toFixed(2).replace(/\.00$/, "")}s`;
}

// ── M12a：鏡頭 ↔ 劇本台詞連動 ────────────────────────────────
// 為什麼要一套共用函式：鏡頭卡、劇本頁、檢查面板都要用「同一份真相」回答
// 「這一句台詞被哪些鏡頭引用」，各自算一次會出現兩頁對不上（就是這次要修的症狀）。
function esDialogueBeats(es) {
  // _skipped 是「整份覆寫時保留的舊項」：不進生成、也不能出現在引用清單裡
  return ((es && es.beats) || []).filter((b) => b && b.type !== "action" && b.id && !b._skipped);
}
// 鏡頭 → 台詞 beat id 清單。新欄位優先；舊資料（只有 dialogLineRefs）在這裡即時換算，
// 舊專案就算沒經過後端遷移也能顯示（後端讀取時已補上，這是第二層保險）。
function shotDialogBeatIds(sh) {
  const found = findEpisodeScene(sh.episodeSceneId);
  const dlg = esDialogueBeats(found && found.es);
  const ids = Array.isArray(sh.dialogBeatIds) ? sh.dialogBeatIds.filter((x) => typeof x === "string") : [];
  const refs = Array.isArray(sh.dialogLineRefs) ? sh.dialogLineRefs.filter((n) => Number.isInteger(n)) : [];
  const want = new Set(ids);
  refs.forEach((n) => { if (n >= 1 && n <= dlg.length) want.add(dlg[n - 1].id); });
  return dlg.filter((b) => want.has(b.id)).map((b) => b.id);
}
// 場次 → {beatId: [引用它的鏡頭 no...]}（依鏡號排序）
function beatRefMap(es) {
  const out = {};
  const id = es && es.id;
  (state.project.shots || []).forEach((sh) => {
    if (sh.episodeSceneId !== id) return;
    shotDialogBeatIds(sh).forEach((b) => { (out[b] = out[b] || []).push(sh.no); });
  });
  Object.values(out).forEach((arr) => arr.sort((a, b) => (a || 0) - (b || 0)));
  return out;
}
// 寫入 dialogBeatIds 時同步重算 dialogLineRefs（後端舊路徑／舊版前端仍讀序號）
function syncShotDialogRefs(sh) {
  const found = findEpisodeScene(sh.episodeSceneId);
  const dlg = esDialogueBeats(found && found.es);
  const ids = shotDialogBeatIds(sh);
  sh.dialogBeatIds = ids;
  sh.dialogLineRefs = ids
    .map((b) => dlg.findIndex((x) => x.id === b) + 1)
    .filter((n) => n > 0)
    .sort((a, b) => a - b);
}


// 集 id：e01、e02…（掃最大號 +1）
function nextEpisodeId() {
  let max = 0;
  for (const ep of episodes()) {
    const m = /^e(\d{2,})$/.exec(String(ep.id || ""));
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  let n = max + 1;
  const used = new Set(episodes().map((e) => e.id));
  let id = `e${String(n).padStart(2, "0")}`;
  while (used.has(id)) { n += 1; id = `e${String(n).padStart(2, "0")}`; }
  return id;
}
// 場次 id：eXXsYY（同集內遞增；集號沿用集的 id）
function nextEpisodeSceneId(ep) {
  const prefix = `${ep.id}s`;
  let max = 0;
  for (const es of ep.scenes || []) {
    const m = new RegExp(`^${prefix}(\\d{2,})$`).exec(String(es.id || ""));
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  let n = max + 1;
  const used = new Set((ep.scenes || []).map((s) => s.id));
  let id = `${prefix}${String(n).padStart(2, "0")}`;
  while (used.has(id)) { n += 1; id = `${prefix}${String(n).padStart(2, "0")}`; }
  return id;
}
// beat id：b01、b02…（同一場次內）
function nextBeatId(es) {
  let max = 0;
  for (const b of es.beats || []) {
    const m = /^b(\d{2,})$/.exec(String(b.id || ""));
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  let n = max + 1;
  const used = new Set((es.beats || []).map((b) => b.id));
  let id = `b${String(n).padStart(2, "0")}`;
  while (used.has(id)) { n += 1; id = `b${String(n).padStart(2, "0")}`; }
  return id;
}
// 鏡頭 id：sh01、sh02…
function nextShotId(extra) {
  // extra：還沒進陣列、但即將建立的鏡頭（一次拆多鏡時要一起算，否則會撞號）
  const pool = [...(state.project.shots || []), ...(extra || [])];
  let max = 0;
  for (const sh of pool) {
    const m = /^sh(\d{2,})$/.exec(String(sh.id || ""));
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  let n = max + 1;
  const used = new Set(pool.map((s) => s.id));
  let id = `sh${String(n).padStart(2, "0")}`;
  while (used.has(id)) { n += 1; id = `sh${String(n).padStart(2, "0")}`; }
  return id;
}

// ── 下拉來源（全部即時掃 project，不寫死）────────────────
// 回 [{value, text}]；value 一律是 id，text 帶名字方便人看
function charOptions(allowEmpty, emptyText) {
  const out = [];
  if (allowEmpty !== false) out.push({ value: "", text: emptyText || "（未指定人物）" });
  for (const ch of state.project.characters || []) {
    out.push({ value: ch.id, text: `${ch.name || "（未命名）"}（${ch.id}${ch.role ? "·" + ch.role : ""}）` });
  }
  return out;
}
function sceneOptions(allowEmpty) {
  const out = [];
  if (allowEmpty !== false) out.push({ value: "", text: "（未指定場景）" });
  for (const sc of state.project.scenes || []) {
    out.push({ value: sc.id, text: `${sc.name || "（未命名）"}（${sc.id}）` });
  }
  return out;
}
function episodeSceneOptions(allowEmpty) {
  const out = [];
  if (allowEmpty !== false) out.push({ value: "", text: "（未指定場次）" });
  for (const ep of episodes()) {
    for (const es of ep.scenes || []) {
      out.push({ value: es.id, text: `${ep.id}／${esLabel(es)}` });
    }
  }
  return out;
}

// 通用下拉：options = [{value,text}]，值不存在時補一條「已失效」提示
function pickSelect(labelText, value, options, onChange) {
  const sel = el("select", {});
  for (const o of options) sel.append(el("option", { value: o.value, text: o.text }));
  if (value && !options.some((o) => o.value === value)) {
    sel.append(el("option", { value, text: `（引用已失效：${value}）` }));
  }
  sel.value = value || "";
  sel.addEventListener("change", () => { onChange(sel.value, sel); });
  return el("label", { class: "field" }, el("span", { text: labelText }), sel);
}

// 多選人物（勾選式，來源人物卡）
function charChecks(labelText, ids, onChange) {
  const box = el("div", { class: "check-grid" });
  const list = state.project.characters || [];
  if (!list.length) {
    box.append(el("p", { class: "hint", text: "尚無人物卡，先到「2 人物」新增人物。" }));
  }
  const current = new Set(Array.isArray(ids) ? ids : []);
  for (const ch of list) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = current.has(ch.id);
    cb.addEventListener("change", () => {
      const next = new Set(cb.checked ? [...current, ch.id] : [...current].filter((x) => x !== ch.id));
      onChange(list.filter((c) => next.has(c.id)).map((c) => c.id));
    });
    box.append(el("label", { class: "checkline" }, cb,
      el("span", { text: `${ch.name || ch.id}（${ch.id}）` })));
  }
  return el("div", { class: "field" }, el("span", { text: labelText }), box);
}

// ── 劇本：集 ─────────────────────────────────────────────
function addEpisode() {
  episodes().push({ id: nextEpisodeId(), title: "", scenes: [] });
  markDirty();
  renderScript();
}
function removeEpisode(ep) {
  const n = (ep.scenes || []).length;
  const msg = n ? `要刪除 ${ep.id}「${ep.title || "（無標題）"}」嗎？它底下 ${n} 個場次一起刪；引用這些場次的鏡頭會把「所屬場次」清空（鏡頭本身不刪）。`
    : `要刪除 ${ep.id}「${ep.title || "（無標題）"}」嗎？`;
  if (!confirm(msg)) return;
  const removed = new Set((ep.scenes || []).map((s) => s.id));
  for (const sh of state.project.shots || []) {
    if (removed.has(sh.episodeSceneId)) sh.episodeSceneId = null;
  }
  const list = episodes();
  list.splice(list.indexOf(ep), 1);
  markDirty();
  renderScript();
  setSaveStatus("pending", "已刪除集，待儲存…");
}

// ── 劇本：場次 ───────────────────────────────────────────
function addSceneToEpisode(ep) {
  if (!Array.isArray(ep.scenes)) ep.scenes = [];
  ep.scenes.push({
    id: nextEpisodeSceneId(ep), title: "", sceneId: null, characterIds: [], summary: "", beats: [],
  });
  markDirty();
  renderScript();
}
function removeEpisodeScene(ep, es) {
  const n = (es.beats || []).length;
  const shots = (state.project.shots || []).filter((sh) => sh.episodeSceneId === es.id).length;
  const bits = [];
  if (n) bits.push(`${n} 個 beat`);
  if (shots) bits.push(`${shots} 個鏡頭「所屬場次」會清空（鏡頭不刪）`);
  if (!confirm(`要刪除場次 ${esLabel(es)} 嗎？${bits.length ? "（" + bits.join("、") + "）" : ""}`)) return;
  for (const sh of state.project.shots || []) {
    if (sh.episodeSceneId === es.id) sh.episodeSceneId = null;
  }
  ep.scenes.splice(ep.scenes.indexOf(es), 1);
  markDirty();
  renderScript();
}
function moveEpisodeScene(ep, idx, delta) {
  const to = idx + delta;
  if (to < 0 || to >= ep.scenes.length) return;
  const [it] = ep.scenes.splice(idx, 1);
  ep.scenes.splice(to, 0, it);
  markDirty();
  renderScript();
}

// ── 劇本：beats ──────────────────────────────────────────
function addBeat(es, type) {
  if (!Array.isArray(es.beats)) es.beats = [];
  es.beats.push({
    id: nextBeatId(es), type: type || "dialogue", characterId: null, line: "", emotion: "", notes: "",
  });
  markDirty();
  renderScript();
}
function removeBeat(es, idx) {
  const gone = es.beats[idx];
  es.beats.splice(idx, 1);
  // M12a：刪掉台詞後，引用它的鏡頭要清掉這筆（留著就是失效引用，存檔會被後端擋）
  if (gone && gone.id) {
    (state.project.shots || []).forEach((sh) => {
      if (sh.episodeSceneId === es.id && Array.isArray(sh.dialogBeatIds) && sh.dialogBeatIds.includes(gone.id)) {
        sh.dialogBeatIds = sh.dialogBeatIds.filter((x) => x !== gone.id);
        syncShotDialogRefs(sh);
      }
    });
  }
  markDirty();
  renderScript();
}
function moveBeat(es, idx, delta) {
  const to = idx + delta;
  if (to < 0 || to >= es.beats.length) return;
  const [it] = es.beats.splice(idx, 1);
  es.beats.splice(to, 0, it);
  markDirty();
  renderScript();
}

function sceneBlock(ep, es, idx) {
  const wrap = el("div", { class: "es-block subcard" });
  wrap.id = `es-${es.id}`;

  wrap.append(el("div", { class: "chapter-bar" },
    el("span", { class: "chapter-id", text: es.id }),
    el("span", { class: "spacer" }),
    chatButton("episodeScene", es.id),
    el("button", { class: "btn btn-small btn-primary", type: "button", title: "用模型把這一場拆成多鏡（預覽後才寫入）", onclick: () => openGenShots(es.id) }, "✦ 生成此場鏡頭"),
    el("button", { class: "btn btn-small", type: "button", title: "往上移", onclick: () => moveEpisodeScene(ep, idx, -1) }, "↑"),
    el("button", { class: "btn btn-small", type: "button", title: "往下移", onclick: () => moveEpisodeScene(ep, idx, 1) }, "↓"),
    el("button", { class: "btn btn-small btn-danger", type: "button", title: "刪除本場次", onclick: () => removeEpisodeScene(ep, es) }, "刪除場次"),
  ));

  const grid = el("div", { class: "field-grid" });
  grid.append(field("場次標題（title）", es, "title", "例如：霧林相遇", false));
  grid.append(pickSelect("場景卡（sceneId）", es.sceneId, sceneOptions(true), (v) => {
    es.sceneId = v || null;
    markDirty(); renderScript();
  }));
  wrap.append(grid);
  wrap.append(charChecks("出場人物（characterIds，勾選＝此場次會出現的人）", es.characterIds, (ids) => {
    es.characterIds = ids;
    markDirty();
  }));
  // 場景名摘要（翻成名字，不只給代號）
  const chNames = (es.characterIds || []).map(charNameById);
  wrap.append(el("p", { class: "ref-line" },
    el("span", { class: "hint", text: "場景：" }),
    el("strong", { text: sceneNameById(es.sceneId) }),
    el("span", { class: "hint", text: "　人物：" }),
    el("strong", { text: chNames.length ? chNames.join("、") : "（未指定）" }),
  ));
  wrap.append(liveText("場次摘要（summary）", es, "summary", "這場戲發生什麼事（給模型與分鏡用）", { rows: "2" }));

  // beats
  if (!Array.isArray(es.beats)) es.beats = [];
  const beats = es.beats;
  // M13a：「解析成多欄」整份覆寫時，舊項會以 _skipped 留在檔案裡（可還原、不進生成）。
  // 畫面上必須只算「有效的」那些，否則使用者會看到同一句台詞出現兩次。
  const liveBeats = beats.filter((b) => b && !b._skipped);
  const deadBeats = beats.filter((b) => b && b._skipped);
  const bHead = el("div", { class: "subcard-head" },
    el("h4", { text: `節拍 beats · ${liveBeats.length}${deadBeats.length ? `（另有 ${deadBeats.length} 項已作廢）` : ""}` }),
    el("span", { class: "spacer" }),
    el("button", { class: "btn btn-small", type: "button", onclick: () => addBeat(es, "dialogue") }, "＋ 對白"),
    el("button", { class: "btn btn-small", type: "button", onclick: () => addBeat(es, "action") }, "＋ 動作"),
  );
  const bCard = el("div", { class: "beat-list" }, bHead);
  if (!liveBeats.length) bCard.append(el("p", { class: "hint", text: "尚無 beat。對白與動作都各算一個 beat，順序就是戲的節奏。" }));
  liveBeats.forEach((b) => bCard.append(beatRow(es, b, beats.indexOf(b))));
  if (deadBeats.length) {
    const dead = el("details", { class: "beat-dead" },
      el("summary", { class: "hint", text: `已作廢的 ${deadBeats.length} 項（整份覆寫時保留的舊內容，不進生成）` }));
    deadBeats.forEach((b) => {
      dead.append(el("div", { class: "beat-dead-row" },
        el("span", { class: "beat-kind", text: b.type === "action" ? "動作" : "對白" }),
        el("span", { class: "hint mono", text: b.id || "" }),
        el("span", { class: "hint", text: (b.line || "").slice(0, 60) }),
        el("span", { class: "spacer" }),
        el("button", {
          class: "btn btn-small", type: "button", title: "把這項重新啟用（回到 beats 列表）",
          onclick: () => { delete b._skipped; markDirty(); renderScript(); },
        }, "還原"),
      ));
    });
    bCard.append(dead);
  }
  wrap.append(bCard);
  return wrap;
}

function beatRow(es, b, idx) {
  const isDialogue = b.type !== "action";
  const row = el("div", { class: "beat-row " + (isDialogue ? "dialogue" : "action") });

  row.append(el("div", { class: "chapter-bar" },
    el("span", { class: "beat-no", text: `#${idx + 1}` }),
    el("span", { class: "beat-id mono", text: b.id || "" }),
    el("span", { class: "beat-kind", text: isDialogue ? "對白" : "動作" }),
    el("span", { class: "spacer" }),
    chatButton("beat", b.id, { parentId: es.id }),
    el("button", { class: "btn btn-small", type: "button", title: "往上移", onclick: () => moveBeat(es, idx, -1) }, "↑"),
    el("button", { class: "btn btn-small", type: "button", title: "往下移", onclick: () => moveBeat(es, idx, 1) }, "↓"),
    el("button", { class: "btn btn-small btn-danger", type: "button", title: "刪除這個 beat", onclick: () => removeBeat(es, idx) }, "✕"),
  ));

  const top = el("div", { class: "field-grid" });
  const whoText = b.characterId
    ? (charNameById(b.characterId) || `（人物已刪除：${b.characterId}）`)
    : "（未指定）";
  top.append(pickSelect(isDialogue ? "角色（characterId）" : "執行角色（characterId，可留空）",
    b.characterId || null, charOptions(true), (v) => {
      b.characterId = v || null;
      markDirty();
      // 角色名顯示行就地更新，不必整頁重畫（保住正在打的字）
      const line = row.querySelector(".beat-who");
      if (line) line.textContent = b.characterId ? charNameById(b.characterId) : "（未指定）";
    }));
  top.append(field("情緒（emotion）", b, "emotion", isDialogue ? "例如：低聲、壓抑、顫抖" : "例如：慌張", false));
  row.append(top);
  row.append(isDialogue
    ? liveText("台詞（line）", b, "line", "這一句話的原文", { rows: "2" })
    : liveText("動作描述（line）", b, "line", "例如：她把斗篷拉緊，往林裡看了第二眼", { rows: "2" }));
  row.append(field("備註（notes）", b, "notes", "例如：此句配旁白、需特寫手部", false));
  row.append(el("p", { class: "ref-line" },
    el("span", { class: "hint", text: "顯示：" }),
    el("strong", { class: "beat-who", text: whoText }),
  ));
  // M12a：這句台詞被哪些鏡頭引用（反過來看劇本頁就知道有沒有鏡頭在演這句）
  if (isDialogue && b.id) {
    const refShots = (beatRefMap(es)[b.id] || []);
    row.append(el("p", { class: refShots.length ? "ref-line beat-refs" : "ref-line beat-refs none" },
      refShots.length
        ? el("span", { text: `← 被 ${refShots.map((n) => `sh${String(n).padStart(2, "0")}`).join("、")} 引用` })
        : el("span", { text: "（尚無鏡頭引用）" }),
    ));
  }
  return row;
}

// ── 劇本分頁入口 ─────────────────────────────────────────
function renderScript() {
  const box = $("#view-script");
  box.textContent = "";
  if (!state.project) { box.append(emptyHint()); return; }
  const eps = episodes();
  box.append(el("h2", { class: "view-title", text: "4 · 劇本" }));
  box.append(el("p", { class: "hint", text: "集 → 場次 → 節拍（beat）。場次對映「3 場景」的場景卡，人物勾選來自「2 人物」的人物卡；所有下拉都即時從專案資料生成，不寫死。" }));

  const card = el("div", { class: "card" });
  card.append(el("div", { class: "card-head" },
    el("h3", { text: `集（episodes）· ${eps.length}` }),
    el("span", { class: "spacer" }),
    el("button", { class: "btn btn-small btn-primary", type: "button", title: "用故事／人物／場景卡一次生成一集（預覽後才寫入）", onclick: () => openGenScript() }, "✦ 由故事生成整集劇本"),
    el("button", { class: "btn btn-small btn-primary", type: "button", onclick: () => addEpisode() }, "＋ 新增一集"),
  ));
  if (!eps.length) {
    card.append(el("p", { class: "hint", text: "尚無集。按「＋ 新增一集」，系統自動編 e01、e02…" }));
  }
  box.append(card);

  for (const ep of eps) {
    const epCard = el("div", { class: "card ep-card" });
    epCard.id = `ep-${ep.id}`;
    if (!Array.isArray(ep.scenes)) ep.scenes = [];
    epCard.append(el("div", { class: "card-head" },
      el("div", { class: "card-title" },
        el("h3", { text: `${ep.id} · ${ep.title || "（未命名集）"}` }),
        el("span", { class: "hint", text: `${ep.scenes.length} 個場次 · ${ep.scenes.reduce((n, s) => n + (s.beats || []).filter((b) => b && !b._skipped).length, 0)} 個 beats` }),
      ),
      el("span", { class: "spacer" }),
      el("button", { class: "btn btn-small btn-primary", type: "button", onclick: () => addSceneToEpisode(ep) }, "＋ 新增場次"),
      el("button", { class: "btn btn-small btn-danger", type: "button", onclick: () => removeEpisode(ep) }, "刪除集"),
    ));
    epCard.append(field("集標題（title）", ep, "title", "例如：第一話 · 進森林", false));
    if (!ep.scenes.length) {
      epCard.append(el("p", { class: "hint", text: "這一集還沒有場次。按「＋ 新增場次」。" }));
    }
    ep.scenes.forEach((es, i) => epCard.append(sceneBlock(ep, es, i)));
    box.append(epCard);
  }
}

// ══════════════════════════════════════════════════════════
// 鏡頭分頁
// ══════════════════════════════════════════════════════════
// spec.shotType 的合法值是英文（docs/data-model.md v0.2；後端 storage.shot_type_en 也照這個轉），
// 顯示翻中文。舊資料若存中文（0.2 之前）用 SHOT_TYPE_ALIAS 轉回來，不要顯示「引用已失效」。
const SHOT_TYPES = [
  { value: "close_up", text: "特寫（close_up）" },
  { value: "medium", text: "中景（medium）" },
  { value: "wide", text: "全景（wide）" },
  { value: "over_shoulder", text: "過肩（over_shoulder）" },
  { value: "pov", text: "POV（pov）" },
];
const SHOT_TYPE_ALIAS = {
  "特寫": "close_up", "中景": "medium", "全景": "wide", "過肩": "over_shoulder", "POV": "pov", "其他": "other",
};
function normShotType(v) {
  const s0 = String(v || "").trim();
  return SHOT_TYPE_ALIAS[s0] || s0;
}
// v0.2：spec.angle = {h, v, s}（見 docs/data-model.md）
const ANGLE_H = [
  ["front", "正面（front）"], ["front_left", "左前（front_left）"], ["left", "左側（left）"],
  ["back_left", "左後（back_left）"], ["back", "背面（back）"], ["back_right", "右後（back_right）"],
  ["right", "右側（right）"], ["front_right", "右前（front_right）"],
];
const ANGLE_V = [["worm", "蟲視（worm）"], ["low", "低角度（low）"], ["eye_level", "平視（eye_level）"], ["high", "高角度（high）"]];
const ANGLE_S = [["close_up", "特寫（close_up）"], ["medium", "中景（medium）"], ["wide", "全景（wide）"]];
const VIDEO_STATUS_LABEL = { none: "未生成", queued: "排隊中", running: "生成中", done: "已完成", failed: "失敗" };
const VIDEO_STATUS_CLASS = { none: "st-none", queued: "st-queued", running: "st-running", done: "st-done", failed: "st-failed" };

function newShotNo(list) {
  return (list || []).reduce((m, s) => Math.max(m, Number(s.no) || 0), 0) + 1;
}

function blankShot(no, esId, extra) {
  const found = findEpisodeScene(esId || "");
  return {
    id: nextShotId(extra),
    no,
    episodeSceneId: found ? found.es.id : null,
    sceneId: found ? (found.es.sceneId || null) : null,
    characterIds: found && Array.isArray(found.es.characterIds) ? [...found.es.characterIds] : [],
    spec: { shotType: "medium", angle: "", cameraMove: "", composition: "", lighting: "" },
    durationSec: 5,
    promptZh: "",
    promptEn: "",
    dialogue: [],
    dialogBeatIds: [],
    dialogLineRefs: [],
    images: { first: "", last: "" },
    video: { path: "", status: "none", taskType: "r2v", seed: 0, note: "" },
    voice: { path: "", engine: "" },
    durationFrames: 0,
    updatedAt: "",
  };
}

function addShot(filterEsId) {
  const list = state.project.shots || (state.project.shots = []);
  list.push(blankShot(newShotNo(list), filterEsId || ""));
  markDirty();
  renderShots();
}
function removeShot(sh) {
  if (!confirm(`要刪除鏡頭 第 ${sh.no} 鏡（${sh.id}）嗎？之後的鏡頭 no 會自動重排。`)) return;
  const list = state.project.shots;
  list.splice(list.indexOf(sh), 1);
  markDirty();
  renderShots();
}
function moveShot(sh, delta) {
  const list = state.project.shots;
  const idx = list.indexOf(sh);
  const to = idx + delta;
  if (to < 0 || to >= list.length) return;
  const [it] = list.splice(idx, 1);
  list.splice(to, 0, it);
  markDirty();
  renderShots();
}
// no 依陣列順序重排成 1..N（不動 id）
function renumberShots() {
  (state.project.shots || []).forEach((sh, i) => { sh.no = i + 1; });
}

// 「由場次拆鏡頭草稿」：純規則，每 beat 一鏡；不覆蓋既有鏡頭
function splitDraftFromScene(esId) {
  const found = findEpisodeScene(esId);
  if (!found) { alert("請先選一個場次。"); return; }
  const { es } = found;
  const beats = Array.isArray(es.beats) ? es.beats : [];
  if (!beats.length) { alert(`場次 ${esLabel(es)} 沒有 beats，先在「4 劇本」加幾個再拆。`); return; }
  const list = state.project.shots || (state.project.shots = []);
  const scene = (state.project.scenes || []).find((s) => s.id === es.sceneId);
  let no = newShotNo(list);
  let created = 0;
  const createdShots = [];
  for (const b of beats) {
    const isDialogue = b.type !== "action";
    const sh = blankShot(no, es.id, createdShots);
    no += 1;
    sh.sceneId = es.sceneId || null;
    sh.characterIds = Array.isArray(es.characterIds) ? [...es.characterIds] : [];
    sh.spec.shotType = isDialogue ? "close_up" : "medium";
    if (isDialogue) {
      // M12a：主要台詞走「引用劇本句子」；舊的 dialogue 只放原文給人看，不再當成台詞來源
      sh.dialogBeatIds = b.id ? [b.id] : [];
      syncShotDialogRefs(sh);
      sh.dialogue = [];
      sh.promptZh = `《${es.title || es.id}》${charNameById(b.characterId)}${b.emotion ? "（" + b.emotion + "）" : ""}說：「${b.line || ""}」`;
    } else {
      sh.promptZh = `《${es.title || es.id}》動作：${b.line || "（未填）"}${b.notes ? "（" + b.notes + "）" : ""}`;
    }
    if (scene) {
      sh.promptZh += ` 場景：${scene.name || scene.id}${scene.timeOfDay ? "·" + scene.timeOfDay : ""}。`;
      if (scene.lighting) sh.spec.lighting = scene.lighting;
    }
    sh.updatedAt = new Date().toISOString();
    createdShots.push(sh);
    created += 1;
  }
  // 新鏡頭插在「同場次最後一鏡」之後（同場次尚無鏡頭就放尾端），讓分鏡表分組保持連續
  const sameIdx = [];
  list.forEach((x, i) => { if (x.episodeSceneId === es.id) sameIdx.push(i); });
  const target = sameIdx.length ? sameIdx[sameIdx.length - 1] + 1 : list.length;
  list.splice(target, 0, ...createdShots);
  markDirty();
  renderShots();
  setSaveStatus("pending", `已由 ${es.id} 拆出 ${created} 個鏡頭草稿，待儲存…`);
}

// 鏡頭卡右上角的批次勾選框（只有「已勾選鏡頭」範圍會用到）
function batchShotCheck(sh) {
  const chk = el("input", { type: "checkbox", class: "batch-check", title: "勾選後可用「批次生圖 → 已勾選鏡頭」" });
  chk.checked = !!batchState.picked[sh.id];
  chk.addEventListener("change", () => {
    batchState.picked[sh.id] = chk.checked;
    const row = document.querySelector(".batch-row");
    if (row) {
      const n = batchPickedIds().length;
      const hint = row.querySelector("span.hint:last-child");
      if (hint) hint.textContent = `· 已勾選 ${n} 鏡` + (n ? "" : "（選「已勾選鏡頭」範圍時要有勾選）");
    }
  });
  return el("label", { class: "hint inline-check", title: "加入批次生圖清單" }, chk, el("span", { text: "批次" }));
}

function shotCard(sh, idx) {
  const card = el("article", { class: "card shot-card" });
  card.dataset.shotId = sh.id;
  const thumb = sh.images && sh.images.first ? picUrl(sh.images.first) : "";
  const foundEs = findEpisodeScene(sh.episodeSceneId);

  card.append(el("div", { class: "card-head shot-head" },
    thumb ? el("img", {
      class: "card-avatar", src: thumb, alt: `第 ${sh.no} 鏡首幀`,
      onclick: () => openLightbox(thumb, `第 ${sh.no} 鏡 · 首幀`),
    }) : el("div", { class: "card-avatar empty shot-no-box" }, el("span", { text: `#${sh.no}` })),
    el("div", { class: "card-title" },
      el("h3", { text: `第 ${sh.no} 鏡 · ${sh.id}` }),
      el("span", { class: "hint", text: `${foundEs ? esLabel(foundEs.es) : "（未指定場次）"} · ${sceneNameById(sh.sceneId)} · ${sh.durationSec || 5} 秒${sh.video?.note ? " · 影片備註：" + sh.video.note : ""}` }),
    ),
    el("span", {
      class: "badge " + (VIDEO_STATUS_CLASS[sh.video?.status] || "st-none"),
      text: `影片：${VIDEO_STATUS_LABEL[sh.video?.status] || sh.video?.status || "未生成"}`,
    }),
    el("span", { class: "spacer" }),
    batchShotCheck(sh),
    chatButton("shot", sh.id),
    el("button", { class: "btn btn-small", type: "button", title: "往上移（no 自動重排）", onclick: () => moveShot(sh, -1) }, "↑"),
    el("button", { class: "btn btn-small", type: "button", title: "往下移（no 自動重排）", onclick: () => moveShot(sh, 1) }, "↓"),
    el("button", { class: "btn btn-small btn-danger", type: "button", onclick: () => removeShot(sh) }, "刪除"),
  ));

  // M14a：這一鏡的職責與它所演的 beats（beatIds 是真相；台詞逐字取自劇本，不在鏡頭裡另存一份）
  const sbInfo = shotBeatsOf(sh);
  const pad = Number(sh.padSec || 0);
  const roleKey = String(sh.role || "");
  const sbBox = el("div", { class: `subcard sb-shot-info${roleKey ? ` sb-role-${roleKey}` : ""}` });
  sbBox.append(el("div", { class: "sb-shot-head" },
    roleKey
      ? el("span", { class: `sb-role sb-role-${roleKey}`, text: SB_ROLE_LABEL[roleKey] || roleKey })
      : el("span", { class: "sb-role sb-role-none", text: "職責未設定" }),
    el("span", { class: "mono sb-beat-ids", text: sbInfo.ids.length ? `beatIds: ${sbInfo.ids.join(", ")}` : "（本鏡沒有引用 beat：舊資料）" }),
    sh.beatPart ? el("span", { class: "sb-tag", text: `拆鏡 ${sh.beatPart.part}/${sh.beatPart.total}（beat ${sh.beatPart.beatId}）` }) : null,
    sh.derivedFrom ? el("span", { class: "hint", text: `來源：${{ storyboard: "規則式分鏡", deploy: "一鍵佈署", manual: "手動" }[sh.derivedFrom] || sh.derivedFrom}` }) : null,
    el("span", { class: "hint", text: `${sbLegalFrames(sh.durationSec)} 幀／${sh.durationSec || 5}s` }),
    pad > 0 ? el("span", { class: "hint warn-text", text: `（其中 ${sbFmtSec(pad)} 為留白：停留／呼吸／環境）` }) : null,
    sbInfo.ids.length > 1 ? el("span", { class: "sb-tag", text: `合併 ${sbInfo.ids.length} 句（H3 最短 5.17 秒）` }) : null,
  ));
  if (sbInfo.beats.length) {
    const ul = el("ul", { class: "sb-beat-list" });
    for (const b of sbInfo.beats) {
      const isDlg = b.type !== "action";
      ul.append(el("li", {},
        el("span", { class: "hint", text: `${b.id} · ${isDlg ? (b.characterId ? charNameById(b.characterId) : "（旁白／畫外）") : "動作"}${b.emotion ? `（${b.emotion}）` : ""}：` }),
        el("span", { text: isDlg ? `「${b.line || "（空台詞）"}」` : (b.line || "（未填動作）") }),
      ));
    }
    sbBox.append(ul);
  } else {
    sbBox.append(el("p", { class: "hint", text: "這一鏡沒有 beatIds。按上方「🎬 由劇本自動分鏡」重建，或到下方「引用劇本台詞」勾選句子。" }));
  }
  card.append(sbBox);

  // 基本
  const base = el("div", { class: "field-grid" });
  const noIn = el("input", { type: "number", min: "1", step: "1" });
  noIn.value = String(sh.no ?? idx + 1);
  noIn.addEventListener("input", () => {
    const v = parseInt(noIn.value || "", 10);
    if (Number.isFinite(v) && v > 0) { sh.no = v; markDirty(); }
  });
  base.append(el("label", { class: "field" }, el("span", { text: "鏡號（no）" }), noIn));
  base.append(pickSelect("所屬場次（episodeSceneId）", sh.episodeSceneId, episodeSceneOptions(true), (v) => {
    sh.episodeSceneId = v || null;
    const f = findEpisodeScene(v);
    if (f) {
      // 換場次時，空著的場景／人物自動帶入該場次的設定
      if (!sh.sceneId) sh.sceneId = f.es.sceneId || null;
      if (!(sh.characterIds || []).length) sh.characterIds = [...(f.es.characterIds || [])];
    }
    markDirty();
    renderShots();
  }));
  card.append(base);
  card.append(charChecks("角色（characterIds，多選）", sh.characterIds, (ids) => {
    sh.characterIds = ids; markDirty();
  }));
  card.append(stageRefChecks(sh));

  // 畫面與運鏡
  const spec = ensure_obj(sh, "spec", {});
  const specCard = el("div", { class: "subcard" });
  specCard.append(el("h4", { text: "畫面與運鏡（spec）" }));
  const sg = el("div", { class: "field-grid" });
  sg.append(pickSelect("鏡頭類型（shotType）", normShotType(spec.shotType) || "medium",
    SHOT_TYPES, (v) => { spec.shotType = v; markDirty(); }));
  sg.append(angleSelects(sh, spec));
  sg.append(field("運鏡（cameraMove）", spec, "cameraMove", "例如：緩慢推近、手持跟拍", false));
  sg.append(field("構圖（composition）", spec, "composition", "例如：中心構圖、三分法、前景遮擋", false));
  sg.append(field("光線（lighting）", spec, "lighting", "例如：側逆光、燭光主光", false));
  const dur = el("input", { type: "number", min: "1", max: "60", step: "0.01" });
  dur.value = String(sh.durationSec ?? 5);
  dur.addEventListener("input", () => {
    const v = parseFloat(dur.value || "5");
    sh.durationSec = Number.isFinite(v) && v > 0 ? v : 5;
    markDirty();
  });
  // 契約 V3：合法格只有 17k+5 幀（124–362）；一按就吸附，免得存檔被校驗擋
  const snapBtn = el("button", { class: "btn btn-small", type: "button", title: "吸附到最近的 H3 合法格（17k+5 幀、124–362）" }, "吸附合法格");
  snapBtn.addEventListener("click", () => {
    const f = sbLegalFrames(sh.durationSec);
    if (f < 124 || f > 362) {
      alert(`吸附後是 ${f} 幀，超出自動可修的範圍（合法域 124–362 幀＝5.17–15.08 秒）。請拆鏡或改長度。`);
      return;
    }
    sh.durationSec = Number((f / 24).toFixed(2));
    const need = Number(sh.padSec || 0);
    if (need > sh.durationSec) sh.padSec = 0;
    markDirty();
    renderShots();
    setSaveStatus("pending", `長度已吸附到 ${f} 幀（${sh.durationSec}s），待儲存…`);
  });
  sg.append(el("label", { class: "field" }, el("span", { text: "長度秒數（durationSec）" }), dur));
  sg.append(el("label", { class: "field" }, el("span", { text: "留白秒數（padSec，純停留不算劇情）" }),
    (() => {
      const padIn = el("input", { type: "number", min: "0", max: "14", step: "0.01" });
      padIn.value = String(sh.padSec || 0);
      padIn.addEventListener("input", () => {
        const v = parseFloat(padIn.value || "0");
        sh.padSec = Number.isFinite(v) && v > 0 ? v : 0;
        markDirty();
      });
      return padIn;
    })()));
  sg.append(pickSelect("職責（role，契約 V5）", sh.role || "", [
    { value: "", text: "（未設定）" },
    ...Object.entries(SB_ROLE_LABEL).map(([v, t]) => ({ value: v, text: `${t}（${v}）` })),
  ], (v) => { sh.role = v || null; markDirty(); renderShots(); }));
  sg.append(el("div", { class: "field" }, el("span", { text: "影格自檢" }),
    el("div", { class: "row wrap" },
      el("span", { class: "hint mono", text: `${sbLegalFrames(sh.durationSec)} 幀` }),
      (sbLegalFrames(sh.durationSec) < 124 || sbLegalFrames(sh.durationSec) > 362)
        ? el("span", { class: "hint err-text", text: "不在 124–362（校驗會報 V3 錯誤）" })
        : null,
      snapBtn)));
  specCard.append(sg);
  card.append(specCard);

  // prompts
  const prCard = el("div", { class: "subcard" });
  prCard.append(el("h4", { text: "提示詞（prompt）" }));
  prCard.append(liveText("中文提示（promptZh，給人看／給模型擴寫）", sh, "promptZh", "這個鏡頭畫面發生什麼事、誰在畫面裡、怎麼拍", { rows: "3" }));
  prCard.append(liveText("英文提示（promptEn，H3 生成用）", sh, "promptEn", "英文描述；不會寫可按下面「✦ 用欄位組英文草稿」或「⌘ 模板編譯 prompt」", { rows: "4", attrs: { "data-prompt-for": sh.id } }));
  const genEn = el("button", { class: "btn btn-small", type: "button" }, "✦ 用欄位組英文草稿");
  genEn.addEventListener("click", () => {
    if (!confirm("用目前的中文提示與運鏡欄位覆寫英文提示嗎？（純字串拼接，不會呼叫模型）")) return;
    sh.promptEn = composeShotPromptEn(sh);
    markDirty();
    renderShots();
    setSaveStatus("pending", "已填入英文草稿，待儲存…");
  });
  prCard.append(el("div", { class: "row wrap" },
    el("span", { class: "hint", text: "H3 六段式寫法：主體／動作／運鏡／場景／光線／風格。" }),
    el("span", { class: "spacer" }),
    genEn,
  ));
  card.append(prCard);

  // M12a：引用劇本台詞（正式台詞的真相在劇本頁的 beat；鏡頭只存 beat id）
  if (!Array.isArray(sh.dialogBeatIds)) sh.dialogBeatIds = shotDialogBeatIds(sh);
  if (!Array.isArray(sh.dialogLineRefs)) sh.dialogLineRefs = [];
  const refCard = el("div", { class: "subcard dialog-ref-card" });
  refCard.append(el("div", { class: "subcard-head" },
    el("h4", { text: "引用劇本台詞（dialogBeatIds）" }),
    el("span", { class: "hint", text: "勾選＝這一鏡演出這幾句劇本台詞（存句子 id，劇本插／刪／搬句都不會錯位）" }),
  ));
  const foundForRef = findEpisodeScene(sh.episodeSceneId);
  const refIds = shotDialogBeatIds(sh);
  if (!foundForRef) {
    refCard.append(el("p", { class: "hint warn-text", text: "請先在上面選所屬場次" }));
  } else {
    const dlgBeats = esDialogueBeats(foundForRef.es);
    const refMap = beatRefMap(foundForRef.es);
    const list = el("div", { class: "dialog-ref-list" });
    if (!dlgBeats.length) {
      list.append(el("p", { class: "hint", text: "這個場次還沒有台詞。請先到劇本頁在這一場加「對白」。" }));
    }
    dlgBeats.forEach((b, bi) => {
      const mine = refIds.includes(b.id);
      const others = (refMap[b.id] || []).filter((n) => n !== sh.no);
      const cb = el("input", { type: "checkbox" });
      cb.checked = mine;
      cb.addEventListener("change", () => {
        const cur = new Set(shotDialogBeatIds(sh));
        if (cb.checked) cur.add(b.id); else cur.delete(b.id);
        sh.dialogBeatIds = [...cur];
        syncShotDialogRefs(sh);
        markDirty();
        renderShots();
      });
      const who = b.characterId ? charNameById(b.characterId) : "（旁白／畫外）";
      const emo = b.emotion ? `（${b.emotion}）` : "";
      const row = el("label", { class: "dialog-ref-row" },
        cb,
        el("span", { class: "dialog-ref-no", text: `第 ${bi + 1} 句` }),
        el("span", { class: "dialog-ref-who", text: `${who}：` }),
        el("span", { class: "dialog-ref-line", text: `「${b.line || "（空台詞）"}」${emo}` }),
      );
      if (mine) row.append(el("span", { class: "dialog-ref-tag mine", text: "本鏡引用" }));
      if (!mine && !others.length) row.append(el("span", { class: "dialog-ref-tag none", text: "沒有鏡頭引用" }));
      if (!mine && others.length) row.append(el("span", { class: "dialog-ref-tag other", text: `被 ${others.map((n) => `sh${String(n).padStart(2, "0")}`).join("、")} 引用` }));
      list.append(row);
    });
    refCard.append(list);
    // 唯讀區：一眼看到這鏡實際會用哪些台詞（依劇本順序＝演出順序）
    const readout = el("div", { class: "dialog-ref-readout" });
    readout.append(el("h5", { text: `本鏡台詞（依引用順序）· ${refIds.length}` }));
    if (!refIds.length) {
      readout.append(el("p", { class: "hint", text: "這一鏡目前不引用任何劇本台詞（會當作純畫面）。" }));
    }
    refIds.forEach((bid) => {
      const b = dlgBeats.find((x) => x.id === bid);
      const n = dlgBeats.findIndex((x) => x.id === bid) + 1;
      const who = b ? (b.characterId ? charNameById(b.characterId) : "（旁白／畫外）") : "（引用失效）";
      readout.append(el("p", { class: "dialog-ref-quote" },
        el("span", { class: "hint", text: `第 ${n} 句 · ${who}：` }),
        b ? `「${b.line || "（空台詞）"}」` : `找不到句子 ${bid}`,
      ));
    });
    refCard.append(readout);
  }
  card.append(refCard);

  // 對白：舊欄位保留（臨時對白），正式台詞請用上面的引用
  const dCard = el("div", { class: "subcard" });
  if (!Array.isArray(sh.dialogue)) sh.dialogue = [];
  dCard.append(el("div", { class: "subcard-head" },
    el("h4", { text: `對白（dialogue）· ${sh.dialogue.length}` }),
    el("span", { class: "spacer" }),
    el("button", {
      class: "btn btn-small", type: "button",
      onclick: () => {
        sh.dialogue.push({ characterId: (sh.characterIds || [])[0] || null, line: "" });
        markDirty(); renderShots();
      },
    }, "＋ 一句對白"),
  ));
  dCard.append(el("p", { class: "hint" },
    "本鏡臨時對白（不寫進劇本）；正式台詞請用上面的『引用劇本台詞』。"));
  if (!sh.dialogue.length) dCard.append(el("p", { class: "hint", text: "這個鏡頭沒有臨時對白。" }));
  sh.dialogue.forEach((d, di) => {
    const row = el("div", { class: "dlg-row" });
    row.append(pickSelect("角色（characterId）", d.characterId || null, charOptions(true), (v) => {
      d.characterId = v || null; markDirty();
    }));
    row.append(liveText("台詞（line）", d, "line", "這一鏡說的台詞", { rows: "2" }));
    row.append(el("div", { class: "row" },
      el("span", { class: "hint", text: `顯示：${charNameById(d.characterId)}` }),
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn btn-small btn-danger", type: "button",
        onclick: () => { sh.dialogue.splice(di, 1); markDirty(); renderShots(); },
      }, "刪除這句"),
    ));
    dCard.append(row);
  });
  card.append(dCard);

  // 首尾幀
  const imgCard = el("div", { class: "subcard" });
  const images = ensure_obj(sh, "images", { first: "", last: "" });
  const genStatus = el("span", { class: "hint" });
  const genBtn = el("button", {
    class: "btn btn-small btn-primary", type: "button", title: "生這一鏡的首幀（Qwen 2.1 i2i，帶參考圖）",
  }, "⚡ 生圖");
  const t2iBtn = el("button", {
    class: "btn btn-small", type: "button",
    title: "純文字生圖（不帶參考圖，Qwen Image 2.1 t2i）：沒有參考圖或想先看構圖時用",
  }, "只文字生圖");
  const curEngine = () => ((state.project && state.project.settings && state.project.settings.imageEngine) || "qwen");
  const onGen = (mode) => {
    const eng = curEngine();
    const engNote = eng === "moss"
      ? "引擎＝MOSS（純文字 caption、不吃參考圖；身份靠 caption 描述，約 1–3 分鐘）"
      : (mode === "ref" ? "引擎＝Qwen（i2i：帶角色鎖臉圖／場景圖，約 10–40 秒）"
                        : "引擎＝Qwen（t2i：不帶參考圖，臉不會鎖）");
    if (!confirm(`用本機 ComfyUI 生「第 ${sh.no} 鏡」的首幀嗎？\n${engNote}\n會覆寫首幀，舊檔保留在 media/`)) return;
    generateShotImage(sh, mode, [genBtn, t2iBtn], genStatus, imgCard, genLogBox);
  };
  genBtn.addEventListener("click", () => onGen("ref"));
  t2iBtn.addEventListener("click", () => onGen("t2i"));
  // M12b：生圖引擎的官方提示詞規則（MOSS caption／Qwen 官方槽位）＋本次要送出的提示詞預覽
  const ruleNow = () => (curEngine() === "moss" ? "moss_t2i" : "qwen_image");
  const ruleBadge = el("span", { class: "badge rule-badge", text: `規則：${promptRuleLabel(ruleNow())}` });
  const pp = promptPreviewPanel(ruleNow(), sh.id);
  const ppBtn = promptPreviewButton(ruleNow(), sh.id, pp);
  const syncRuleBadge = () => { ruleBadge.textContent = `規則：${promptRuleLabel(ruleNow())}`; };
  imgCard.append(el("div", { class: "subcard-head" },
    el("h4", { text: "首尾幀（images）" }),
    el("span", { class: "spacer" }),
    ruleBadge, ppBtn, genStatus, genBtn, t2iBtn,
  ));
  imgCard.append(pp.box);
  imgCard.append(el("p", { class: "hint",
    text: "生圖前會先過該引擎的官方提示詞規則（app/promptrules.py）；不合格會擋下並列出缺什麼，不會默默送出。" }));
  const genLogBox = el("div", { class: "gen-log hidden" });
  imgCard.append(genLogBox);
  // 重繪後把生圖狀態（忙碌／錯誤／上一次結果）接回來
  setTimeout(() => paintGenState(sh, [genBtn, t2iBtn], genStatus, imgCard, genLogBox), 0);
  const frameGrid = el("div", { class: "frame-grid" });
  imgCard.append(frameGrid);
  frameGrid.append(...["first", "last"].map((kind) => {
    const label = kind === "first" ? "首幀（first）" : "尾幀（last）";
    const box = el("div", { class: "frame-box" },
      el("div", { class: "frame-label", text: label }),
    );
    const path = images[kind];
    if (path) {
      const src = picUrl(path);
      box.append(el("div", { class: "frame-thumb" },
        el("img", { src, alt: label, loading: "lazy", class: "zoomable", onclick: () => openLightbox(src, `${sh.id} · ${label}`) }),
        el("button", {
          class: "thumb-del frame-del", type: "button", title: "移除這張（檔案仍留在 media/）",
          onclick: () => { if (confirm("要移除這張幀嗎？（檔案仍留在 media/）")) { images[kind] = ""; markDirty(); renderShots(); } },
        }, "✕"),
      ));
      box.append(el("p", { class: "hint mono path-line", text: path }));
    } else {
      box.append(el("p", { class: "hint", text: "尚無圖片" }));
    }
    box.append(uploadRow("image/*", async (files) => {
      const paths = await uploadFiles(state.slug, "shots", files);
      if (!paths.length) return;
      images[kind] = paths[0];
      markDirty(); renderShots();
    }, { label: path ? "換一張：" : "上傳：" }));
    return box;
  }));
  imgCard.append(el("p", { class: "hint", text: "上傳走 POST /api/projects/{slug}/upload?kind=shots，路徑存成 media/shots/…（首尾幀在 M4／M5 生成時才使用）。" }));
  card.append(imgCard);

  // 影片狀態（只顯示，不生成）
  const video = ensure_obj(sh, "video", { path: "", status: "none", taskType: "r2v", seed: 0, note: "" });
  const vCard = el("div", { class: "subcard" });
  vCard.append(el("h4", { text: "影片（video）· 生成在 M4／M5" }));
  vCard.append(el("div", { class: "row wrap" },
    el("span", { class: "badge " + (VIDEO_STATUS_CLASS[video.status] || "st-none"), text: VIDEO_STATUS_LABEL[video.status] || video.status || "未生成" }),
    el("span", { class: "hint mono", text: `status=${video.status || "none"} · taskType=${video.taskType || "—"}${video.path ? " · " + video.path : ""}` }),
  ));
  const noteIn = el("input", { type: "text", placeholder: "影片備註（例如：排隊等 ComfyUI、要重拍）" });
  noteIn.value = video.note || "";
  noteIn.addEventListener("input", () => { video.note = noteIn.value; markDirty(); });
  vCard.append(el("label", { class: "field" }, el("span", { text: "影片備註（video.note）" }), noteIn));
  card.append(vCard);

  // M5a：生影片（H3 導演台 r2v，背景任務 + 進度 + 內嵌播放器）
  card.append(videoGenCard(sh));

  // M3d：參考圖槽位 + 模板編譯（純邏輯，不花 token）
  card.append(m3dShotTools(sh));
  return card;
}

// ── M5a：生影片（H3 導演台 r2v）──────────────────────────────────
// 設計：後端跑背景任務，前端每 1.5 秒 poll 一次。
// 為什麼不 await 一個長請求：H3 一鏡要 20 秒到數分鐘，長連線在瀏覽器／uvicorn 兩邊都容易被切。
const videoJobs = {};      // shotId → {jobId, timer, job}

function videoGenCard(sh) {
  const box = el("div", { class: "subcard video-gen" });
  const st = videoJobs[sh.id] || {};
  const status = el("div", { class: "vg-status hint" });
  const bar = el("div", { class: "vg-bar" }, el("div", { class: "vg-bar-fill" }));
  const body = el("div", { class: "vg-body" });
  const adv = el("div", { class: "vg-adv hidden" });
  const errBox = el("div", { class: "vg-error hidden" });

  // 進階參數（預設收合）。M9：預設值跟隨 settings.video（Pika 官方建議），可在這裡單鏡覆寫。
  const vsNow = (state.project && state.project.settings && state.project.settings.video) || {};
  const vsLora = vsNow.lora && typeof vsNow.lora === "object" ? vsNow.lora : (VIDEO_GEN_DEFAULTS.lora || {});
  // H3 合法影格是 17k+5；影格跟隨「本鏡長度」，不要一律 124（5.17 秒）。
  // 之前寫死 124，9.42 秒的鏡也只生 5.17 秒，台詞一定唸不完。
  const FPS = Number((state.project && state.project.meta && state.project.meta.fps) || 24) || 24;
  const legalFrames = (n) => 17 * Math.max(0, Math.round((Number(n) - 5) / 17)) + 5;
  const shotSec = Number(sh.durationSec) || 0;
  const shotFrames = shotSec > 0 ? legalFrames(Math.round(shotSec * FPS)) : 124;
  const params = Object.assign({ width: 768, height: 1344, frames: shotFrames,
    steps: vsNow.steps ?? VIDEO_GEN_DEFAULTS.steps, cfg: vsNow.cfg ?? VIDEO_GEN_DEFAULTS.cfg,
    shiftVideo: vsNow.shiftVideo ?? VIDEO_GEN_DEFAULTS.shiftVideo,
    shiftAudio: vsNow.shiftAudio ?? VIDEO_GEN_DEFAULTS.shiftAudio,
    seed: "", loraName: vsLora.name || "", loraStrength: vsLora.strength ?? 1.0 },
    st.params || {});
  const mk = (label, key, attrs) => {
    const inp = el("input", Object.assign({ type: "number" }, attrs));
    inp.value = params[key] === "" ? "" : String(params[key]);
    inp.addEventListener("input", () => {
      const v = inp.value.trim();
      params[key] = v === "" ? "" : Number(v);
    });
    return el("label", { class: "field" }, el("span", { text: label }), inp);
  };
  // 影格輸入：直接在旁邊顯示「= 幾秒」與本鏡設定，避免 9.42 秒的鏡被默默生 5.17 秒
  const frameHint = el("span", { class: "hint" });
  const frameInp = el("input", { type: "number", min: "5", max: "1000", step: "1" });
  frameInp.value = params.frames === "" ? "" : String(params.frames);
  const refreshFrameHint = () => {
    const f = Number(params.frames);
    if (!(f > 0)) { frameHint.textContent = ""; return; }
    const sec = (f / FPS).toFixed(2);
    frameHint.textContent = `（${sec} 秒${shotSec > 0 ? `；本鏡設定 ${shotSec} 秒` : ""}）`;
  };
  frameInp.addEventListener("input", () => {
    params.frames = frameInp.value.trim() === "" ? "" : Number(frameInp.value);
    refreshFrameHint();
  });
  refreshFrameHint();
  adv.append(mk("寬度 width", "width", { min: "32", max: "8192", step: "32" }),
    mk("高度 height", "height", { min: "32", max: "8192", step: "32" }),
    el("label", { class: "field" }, el("span", { text: "影格 frames（17k+5）" }), frameInp, frameHint),
    mk("步數 steps", "steps", { min: "1", max: "60", step: "1" }),
    mk("CFG", "cfg", { min: "0", max: "30", step: "0.1" }),
    mk("shift video", "shiftVideo", { min: "0.01", max: "100", step: "0.5" }),
    mk("shift audio", "shiftAudio", { min: "0.01", max: "100", step: "0.5" }),
    mk("seed（留白＝隨機）", "seed", { min: "0", max: "2147483647", step: "1" }));
  // M9：這一鏡要用哪支 LoRA（預設跟隨設定頁；空字串＝這一鏡不掛）
  const loraSel = el("select", {}, ...VIDEO_LORA_CHOICES.map((c) => el("option", { value: c.name, text: c.label })));
  loraSel.value = VIDEO_LORA_CHOICES.some((c) => c.name === params.loraName) ? params.loraName : "__custom__";
  const loraCustom = el("input", { type: "text", placeholder: "自訂 LoRA 檔名（空白＝不掛）", value: loraSel.value === "__custom__" ? (params.loraName || "") : "" });
  loraCustom.classList.toggle("hidden", loraSel.value !== "__custom__");
  const loraStrength = el("input", { type: "number", min: "0", max: "1.5", step: "0.05", value: String(params.loraStrength ?? 1.0) });
  const pullLora = () => {
    params.loraName = loraSel.value === "__custom__" ? loraCustom.value.trim() : loraSel.value;
    params.loraStrength = Number(loraStrength.value || 1.0);
  };
  loraSel.addEventListener("change", () => { loraCustom.classList.toggle("hidden", loraSel.value !== "__custom__"); pullLora(); });
  loraCustom.addEventListener("input", pullLora);
  loraStrength.addEventListener("input", pullLora);
  adv.append(el("label", { class: "field" }, el("span", { text: "LoRA（單鏡覆寫）" }), loraSel), loraCustom,
    el("label", { class: "field" }, el("span", { text: "LoRA strength" }), loraStrength));
  adv.append(el("p", { class: "hint", text: "預設 768×1344（9:16）；影格數跟隨本鏡的「長度秒數」（會自動吸附到 17k+5）。LoRA 寫進導演台 timeline 每段的 loras 欄位；空字串＝這一鏡不掛。" }));

  const playBox = el("div", { class: "vg-player" });
  const renderVideo = () => {
    const path = sh.video && sh.video.path;
    playBox.textContent = "";
    if (!path) {
      playBox.append(el("p", { class: "hint", text: "這一鏡還沒有影片。" }));
      return;
    }
    const url = picUrl(path) + `?t=${encodeURIComponent(sh.video.note || "")}`;
    playBox.append(el("video", {
      class: "vg-video", controls: "", preload: "metadata", playsinline: "",
      src: url,
    }));
    playBox.append(el("p", { class: "hint mono path-line", text: `${path}${sh.video.note ? " · " + sh.video.note : ""}` }));
    playBox.append(el("div", { class: "row" },
      el("a", { class: "btn btn-small", href: url, download: "", target: "_blank" }, "下載 mp4")));
  };

  const genBtn = el("button", {
    class: "btn btn-small btn-primary", type: "button",
    title: "用本機 ComfyUI 的 MiniMax H3 導演台（r2v）生這一鏡的影片（背景執行，可離開）",
  }, "🎬 生影片");
  const advBtn = el("button", { class: "btn btn-small", type: "button" }, "進階參數 ▾");
  advBtn.addEventListener("click", () => {
    adv.classList.toggle("hidden");
    advBtn.textContent = adv.classList.contains("hidden") ? "進階參數 ▾" : "進階參數 ▴";
  });

  const paint = () => {
    const j = (videoJobs[sh.id] || {}).job;
    const busy = j && (j.status === "queued" || j.status === "running");
    genBtn.disabled = !!busy;
    genBtn.textContent = busy ? "生成中…" : "🎬 生影片";
    if (!j) {
      status.className = "vg-status hint";
      const vLora = sh.video && sh.video.lora;
      const lastSrc = videoJobs[sh.id] && videoJobs[sh.id].lastPromptSource;
      const srcTxt = lastSrc === "compiled" ? " · 上次提示詞：模板編譯"
        : lastSrc === "promptEn" ? " · 上次提示詞：promptEn" : "";
      status.textContent = sh.video && sh.video.status === "done"
        ? `✓ 已有影片（${sh.video.taskType || "r2v"}${vLora !== undefined ? " · " + (vLora ? "LoRA " + videoLoraLabel(vLora) : "無 LoRA") : ""}${srcTxt}）` : "";
      bar.classList.add("hidden");
    } else if (busy) {
      status.className = "vg-status hint gen-busy-text";
      const waited = j.elapsed ? `${Math.round(j.elapsed)} 秒` : "0 秒";
      const q = j.position > 1 ? ` · 佇列第 ${j.position} 位` : "";
      status.textContent = "";
      status.append(document.createTextNode(`⏳ ${j.phase || "生成中…"} 已等 ${waited}${q}`));
      const gtxt = gpuBarText(j.gpu) || (j.restoreNote || "");
      if (j.gpu && j.gpu.stoppedLlama) {
        status.append(el("span", { class: "gpu-inline", text: `　${gtxt || "已自動停止本機 LLM，釋放 VRAM 中…"}` }));
      }
      bar.classList.remove("hidden");
      // 沒有真實百分比可用（ComfyUI 的 /progress 是 websocket）：用時間做不確定態動畫
    } else if (j.status === "failed") {
      status.className = "vg-status hint err-text";
      if (j.gpu && j.gpu.busy) {
        status.textContent = "";
        status.append(gpuBusyBanner(j.gpu, j.error));
      } else {
        status.textContent = `✕ 失敗（${Math.round(j.elapsed || 0)} 秒）`;
      }
      bar.classList.add("hidden");
    } else if (j.status === "done") {
      status.className = "vg-status hint ok-text";
      const usedLora = (j.loras || [])[0];
      const loraTxt = usedLora ? ` · LoRA ${videoLoraLabel(usedLora.name)}@${usedLora.strength}` : " · 無 LoRA";
      // M12b：這次實際送出的提示詞來源（promptEn 原稿 or 模板編譯）
      const psTxt = j.promptSource === "compiled" ? " · 提示詞：模板編譯（promptEn 未過規則）"
        : j.promptSource === "promptEn" ? " · 提示詞：promptEn ✓" : "";
      if (j.promptSource) videoJobs[sh.id] = Object.assign({}, videoJobs[sh.id], { lastPromptSource: j.promptSource });
      status.textContent = `✓ 完成：${Math.round(j.elapsed || 0)} 秒 · pid ${j.pid || "—"} · ${j.width}×${j.height} · ${j.frames} 幀 · steps ${j.steps ?? "—"}${loraTxt}${psTxt}`;
      const gdone = gpuBarText(j.gpu);
      if (gdone) status.append(el("span", { class: "gpu-inline", text: `　${gdone}` }));
      bar.classList.add("hidden");
    }
    errBox.textContent = "";
    const err = j && j.status === "failed" ? (j.error || "（沒有錯誤訊息）") : "";
    errBox.classList.toggle("hidden", !err);
    if (err) errBox.append(el("div", { class: "vg-error-head" }, el("strong", { text: "ComfyUI 回報：" })),
      el("pre", { class: "vg-error-text", text: err.slice(0, 500) }));
    const ws = (j && j.warnings) || [];
    if (ws.length) {
      const ul = el("ul", { class: "warn-list" });
      for (const w of ws) ul.append(el("li", { text: w }));
      body.textContent = "";
      body.append(ul);
    } else {
      body.textContent = "";
    }
    renderVideo();
  };

  const stopPoll = () => {
    const cur = videoJobs[sh.id];
    if (cur && cur.timer) { clearInterval(cur.timer); cur.timer = null; }
  };

  const poll = async (jobId) => {
    try {
      const res = await api(`/api/projects/${state.slug}/generate/video/${jobId}`);
      if (!res.ok) throw new Error(res.error || "查詢任務失敗");
      const j = res.job;
      videoJobs[sh.id] = Object.assign({}, videoJobs[sh.id], { job: j });
      if (j.status === "done") {
        stopPoll();
        sh.video = { path: j.path, status: "done", taskType: "r2v", seed: j.seed, note: "" };
        // 後端已存檔；同步 updatedAt 免得下一次自動儲存把 video 蓋回去
        try {
          const fresh = await api(`/api/projects/${state.slug}`);
          state.project.meta.updatedAt = fresh.project.meta.updatedAt;
          const fs = (fresh.project.shots || []).find((x) => x.id === sh.id);
          if (fs) sh.video = fs.video;
          state.dirty = false;
        } catch (_) { /* 拿不到 updatedAt 不影響已落地的影片 */ }
        setSaveStatus("saved", `已生成 ${sh.id} 影片（${j.elapsed} 秒）`);
      } else if (j.status === "failed") {
        stopPoll();
      }
      paint();
    } catch (e) {
      stopPoll();
      videoJobs[sh.id] = Object.assign({}, videoJobs[sh.id], {
        job: { status: "failed", error: `查詢進度失敗：${e.message}`, elapsed: 0 },
      });
      paint();
    }
  };

  const attach = (jobId) => {
    stopPoll();
    const t = setInterval(() => poll(jobId), 1500);
    videoJobs[sh.id] = Object.assign({}, videoJobs[sh.id], { jobId, timer: t });
  };

  genBtn.addEventListener("click", async () => {
    if (state.dirty) { try { await saveNow(); } catch (_) { /* 存檔失敗仍可試 */ } }
    // M12b：promptEn 不符 H3 官方規則時，事前講清楚「按下去會自動改用模板編譯」
    let willCompile = false;
    try {
      const pv = await api(`/api/projects/${state.slug}/shots/${sh.id}/prompt-preview?engine=h3_video`);
      willCompile = pv.promptSource === "compiled";
      if (willCompile) {
        const lines = (pv.problems || []).map((t) => `· ${t}`).filter(Boolean);
        const okToGen = await (window.SVSConfirm
          ? SVSConfirm({
              title: "提示詞格式不符，要自動改寫嗎？",
              plain: true,
              okText: "自動改寫並生成",
              refs: [
                ...lines,
                "按下去會用 H3 官方六段式重新組稿再送出（不會改動你的 project.json）。",
              ],
            })
          : Promise.resolve(confirm(`這一鏡的 promptEn 未符合 H3 官方規則（六段式＋<Picture N>）：\n${lines.join("\n")}\n\n`
              + "按下去會自動改用「模板編譯」的提示詞送出（不會改動你的 project.json）。\n要繼續生影片嗎？")));
        if (!okToGen) return;
      }
    } catch (_) { /* 預覽失敗不擋送件：送件口本身仍會硬驗 */ }
    pullLora();
    const p = { width: params.width, height: params.height, frames: params.frames,
                steps: params.steps, cfg: params.cfg, shiftVideo: params.shiftVideo, shiftAudio: params.shiftAudio,
                lora: params.loraName ? { name: params.loraName, strength: params.loraStrength, active: true }
                  : { name: "", strength: 1.0, active: false } };
    if (params.seed !== "" && params.seed !== null) p.seed = params.seed;
    Object.keys(p).forEach((k) => { if (p[k] === "" || p[k] === null) delete p[k]; });
    videoJobs[sh.id] = { params, job: { status: "queued", phase: "送件中…", elapsed: 0 } };
    paint();
    try {
      const res = await api(`/api/projects/${state.slug}/shots/${sh.id}/generate/video`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p),
      });
      if (!res.ok) throw new Error(res.error || "送件失敗");
      attach(res.jobId);
      poll(res.jobId);
    } catch (e) {
      videoJobs[sh.id] = { params, job: { status: "failed", error: e.message, elapsed: 0 } };
      paint();
    }
  });

  const pp = promptPreviewPanel("h3_video", sh.id);
  const ppBtn = promptPreviewButton("h3_video", sh.id, pp);
  box.append(el("div", { class: "subcard-head" },
    el("h4", { text: "🎬 生影片（H3 導演台 r2v）" }),
    el("span", { class: "badge rule-badge", text: `規則：${promptRuleLabel("h3_video")}` }),
    ppBtn,
    el("span", { class: "spacer" }), status, advBtn, genBtn));
  // M12b：promptEn 不符規則的事前提醒（純讀預覽，不碰 GPU）
  // 分成「已經幫你處理好的」與「真的需要你動手的」兩類——以前全部塞成一條 ⚠，
  // 使用者會以為程式壞了（實測 6 條互相矛盾的警告就是這樣來的）。
  const preWarn = el("div", { class: "hint pp-pre-warn hidden" });
  box.append(preWarn, pp.box, adv, bar, errBox, body, playBox);
  (async () => {
    try {
      const pv = await api(`/api/projects/${state.slug}/shots/${sh.id}/prompt-preview?engine=h3_video`);
      if (pv.promptSource !== "compiled" || !pv.prompt) return;
      const nProb = (pv.problems || []).length;
      preWarn.classList.remove("hidden");
      preWarn.textContent = "";
      preWarn.append(el("p", { class: "hint ok-text", text:
        `✓ 已依 H3 官方六段式自動改寫提示詞（原 promptEn 不符格式 ${nProb} 項；不會改動你的資料）`
        + " — 可以直接按「生影片」。" }));
      // 「真的要你動手」的才列，而且每種只說一次（以前把後端原文全貼上，
      // 兩條意思一樣的建議會連著出現，看起來像一堆錯誤）。
      const ws = pv.warnings || [];
      if (ws.some((t) => t.includes("參考圖槽位"))) {
        preWarn.append(el("p", { class: "hint warn-text", text:
          "⚠ 這鏡沒有角色／場景參考圖，本次用「首幀」當 <Picture 1>；要鎖同一張臉，"
          + "請先在人物頁按「生成立繪」→ 回鏡頭頁按「建參考圖槽位」。" }));
      }
      const lineTodo = ws.find((t) => t.includes("未覆蓋所屬場次"));
      if (lineTodo) preWarn.append(el("p", { class: "hint warn-text", text: "⚠ " + lineTodo }));
      preWarn.append(el("p", { class: "hint", text: "完整規則與這次要送出的全文：按上方「看這次要送出的提示詞」。" }));
    } catch (_) { /* 拿不到就安靜 */ }
  })();

// 換頁／重繪後接回：這個鏡頭有在跑的任務就繼續 poll，否則顯示最近一次任務
  if (videoJobs[sh.id] && videoJobs[sh.id].jobId && !videoJobs[sh.id].timer &&
      videoJobs[sh.id].job && ["queued", "running"].includes(videoJobs[sh.id].job.status)) {
    attach(videoJobs[sh.id].jobId);
  }
  paint();
  return box;
}

// ── M12b：提示詞規則（規則的唯一真相在 app/promptrules.py，前端只顯示與呼叫預覽）──
// 三個引擎的顯示名；label／rulesZh／source 一律向後端拿（GET /api/prompt-rules），前端不複製規則文字。
const PROMPT_RULES = {};       // ruleId → {id, label, source, rulesZh[]}

async function loadPromptRules() {
  try {
    const res = await api("/api/prompt-rules");
    (res.rules || []).forEach((r) => { PROMPT_RULES[r.id] = r; });
  } catch (_) { /* 拿不到規則卡不影響生圖生影，只是少了條列說明 */ }
}

function promptRuleLabel(ruleId) {
  const r = PROMPT_RULES[ruleId];
  return r ? r.label : (ruleId || "—");
}

// 先把畫面上的編輯存下去，預覽才是「現在這份」的提示詞
async function saveBeforePreview() {
  if (state.dirty) { try { await saveNow(); } catch (_) { /* 存檔失敗仍可預覽舊資料 */ } }
}

// 「看這次要送出的提示詞」→ 打 prompt-preview，在頁內 panel 顯示規則條列＋提示詞全文＋warnings／problems
function promptPreviewPanel(engine, shotId) {
  const box = el("div", { class: "pp-box hidden" });
  const head = el("div", { class: "row wrap pp-head" },
    el("span", { class: "badge rule-badge", text: `規則：${promptRuleLabel(engine)}` }));
  const body = el("div", {});
  const state_txt = el("span", { class: "hint" });
  head.append(el("span", { class: "spacer" }), state_txt);
  box.append(head, body);

  const render = (res) => {
    body.textContent = "";
    if (res.error) {
      body.append(el("p", { class: "hint err-text", text: res.error }));
    }
    const rules = res.rulesZh || (PROMPT_RULES[res.ruleId] || {}).rulesZh || [];
    const src = res.source || (PROMPT_RULES[res.ruleId] || {}).source || "";
    body.append(el("div", { class: "pp-sec" },
      el("div", { class: "pp-sec-title", text: `官方規則（${res.label || engine}）` }),
      src ? el("p", { class: "hint mono", text: `來源：${src}` }) : null,
      el("ol", { class: "pp-rules" }, ...rules.map((t) => el("li", { text: t })))));
    if (res.problems && res.problems.length) {
      body.append(el("div", { class: "pp-sec" },
        el("div", { class: "pp-sec-title err-text", text: `原提示詞未過規則（${res.problems.length}）` }),
        el("p", { class: "hint", text: res.promptSource === "compiled"
          ? "送出時會自動改用下面「這次要送出的提示詞」，這裡列的是你原稿不合規的地方（不用手動改）。"
          : "這些地方要先改，否則送不出去。" }),
        el("ul", { class: "warn-list" }, ...res.problems.map((t) => el("li", { text: t })))));
    }
    if (res.notes && res.notes.length) {
      body.append(el("div", { class: "pp-sec" },
        el("div", { class: "pp-sec-title", text: `品質提醒（${res.notes.length} 項，不影響送出）` }),
        el("ul", { class: "warn-list" }, ...res.notes.map((t) => el("li", { text: t })))));
    }
    if (res.warnings && res.warnings.length) {
      body.append(el("div", { class: "pp-sec" },
        el("div", { class: "pp-sec-title", text: `警告（${res.warnings.length}）` }),
        el("ul", { class: "warn-list" }, ...res.warnings.map((t) => el("li", { text: t })))));
    }
    const prompt = res.prompt || "";
    const pre = el("pre", { class: "pp-prompt" + (prompt.length > 900 ? " collapsed" : ""), text: prompt });
    const toggle = el("button", { class: "btn btn-small", type: "button" },
      prompt.length > 900 ? "展開全文" : "收合");
    toggle.classList.toggle("hidden", prompt.length <= 900);
    toggle.addEventListener("click", () => {
      const c = pre.classList.toggle("collapsed");
      toggle.textContent = c ? "展開全文" : "收合";
    });
    const copy = el("button", { class: "btn btn-small", type: "button" }, "複製提示詞");
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(prompt); copy.textContent = "已複製"; }
      catch (_) { copy.textContent = "複製失敗（可手動選取）"; }
      setTimeout(() => { copy.textContent = "複製提示詞"; }, 1500);
    });
    body.append(el("div", { class: "pp-sec" },
      el("div", { class: "row wrap" },
        el("div", { class: "pp-sec-title", text: `這次要送出的提示詞（${res.chars ?? prompt.length} 字元）` }),
        el("span", { class: "spacer" }), toggle, copy),
      pre));
  };

  const load = async (force) => {
    if (!force && box.dataset.loaded === "1") { box.classList.toggle("hidden"); return; }
    await saveBeforePreview();
    box.classList.remove("hidden");
    state_txt.textContent = "載入中…";
    try {
      const res = await api(`/api/projects/${state.slug}/shots/${shotId}/prompt-preview?engine=${encodeURIComponent(engine)}`);
      box.dataset.loaded = "1";
      state_txt.textContent = res.promptSource === "compiled" ? "來源：模板編譯（promptEn 未過規則）"
        : res.ok ? "✓ 符合規則" : "⚠ 提示詞有問題（見下方）";
      render(res);
    } catch (e) {
      box.dataset.loaded = "";
      state_txt.textContent = "";
      body.textContent = "";
      body.append(el("p", { class: "hint err-text", text: `預覽失敗：${e.message}` }));
    }
  };
  return { box, load };
}

function promptPreviewButton(engine, shotId, pp) {
  const btn = el("button", { class: "btn btn-small", type: "button", title: "看這次真的要送進模型的提示詞與規則驗證" },
    "看這次要送出的提示詞");
  btn.addEventListener("click", () => pp.load(false));
  return btn;
}

// ── M3d：鏡頭卡的工具列 ────────────────────────────────────────
function slotKindLabel(kind) {
  return { character: "角色", scene: "場景", prop: "道具" }[kind] || kind || "—";
}

function renderReferencePlan(sh) {
  const rp = sh.referencePlan || {};
  const slots = rp.slots || [];
  const box = el("div", { class: "m3d-result" });
  if (!slots.length) {
    box.append(el("p", { class: "hint", text: "referencePlan.slots 目前是空的。" }));
  } else {
    const tbl = el("table", { class: "m3d-table" },
      el("tr", {}, ...["ordinal", "kind", "id", "stage", "role", "path"].map((h) => el("th", { text: h }))));
    for (const s of slots) {
      tbl.append(el("tr", {},
        el("td", { class: "mono", text: `<Picture ${s.ordinal}>` }),
        el("td", { text: slotKindLabel(s.kind) }),
        el("td", { class: "mono", text: s.id || "" }),
        el("td", { class: "mono", text: s.stage || "—" }),
        el("td", { class: "mono", text: s.role || "" }),
        el("td", { class: "mono path-line", text: s.path || "" }),
      ));
    }
    box.append(tbl);
  }
  const ph = Object.entries(rp.promptPlaceholders || {});
  if (ph.length) {
    box.append(el("p", { class: "hint mono", text: "promptPlaceholders：" + ph.map(([k, v]) => `${k} → ${v}`).join("、") }));
  }
  if ((rp.weakReferences || []).length) {
    box.append(el("p", { class: "hint warn-text", text: "weakReferences（無圖，只靠文字）：" + rp.weakReferences.join("、") }));
  }
  return box;
}

// 本鏡要用角色的哪個 stage（stageIds 存 "c01:s2"）；不勾就用角色主參考圖
function stageRefChecks(sh) {
  if (!Array.isArray(sh.stageIds)) sh.stageIds = [];
  const opts = [];
  for (const ch of (state.project.characters || [])) {
    for (const st of (ch.stages || [])) {
      opts.push({ value: `${ch.id}:${st.id}`, text: `${ch.name || ch.id} · ${ch.id}:${st.id}${st.label ? "（" + st.label + "）" : ""}` });
    }
  }
  const box = el("div", { class: "row wrap stage-refs" },
    el("span", { class: "hint", text: "本鏡造型階段（stageIds）：" }));
  if (!opts.length) {
    box.append(el("span", { class: "hint", text: "（尚無角色階段；可在人物卡「分階段造型」新增）" }));
    return box;
  }
  for (const o of opts) {
    const cb = el("input", { type: "checkbox", value: o.value });
    cb.checked = sh.stageIds.includes(o.value);
    cb.addEventListener("change", () => {
      sh.stageIds = sh.stageIds.filter((x) => x !== o.value);
      if (cb.checked) sh.stageIds.push(o.value);
      markDirty();
    });
    box.append(el("label", { class: "checkline" }, cb, el("span", { text: o.text })));
  }
  return box;
}

// 角度三下拉：spec.angle = {h,v,s}（v0.2）。舊資料若是字串，view 已經是物件；這裡仍防呆。
function angleLabel(value, table) {
  if (!value) return "";
  const hit = table.find(([v]) => v === value);
  return hit ? hit[0] : value;
}

function angleSelects(sh, spec) {
  const cur = (spec.angle && typeof spec.angle === "object") ? spec.angle : { h: "", v: "", s: "" };
  const wrap = el("div", { class: "angle-row" });
  const mk = (key, table, title) => {
    const sel = el("select", {},
      el("option", { value: "", text: "（未設）" }),
      ...table.map(([v, text]) => el("option", { value: v, text })));
    const value = cur[key] || "";
    if (value && !table.some(([v]) => v === value)) sel.append(el("option", { value, text: `（舊值：${value}）` }));
    sel.value = value;
    sel.addEventListener("change", () => {
      if (!spec.angle || typeof spec.angle !== "object") spec.angle = { h: "", v: "", s: "" };
      spec.angle[key] = sel.value;
      markDirty();
    });
    return el("label", { class: "field angle-field" }, el("span", { text: title }), sel);
  };
  wrap.append(mk("h", ANGLE_H, "水平（h）"), mk("v", ANGLE_V, "垂直（v）"), mk("s", ANGLE_S, "景別（s）"));
  wrap.dataset.shot = sh.id;
  return el("div", { class: "field angle-group" }, el("span", { text: "角度（angle）" }), wrap);
}

function m3dShotTools(sh) {
  const box = el("div", { class: "subcard m3d-tools" });
  const body = el("div", { class: "m3d-body" });
  box.append(el("div", { class: "subcard-head" },
    el("h4", { text: "送生成前準備（M3d · 純邏輯，不呼模型）" }),
    el("span", { class: "spacer" }),
    el("span", { class: "hint", text: `${Object.keys(sh.referencePlan?.promptPlaceholders || {}).length} 個佔位符` }),
  ));

  const status = el("p", { class: "hint", text: "" });
  const showWarnings = (warns) => {
    const list = el("ul", { class: "warn-list" });
    for (const w of warns || []) list.append(el("li", { text: w }));
    return (warns || []).length ? list : el("p", { class: "hint ok-text", text: "沒有警告。" });
  };

  const btnPlan = el("button", { class: "btn btn-small", type: "button", title: "依 stageIds／characterIds＋sceneId 產生參考圖槽位（最多 9 張）" }, "▣ 建參考圖槽位");
  btnPlan.addEventListener("click", async () => {
    btnPlan.disabled = true;
    status.textContent = "建立參考圖槽位中…";
    try {
      await saveNow();
      const res = await api(`/api/projects/${state.slug}/shots/${sh.id}/build-reference-plan`, { method: "POST" });
      sh.referencePlan = res.referencePlan;
      body.textContent = "";
      body.append(renderReferencePlan(sh), showWarnings(res.warnings));
      status.className = "hint ok-text";
      status.textContent = `已寫回 ${res.count} 個槽位（HTTP 200）。`;
      setSaveStatus("saved", `已建立 ${sh.id} 的參考圖槽位`);
    } catch (e) {
      status.className = "hint err-text";
      status.textContent = `失敗：${e.message}`;
    } finally { btnPlan.disabled = false; }
  });

  const btnCompile = el("button", { class: "btn btn-small btn-primary", type: "button", title: "用錨點＋spec＋beats＋台詞組 H3 六段式英文 prompt（不花 token）" }, "⌘ 模板編譯 prompt（不花 token）");
  btnCompile.addEventListener("click", async () => {
    if (!confirm("用模板編譯的結果覆寫這鏡的英文提示（promptEn）嗎？（不會呼叫模型、不花 token）")) return;
    btnCompile.disabled = true;
    status.textContent = "模板編譯中…";
    try {
      await saveNow();
      const res = await api(`/api/projects/${state.slug}/shots/${sh.id}/compile-prompt`, { method: "POST" });
      sh.promptEn = res.promptEn;
      body.textContent = "";
      const pre = el("pre", { class: "m3d-prompt" });
      pre.textContent = res.promptEn;
      body.append(el("p", { class: "hint ok-text", text: `已編譯 ${res.meta.charCount} 字元、6 段、${res.meta.pictures.length} 張圖引用（HTTP 200）` }),
        pre, showWarnings(res.warnings));
      status.className = "hint ok-text";
      status.textContent = "已寫回 promptEn。";
      const ta = document.querySelector(`textarea[data-prompt-for="${sh.id}"]`);
      if (ta) ta.value = res.promptEn;
      setSaveStatus("saved", `已編譯 ${sh.id} 的 promptEn`);
    } catch (e) {
      status.className = "hint err-text";
      status.textContent = `失敗：${e.message}`;
    } finally { btnCompile.disabled = false; }
  });

  box.append(el("div", { class: "row wrap" }, btnPlan, btnCompile,
    el("span", { class: "spacer" }), status));
  body.append(renderReferencePlan(sh));
  box.append(body);
  return box;
}

// 純字串拼接的英文草稿（不呼叫任何模型；M3 才會是模型改寫）
function composeShotPromptEn(sh) {
  const spec = sh.spec || {};
  const names = (sh.characterIds || []).map((id) => {
    const ch = (state.project.characters || []).find((c) => c.id === id);
    if (!ch) return id;
    return ch.appearance && ch.appearance.promptEn ? `${ch.name || ch.id} (${ch.appearance.promptEn})` : (ch.name || ch.id);
  });
  const sc = (state.project.scenes || []).find((s) => s.id === sh.sceneId);
  const parts = [];
  if (names.length) parts.push("Subject: " + names.join("; "));
  if (sh.promptZh) parts.push("Action: " + sh.promptZh);
  const ang = spec.angle && typeof spec.angle === "object"
    ? angleLabel(spec.angle.h, ANGLE_H) + " " + angleLabel(spec.angle.v, ANGLE_V) + " " + angleLabel(spec.angle.s, ANGLE_S)
    : (spec.angle || "");
  const cam = [spec.shotType, ang, spec.cameraMove].filter(Boolean).join(", ");
  if (cam) parts.push("Camera: " + cam);
  if (sc) parts.push("Scene: " + (sc.promptEn || sc.desc || sc.name || sc.id) + (sc.timeOfDay ? `, ${sc.timeOfDay}` : ""));
  if (spec.lighting) parts.push("Lighting: " + spec.lighting);
  return parts.length ? parts.join(". ") + "." : "";
}

// ── M4a：單鏡生圖（本機 ComfyUI · Qwen 2.1）──────────────────
// 同步呼叫後端（最多 180 秒），成功後直接把首幀縮圖換掉，不需要整頁重繪。
const imagegenState = {};   // shotId → {busy, msg, err, last}

function paintGenState(sh, btns, status, imgCard, logBox) {
  const st = imagegenState[sh.id] || {};
  const box = imgCard && imgCard.querySelector(".frame-box .frame-thumb");
  btns.forEach((b) => { b.disabled = !!st.busy; });
  if (st.busy) {
    status.className = "hint gen-busy-text";
    status.textContent = `⏳ ${st.msg || "生成中…"}`;
  } else if (st.err) {
    status.className = "hint err-text";
    status.textContent = "";
    if (st.busyGpu) status.append(gpuBusyBanner(st.busyGpu, st.err));
    else status.textContent = `✕ ${st.err}`;
  } else if (st.last) {
    status.className = "hint ok-text";
    status.textContent = `✓ 已完成（${st.last.elapsed} 秒 · ${st.last.mode === "t2i" ? "文字生圖" : "帶參考圖 i2i"}）`;
    const gtxt = gpuBarText(st.last.gpu);
    if (gtxt) status.append(el("span", { class: "gpu-inline", text: `　${gtxt}` }));
  } else {
    status.className = "hint";
    status.textContent = "";
  }
  if (logBox && st.last) {
    logBox.classList.remove("hidden");
    logBox.textContent = "";
    const r = st.last;
    logBox.append(
      el("div", { class: "row wrap" },
        el("span", { class: "hint mono", text: `prompt_id=${r.pid || "—"} · ${r.elapsed}s · seed=${r.seed}`
          + (r.engine === "moss" ? ` · MOSS ${r.size ? r.size.width + "×" + r.size.height : ""} · encoder=${(r.loaders || {}).clip || "?"}` : "") }),
        el("span", { class: "spacer" }),
        el("button", {
          class: "btn btn-small", type: "button",
          onclick: () => { logBox.classList.add("hidden"); },
        }, "收起")),
      el("p", { class: "hint mono path-line", text: `圖片：${r.path || ""}（${r.width || "?"}×${r.height || "?"}）` }),
      el("p", { class: "hint", text: `參考圖（${(r.refs || []).length} 張）：${(r.refs || []).map((x) => x.path + " ［" + x.label + "］").join("、") || "無"}` }),
    );
    if ((r.warnings || []).length) {
      const ul = el("ul", { class: "warn-list" });
      for (const w of r.warnings) ul.append(el("li", { text: w }));
      logBox.append(ul);
    }
    if (r.prompt) {
      const pre = el("pre", { class: "m3d-prompt" });
      pre.textContent = r.prompt;
      logBox.append(el("details", { open: r.engine === "moss" ? "" : undefined }, el("summary", { class: "hint", text: `送進 ${r.engine === "moss" ? "MOSS" : "Qwen"} 的${r.engine === "moss" ? " caption" : "提示詞"}（${r.prompt.length} 字元）` }), pre));
    }
  } else if (logBox) {
    logBox.classList.add("hidden");
  }
  return box;
}

async function generateShotImage(sh, mode, btns, status, imgCard, logBox) {
  const st = imagegenState[sh.id] = imagegenState[sh.id] || {};
  if (st.busy) return;
  // 先把畫面上的編輯存下去，後端讀到的是最新 promptEn／角度
  if (state.dirty) { try { await saveNow(); } catch (_) { /* 存檔失敗仍可試生圖 */ } }
  st.busy = true; st.err = ""; st.busyGpu = null;
  st.msg = mode === "t2i" ? "文字生圖中（第一次載模型會久一點）…" : "帶參考圖生圖中（第一次載模型會久一點）…";
  paintGenState(sh, btns, status, imgCard, logBox);
  const t0 = Date.now();
  const tick = setInterval(() => {
    st.msg = `生成中… 已等 ${Math.round((Date.now() - t0) / 1000)} 秒（ComfyUI 同步處理，最多 180 秒）`;
    paintGenState(sh, btns, status, imgCard, logBox);
  }, 1000);
  let res = null;
  try {
    const eng = (state.project && state.project.settings && state.project.settings.imageEngine) || "qwen";
    res = await api(`/api/projects/${state.slug}/shots/${sh.id}/generate/image`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(eng === "moss" ? { mode: "t2i", engine: "moss" }
                                         : { mode, engine: "qwen", resolution: 1024 }),
    });
  } catch (e) {
    st.err = `生圖請求失敗：${e.message}`;
  } finally {
    clearInterval(tick);
    st.busy = false;
  }
  if (res && res.ok) {
    sh.images = Object.assign({}, sh.images, res.shot.images);
    st.last = res;
    st.err = "";
    // 縮圖直接換到首幀位置（不整頁重繪，保得住捲動位置與輸入焦點）
    const firstImg = imgCard.querySelector(".frame-grid .frame-box:first-child");
    if (firstImg) {
      const url = picUrl(res.path) + `?t=${Date.now()}`;
      const box = firstImg.querySelector(".frame-thumb");
      if (box) {
        const img = box.querySelector("img");
        if (img) {
          img.src = url;
          img.onclick = () => openLightbox(url, `${sh.id} · 首幀（剛生成）`);
        } else {
          const newImg = el("img", { src: url, alt: "首幀（剛生成）", onclick: () => openLightbox(url, `${sh.id} · 首幀（剛生成）`) });
          box.prepend(newImg);
          const del = box.querySelector(".frame-del");
          if (!del) box.append(el("button", {
            class: "thumb-del frame-del", type: "button", title: "移除這張（檔案仍留在 media/）",
            onclick: () => { if (confirm("要移除這張幀嗎？（檔案仍留在 media/）")) { sh.images.first = ""; markDirty(); renderShots(); } },
          }, "✕"));
        }
      } else {
        firstImg.append(el("div", { class: "frame-thumb" },
          el("img", { src: url, alt: "首幀（剛生成）", onclick: () => openLightbox(url, `${sh.id} · 首幀（剛生成）`) })));
      }
      const del = firstImg.querySelector(".frame-del");
      if (del) del.classList.remove("hidden");
      let pathLine = firstImg.querySelector(".path-line");
      if (pathLine) pathLine.textContent = res.path;
      else {
        pathLine = el("p", { class: "hint mono path-line", text: res.path });
        const hint = firstImg.querySelector("p.hint:not(.path-line)");
        if (hint) hint.replaceWith(pathLine); else firstImg.append(pathLine);
      }
      // 「尚無圖片」提示在新縮圖出現後就該消失
      firstImg.querySelectorAll("p.hint").forEach((n) => {
        if (!n.classList.contains("path-line") && n.textContent === "尚無圖片") n.remove();
      });
    }
    setSaveStatus("saved", `已生成 ${sh.id} 首幀，並寫回 project.json`);
    // 後端已存檔，但本地副本要同步 meta.updatedAt 才不會被下一次自動儲存蓋回去
    try {
      const fresh = await api(`/api/projects/${state.slug}`);
      state.project.meta.updatedAt = fresh.project.meta.updatedAt;
      const fs = (fresh.project.shots || []).find((x) => x.id === sh.id);
      if (fs) sh.images = fs.images;
      state.dirty = false;
    } catch (_) { /* 拿不到新 updatedAt 不影響已落地的圖 */ }
  } else if (res) {
    st.err = res.error || "生圖失敗（後端沒有給原因）";
    st.busyGpu = (res.gpu && res.gpu.busy) ? res.gpu : null;
    setSaveStatus("error", `生圖失敗：${sh.id}`);
  }
  paintGenState(sh, btns, status, imgCard, logBox);
}

// ── M4b：批次生圖（前置檢查 + 依序送件 + 每 2 秒 poll 進度）──────────
// 後端 /generate/images 是背景 task：回 jobId，前端 poll GET .../{jobId}。
// 「先試 1 鏡」預設勾選：批次只跑第一鏡，確認 OK 再取消勾選放量。
const batchState = {
  job: null,          // 最近一次 job 狀態（後端回來的）
  jobId: null,
  skipped: [],        // 前置檢查沒過而未送的鏡頭
  timer: null,
  busy: false,        // 送件請求進行中
  picked: {},         // shotId → true（勾選）
  autoOpen: true,     // 有新任務時自動展開面板
};

function batchPickedIds() {
  return Object.keys(batchState.picked).filter((k) => batchState.picked[k]);
}

function batchShotIds(scope, filterEs) {
  const list = state.project?.shots || [];
  if (scope === "checked") return batchPickedIds();
  const src = filterEs ? list.filter((s) => s.episodeSceneId === filterEs) : list;
  return src.map((s) => s.id);
}

function batchLabel(text) {
  return el("span", { class: "hint", text });
}

// 批次按鈕（工具列一顆）
function batchToolbarRow(filterEs) {
  const row = el("div", { class: "row wrap batch-row" });
  const scopeSel = el("select", { class: "inline-select" },
    el("option", { value: "scene", text: "本場次" }),
    el("option", { value: "checked", text: "已勾選鏡頭" }),
    el("option", { value: "all", text: "全部鏡頭" }));
  scopeSel.value = batchState.scope || "scene";
  const chk = el("input", { type: "checkbox" });
  chk.checked = batchState.trialFirst !== false;
  const runBtn = el("button", { class: "btn btn-small btn-primary", type: "button" },
    "⚡ 批次生圖（本場次／已勾選）");
  const countHint = batchLabel("");
  const updateCount = () => {
    const ids = batchShotIds(scopeSel.value, filterEs);
    countHint.textContent = `· 會處理 ${ids.length} 鏡`
      + (chk.checked ? "（先試 1 鏡）" : "")
      + (ids.length && batchShotIds(scopeSel.value, filterEs).length === 0 ? "（請先勾選或選有鏡頭的場次）" : "");
  };
  scopeSel.addEventListener("change", () => { batchState.scope = scopeSel.value; updateCount(); });
  chk.addEventListener("change", () => { batchState.trialFirst = chk.checked; updateCount(); });
  updateCount();
  runBtn.addEventListener("click", () => runBatch(scopeSel.value, filterEs));
  row.append(el("span", { class: "hint", text: "批次生圖：" }), scopeSel,
    el("label", { class: "hint inline-check", title: "只先生第一鏡讓人看，確認 OK 再取消勾選放量（建議）" },
      chk, el("span", { text: "先試 1 鏡" })),
    runBtn, countHint);
  return row;
}

async function runBatch(scope, filterEs) {
  if (batchState.busy) return;
  const ids = batchShotIds(scope, filterEs);
  if (!ids.length) {
    alert(scope === "checked" ? "先勾選要生的鏡頭（每張鏡頭卡右上角有「批次」勾選框）" : "這個範圍裡沒有鏡頭");
    return;
  }
  if (!confirm(`批次生圖：${ids.length} 鏡${batchState.trialFirst !== false && ids.length > 1 ? "（只會先試第一鏡）" : ""}。\n`
    + "會依序送本機 ComfyUI（一次一鏡，不並行），前置檢查沒過的鏡頭會直接略過。要開始嗎？")) return;
  batchState.busy = true;
  if (state.dirty) { try { await saveNow(); } catch (_) { /* 存檔失敗仍可試 */ } }
  setSaveStatus("pending", "送出生圖批次…");
  try {
    const res = await api(`/api/projects/${state.slug}/generate/images`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        shotIds: ids, mode: "ref", resolution: 1024,
        trialFirst: batchState.trialFirst !== false,
      }),
    });
    if (!res || !res.ok) {
      const msg = (res && res.error) || "批次生圖被擋下（後端沒有給原因）";
      batchState.skipped = (res && res.blockers) || [];
      batchState.job = { status: "blocked", blockers: batchState.skipped, items: [], done: 0, total: 0, okCount: 0, failCount: 0 };
      setSaveStatus("error", "批次生圖未送出：前置檢查沒過（清單見鏡頭頁面板）");
      renderShots();
      alert(msg);
      return;
    }
    batchState.jobId = res.job.jobId;
    batchState.job = res.job;
    batchState.skipped = res.skipped || [];
    batchState.autoOpen = true;
    setSaveStatus("pending", `批次已送出（${res.job.total} 鏡，依序處理中）`);
    renderShots();
    pollBatch();
  } catch (e) {
    setSaveStatus("error", `批次生圖請求失敗：${e.message}`);
    alert(`批次生圖請求失敗：${e.message}`);
  } finally {
    batchState.busy = false;
  }
}

async function pollBatch() {
  clearTimeout(batchState.timer);
  if (!batchState.jobId) return;
  try {
    const res = await api(`/api/projects/${state.slug}/generate/images/${batchState.jobId}`);
    batchState.job = res.job;
  } catch (e) {
    if (e.status === 404) {
      setSaveStatus("error", "批次進度遺失（伺服器重啟過）：圖仍在 media/shots/");
      batchState.jobId = null;
      return;
    }
  }
  paintBatchPanel();
  const running = batchState.job && ["queued", "running"].includes(batchState.job.status);
  if (running) {
    batchState.timer = setTimeout(pollBatch, 2000);
  } else {
    // 完成：拉一次專案，把每鏡首幀縮圖換新（整頁重繪最省事且狀態一致）
    try {
      const fresh = await api(`/api/projects/${state.slug}`);
      state.project = fresh.project;
      state.dirty = false;
      if (state.view === "shots") renderShots();
    } catch (_) { /* 拉不到新專案不影響已落地的圖 */ }
    const j = batchState.job || {};
    setSaveStatus(j.failCount ? "error" : "saved",
      `批次結束：成功 ${j.okCount || 0}、失敗 ${j.failCount || 0}` + (batchState.skipped.length ? `、未送 ${batchState.skipped.length}` : ""));
    if (j.gpu && j.gpu.busy) {
      setSaveStatus("error", `批次未送件：本機 LLM 正在運算中（${j.error || ""}）`);
    }
  }
}

// 換專案／F5 後把最近一次批次接回
async function reattachLatestBatch() {
  batchState.job = null; batchState.jobId = null; batchState.skipped = []; batchState.picked = {};
  clearTimeout(batchState.timer);
  try {
    const res = await api(`/api/projects/${state.slug}/generate/images/latest`);
    if (res && res.job) {
      batchState.job = res.job;
      batchState.jobId = res.job.jobId;
      batchState.skipped = res.job.skipped || [];
      batchState.autoOpen = false;
      if (["queued", "running"].includes(res.job.status)) pollBatch();
    }
  } catch (_) { /* 沒任務就沒面板 */ }
}

const BATCH_STATUS_TEXT = {
  queued: "排隊中", running: "進行中", done: "已完成", failed: "全數失敗",
  partial: "部分成功", cancelled: "已取消", blocked: "前置檢查未過",
  pending: "等待", skipped: "略過",
};

function paintBatchPanel() {
  const box = document.querySelector("#batch-panel");
  if (!box) return;
  box.textContent = "";
  const j = batchState.job;
  if (!j) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const running = ["queued", "running"].includes(j.status);
  const statusCls = j.status === "done" ? "ok-text" : (j.status === "running" || j.status === "queued") ? "gen-busy-text" : "err-text";
  box.append(el("div", { class: "subcard-head" },
    el("h4", { text: "⚡ 批次生圖進度" }),
    el("span", { class: `hint ${statusCls}`, text: `${BATCH_STATUS_TEXT[j.status] || j.status} · ${j.done || 0}/${j.total || 0}`
      + (j.elapsed ? ` · ${j.elapsed} 秒` : "") }),
    el("span", { class: "spacer" }),
    el("span", { class: "hint", text: `成功 ${j.okCount || 0} · 失敗 ${j.failCount || 0}`
      + (batchState.skipped.length ? ` · 未送 ${batchState.skipped.length}` : "") }),
    el("button", { class: "btn btn-small", type: "button", onclick: () => { batchState.job = null; paintBatchPanel(); } }, "收起"),
  ));
  // GPU 自動切換（整批只切一次）：忙碌橫幅／停止與啟回狀態
  const g = j.gpu;
  if (g && g.busy) {
    box.append(gpuBusyBanner(g, j.error));
  } else if (g && (g.stoppedLlama || g.restored)) {
    box.append(el("p", { class: "hint gpu-progress" },
      el("span", { class: "gpu-progress-on", text: gpuBarText(g) }),
      running && !g.restored ? el("span", { text: "　→ 載入模型／生成中（跑完會自動啟回本機 LLM）" }) : null));
  } else if (running) {
    box.append(el("p", { class: "hint gpu-progress", text: `⏳ ${j.phase || "檢查 GPU（必要時暫停本機 LLM）…"}` }));
  }
  if (j.trialFirst && (j.skippedByTrial || 0) > 0) {
    box.append(el("p", { class: "hint", text: `「先試 1 鏡」模式：只跑了第 1 鏡，另有 ${j.skippedByTrial} 鏡沒送。`
      + "看過結果 OK 的話，取消勾選「先試 1 鏡」再按一次即可放量。" }));
  }
  const ul = el("ul", { class: "batch-list" });
  for (const it of (j.items || [])) {
    const cls = it.status === "done" ? "ok-text" : it.status === "failed" ? "err-text" : it.status === "running" ? "gen-busy-text" : "hint";
    const line = el("li", { class: "batch-item" },
      el("span", { class: `mono ${cls}`, text: `${it.shotId}${it.no ? `（第 ${it.no} 鏡）` : ""}` }),
      el("span", { class: "hint", text: ` · ${BATCH_STATUS_TEXT[it.status] || it.status}` }),
      it.elapsed ? el("span", { class: "hint mono", text: ` · ${it.elapsed}s` }) : null,
      it.path ? el("span", { class: "hint mono path-line", text: ` · ${it.path}` }) : null,
      (it.refs ? el("span", { class: "hint", text: ` · 參考圖 ${it.refs} 張` }) : null),
      it.error ? el("div", { class: "hint err-text", text: it.error }) : null,
      (it.warnings || []).length ? el("div", { class: "hint", text: (it.warnings || []).join("；") }) : null,
    );
    ul.append(line);
  }
  box.append(ul);
  if (batchState.skipped.length) {
    const skipBox = el("div", { class: "batch-skip" },
      el("p", { class: "hint err-text", text: "以下鏡頭前置檢查沒過，沒有送 GPU（修好後再跑）：" }));
    const ul2 = el("ul", { class: "warn-list" });
    for (const s of batchState.skipped) {
      const text = Array.isArray(s.blockers) ? s.blockers.join("；") : String(s.blockers || s.blocker || s.error || s || "");
      const head = s.shotId ? `${s.shotId}${s.no ? `（第 ${s.no} 鏡）` : ""}` : "";
      ul2.append(el("li", {}, head ? el("b", { text: head }) : null, el("span", { text: head ? "：" + text : text })));
    }
    skipBox.append(ul2);
    box.append(skipBox);
  }
  if (running) {
    box.append(el("p", { class: "hint", text: "每 2 秒自動更新；完成一鏡就會立刻寫回 project.json（失敗不中斷）。" }));
  }
}

// ── M14a：分鏡表 ＋「由劇本自動分鏡」────────────────────────────
// 資料真相仍是 project.shots；分鏡表是同一份資料的另一種呈現（由上往下讀即整齣戲）。
// 自動分鏡一律走後端 POST /storyboard/build（純規則、零模型），回來後重抓專案 + 分鏡表。
const sbState = { panel: false, result: null, rows: null };

// 分鏡表表格：鏡號｜職責｜誰｜台詞（逐字，動作鏡給動作）｜秒數（含留白）｜累計
function storyboardTable(rows) {
  const wrap = el("div", { class: "card sb-table-card" });
  const total = Number(rows.totalSeconds || 0);
  wrap.append(el("div", { class: "card-head" },
    el("h3", { text: "🎬 分鏡表" }),
    el("span", { class: "hint", text: `${rows.shotCount || 0} 鏡 · 總長 ${sbFmtSec(total)} · 由上往下讀就是整齣戲` }),
    el("span", { class: "spacer" }),
    el("span", { class: "hint", text: "點列可捲到對應鏡頭卡" }),
  ));
  if (!rows || !(rows.rows || []).length) {
    wrap.append(el("p", { class: "hint", text: "尚未分鏡。按上方「🎬 由劇本自動分鏡」依劇本 beats 產生。" }));
    return wrap;
  }
  // 「劇本寫了、鏡頭沒演」的缺口：這是以往看不出「影片不懂在演什麼」的主因之一，
  // 部署產生的鏡頭只挑台詞，動作 beat 從不進 beatIds。這裡明列是哪幾個演出點沒鏡頭。
  const gaps = (rows.scenes || []).filter((s) => (s.unshotCount || 0) > 0);
  if (gaps.length) {
    const box = el("div", { class: "sb-gap-box" });
    box.append(el("div", { class: "hint", style: "margin-bottom:4px" },
      el("b", { text: `⚠ 劇本有 ${rows.unshotTotal || gaps.reduce((a, s) => a + s.unshotCount, 0)} 個演出點沒有對應鏡頭` }),
      el("span", { text: "（沒被演出來的台詞與動作，觀眾看不到）" })));
    for (const g of gaps) {
      box.append(el("div", { class: "hint", style: "margin-left:8px", text: `· ${g.title || g.episodeSceneId}：${g.note}` }));
    }
    box.append(el("div", { class: "hint", style: "margin-top:4px",
      text: "要補齊請按上方「🎬 由劇本自動分鏡」（動作 beat 目前不會自動生成鏡頭，需在那之後人工加鏡）。" }));
    wrap.append(box);
  }
  const table = el("table", { class: "m3d-table sb-table" });
  table.append(el("thead", {},
    el("tr", {},
      el("th", { text: "鏡號" }), el("th", { text: "職責" }), el("th", { text: "誰" }),
      el("th", { text: "台詞（逐字）／動作" }), el("th", { text: "秒數" }), el("th", { text: "累計" }),
    )));
  const tbody = el("tbody", {});
  let lastScene = "__init__";
  for (const r of rows.rows) {
    if (r.episodeSceneId !== lastScene) {
      lastScene = r.episodeSceneId;
      tbody.append(el("tr", { class: "sb-scene-row" },
        el("td", { colspan: "6", text: `— ${r.sceneTitle || "（未命名場次）"}（${r.episodeSceneId}）` })));
    }
    const isDlg = !!r.hasLine;
    const lineText = isDlg
      ? r.lines.filter((x) => x.type !== "action").map((x) => `${x.speaker}：「${x.text}」`).join("　")
      : (r.lines.map((x) => x.text).join("／") || r.text || "");
    const merged = (r.mergedCount || 0) > 1;
    const tdLine = el("td", { class: "sb-line" }, el("span", { text: lineText || "（無台詞／動作）" }));
    const tdDur = el("td", { class: "mono" },
      el("div", { text: sbFmtSec(r.durationSec) }),
      r.padSec > 0 ? el("div", { class: "hint", text: `留白 ${sbFmtSec(r.padSec)}` }) : null,
      merged ? el("span", { class: "sb-tag", text: `合併 ${r.mergedCount} 句` }) : null,
    );
    tbody.append(el("tr", {
      class: "sb-row" + (isDlg ? "" : " sb-row-action"),
      title: "點一下捲到這張鏡頭卡",
      onclick: () => {
        setSaveStatus("saved", `跳到第 ${r.no} 鏡…`);
        if (state.shotFilter && state.shotFilter !== r.episodeSceneId) { state.shotFilter = ""; renderShots(); }
        const node = document.getElementById(`shot-${r.shotId}`);
        if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
      },
    },
      el("td", { class: "mono", text: `${String(r.no).padStart(2, "0")}${r.shotId ? ` · ${r.shotId}` : ""}` }),
      el("td", {}, el("span", { class: `sb-role sb-role-${r.role || "none"}`, text: r.roleLabel || "（未設定）" })),
      el("td", { text: r.chars || r.speaker || "—" }),
      tdLine,
      tdDur,
      el("td", { class: "mono", text: `${sbFmtSec(r.startSec)} → ${sbFmtSec(r.endSec)}` }),
    ));
  }
  table.append(tbody);
  wrap.append(table);
  return wrap;
}

// 「由劇本自動分鏡」小面板：範圍（全部／單一場次）＋保留手動鏡頭
function sbBuildPanel() {
  const card = el("div", { class: "card sb-panel" });
  card.append(el("div", { class: "card-head" },
    el("h3", { text: "🎬 由劇本自動分鏡" }),
    el("span", { class: "hint", text: "依 docs/storyboard-contract.md：一鏡一事件、長度吸附 H3 合法格、台詞逐字照抄（純規則，不呼叫模型）" }),
  ));
  const scopeSel = el("select", { class: "inline-select" },
    el("option", { value: "all", text: "全部場次" }),
    ...episodeSceneOptions(false).map((o) => el("option", { value: `scene:${o.value}`, text: `單一場次：${o.text}` })));
  const keepChk = el("input", { type: "checkbox" });
  keepChk.checked = true;
  const forceChk = el("input", { type: "checkbox" });
  forceChk.checked = false;
  keepChk.addEventListener("change", () => { if (keepChk.checked) forceChk.checked = false; });
  forceChk.addEventListener("change", () => { if (forceChk.checked) keepChk.checked = false; });

  const runBtn = el("button", { class: "btn btn-small btn-primary", type: "button" }, "開始分鏡");
  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    runBtn.textContent = "分鏡中…";
    try {
      await buildStoryboard(scopeSel.value, keepChk.checked, forceChk.checked);
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = "開始分鏡";
    }
  });
  card.append(el("div", { class: "row wrap" },
    el("span", { class: "hint", text: "範圍：" }), scopeSel,
    el("label", { class: "hint inline-check" }, keepChk, el("span", { text: "保留手動調整過的鏡頭" })),
    el("label", { class: "hint inline-check" }, forceChk, el("span", { text: "覆蓋手動鏡頭（force）" })),
    el("span", { class: "spacer" }),
    runBtn,
  ));
  if (state.project && (state.project.shots || []).length) {
    card.append(el("p", { class: "hint", text: "提醒：重建會換掉「非手動」的舊鏡頭（含一鍵佈署產的），已在該鏡生過的影格／影片路徑不會跟著搬。" }));
  }
  if (sbState.result) card.append(sbResultBox(sbState.result));
  return card;
}

function sbResultBox(res) {
  const ok = !res.__error;
  const box = el("div", { class: "subcard sb-result" });
  if (!ok) {
    box.append(el("p", { class: "err-text", text: `分鏡失敗：${res.__error}` }));
    return box;
  }
  box.append(el("div", { class: "subcard-head" },
    el("h4", { text: "分鏡結果" }),
    el("span", { class: "hint ok-text", text: `新增 ${res.created || 0} 鏡 · 保留 ${res.kept || 0} 鏡 · 總長 ${sbFmtSec(res.totalSeconds || 0)}` }),
    el("span", { class: "spacer" }),
    el("span", { class: `hint ${(res.counts && res.counts.error) ? "err-text" : "ok-text"}`,
      text: `校驗：${(res.counts && res.counts.error) || 0} 錯誤 / ${(res.counts && res.counts.warn) || 0} 警告` }),
    el("button", { class: "btn btn-small", type: "button", onclick: () => { sbState.result = null; renderShots(); } }, "收起"),
  ));
  for (const w of res.warnings || []) box.append(el("p", { class: "hint warn-text", text: `⚠ ${w}` }));
  const errs = (res.issues || []).filter((i) => i.level === "error");
  for (const e of errs.slice(0, 12)) {
    box.append(el("p", { class: "hint err-text", text: `✗ ${e.name || e.scope}：${e.message}` }));
  }
  if (errs.length > 12) box.append(el("p", { class: "hint err-text", text: `…另有 ${errs.length - 12} 個錯誤，按「✓ 檢查全專案」看全部` }));
  return box;
}

async function buildStoryboard(scopeValue, keepManual, force) {
  const isScene = String(scopeValue || "").startsWith("scene:");
  const body = {
    scope: isScene ? "scene" : "all",
    sceneId: isScene ? scopeValue.slice(6) : null,
    force: !!force,
    keepManual: keepManual !== false,
  };
  try {
    await saveNow();  // 先把畫面上的編輯存回去，分鏡才不會吃掉未存的改動
    const res = await api(`/api/projects/${state.slug}/storyboard/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    sbState.result = res;
    sbState.panel = true;   // 讓「分鏡結果」面板留在畫面上
    state.validateResult = { issues: res.issues || [], counts: res.counts || { error: 0, warn: 0 }, checked: {}, at: "" };
    await reloadProject();
    setSaveStatus("saved", `自動分鏡完成：新增 ${res.created || 0} 鏡、保留 ${res.kept || 0} 鏡`);
  } catch (e) {
    sbState.result = { __error: e.message };
    setSaveStatus("error", `自動分鏡失敗：${e.message}`);
    renderShots();
    alert(`自動分鏡失敗：${e.message}`);
  }
}

async function reloadProject() {
  const data = await api(`/api/projects/${state.slug}`);
  state.project = data.project || data;
  state.dirty = false;
  if (typeof renderProjectList === "function") { /* 列表由呼叫端決定是否重畫 */ }
  renderShots();
}

async function loadStoryboardRows() {
  try {
    sbState.rows = await api(`/api/projects/${state.slug}/storyboard`);
  } catch (e) {
    sbState.rows = { rows: [], shotCount: 0, totalSeconds: 0, __error: e.message };
  }
  const node = document.getElementById("sb-table-host");
  if (node) {
    node.textContent = "";
    if (sbState.rows.__error) {
      node.append(el("p", { class: "hint err-text", text: `讀取分鏡表失敗：${sbState.rows.__error}` }));
    } else {
      node.append(storyboardTable(sbState.rows));
    }
  }
}

// ── 鏡頭分頁入口 ─────────────────────────────────────────
function renderShots() {
  const box = $("#view-shots");
  box.textContent = "";
  if (!state.project) { box.append(emptyHint()); return; }
  const list = state.project.shots || (state.project.shots = []);
  renumberShots();  // no 一律照陣列順序連號

  box.append(el("h2", { class: "view-title", text: "5 · 鏡頭" }));
  box.append(el("p", { class: "hint", text: `分鏡表 · ${list.length} 個鏡頭。可用場次篩選；「🎬 由劇本自動分鏡」依劇本 beats 一鍵產生分鏡（純規則，不呼叫模型）。` }));

  // 工具列
  const filterSel = el("select", { class: "inline-select" },
    el("option", { value: "", text: "全部場次" }),
    ...episodeSceneOptions(false).map((o) => el("option", { value: o.value, text: o.text })));
  filterSel.value = state.shotFilter || "";
  filterSel.addEventListener("change", () => { state.shotFilter = filterSel.value; renderShots(); });

  const splitSel = el("select", { class: "inline-select" },
    ...episodeSceneOptions(false).map((o) => el("option", { value: o.value, text: o.text })));
  if (!splitSel.options.length) splitSel.append(el("option", { value: "", text: "（尚無場次）" }));
  if (state.shotFilter) splitSel.value = state.shotFilter;

  box.append(el("div", { class: "card toolbar" },
    el("div", { class: "row wrap" },
      el("span", { class: "hint", text: "篩選場次：" }), filterSel,
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn btn-small btn-primary", type: "button", id: "btn-sb-build",
        title: "依 docs/storyboard-contract.md 把劇本 beats 派生成分鏡（純規則，不呼叫模型）",
        onclick: () => { sbState.panel = !sbState.panel; if (sbState.panel) sbState.result = null; renderShots(); },
      }, "🎬 由劇本自動分鏡"),
      el("button", { class: "btn btn-small", type: "button", title: "跑機械校驗（含分鏡契約 V1–V5；純邏輯，零 API 成本）", onclick: () => runValidate() }, "✓ 檢查全專案"),
      el("button", { class: "btn btn-small btn-primary", type: "button", onclick: () => addShot(filterSel.value) }, "＋ 新增鏡頭"),
    ),
    batchToolbarRow(filterSel.value),
    el("div", { class: "row wrap split-row" },
      el("span", { class: "hint", text: "由場次拆鏡頭草稿：" }), splitSel,
      el("button", { class: "btn btn-small", type: "button", onclick: () => splitDraftFromScene(splitSel.value) }, "⊞ 拆成鏡頭草稿"),
      el("span", { class: "hint", text: "每 beat 一鏡；對白帶入台詞，場景與人物繼承該場次；已存在的鏡頭不會被覆蓋或刪除。" }),
    ),
  ));

  // M14a：「由劇本自動分鏡」面板（按下 🎬 才展開）＋分鏡表（由上往下讀＝整齣戲）
  if (sbState.panel) box.append(sbBuildPanel());
  const sbHost = el("div", { id: "sb-table-host" }, el("p", { class: "hint", text: "分鏡表載入中…" }));
  box.append(sbHost);

  // M4b：批次生圖進度面板（有任務才顯示；每 2 秒 poll 更新）
  // 注意：要在 div 已經插進 DOM 之後才 paint（paintBatchPanel 用 querySelector 找 #batch-panel，
  // 太早呼叫會把內容畫到上一次渲染留下的舊節點，畫面上就看不到）。
  const bBox = el("div", { id: "batch-panel", class: "card batch-panel" + (batchState.job ? "" : " hidden") });
  box.append(bBox);
  document.body.classList.toggle("has-batch", !!batchState.job);
  if (batchState.job) paintBatchPanel();

  // M3d：校驗面板（放在工具列正下方，展開顯示問題清單）
  const vBox = el("div", { id: "validate-panel", class: "card validate-panel" + (state.validateResult ? "" : " hidden") });
  box.append(vBox);
  if (state.validateResult) paintValidate(vBox, state.validateResult);

  const filtered = (state.shotFilter ? list.filter((s) => s.episodeSceneId === state.shotFilter) : list);
  if (!filtered.length) {
    box.append(el("div", { class: "card placeholder-card" },
      el("h3", { text: state.shotFilter ? "這個場次還沒有鏡頭" : "尚無鏡頭" }),
      el("p", { class: "hint", text: "按上方「🎬 由劇本自動分鏡」依劇本 beats 一次產生，或按「＋ 新增鏡頭」手動加。" }),
    ));
    loadStoryboardRows();
    return;
  }
  // 場次分組標題（有篩選時就不重複顯示）
  let lastEs = "__init__";
  for (const sh of filtered) {
    const esKey = sh.episodeSceneId || "";
    if (!state.shotFilter && esKey !== lastEs) {
      const f = findEpisodeScene(esKey);
      box.append(el("div", { class: "shot-group-title" },
        el("span", { text: f ? `${f.ep.id}／${esLabel(f.es)}` : "（未指定場次）" }),
        el("span", { class: "hint", text: ` · ${filtered.filter((x) => (x.episodeSceneId || "") === esKey).length} 鏡` }),
      ));
      lastEs = esKey;
    }
    const card = shotCard(sh, idxOfShot(sh));
    card.id = `shot-${sh.id}`;
    box.append(card);
  }
  loadStoryboardRows();
}

function idxOfShot(sh) {
  return (state.project.shots || []).indexOf(sh);
}

// ── M3d：全專案機械校驗 ───────────────────────────────────────
const SCOPE_LABEL = { shot: "鏡頭", scene: "場景", character: "人物", episodeScene: "劇本場次" };

function jumpTo(issue) {
  if (issue.scope === "shot") {
    if (state.shotFilter) { state.shotFilter = ""; renderShots(); }
    const node = document.getElementById(`shot-${issue.targetId}`);
    if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  setView(issue.scope === "scene" ? "scenes" : issue.scope === "character" ? "characters" : "script");
  const id = issue.scope === "scene" ? `scene-${issue.targetId}`
    : issue.scope === "character" ? `char-${issue.targetId}`
      : `es-${issue.targetId}`;
  const node = document.getElementById(id);
  if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
  else alert(`找不到 ${SCOPE_LABEL[issue.scope] || issue.scope} 卡片（${issue.targetId}）；資料可能已被刪除。`);
}

function paintValidate(box, res) {
  box.textContent = "";
  const c = res.counts || { error: 0, warn: 0 };
  box.append(el("div", { class: "card-head" },
    el("h3", { text: `機械校驗結果 · ${c.error} 錯誤 / ${c.warn} 警告` }),
    el("span", { class: "spacer" }),
    el("span", { class: "hint", text: `${res.at || ""} · 檢查 ${res.checked?.shots ?? 0} 鏡 / ${res.checked?.scenes ?? 0} 場景 / ${res.checked?.characters ?? 0} 人物` }),
    el("button", { class: "btn btn-small", type: "button", onclick: () => { state.validateResult = null; renderShots(); } }, "收起"),
  ));
  const list = res.issues || [];
  const fixes = res.fixes || [];
  if (fixes.length) {
    // 「已自動處理」獨立一區：與「需要你決定」混在一起時，使用者會把已修好的東西當成沒修。
    const fbox = el("div", { class: "card validate-fixes" });
    fbox.append(el("div", { class: "card-head" },
      el("h3", { text: `✅ 已自動處理 ${fixes.length} 項` }),
      el("span", { class: "hint", text: "這些不是錯誤，但建議到對應頁面確認一次" })));
    for (const f of fixes) fbox.append(el("p", { class: "hint", text: `· ${f}` }));
    box.append(fbox);
  }
  if (!list.length) {
    box.append(el("p", { class: "hint ok-text", text: "全部通過，沒有問題。" }));
    return;
  }
  const ul = el("ul", { class: "issue-list" });
  for (const it of list) {
    ul.append(el("li", { class: `issue issue-${it.level}` },
      el("span", { class: `issue-level issue-${it.level}`, text: it.level === "error" ? "錯誤" : "警告" }),
      el("span", { class: "issue-scope", text: SCOPE_LABEL[it.scope] || it.scope }),
      el("button", {
        class: "issue-target", type: "button", title: "跳到這張卡",
        onclick: () => jumpTo(it),
      }, `${it.targetId}${it.name && it.name !== it.targetId ? " · " + it.name : ""}`),
      el("span", { class: "issue-msg", text: it.message }),
    ));
  }
  box.append(ul);
}

async function runValidate() {
  const btn = document.activeElement;
  if (btn && btn.tagName === "BUTTON") btn.disabled = true;
  try {
    await saveNow();
    const res = await api(`/api/projects/${state.slug}/validate`, { method: "POST" });
    state.validateResult = res;
    renderShots();
    setSaveStatus("saved", `校驗完成：${res.counts.error} 錯誤 / ${res.counts.warn} 警告`);
  } catch (e) {
    setSaveStatus("error", `校驗失敗：${e.message}`);
    alert(`校驗失敗：${e.message}`);
  } finally {
    if (btn && btn.tagName === "BUTTON") btn.disabled = false;
  }
}

// ── GPU 資源卡（VRAM 狀態 ＋ 三顆切換按鈕）─────────────────
// 後端只有量測與切換，不改任何模型參數（ctx／量化／MTP 一律不動）。
const gpuState = { status: null, timer: null, busy: false };

function gpuGB(mb) {
  if (mb === null || mb === undefined) return "?";
  return (mb / 1024).toFixed(1);
}

async function fetchGpuStatus() {
  const res = await api("/api/gpu/status");
  gpuState.status = res;
  return res;
}

function gpuBar(fillClass, ratio) {
  const pct = Math.max(0, Math.min(100, Math.round((ratio || 0) * 100)));
  return el("div", { class: "gpu-bar" }, el("div", { class: "gpu-bar-fill " + (fillClass || ""), style: `width:${pct}%` }));
}

function gpuSettingsCard() {
  const card = el("div", { class: "card" }, el("h3", { text: "GPU 資源（VRAM 切換）" }));

  const barWrap = el("div");
  const msg = el("p", { class: "gpu-msg hidden" });

  function showMsg(kind, text, delta) {
    msg.className = `gpu-msg ${kind}`;
    msg.textContent = text;
    if (delta) msg.append(el("span", { class: "gpu-msg-delta", text: delta }));
  }

  function render(st) {
    barWrap.textContent = "";
    const v = (st && st.vram) || {};
    const used = v.usedMB, total = v.totalMB, free = v.freeMB;
    barWrap.append(el("div", { class: "gpu-row" },
      el("span", { class: "gpu-label", text: "顯示卡 VRAM" }),
      gpuBar(free !== null && free < 2048 ? "warn" : "",
        total ? used / total : 0),
      el("span", { class: "gpu-num", text: used !== null ? `${gpuGB(used)} / ${gpuGB(total)} GB（空閒 ${gpuGB(free)} GB）` : "讀不到（nvidia-smi 不可用）" }),
    ));
    if (v.source && v.source !== "nvidia-smi") {
      barWrap.append(el("p", { class: "hint warn-text", text: v.source }));
    }

    const c = (st && st.comfy) || {};
    barWrap.append(el("div", { class: "gpu-row" },
      el("span", { class: "gpu-label", text: "ComfyUI :8000" }),
      c.running
        ? el("span", { class: "gpu-state-ok", text: `● 執行中${c.version ? "（v" + c.version + "）" : ""} · 自報可用 VRAM ${gpuGB(c.vramFreeMB)} GB` })
        : el("span", { class: "gpu-state-off", text: "○ 沒在跑" }),
    ));

    const l = (st && st.llama) || {};
    let llamaText;
    if (!l.running) llamaText = el("span", { class: "gpu-state-off", text: `○ 沒在跑（埠 ${l.port || 8080}）` });
    else {
      const name = (l.model || "").split(/[\\/]/).pop();
      const proc = l.isProcessing === true
        ? el("span", { class: "gpu-state-warn", text: " · ⏳ 運算中（此時不要停止）" })
        : el("span", { class: "gpu-state-ok", text: " · 閒置" });
      llamaText = el("span", {}, el("span", { class: "gpu-state-ok", text: `● 執行中 :${l.port}` }),
        el("span", { class: "hint", text: ` · ${name || "（未回報模型名）"}` }), proc);
    }
    barWrap.append(el("div", { class: "gpu-row" },
      el("span", { class: "gpu-label", text: "本機 LLM :8080" }), llamaText));

    if (st && st.warning) {
      barWrap.append(el("p", { class: "hint warn-text", text: "⚠ " + st.warning }));
    }
  }

  async function refresh() {
    try {
      const st = await fetchGpuStatus();
      render(st);
      fillModels(st);
      renderGpuStrip(st);
    } catch (e) {
      render(null);
      barWrap.append(el("p", { class: "hint err-text", text: `讀不到 GPU 狀態：${e.message}` }));
    }
  }

  function deltaText(before, after, extra) {
    const parts = [];
    if (before && after && before.freeMB !== null && after.freeMB !== null) {
      parts.push(`空閒 VRAM：${gpuGB(before.freeMB)} GB → ${gpuGB(after.freeMB)} GB（${after.freeMB - before.freeMB >= 0 ? "+" : ""}${after.freeMB - before.freeMB} MB）`);
    }
    if (extra) parts.push(extra);
    return parts.join("　");
  }

  async function run(kind, url, btn, busyText, bodyFn) {
    if (gpuState.busy) return;
    gpuState.busy = true;
    const all = [releaseBtn, stopBtn, startBtn];
    all.forEach((b) => { b.disabled = true; });
    showMsg("", busyText);
    const opts = { method: "POST" };
    if (bodyFn) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(bodyFn());
    }
    try {
      const res = await api(url, opts);
      showMsg(kind, res.message || "完成。", deltaText(res.before, res.after,
        res.freedMB !== null && res.freedMB !== undefined ? `本次回收 ${res.freedMB} MB` : ""));
    } catch (e) {
      showMsg("err", `❌ ${e.message}`);
    } finally {
      gpuState.busy = false;
      all.forEach((b) => { b.disabled = false; });
      await refresh();
    }
  }

  const releaseBtn = el("button", { class: "btn btn-small", type: "button", title: "打 ComfyUI /free，卸載生圖模型把 VRAM 讓出來（不會關掉 ComfyUI）" }, "釋放 ComfyUI 模型");
  const stopBtn = el("button", { class: "btn btn-small btn-danger", type: "button", title: "停止 llama-server（會先確認沒有在運算）" }, "停止本機 LLM（給生圖用）");
  const startBtn = el("button", { class: "btn btn-small btn-primary", type: "button", title: "用原本的參數背景啟動 llama-server，最多等 120 秒" }, "啟動本機 LLM");
  // 模型下拉：後端 /api/gpu/status 的 models（models 目錄裡的 gguf），預設跟桌面 llama-star bat 一致
  const modelSel = el("select", { id: "gpu-model-select", title: "「啟動本機 LLM」要用哪個 gguf" });
  function fillModels(st) {
    const keep = modelSel.value;
    modelSel.textContent = "";
    const models = (st && st.models) || [];
    if (!models.length) { modelSel.append(el("option", { value: "", text: "（模型目錄讀不到 gguf）" })); return; }
    models.forEach((m) => modelSel.append(el("option", {
      value: m.file, text: `${m.file}（${m.sizeGB} GB）`,
    })));
    const want = keep || (st && st.defaultModel) || models[0].file;
    if ([...modelSel.options].some((o) => o.value === want)) modelSel.value = want;
  }

  releaseBtn.addEventListener("click", () => run("ok", "/api/gpu/release-comfy", releaseBtn, "正在釋放 ComfyUI 模型…（最多等 6 秒看 VRAM）"));
  stopBtn.addEventListener("click", () => run("ok", "/api/gpu/stop-llama", stopBtn, "正在停止本機 LLM…"));
  startBtn.addEventListener("click", () => run("ok", "/api/gpu/start-llama", startBtn,
    "正在啟動本機 LLM…（載入 27B 模型最久等 120 秒，請不要關頁面）", () => ({ model: modelSel.value || null })));

  const refreshBtn = el("button", { class: "btn btn-small", type: "button" }, "重新整理狀態");
  refreshBtn.addEventListener("click", async () => { refreshBtn.disabled = true; await refresh(); refreshBtn.disabled = false; });

  // 自動切換開關（settings.gpu.autoSwitch，預設開）：勾起來＝生圖／生影片前自動停 LLM、跑完自動啟回
  const autoChk = el("input", { type: "checkbox", id: "gpu-auto-switch" });
  const autoMsg = el("span", { class: "hint gpu-auto-msg" });
  async function loadAuto() {
    try {
      const r = await api("/api/gpu/settings");
      autoChk.checked = !!r.autoSwitch;
    } catch (_) { autoChk.checked = true; }
  }
  autoChk.addEventListener("change", async () => {
    autoChk.disabled = true;
    autoMsg.className = "hint gpu-auto-msg";
    autoMsg.textContent = "正在儲存…";
    try {
      const r = await api("/api/gpu/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ autoSwitch: autoChk.checked }),
      });
      autoChk.checked = !!r.autoSwitch;
      autoMsg.className = "hint ok-text gpu-auto-msg";
      autoMsg.textContent = r.message || "已儲存";
    } catch (e) {
      autoChk.checked = !autoChk.checked;
      autoMsg.className = "hint err-text gpu-auto-msg";
      autoMsg.textContent = `儲存失敗：${e.message}`;
    } finally { autoChk.disabled = false; }
  });
  loadAuto();

  card.append(barWrap);
  card.append(el("div", { class: "gpu-actions" }, releaseBtn, stopBtn, startBtn, refreshBtn));
  card.append(el("div", { class: "gpu-row", style: "margin-top:8px" },
    el("span", { class: "gpu-label", text: "啟動用模型" }), modelSel));
  card.append(el("label", { class: "gpu-auto-switch", for: "gpu-auto-switch" },
    autoChk,
    el("span", { text: "生圖／生影片前自動停止本機 LLM，完成後自動啟回（批次只切換一次）" })));
  card.append(autoMsg);
  card.append(msg);
  card.append(el("p", { class: "hint", text:
    "生圖與本機 LLM 共用同一張卡：勾選上面那項後，生圖／生影片會自己把閒置的本機 LLM 停掉、生完自動啟回；"
    + "若 LLM 正在回覆（slot 忙碌）會擋下並提示，不會硬斷。要手動控制就取消勾選，改用下面三顆按鈕。"
    + "啟動後若空閒 VRAM 少於 2 GB，llama.cpp 的層會落到 CPU，速度會從幾十 t/s 掉到 1 t/s 以下。" }));

  // 進頁面先抓一次；離開設定頁時停掉輪詢
  refresh();
  if (gpuState.timer) clearInterval(gpuState.timer);
  gpuState.timer = setInterval(() => { if (document.querySelector("#view-settings .card")) refresh(); }, 15000);
  return card;
}

// 全域狀態列（右下角）：GPU 空閒 x GB ＋ 低 VRAM 提醒
function renderGpuStrip(st) {
  const strip = $("#gpu-strip");
  if (!strip) return;
  const v = (st && st.vram) || {};
  const l = (st && st.llama) || {};
  if (!v.totalMB) { strip.classList.add("hidden"); return; }
  strip.classList.remove("hidden");
  const text = $("#gpu-strip-text");
  text.textContent = "";
  text.append(el("span", { class: "gpu-strip-main", text: `GPU 空閒 ${gpuGB(v.freeMB)} GB` }));
  text.append(el("span", { text: ` / ${gpuGB(v.totalMB)} GB` }));
  text.append(el("span", { text: `　ComfyUI ${(st.comfy && st.comfy.running) ? "開" : "關"}　本機 LLM ${l.running ? "開" : "關"}` }));
  const warn = $("#gpu-strip-warn");
  const llmProvider = (state.project && state.project.settings && state.project.settings.llm
    && state.project.settings.llm.provider) || "local";
  const lowVram = v.freeMB !== null && v.freeMB < 2048;
  if (lowVram && (llmProvider === "local" || l.running)) {
    warn.textContent = "本機 LLM 可能落到 CPU（會很慢）";
    warn.classList.remove("hidden");
    strip.classList.add("has-warn");
  } else {
    warn.classList.add("hidden");
    strip.classList.remove("has-warn");
  }
}

async function refreshGpuStrip() {
  try { renderGpuStrip(await fetchGpuStatus()); } catch (_) { /* 讀不到就不顯示 */ }
}

// ── M4a：ComfyUI（settings.comfy）設定卡 ───────────────────
function comfySettingsCard() {
  const card = el("div", { class: "card" }, el("h3", { text: "本機生圖服務（settings.comfy）" }));
  const settings = ensure_obj(state.project, "settings", {});
  const comfy = settings.comfy && typeof settings.comfy === "object" ? settings.comfy
    : (settings.comfy = { baseUrl: "http://127.0.0.1:8000" });
  if (!comfy.baseUrl) comfy.baseUrl = "http://127.0.0.1:8000";

  const baseIn = el("input", { type: "text", placeholder: "http://127.0.0.1:8000", value: comfy.baseUrl });
  baseIn.addEventListener("input", () => { comfy.baseUrl = baseIn.value.trim(); markDirty(); });
  card.append(el("label", { class: "field" }, el("span", { text: "ComfyUI 位址（comfy.baseUrl）" }), baseIn));
  card.append(el("p", { class: "hint", text: "生圖（Qwen Image 2.1 i2i／t2i）走這裡的 ComfyUI；模型是 qwen_image_2.1_int8_convrot，解析度預設 1024。" }));

  const status = el("p", { class: "hint", text: "按「檢查 ComfyUI」測 /system_stats。" });
  const btn = el("button", { class: "btn btn-small", type: "button" }, "檢查 ComfyUI");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    status.className = "hint";
    status.textContent = "檢查中…";
    try {
      const res = await api(`/api/comfy/status?slug=${encodeURIComponent(state.slug)}&baseUrl=${encodeURIComponent(baseIn.value.trim())}`);
      if (res.ok) {
        status.className = "hint ok-text";
        status.textContent = `✓ 可用：v${res.version || "?"} · ${res.device || "?"} · VRAM 剩 ${res.vramFreeMB ?? "?"} MB／${res.vramTotalMB ?? "?"} MB`;
      } else {
        status.className = "hint err-text";
        status.textContent = `✕ ${res.message}`;
      }
    } catch (e) {
      status.className = "hint err-text";
      status.textContent = `✕ 檢查失敗：${e.message}`;
    } finally { btn.disabled = false; }
  });
  card.append(el("div", { class: "row wrap" }, btn, el("span", { class: "spacer" }), status));

  // M6：生圖引擎下拉（settings.imageEngine，預設 qwen）
  const engWrap = el("div", { class: "row wrap" });
  const curEng = settings.imageEngine || "qwen";
  const engSel = el("select", {},
    el("option", { value: "qwen", text: "Qwen Image 2.1（i2i 編輯／t2i，預設）" }),
    el("option", { value: "moss", text: "MOSS Image SFT（t2i，純文字 caption）" }));
  engSel.value = curEng;
  const engInfo = el("span", { class: "hint", text: `目前引擎：${curEng === "moss" ? "MOSS Image SFT" : "Qwen Image 2.1"}` });
  const engLoader = el("p", { class: "hint mono", text: "" });
  const engBtn = el("button", { class: "btn btn-small", type: "button" }, "儲存引擎");
  const loadEngineInfo = async () => {
    try {
      const r = await api(`/api/projects/${encodeURIComponent(state.slug)}/image/engine`);
      settings.imageEngine = r.engine;   // 以後端為準（避免本地副本跟檔案不一致）
      if (r.loaders && !r.loaders.error) {
        const f = (k) => `${k}=${r.loaders[k] && r.loaders[k].exists ? "✓" : "✕"}${r.loaders[k] && r.loaders[k].want ? " " + r.loaders[k].want : ""}`;
        engLoader.textContent = `MOSS 三件套（ComfyUI 實測清單）：${f("unet")}｜${f("clip")}｜${f("vae")}`
          + (r.loaders.clipTypeOk ? "｜type=moss_image ✓" : "｜type=moss_image ✕");
      } else if (r.loaders && r.loaders.error) {
        engLoader.textContent = `讀不到 ComfyUI 模型清單：${r.loaders.error}`;
      }
    } catch (_) { /* 讀不到就只顯示本地值 */ }
  };
  engBtn.addEventListener("click", async () => {
    engBtn.disabled = true;
    try {
      const r = await api(`/api/projects/${encodeURIComponent(state.slug)}/image/engine`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine: engSel.value }),
      });
      settings.imageEngine = r.engine;
      engInfo.textContent = `目前引擎：${r.label}（已存到 project.json）`;
      setSaveStatus("saved", `生圖引擎已改為 ${r.engine} ${clockText()}`);
    } catch (e) {
      engInfo.className = "hint err-text";
      engInfo.textContent = `儲存失敗：${e.message}`;
    } finally { engBtn.disabled = false; }
  });
  engWrap.append(engSel, el("span", { class: "spacer" }), engBtn, engInfo);
  card.append(el("label", { class: "field" }, el("span", { text: "生圖引擎（settings.imageEngine）" }), engWrap));
  card.append(el("p", { class: "hint", text: "MOSS 不吃參考圖：身份靠 caption 裡的 identityAnchors 逐字描述；角色立繪請用「生成與合併 → 角色立繪」。切換後不重啟 ComfyUI，它會自己卸載舊模型載入 MOSS（第一次約 1–2 分鐘）。" }));
  card.append(engLoader);
  // 兩層圖片生成的分工（照使用者要的順序寫清楚）
  const layers = el("div", { class: "subcard h3sheet-layers" });
  layers.append(el("h4", { text: "圖片生成有兩層（順序固定）" }));
  layers.append(el("ul", { class: "layer-list" },
    el("li", {}, el("b", { text: "第一層：單張圖 — MOSS / Qwen（文字或圖生圖）" }),
      el("span", { class: "hint", text: " 上面這個下拉選的就是「生一張」用哪一顆：MOSS＝純文字 t2i（不吃參考圖）；Qwen＝i2i 編輯／t2i。角色立繪、鏡頭首幀都走這層，幾十秒到幾分鐘一張。" })),
    el("li", {}, el("b", { text: "第二層：多角度 — H3 Character Sheet Designer（i2i）" }),
      el("span", { class: "hint", text: " 角色卡上的「🎭 生成多角度設定表（H3）」：把第一層生的立繪轉成正面／側面／背面的設定表。很慢（21GB UNET＋25GB text encoder，16GB VRAM 會大量 RAM offload，實測 10–25 分鐘），也很吃記憶體；不受上面這個下拉影響，固定走 H3。" })),
  ));
  layers.append(el("p", { class: "hint", text: "建議順序：先用 MOSS 生一張立繪 → 覺得角度不夠，再用 H3 做多角度設定表 → 之後的鏡頭首幀可以拿設定表的某個角度當參考圖。" }));
  card.append(layers);
  loadEngineInfo();
  return card;
}


// ── M9：影片生成設定卡（settings.video：LoRA / steps / cfg / shift）──────────
// LoRA 走導演台 timeline 每段的 loras 欄位（不是外掛 LoraLoaderModelOnly 節點）。
const VIDEO_LORA_CHOICES = [
  { name: "", label: "不掛 LoRA" },
  { name: "pika_h3_flex_turbo_4step_ref2va.safetensors", label: "Pika Flex Turbo 4step Ref2VA" },
  { name: "pika_h3_flex_ref2va_comfy_bf16.safetensors", label: "Pika Flex Ref2VA bf16" },
  { name: "__custom__", label: "自訂檔名…" },
];
const VIDEO_GEN_DEFAULTS = {
  lora: { name: "pika_h3_flex_ref2va_comfy_bf16.safetensors", strength: 1.0, active: true },
  steps: 6, cfg: 1.0, shiftVideo: 12.0, shiftAudio: 3.0, sampler: "euler", scheduler: "simple",
};

function videoSettings() {
  const settings = ensure_obj(state.project, "settings", {});
  if (!settings.video || typeof settings.video !== "object") settings.video = JSON.parse(JSON.stringify(VIDEO_GEN_DEFAULTS));
  return settings.video;
}

function videoLoraLabel(name) {
  if (!name) return "不掛 LoRA";
  const hit = VIDEO_LORA_CHOICES.find((c) => c.name === name);
  return hit && hit.name !== "__custom__" ? hit.label : name;
}

function videoGenSettingsCard() {
  const card = el("div", { class: "card" }, el("h3", { text: "影片生成（settings.video・H3 導演台）" }));
  const vs = videoSettings();
  const cur = vs.lora || VIDEO_GEN_DEFAULTS.lora;
  const known = VIDEO_LORA_CHOICES.some((c) => c.name === cur.name);
  const status = el("p", { class: "hint", text: "" });

  const sel = el("select", {}, ...VIDEO_LORA_CHOICES.map((c) => el("option", { value: c.name, text: c.label })));
  sel.value = known ? cur.name : "__custom__";
  const custom = el("input", { type: "text", placeholder: "models/loras 下的檔名，例如 pika_h3_flex_ref2va_comfy_bf16.safetensors",
    value: known ? "" : (cur.name || "") });
  custom.classList.toggle("hidden", sel.value !== "__custom__");
  const strength = el("input", { type: "number", min: "0", max: "1.5", step: "0.05", value: String(cur.strength ?? 1.0) });
  const num = (key, min, max, step) => {
    const i = el("input", { type: "number", min, max, step, value: String(vs[key] ?? VIDEO_GEN_DEFAULTS[key]) });
    i.addEventListener("input", () => { vs[key] = i.value === "" ? "" : Number(i.value); markDirty(); });
    return el("label", { class: "field" }, el("span", { text: key }), i);
  };

  const save = async () => {
    const name = sel.value === "__custom__" ? custom.value.trim() : sel.value;
    vs.lora = name ? { name, strength: Number(strength.value || 1.0), active: true }
      : { name: "", strength: 1.0, active: false };
    try {
      const r = await api(`/api/projects/${encodeURIComponent(state.slug)}/video/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...vs, lora: vs.lora }),
      });
      Object.assign(vs, r.settings);
      status.className = "hint ok-text";
      status.textContent = `✓ 已存：${videoLoraLabel(vs.lora && vs.lora.name)} @ ${vs.lora && vs.lora.strength}｜steps ${vs.steps}｜cfg ${vs.cfg}｜shift ${vs.shiftVideo}/${vs.shiftAudio}`;
      setSaveStatus("saved", "影片 LoRA 設定已儲存 " + clockText());
    } catch (e) {
      status.className = "hint err-text";
      status.textContent = `✕ 儲存失敗：${e.message}`;
    }
  };
  sel.addEventListener("change", () => {
    custom.classList.toggle("hidden", sel.value !== "__custom__");
    vs.lora = { name: sel.value === "__custom__" ? custom.value.trim() : sel.value,
      strength: Number(strength.value || 1.0), active: true };
    markDirty(); save();
  });
  strength.addEventListener("change", () => { markDirty(); save(); });
  custom.addEventListener("change", () => { markDirty(); save(); });

  card.append(el("label", { class: "field" }, el("span", { text: "影片 LoRA（settings.video.lora）" }), sel), custom,
    el("label", { class: "field" }, el("span", { text: "strength（0–1.5）" }), strength));
  card.append(el("div", { class: "row wrap" }, num("steps", "1", "60", "1"), num("cfg", "0", "30", "0.1"),
    num("shiftVideo", "0.01", "100", "0.5"), num("shiftAudio", "0.01", "100", "0.5")));
  card.append(el("p", { class: "hint", text: "預設＝Pika Flex 官方建議：strength 1.0、cfg 1.0、euler/simple、shift video 12／audio 3、steps 6（音訊在 6 步以下會崩）。加速 LoRA（Pika／LightX2V／larryvrh／DMAD）永不互相疊加，一支為限；底模維持 minimax_h3_ref2va_pruned_int8_convrot（不要換成已烘 turbo 的融合底模）。" }));
  card.append(el("div", { class: "row wrap" }, el("button", { class: "btn btn-small btn-primary", type: "button", onclick: save }, "儲存影片設定"), status));
  return card;
}

// ── 渲染：設定 / 原始資料 ────────────────────────────────
function renderSettings() {
  const box = $("#view-settings");
  box.textContent = "";
  if (!state.project) { box.append(emptyHint()); return; }
  box.append(el("h2", { class: "view-title", text: "設定 / 原始資料" }));

  const meta = state.project.meta;
  const metaCard = el("div", { class: "card" }, el("h3", { text: "專案中繼資料（meta）" }));
  metaCard.append(field("名稱（name）", meta, "name", "專案顯示名稱", false));
  const aspectSel = el("select", {},
    ...["9:16", "3:4", "4:3", "1:1", "16:9", "21:9"].map((v) =>
      el("option", { value: v, text: v })));
  aspectSel.value = meta.aspectRatio || "9:16";
  aspectSel.addEventListener("change", () => { meta.aspectRatio = aspectSel.value; markDirty(); });
  metaCard.append(el("label", { class: "field" }, el("span", { text: "畫面比例（aspectRatio）" }), aspectSel));
  const fps = el("input", { type: "number", min: "1", max: "60", value: String(meta.fps ?? 24) });
  fps.addEventListener("input", () => { meta.fps = parseInt(fps.value || "24", 10); markDirty(); });
  metaCard.append(el("label", { class: "field" }, el("span", { text: "fps" }), fps));
  metaCard.append(el("p", { class: "hint", text: `slug：${meta.slug} · 建立於 ${meta.createdAt} · 更新於 ${meta.updatedAt}` }));
  box.append(metaCard);
  box.append(gpuSettingsCard());
  box.append(comfySettingsCard());
  box.append(videoGenSettingsCard());

  // M3a：模型服務設定（settings.llm）＋即時狀態；M3b：供應商（local/online/custom）＋線上 key
  const llmCard = el("div", { class: "card" }, el("h3", { text: "模型服務（settings.llm）" }));
  const llm = ensure_obj(state.project, "settings", {}).llm || (state.project.settings.llm = {
    baseUrl: "http://127.0.0.1:8080/v1", model: "auto", temperature: 0.8, provider: "local",
  });
  if (!llm.provider) llm.provider = "local";

  const LLM_PRESETS = {
    local: { baseUrl: "http://127.0.0.1:8080/v1", model: "auto", label: "本機服務（local，OpenAI 相容，預設 127.0.0.1:8080）" },
    online: { baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat", label: "線上 DeepSeek（online，需 API key）" },
    custom: { baseUrl: "", model: "", label: "自訂（custom，任何 OpenAI 相容端點＋key）" },
  };

  const provSel = el("select", {},
    ...Object.entries(LLM_PRESETS).map(([v, p]) => el("option", { value: v, text: p.label })));
  provSel.value = llm.provider;
  llmCard.append(el("label", { class: "field" }, el("span", { text: "供應商（provider）" }), provSel));

  const baseIn = el("input", { type: "text", placeholder: "例如：https://api.deepseek.com/v1", value: llm.baseUrl || "" });
  llmCard.append(el("label", { class: "field" }, el("span", { text: "服務位址（baseUrl）" }), baseIn));

  const modelIn = el("input", { type: "text", placeholder: "deepseek-chat / auto", value: llm.model || "" });
  llmCard.append(el("label", { class: "field" }, el("span", { text: "模型（model；auto＝取伺服器第一個）" }), modelIn));

  // API key：填了只供本次連線／儲存後的當次測試用，不寫進 project.json
  const keyIn = el("input", { type: "password", autocomplete: "off",
    placeholder: "留空＝用環境變數 LLM_API_KEY 或 hermes .env 的 DEEPSEEK_API_KEY" });
  const keyRow = el("label", { class: "field" },
    el("span", { text: "API Key（不會寫進 project.json）" }), keyIn);
  llmCard.append(keyRow);

  const modelListBox = el("div", { id: "llm-model-list", class: "row wrap", style: "margin-top:4px" });
  const tempIn = el("input", { type: "number", min: "0", max: "2", step: "0.1" });
  tempIn.value = String(llm.temperature ?? 0.8);
  tempIn.addEventListener("input", () => {
    const v = parseFloat(tempIn.value || "0.8");
    if (Number.isFinite(v)) { llm.temperature = v; markDirty(); }
  });
  llmCard.append(el("label", { class: "field" }, el("span", { text: "溫度（temperature）" }), tempIn));

  const statusLine = el("p", { class: "hint", text: "按「重新檢查」測連線（走 GET /v1/models）；有 key 時會抓到可用模型列表。" });
  const saveBtn = el("button", { class: "btn btn-small btn-primary", type: "button" }, "儲存供應商設定");
  const checkBtn = el("button", { class: "btn btn-small", type: "button" }, "重新檢查");

  function collectLlmBody() {
    const body = { provider: provSel.value, baseUrl: baseIn.value.trim(), model: modelIn.value.trim() };
    if (keyIn.value.trim()) body.apiKey = keyIn.value.trim();
    return body;
  }

  function fillProviderDefaults(next, prev) {
    const p = LLM_PRESETS[next];
    if (!p) return;
    const curBase = (baseIn.value || "").trim();
    // 位址還是上一個供應商的預設（或空）才自動代換，避免蓋掉使用者手打的
    if (!curBase || (prev && curBase === LLM_PRESETS[prev].baseUrl)) baseIn.value = p.baseUrl;
    const curModel = (modelIn.value || "").trim();
    if (!curModel || (prev && curModel === LLM_PRESETS[prev].model)) modelIn.value = p.model;
    if (next === "local") keyRow.classList.add("hidden"); else keyRow.classList.remove("hidden");
  }

  provSel.addEventListener("change", () => {
    const prev = llm.provider;
    llm.provider = provSel.value;
    fillProviderDefaults(llm.provider, prev);
    llm.baseUrl = baseIn.value.trim();
    llm.model = modelIn.value.trim();
    markDirty();   // baseUrl/model/provider 是真正的設定 → 走自動儲存
  });
  baseIn.addEventListener("input", () => { llm.baseUrl = baseIn.value.trim(); markDirty(); });
  modelIn.addEventListener("input", () => { llm.model = modelIn.value.trim(); markDirty(); });
  if (llm.provider === "local") keyRow.classList.add("hidden");

  // 有 key 時，把可用模型列成可點選的 chip
  function renderModelChips(res) {
    modelListBox.textContent = "";
    const models = (res && res.models) || [];
    if (!models.length) return;
    modelListBox.append(el("span", { class: "hint", text: `可用模型（${models.length}）：` }));
    models.forEach((m) => {
      const chip = el("button", { class: "chip" + (m === res.model ? " active" : ""), type: "button", title: "用這個模型" }, m);
      chip.addEventListener("click", () => { modelIn.value = m; llm.model = m; markDirty(); renderModelChips({ ...res, model: m }); });
      modelListBox.append(chip);
    });
  }

  function showStatus(res) {
    const keyTxt = res.hasApiKey
      ? `API key：已設定（${res.apiKeyMasked || "****"}，來源：${res.apiKeySource || "—"}）`
      : "API key：未設定";
    if (res.ok) {
      statusLine.textContent = `✅ ${res.providerLabel || res.provider} 已連線 · 使用模型：${res.model}` +
        (res.modelNote ? `（${res.modelNote}）` : "") + ` · ${keyTxt}`;
      statusLine.className = "hint ok-line";
      renderModelChips(res);
    } else {
      statusLine.textContent = `❌ ${res.error || "連不上"} · ${keyTxt}`;
      statusLine.className = "hint err-line";
      modelListBox.textContent = "";
    }
  }

  checkBtn.addEventListener("click", async () => {
    checkBtn.disabled = true;
    statusLine.textContent = "檢查中…（線上供應商需連外網）";
    try {
      const q = new URLSearchParams();
      if (state.slug) q.set("slug", state.slug);
      const res = await api(`/api/llm/test?${q.toString()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectLlmBody()),
      });
      showStatus(res);
    } catch (e) {
      statusLine.textContent = `❌ ${e.message}`;
      statusLine.className = "hint err-line";
    } finally {
      checkBtn.disabled = false;
    }
  });

  saveBtn.addEventListener("click", async () => {
    if (!state.slug) return;
    saveBtn.disabled = true;
    statusLine.textContent = "儲存中…";
    try {
      clearTimeout(state.saveTimer);   // 先不要把整份（含尚未儲存的其它編輯）蓋回去
      const res = await api(`/api/projects/${state.slug}/llm/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectLlmBody()),
      });
      if (state.project.settings && state.project.settings.llm) {
        state.project.settings.llm.provider = provSel.value;
        state.project.settings.llm.baseUrl = baseIn.value.trim();
        state.project.settings.llm.model = modelIn.value.trim();
        delete state.project.settings.llm.apiKey;   // key 不留在記憶體副本裡
      }
      keyIn.value = "";
      if (res.updatedAt) state.project.meta.updatedAt = res.updatedAt;
      state.dirty = false;
      setSaveStatus("saved", `供應商設定已儲存 ${clockText()}`);
      showStatus(res);
      chatState.llm = null;   // 讓抽屜下次打開重新檢查
    } catch (e) {
      statusLine.textContent = `❌ 儲存失敗：${e.message}`;
      statusLine.className = "hint err-line";
    } finally {
      saveBtn.disabled = false;
    }
  });

  llmCard.append(el("div", { class: "row wrap" }, saveBtn, checkBtn,
    el("span", { class: "hint", text: "改 baseUrl／model 也會自動儲存（1.5 秒後）；key 只在後端解析。" })));
  llmCard.append(statusLine, modelListBox);
  llmCard.append(el("p", { class: "hint", text:
    "API key 解析順序：專案 apiKey → 環境變數 LLM_API_KEY →（online）hermes .env 的 DEEPSEEK_API_KEY。"
    + " key 永遠不會回傳到畫面，也不會寫進 project.json。" }));
  box.append(llmCard);

  // 進頁面先偷看一下目前狀態（只讀，不寫檔）
  (async () => {
    try {
      const q = new URLSearchParams();
      if (state.slug) q.set("slug", state.slug);
      const res = await api(`/api/llm/status?${q.toString()}`);
      showStatus(res);
    } catch (_) { /* 狀態查不到不影響設定編輯 */ }
  })();

  const jsonCard = el("div", { class: "card" });
  jsonCard.append(el("div", { class: "card-head" },
    el("h3", { text: "project.json（唯讀預覽）" }),
    el("button", { class: "btn btn-small", type: "button", onclick: downloadBackup }, "⬇ 下載備份"),
  ));
  const pre = el("pre", { class: "json-preview", text: JSON.stringify(state.project, null, 2) });
  jsonCard.append(pre);
  box.append(jsonCard);
}

function downloadBackup() {
  if (!state.project) return;
  const blob = new Blob([JSON.stringify(state.project, null, 2)], { type: "application/json" });
  const a = el("a", {
    href: URL.createObjectURL(blob),
    download: `${state.project.meta.slug}-project.json`,
  });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ══════════════════════════════════════════════════════════════════
// M5b / M6：「生成與合併」頁（第六頁）
//   · 批次生影片（依序、不並行；預設先跑 1 鏡）＋ 每 2 秒 poll 進度面板
//   · 合併成長片（copy / reencode / auto）＋ 成品播放與下載
// ══════════════════════════════════════════════════════════════════
const mkState = {
  vidJob: null,        // 最近一次批次生影片 job（後端回來的）
  vidJobId: null,
  vidTimer: null,
  vidBusy: false,      // 送件請求進行中
  params: { width: 720, height: 1280, frames: 121, steps: 12, cfg: 1.0, seed: "" },
  trialCount: 1,
  scope: "all",
  picked: {},          // shotId → true（合併用：要不要納入）
  order: null,         // 合併排序（shotId 陣列；null＝照鏡號）
  assemble: null,      // 最近一次合併結果
  asmError: "",
  asmBusy: false,
  plan: null,          // /assemble/plan 結果
  outputs: [],         // 已合併成品
  showAll: false,      // 清單是否顯示全部鏡頭（預設只顯示有影片的）
};

const VID_BATCH_STATUS_TEXT = {
  queued: "排隊中", running: "進行中", done: "全部完成", failed: "全部失敗",
  partial: "部分成功", cancelled: "已取消", pending: "等待", blocked: "前置未過", skipped: "已取消",
};

function fmtDur(sec) {
  if (sec == null || isNaN(sec)) return "—";
  return `${Number(sec).toFixed(2)}s`;
}

function fmtBytes(n) {
  if (n == null) return "—";
  if (n >= 1048576) return `${(n / 1048576).toFixed(2)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

// 合併清單（依鏡號排序；order 可自訂）
function assembleShots() {
  const shots = (state.project?.shots || []).slice();
  const byId = new Map(shots.map((s) => [s.id, s]));
  const base = shots.slice().sort((a, b) => Number(a.no || 0) - Number(b.no || 0));
  let ordered = base;
  if (mkState.order) {
    const known = mkState.order.filter((id) => byId.has(id));
    const rest = base.filter((s) => !known.includes(s.id));
    ordered = known.map((id) => byId.get(id)).concat(rest);
  }
  return ordered.map((s) => ({
    shot: s,
    path: (s.video && s.video.path) || "",
    has: !!((s.video && s.video.path)),
  }));
}

function assemblePickedIds() {
  return assembleShots().filter((x) => x.has && mkState.picked[x.shot.id] !== false)
    .map((x) => x.shot.id);
}

// ── 頁面 ─────────────────────────────────────────────────
function renderGenerate() {
  const box = $("#view-generate");
  if (!box) return;
  box.textContent = "";
  if (!state.project) { box.append(emptyHint()); return; }

  box.append(el("h2", { class: "view-title", text: "6 · 生成與合併" }));
  box.append(el("p", { class: "hint", text: "批次生影片（H3 導演台 r2v，依序一鏡一鏡跑）→ 確認每鏡 OK → 合併成長片。"
    + "生圖在鏡頭頁的鏡頭卡上；這裡管影片與成品。" }));

  // ① 批次生影片
  box.append(videoBatchCard());
  // ② 進度面板（有任務才顯示）
  const pBox = el("div", { id: "vid-batch-panel", class: "card batch-panel" + (mkState.vidJob ? "" : " hidden") });
  box.append(pBox);
  if (mkState.vidJob) paintVideoBatchPanel();
  else box.append(el("p", { class: "hint", text: "② 進度面板：按下「🎬 批次生影片」後會出現在這裡（每 2 秒自動更新；換頁或 F5 也會接回）。" }));
  // ③ 合併
  box.append(assembleCard());
  // ④ 成品
  box.append(outputsCard());
}

function videoBatchCard() {
  const card = el("div", { class: "card" });
  const p = mkState.params;
  const numInput = (label, key, attrs, hint) => {
    const inp = el("input", Object.assign({ type: "number" }, attrs));
    inp.value = p[key] === "" ? "" : String(p[key]);
    inp.addEventListener("input", () => {
      const v = inp.value.trim();
      p[key] = v === "" ? "" : Number(v);
    });
    return el("label", { class: "field" }, el("span", { text: label }), inp,
      hint ? el("span", { class: "hint", text: hint }) : null);
  };
  const adv = el("div", { class: "vg-adv hidden" },
    numInput("寬度 width", "width", { min: "32", max: "8192", step: "32" }),
    numInput("高度 height", "height", { min: "32", max: "8192", step: "32" }),
    numInput("影格 frames（17k+5）", "frames", { min: "5", max: "1000", step: "1" }, "121 幀 ≈ 5 秒"),
    numInput("步數 steps", "steps", { min: "1", max: "60", step: "1" }, "12 是實測夠用的值；20 更細但更慢"),
    numInput("CFG", "cfg", { min: "0", max: "30", step: "0.1" }),
    numInput("seed（留白＝隨機）", "seed", { min: "0", max: "2147483647", step: "1" }),
    el("p", { class: "hint", text: "影格數會自動吸附到 17k+5。參考圖不變、只改 steps 就是品質對照。" }));

  const scopeSel = el("select", { class: "inline-select" },
    el("option", { value: "all", text: "本專案全部鏡頭" }),
    el("option", { value: "scene", text: "本場次（依上方鏡頭頁篩選）" }),
    el("option", { value: "picked", text: "已勾選鏡頭" }),
    el("option", { value: "first", text: "只跑第 1 鏡" }));
  scopeSel.value = mkState.scope || "all";
  scopeSel.addEventListener("change", () => { mkState.scope = scopeSel.value; countHint.textContent = "· " + scopeText(); });

  const trialInp = el("input", { type: "number", min: "0", max: "99", step: "1", class: "tiny-num" });
  trialInp.value = String(mkState.trialCount);
  trialInp.addEventListener("input", () => {
    mkState.trialCount = trialInp.value.trim() === "" ? 1 : Number(trialInp.value);
  });

  const countHint = el("span", { class: "hint", text: "· " + scopeText() });
  const advBtn = el("button", { class: "btn btn-small", type: "button" }, "參數 ▾");
  advBtn.addEventListener("click", () => {
    adv.classList.toggle("hidden");
    advBtn.textContent = adv.classList.contains("hidden") ? "參數 ▾" : "參數 ▴";
  });
  const runBtn = el("button", { class: "btn btn-primary", type: "button", title: "依序送本機 ComfyUI（不並行）；每鏡完成立刻寫回鏡頭卡" },
    "🎬 批次生影片");
  runBtn.addEventListener("click", () => runVideoBatch());

  card.append(el("div", { class: "subcard-head" },
    el("h3", { text: "① 批次生影片", class: "card-h3" }), el("span", { class: "spacer" }), advBtn, runBtn));
  card.append(el("div", { class: "row wrap" },
    el("span", { class: "hint", text: "範圍：" }), scopeSel,
    el("label", { class: "hint inline-check", title: "只先跑前 N 鏡，看結果 OK 再放量（0＝全部照排）" },
      el("span", { text: "先試" }), trialInp, el("span", { text: "鏡" })),
    countHint));
  card.append(adv);
  card.append(el("p", { class: "hint", text: "前置檢查沒過的鏡頭（沒提示詞／沒有可用參考圖與首幀）會被標「前置未過」並不送 GPU，其餘照跑。" }));
  if (mkState.vidJob && ["queued", "running"].includes(mkState.vidJob.status)) {
    card.append(el("p", { class: "hint gen-busy-text", text: `目前有任務在跑（${mkState.vidJob.jobId}）；換頁或 F5 後會自動接回進度。` }));
  }
  return card;
}

function scopeText() {
  const list = state.project?.shots || [];
  const n = list.length;
  const picked = batchPickedIds().length;
  const sc = mkState.scope || "all";
  let base = "";
  if (sc === "all") base = `全部 ${n} 鏡`;
  else if (sc === "scene") base = `本場次（鏡頭頁目前的篩選：${state.shotFilter || "全部"}）`;
  else if (sc === "picked") base = `已勾選 ${picked} 鏡`;
  else base = "第 1 鏡";
  const t = Number(mkState.trialCount) || 0;
  return base + (t > 0 ? ` · 只先跑 ${t} 鏡` : " · 全部照排");
}

function videoScopeIds() {
  const list = (state.project?.shots || []).slice().sort((a, b) => Number(a.no || 0) - Number(b.no || 0));
  const sc = mkState.scope || "all";
  if (sc === "picked") return batchPickedIds();
  if (sc === "scene") {
    const f = state.shotFilter;
    return (f ? list.filter((s) => s.episodeSceneId === f) : list).map((s) => s.id);
  }
  if (sc === "first") return list.slice(0, 1).map((s) => s.id);
  return list.map((s) => s.id);
}

async function runVideoBatch() {
  if (mkState.vidBusy) return;
  const ids = videoScopeIds();
  if (!ids.length) { alert("這個範圍裡沒有鏡頭"); return; }
  const p = mkState.params;
  const preset = {};
  ["width", "height", "frames", "steps", "cfg", "seed"].forEach((k) => {
    if (p[k] !== "" && p[k] !== null && p[k] !== undefined) preset[k] = Number(p[k]);
  });
  const t = Number(mkState.trialCount) || 0;
  const willRun = t > 0 ? Math.min(t, ids.length) : ids.length;
  if (!confirm(`批次生影片：範圍 ${ids.length} 鏡，實際會跑 ${willRun} 鏡。\n`
    + `參數：${preset.width || 720}×${preset.height || 1280} · ${preset.frames || 121} 幀 · steps ${preset.steps || 12}\n`
    + "會依序送本機 ComfyUI（一次一鏡，不並行）。要開始嗎？")) return;
  mkState.vidBusy = true;
  if (state.dirty) { try { await saveNow(); } catch (_) { /* 存檔失敗仍可試 */ } }
  setSaveStatus("pending", "送出批次生影片…");
  try {
    const res = await api(`/api/projects/${state.slug}/generate/videos`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shotIds: ids, preset, trialCount: t }),
    });
    if (!res || !res.ok) {
      mkState.vidJob = res?.job || { status: "failed", error: res?.error || "批次未送出", items: [] };
      mkState.vidJobId = null;
      setSaveStatus("error", "批次生影片未送出");
      renderGenerate();
      alert((res && res.error) || "批次生影片被擋下（後端沒有給原因）");
      return;
    }
    mkState.vidJobId = res.jobId;
    mkState.vidJob = res.job;
    setSaveStatus("pending", `批次已送出（${res.job.runnable} 鏡會跑）`);
    renderGenerate();
    pollVideoBatch();
  } catch (e) {
    setSaveStatus("error", `批次生影片請求失敗：${e.message}`);
    alert(`批次生影片請求失敗：${e.message}`);
  } finally {
    mkState.vidBusy = false;
  }
}

async function pollVideoBatch() {
  clearTimeout(mkState.vidTimer);
  if (!mkState.vidJobId) return;
  try {
    const res = await api(`/api/projects/${state.slug}/generate/videos/${mkState.vidJobId}`);
    mkState.vidJob = res.job;
  } catch (e) {
    if (e.status === 404) {
      setSaveStatus("error", "批次影片進度遺失（伺服器重啟過）：影片仍在 media/clips/");
      mkState.vidJobId = null;
      return;
    }
  }
  paintVideoBatchPanel();
  const running = mkState.vidJob && ["queued", "running"].includes(mkState.vidJob.status);
  if (running) {
    mkState.vidTimer = setTimeout(pollVideoBatch, 2000);
  } else {
    const j = mkState.vidJob || {};
    setSaveStatus(j.failCount ? "error" : "saved",
      `批次影片結束：成功 ${j.okCount || 0}、失敗 ${j.failCount || 0}、前置未過 ${j.blockedCount || 0}`);
    try {
      const fresh = await api(`/api/projects/${state.slug}`);
      state.project = fresh.project;
      state.dirty = false;
      if (state.view === "generate") renderGenerate();
      else if (state.view === "shots") renderShots();
    } catch (_) { /* 拉不到不影響已落地的影片 */ }
    loadAssemblePlan();
    loadOutputs();
  }
}

async function reattachLatestVideoBatch() {
  mkState.vidJob = null; mkState.vidJobId = null;
  clearTimeout(mkState.vidTimer);
  try {
    const res = await api(`/api/projects/${state.slug}/generate/videos/latest`);
    if (res && res.job) {
      mkState.vidJob = res.job;
      mkState.vidJobId = res.job.jobId;
      if (["queued", "running"].includes(res.job.status)) pollVideoBatch();
    }
  } catch (_) { /* 沒任務就沒面板 */ }
}

async function cancelVideoBatch() {
  if (!mkState.vidJobId) return;
  if (!confirm("取消批次？還排在後面的鏡頭會標「已取消」；正在生成的那一鏡會讓它跑完（避免白佔 GPU、留下殘檔）。")) return;
  try {
    const res = await api(`/api/projects/${state.slug}/generate/videos/${mkState.vidJobId}/cancel`, { method: "POST" });
    mkState.vidJob = res.job;
    paintVideoBatchPanel();
  } catch (e) { alert(`取消失敗：${e.message}`); }
}

function paintVideoBatchPanel() {
  const box = document.querySelector("#vid-batch-panel");
  if (!box) return;
  box.textContent = "";
  const j = mkState.vidJob;
  if (!j) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const running = ["queued", "running"].includes(j.status);
  const cls = j.status === "done" ? "ok-text" : running ? "gen-busy-text" : j.status === "partial" ? "warn-text" : "err-text";
  const head = el("div", { class: "subcard-head" },
    el("h3", { class: "card-h3", text: "② 批次生影片進度" }),
    el("span", { class: `hint ${cls}`, text: `${VID_BATCH_STATUS_TEXT[j.status] || j.status} · ${j.done || 0}/${j.total || 0}` }),
    el("span", { class: "spacer" }),
    j.preset ? el("span", { class: "hint mono", text: `${j.preset.width}×${j.preset.height} · ${j.preset.frames}幀 · steps ${j.preset.steps}` }) : null,
    el("span", { class: "hint", text: `成功 ${j.okCount || 0} · 失敗 ${j.failCount || 0} · 前置未過 ${j.blockedCount || 0}` }),
    running ? el("button", { class: "btn btn-small", type: "button", onclick: () => cancelVideoBatch() }, "取消") : null,
    el("button", { class: "btn btn-small", type: "button", onclick: () => { mkState.vidJob = null; mkState.vidJobId = null; clearTimeout(mkState.vidTimer); renderGenerate(); } }, "收起"),
  );
  box.append(head);
  // GPU 自動切換：進度與結果（已自動停止 → 載入模型 → 生成中 → 已重新啟動）
  const g = j.gpu;
  if (g && g.busy) {
    box.append(gpuBusyBanner(g, j.error));
  } else if (g && (g.stoppedLlama || g.restored)) {
    box.append(el("p", { class: "hint gpu-progress" },
      el("span", { class: "gpu-progress-on", text: gpuBarText(g) }),
      running && !g.restored ? el("span", { text: "　→ 載入模型／生成中（跑完會自動啟回本機 LLM）" }) : null));
  } else if (running && j.phase && j.phase.includes("GPU")) {
    box.append(el("p", { class: "hint gpu-progress", text: `⏳ ${j.phase}` }));
  }
  if (j.remaining > 0 && running) {
    box.append(el("p", { class: "hint", text: `目前第 ${j.currentIndex}/${j.runnable} 鏡：${j.current || "—"}`
      + ` · 後面還有 ${j.remaining} 鏡在排（一次只跑一鏡）` + (j.elapsed ? ` · 已 ${Math.round(j.elapsed)} 秒` : "") }));
  }
  if (j.skippedByTrial > 0) {
    box.append(el("p", { class: "hint", text: `「先試 ${j.trialCount} 鏡」模式：另有 ${j.skippedByTrial} 鏡沒排。看過結果 OK 的話，把「先試」改成 0 再按一次即可放量。` }));
  }
  const ul = el("ul", { class: "batch-list" });
  for (const it of (j.items || [])) {
    const c = it.status === "done" ? "ok-text" : it.status === "failed" || it.status === "blocked" ? "err-text"
      : it.status === "running" ? "gen-busy-text" : "hint";
    ul.append(el("li", { class: "batch-item" },
      el("span", { class: `mono ${c}`, text: `${it.shotId}${it.no ? `（第 ${it.no} 鏡）` : ""}` }),
      el("span", { class: "hint", text: ` · ${VID_BATCH_STATUS_TEXT[it.status] || it.status}` }),
      it.phase && it.status === "running" ? el("span", { class: "hint", text: ` · ${it.phase}` }) : null,
      it.elapsed ? el("span", { class: "hint mono", text: ` · ${it.elapsed}s` }) : null,
      it.width ? el("span", { class: "hint mono", text: ` · ${it.width}×${it.height} · ${it.frames}幀 · steps ${it.steps}` }) : null,
      it.path ? el("span", { class: "hint mono path-line", text: ` · ${it.path}` }) : null,
      it.error ? el("div", { class: "hint err-text", text: it.error }) : null,
      (it.warnings || []).length && it.status !== "blocked"
        ? el("div", { class: "hint", text: (it.warnings || []).slice(0, 3).join("；") }) : null,
    ));
  }
  box.append(ul);
  if (running) box.append(el("p", { class: "hint", text: "每 2 秒自動更新；完成一鏡就立刻寫回 project.json（失敗不中斷，會繼續下一鏡）。" }));
}

// ── 合併 ─────────────────────────────────────────────────
async function loadAssemblePlan() {
  if (!state.slug) return;
  try {
    const res = await api(`/api/projects/${state.slug}/assemble/plan?skipMissing=true`);
    mkState.plan = res && res.ok ? res : null;
  } catch (_) { mkState.plan = null; }
  if (state.view === "generate") {
    const c = document.querySelector("#asm-card");
    if (c) c.replaceWith(assembleCard());
  }
}

async function loadOutputs() {
  if (!state.slug) return;
  try {
    const res = await api(`/api/projects/${state.slug}/outputs`);
    mkState.outputs = (res && res.items) || [];
  } catch (_) { mkState.outputs = []; }
  if (state.view === "generate") {
    const c = document.querySelector("#out-card");
    if (c) c.replaceWith(outputsCard());
  }
}

function moveInOrder(shotId, delta) {
  const ids = assembleShots().map((x) => x.shot.id);
  const i = ids.indexOf(shotId);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  mkState.order = ids;
  renderGenerate();
}

function assembleCard() {
  const card = el("div", { class: "card", id: "asm-card" });
  card.append(el("div", { class: "subcard-head" },
    el("h3", { class: "card-h3", text: "③ 合併成長片" }), el("span", { class: "spacer" })));
  const rows = assembleShots();
  const withVid = rows.filter((x) => x.has);
  const missing = rows.filter((x) => !x.has);
  const pickedN = assemblePickedIds().length;
  const plan = mkState.plan;

  // 模式
  const modeSel = el("select", { class: "inline-select" },
    el("option", { value: "auto", text: "自動（能直接串就串）" }),
    el("option", { value: "copy", text: "直接串（不重編碼，快）" }),
    el("option", { value: "reencode", text: "統一重編碼（最保險）" }));
  modeSel.value = mkState.mode || "auto";
  modeSel.addEventListener("change", () => { mkState.mode = modeSel.value; });
  const skipChk = el("input", { type: "checkbox" });
  skipChk.checked = mkState.skipMissing !== false;
  skipChk.addEventListener("change", () => { mkState.skipMissing = skipChk.checked; loadAssemblePlan(); });
  mkState.mode = mkState.mode || "auto";
  if (mkState.skipMissing === undefined) mkState.skipMissing = true;

  const showAllChk = el("input", { type: "checkbox" });
  showAllChk.checked = !!mkState.showAll;
  showAllChk.addEventListener("change", () => { mkState.showAll = showAllChk.checked; renderGenerate(); });

  const asmBtn = el("button", { class: "btn btn-primary", type: "button" }, "🎞 合併成長片");
  asmBtn.disabled = !!mkState.asmBusy || pickedN === 0;
  asmBtn.addEventListener("click", () => runAssemble(modeSel.value, skipChk.checked));

  card.append(el("div", { class: "row wrap" },
    el("span", { class: "hint", text: "模式：" }), modeSel,
    el("label", { class: "hint inline-check" }, skipChk, el("span", { text: "略過沒有影片的鏡頭" })),
    el("label", { class: "hint inline-check" }, showAllChk, el("span", { text: "顯示全部鏡頭" })),
    el("span", { class: "spacer" }), asmBtn));

  // 狀態摘要
  const sum = [];
  sum.push(`要合併 ${pickedN} 段`);
  if (missing.length) sum.push(`缺影片 ${missing.length} 鏡`);
  if (plan && plan.targetDuration != null) sum.push(`各段合計 ${fmtDur(plan.targetDuration)}`);
  if (plan && plan.resolution && plan.resolution.width) sum.push(`解析度 ${plan.resolution.width}×${plan.resolution.height}`);
  card.append(el("p", { class: "hint", text: sum.join(" · ") }));
  if (plan) {
    if (plan.uniform) {
      card.append(el("p", { class: "hint ok-text", text: "✓ 各段參數一致（h264 / yuv420p / 24fps / aac 48k）：可以直接串（-c copy，零轉碼）。" }));
    } else {
      const ul = el("ul", { class: "warn-list" });
      (plan.reasons || []).slice(0, 6).forEach((r) => ul.append(el("li", { text: r })));
      card.append(el("p", { class: "hint warn-text", text: "參數不一致 → 合併會自動走「統一重編碼」（filter_complex），比直接串慢但不會時間戳錯位：" }), ul);
    }
  }
  if (missing.length) {
    const names = missing.map((m) => `${m.shot.id}（第 ${m.shot.no} 鏡）`).join("、");
    card.append(el("p", { class: mkState.skipMissing === false ? "hint err-text" : "hint warn-text",
      text: mkState.skipMissing === false ? `✕ 缺影片會中止合併：${names}。要略過請勾上方的勾選框。` : `缺影片（會被略過）：${names}` }));
  }

  // 清單（可排序、可勾選納入）
  const list = el("div", { class: "asm-list" });
  for (const [i, r] of rows.entries()) {
    const sh = r.shot;
    const included = mkState.picked[sh.id] !== false;
    if (!r.has && !mkState.showAll) {
      list.append(el("div", { class: "asm-row asm-missing" },
        el("span", { class: "mono", text: `${sh.no ?? "?"}` }),
        el("span", { class: "asm-no", text: `${sh.id} · 還沒有影片` })));
      continue;
    }
    const chk = el("input", { type: "checkbox", disabled: r.has ? null : "" });
    chk.checked = r.has && included;
    chk.addEventListener("change", () => { mkState.picked[sh.id] = chk.checked; });
    const up = el("button", { class: "btn btn-tiny", type: "button", title: "上移", onclick: () => moveInOrder(sh.id, -1) }, "↑");
    const dn = el("button", { class: "btn btn-tiny", type: "button", title: "下移", onclick: () => moveInOrder(sh.id, 1) }, "↓");
    const dur = plan && plan.usable ? (plan.usable.find((u) => u.shotId === sh.id)?.probe?.duration) : null;
    const url = r.has ? picUrl(r.path) : "";
    list.append(el("div", { class: "asm-row" },
      el("span", { class: "mono asm-idx", text: String(i + 1) }),
      r.has ? el("video", { class: "asm-thumb", src: url + "#t=0.1", preload: "metadata", muted: "", playsinline: "" })
        : el("span", { class: "asm-thumb asm-empty", text: "無" }),
      el("div", { class: "asm-info" },
        el("div", {}, el("b", { text: `${sh.id}（第 ${sh.no ?? "?"} 鏡）` }),
          el("span", { class: "hint", text: ` · ${fmtDur(dur)}` }),
          el("span", { class: `hint ${r.has ? "ok-text" : "err-text"}`, text: r.has ? " · ✓ 有影片" : " · ✕ 缺影片" })),
        el("div", { class: "hint mono path-line", text: r.path || "—" })),
      el("div", { class: "asm-ops" }, chk, up, dn),
    ));
  }
  card.append(list);
  if (mkState.asmError) {
    card.append(el("div", { class: "vg-error" }, el("div", { class: "vg-error-head" }, el("strong", { text: "合併失敗：" })),
      el("pre", { class: "vg-error-text", text: mkState.asmError })));
  }
  if (mkState.assemble) {
    const a = mkState.assemble;
    card.append(el("div", { class: "asm-result" },
      el("p", { class: "hint ok-text", text: `✓ 合併完成（${a.mode === "copy" ? "直接串" : "統一重編碼"}）：${a.relPath}` }),
      el("p", { class: "hint", text: `${fmtDur(a.duration)}（各段合計 ${fmtDur(a.expectedDuration)}，差 ${a.driftSec >= 0 ? "+" : ""}${a.driftSec}s）· `
        + `${a.width}×${a.height} · ${a.fps ?? "?"}fps · ${a.frames ?? "?"} 幀 · ${fmtBytes(a.bytes)} · 用時 ${a.elapsed} 秒` }),
      el("p", { class: `hint ${a.syncedOk ? "ok-text" : "err-text"}`, text: a.syncedOk ? "✓ 影音長度相符（總時長 ≈ 各段相加）" : "⚠ 總時長與各段相加差距偏大，請檢查" }),
      el("video", { class: "asm-final", controls: "", preload: "metadata", playsinline: "", src: picUrl(a.relPath) + `?t=${encodeURIComponent(a.relPath)}` })));
  }
  return card;
}

async function runAssemble(mode, skipMissing) {
  if (mkState.asmBusy) return;
  if (!assemblePickedIds().length) { alert("沒有可合併的影片：先批次生影片"); return; }
  mkState.asmBusy = true;
  mkState.asmError = "";
  mkState.assemble = null;
  setSaveStatus("pending", "合併中…（同步執行，數十秒內）");
  const card = document.querySelector("#asm-card");
  if (card) card.replaceWith(assembleCard());
  try {
    const res = await api(`/api/projects/${state.slug}/assemble`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, skipMissing }),
    });
    if (!res || !res.ok) {
      mkState.asmError = (res && res.error) || "合併失敗（後端沒有給原因）";
      setSaveStatus("error", "合併失敗");
    } else {
      mkState.assemble = res;
      setSaveStatus("saved", `已合併：${res.relPath}（${fmtDur(res.duration)}）`);
      loadOutputs();
    }
  } catch (e) {
    mkState.asmError = `合併請求失敗：${e.message}`;
    setSaveStatus("error", "合併請求失敗");
  } finally {
    mkState.asmBusy = false;
    const c2 = document.querySelector("#asm-card");
    if (c2) c2.replaceWith(assembleCard());
  }
}

function outputsCard() {
  const card = el("div", { class: "card", id: "out-card" });
  card.append(el("div", { class: "subcard-head" },
    el("h3", { class: "card-h3", text: "④ 已合併的成品" }), el("span", { class: "spacer" }),
    el("span", { class: "hint", text: `output/ · ${mkState.outputs.length} 支` })));
  if (!mkState.outputs.length) {
    card.append(el("p", { class: "hint", text: "還沒有成品。合併成功後會出現在這裡（projects/<slug>/output/final_<時間戳>.mp4）。" }));
  }
  const ul = el("div", { class: "out-list" });
  for (const o of mkState.outputs) {
    ul.append(el("div", { class: "out-row" },
      el("video", { class: "out-video", controls: "", preload: "metadata", playsinline: "", src: o.url }),
      el("div", { class: "asm-info" },
        el("div", { class: "mono", text: o.path }),
        el("div", { class: "hint", text: `${fmtBytes(o.bytes)} · ${o.mtime}` }),
        el("div", { class: "row" },
          el("a", { class: "btn btn-small", href: o.url, download: "", target: "_blank" }, "下載 mp4"),
          el("a", { class: "btn btn-small", href: o.url, target: "_blank" }, "新視窗播放")))));
  }
  card.append(ul);
  return card;
}

// ── 渲染總入口 ───────────────────────────────────────────
function renderViews() {
  const p = state.project;
  $("#current-project-name").textContent = p ? p.meta.name : "請選擇專案";
  $("#current-project-slug").textContent = p ? `（${p.meta.slug} · ${p.meta.aspectRatio} · ${p.meta.fps}fps）` : "";
  $("#project-meta-line") && renderMetaLine();

  renderStory();
  renderCharacters();
  renderScenes();
  renderScript();   // 4 · 劇本（M2b）
  renderShots();    // 5 · 鏡頭（M2b）
  renderGenerate();  // 6 · 生成與合併（M5b 批次生影片 + M6 合併）
  if (!state.slug) { /* 沒開專案 */ }
  renderSettings();
}

function renderAll() {
  renderProjectList();
  renderViews();
}

// ── 新專案彈窗 ───────────────────────────────────────────
function openNewModal() {
  $("#new-name").value = "";
  $("#new-aspect").value = "9:16";
  $("#new-error").classList.add("hidden");
  $("#modal-new").classList.remove("hidden");
  $("#new-name").focus();
}
function closeNewModal() { $("#modal-new").classList.add("hidden"); }

async function createProject() {
  const name = $("#new-name").value.trim();
  const aspect = $("#new-aspect").value;
  const errBox = $("#new-error");
  if (!name) { errBox.textContent = "請輸入專案名稱。"; errBox.classList.remove("hidden"); return; }
  try {
    const res = await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, aspectRatio: aspect }),
    });
    closeNewModal();
    await loadProjectList();
    await openProject(res.slug);
  } catch (e) {
    errBox.textContent = `建立失敗：${e.message}`;
    errBox.classList.remove("hidden");
  }
}

// ══════════════════════════════════════════════════════════
// M3a：與模型對話抽屜（右側，不遮擋主畫面）
// 只打兩支 API：POST chat（只回文字）、POST chat/apply（使用者按「套用」才寫檔）。
// ══════════════════════════════════════════════════════════

const CHAT_TASKS = {
  story: [
    { id: "story_outline", label: "① 擴寫成完整大綱",
      prompt: (m) => `請把目前的故事概念擴寫成完整大綱（約 600–1000 字）。\n目前的概念是：「${m.premise || "（尚未填寫，請自行提出一個合理的概念）"}」\n若已有大綱，請以它為骨幹整理並補強，直接給我完整版本。`,
      applyHint: { fields: ["synopsis"], target: "故事大綱" } },
    { id: "story_chapters", label: "② 依大綱寫成章回",
      prompt: (m) => `依大綱寫成 3 回章回，每回 500–800 字、開頭「第 N 回　標題」。`
        + `每回順序：開場爆點 → 逼到二選一 → 中段爆點 → 結尾鉤子；爆點抓注意力，鉤子留未解問題，不准收束。`
        + `風格：類型 ${(m.style || {}).genre || "—"}／基調 ${(m.style || {}).tone || "—"}／視覺 ${(m.style || {}).visualStyle || "—"}；大綱：${m.synopsis || "（沒有大綱，請先自行擬定一個再寫）"}。`,
      applyHint: { fields: ["chapters"], multi: true, target: "整份章回" } },
    { id: "story_style", label: "③ 調整風格", prompt: () => "請依這個故事給出風格設定建議（類型／基調／視覺風格／負向提示），每項一行。",
      applyHint: { fields: ["style"], multi: true, target: "風格四項" } },
    { id: "custom", label: "調整這份大綱…",
      prompt: () => "請在保持原有設定的前提下，把大綱的節奏改得更緊湊，並讓結尾和開頭的首尾呼應：",
      applyHint: { fields: ["synopsis"], target: "故事大綱" } },
  ],
  character: [
    { id: "character_look", label: "① 生成外型（含 promptEn）",
      prompt: (c) => `請為人物「${c.name || c.id}」（定位：${c.role || "未設定"}）生成外型：五官、髮型、體型、辨識特徵，\n並在第二段寫出可以直接餵生圖的英文 promptEn。`,
      applyHint: { fields: ["appearance.promptEn", "appearance.face", "appearance.hair", "appearance.body", "appearance.skin"],
        multi: true, target: "外型四欄＋promptEn" } },
    { id: "character_outfit", label: "② 生成服裝",
      prompt: (c) => `請為「${c.name || c.id}」設計一套符合世界觀的日常服裝，並附英文 promptEn。`,
      applyHint: { fields: ["outfits"], multi: true, target: "服裝" } },
    { id: "character_voice", label: "③ 建議聲音音色",
      prompt: (c) => `依「${c.name || c.id}」的外型與定位，建議 3 個適合的配音音色，並說明各自的演出方式。`,
      applyHint: { fields: ["voice.desc"], target: "音色描述", variants: true } },
    { id: "custom", label: "幫我改一下這個人物…",
      prompt: () => "請調整這個角色的設定，讓它更有記憶點，但不要推翻已填好的內容：",
      applyHint: { fields: ["appearance.promptEn", "appearance.face", "appearance.hair", "appearance.body", "appearance.skin"],
        multi: true, target: "外型四欄＋promptEn" } },
  ],
  scene: [
    { id: "scene_expand", label: "① 擴寫場景描述",
      prompt: (s) => `請把場景「${s.name || s.id}」擴寫成 150–300 字的可拍描述：時間、光線、氣氛、具體物件、環境音。`,
      applyHint: { fields: ["desc"], target: "場景描述" } },
    { id: "scene_prompt_en", label: "② 生成英文 promptEn",
      prompt: (s) => `請把場景「${s.name || s.id}」寫成一段英文生圖 prompt（comma-separated，一段到底）。`,
      applyHint: { fields: ["promptEn"], target: "英文 promptEn" } },
    { id: "custom", label: "調整這個場景…",
      prompt: () => "請讓這個場景更有記憶點、更能一眼認出，但保持原本的時間與光線設定：",
      applyHint: { fields: ["desc"], target: "場景描述" } },
  ],
  // ── M3b：劇本場次／beat ────────────────────────────────────
  episodeScene: [
    { id: "episode_scene_dialogue", label: "① 寫成完整對白",
      prompt: (es) => `請把場次「${es.title || es.id}」寫成完整對白，只保留這一場現有的角色，`
        + `依現有 beats 的順序與類型改寫，每句一行「角色名：台詞」，情緒／動作用全形括號附在台詞後。`,
      applyHint: { fields: ["beats"], multi: true, target: "整場 beats" } },
    { id: "episode_scene_rewrite", label: "② 改寫得更口語／更有張力",
      prompt: (es) => `請把場次「${es.title || es.id}」改寫得更口語、更有張力：保留原有角色與劇情走向，`
        + `句子縮短、關鍵資訊延後揭曉，輸出整場（含所有 beats）的完整版本。`,
      applyHint: { fields: ["beats"], multi: true, target: "整場 beats" } },
    { id: "episode_scene_action", label: "③ 加動作描述",
      prompt: (es) => `請為場次「${es.title || es.id}」的每句對白補上具體可拍的動作描述，`
        + `每句一行「角色名（動作）：台詞」；沒有台詞的節奏用「（動作：…）」單獨一行。`,
      applyHint: { fields: ["beats"], multi: true, target: "整場 beats" } },
    { id: "custom", label: "調整這一場…",
      prompt: () => "請在保留現有角色與劇情的前提下調整這一場：\n",
      applyHint: { fields: ["beats"], multi: true, target: "整場 beats" } },
  ],
  beat: [
    { id: "beat_rewrite", label: "① 改寫這句台詞",
      prompt: (b) => `請改寫這句台詞（情緒：${b.emotion || "請你判斷最合適的情緒"}）：`
        + `「${b.line || "（空）"}」\n只回那一句台詞本身，不要角色名、不要引號、不要說明。`,
      applyHint: { fields: ["line"], target: "台詞（line）" } },
    { id: "beat_variants", label: "② 三個版本的台詞",
      prompt: (b) => `請給這句台詞三個版本（更口語／更有張力／更含蓄），資訊量與原句相同：「${b.line || "（空）"}」`
        + `\n每版一行「1. 台詞（風格）」，不要角色名或開場白。`,
      applyHint: { fields: ["line"], target: "台詞（line）", variants: true } },
    { id: "custom", label: "調整這一句…",
      prompt: () => "請把這句台詞改成：\n",
      applyHint: { fields: ["line"], target: "台詞（line）" } },
  ],
  // ── M3b：鏡頭（H3 六段式）────────────────────────────────
  shot: [
    { id: "shot_prompt_h3", label: "① 寫 H3 六段式英文 promptEn",
      prompt: (sh) => `請依現有設定寫出第 ${sh.no} 鏡的英文 promptEn（H3 六段式）：主體定義／動作／鏡頭語言／光影／氛圍／音訊，`
        + `每段一行、以「1. Subject:」到「6. Audio:」開頭，只回英文。`,
      applyHint: { fields: ["promptEn"], target: "英文 promptEn" } },
    { id: "shot_prompt_en_from_zh", label: "② 把 promptZh 翻成 promptEn",
      prompt: (sh) => `請把下面的中文提示翻成英文 H3 六段式 promptEn（中文沒寫到的段落用 spec／場景卡／人物卡補齊）：`
        + `\n${sh.promptZh || "（promptZh 目前是空的，請依 spec 與人物、場景設定自行描述這個鏡頭）"}`,
      applyHint: { fields: ["promptEn"], target: "英文 promptEn" } },
    { id: "shot_check_consistency", label: "③ 檢查與人物卡是否矛盾",
      prompt: () => "請檢查這個鏡頭的 promptEn 與人物卡／場景卡設定是否矛盾，逐條列出並給建議改法。",
      applyHint: null, adviceOnly: "僅建議，不回填" },
    { id: "custom", label: "調整這個鏡頭…",
      prompt: () => "請在保留人物與場景設定的前提下調整這個鏡頭：",
      applyHint: { fields: ["promptEn"], target: "英文 promptEn" } },
  ],
};

// M13b：任務 → 回填目標。applyHint 形狀：
//   { fields: [...], multi?: true, target: "顯示用的短名", variants?: true }
//   多欄格子（chapters／style／background／outfits／beats）直接用格子的 path；
//   單欄用 apply-batch 的欄位路徑（synopsis／desc／promptEn／line／voice.desc／appearance.*）。
//   applyHint: null ＝ 這個任務只給建議、不回填（UI 會明說）。
function chatTaskById(kind, id) {
  return (CHAT_TASKS[kind] || []).find((t) => t.id === id) || null;
}

// 找出 applyHint.fields 裡哪些是「多欄格子」、哪些是「單欄」
function splitHintFields(t, hint) {
  const specs = multiApplyFieldsFor(t);
  const multiByPath = {};
  for (const s of specs) multiByPath[s.path] = s;
  const multi = [];
  const plain = [];
  for (const f of (hint && hint.fields) || []) {
    if (multiByPath[f]) multi.push(multiByPath[f]);
    else plain.push(f);
  }
  return { multi, plain };
}

function applyHintLabel(hint) {
  if (!hint) return "";
  return hint.target || (hint.fields || []).join("、");
}

// M13b：applyHint 用到的單欄路徑 → 中文標籤（不依賴目標卡片是否載入）
const HINT_FIELD_LABELS = {
  synopsis: "故事大綱（synopsis）",
  desc: "場景描述（desc）",
  promptEn: "英文提示（promptEn）",
  line: "台詞／動作（line）",
  "voice.desc": "音色描述（voice.desc）",
  "appearance.promptEn": "英文外型 prompt（appearance.promptEn）",
  "appearance.face": "臉型五官（appearance.face）",
  "appearance.hair": "髮型髮色（appearance.hair）",
  "appearance.body": "體型（appearance.body）",
  "appearance.skin": "膚色（appearance.skin）",
};
function hintFieldLabel(list, path) {
  const hit = (list || []).find(([p]) => p === path);
  return (hit && hit[1]) || HINT_FIELD_LABELS[path] || path;
}

// 卡片 → 可套用的欄位（路徑一律用點分；appearance/voice 等物件在缺時後端會自動建）
// M3b：後端有白名單（storage.APPLY_FIELD_WHITELIST），這裡只決定下拉顯示什麼。
function shotDialogueFields(sh) {
  return (sh.dialogue || []).map((d, i) => {
    const who = d.characterId ? charNameById(d.characterId) : `對白 ${i + 1}`;
    const txt = (d.line || "").trim();
    const short = txt.length > 10 ? txt.slice(0, 10) + "…" : txt;
    return [`dialogue.${i}.line`, `本鏡台詞 ${i + 1}（${who}：${short || "空"}）`];
  });
}

// 章回：每回的標題／正文各一格（章回正文存在 content，body 是同義別名）
function chapterFields(story) {
  return ((story && story.chapters) || []).map((ch, i) => {
    const head = `${ch.id || `第 ${i + 1} 回`}${ch.title ? " " + ch.title : ""}`;
    return [
      [`chapters.${i}.title`, `章回標題 · ${head}`],
      [`chapters.${i}.body`, `章回正文 · ${head}`],
    ];
  }).flat();
}

function outfitFields(ch) {
  return ((ch && ch.outfits) || []).map((o, i) => [
    [`outfits.${i}.desc`, `第 ${i + 1} 套服裝描述（${o.name || o.id || "未命名"}）`],
    [`outfits.${i}.promptEn`, `第 ${i + 1} 套服裝 promptEn（${o.name || o.id || "未命名"}）`],
  ]).flat();
}

const APPLY_FIELDS = {
  story: [
    ["synopsis", "故事大綱（synopsis）"],
    ["premise", "故事概念（premise）"],
    ["style.genre", "類型（genre）"],
    ["style.tone", "基調（tone）"],
    ["style.visualStyle", "視覺風格（visualStyle）"],
    ["style.negative", "負向提示（negative）"],
  ],
  character: [
    ["appearance.promptEn", "英文外型 prompt（appearance.promptEn）"],
    ["appearance.face", "臉型五官（appearance.face）"],
    ["appearance.hair", "髮型髮色（appearance.hair）"],
    ["appearance.body", "體型（appearance.body）"],
    ["appearance.skin", "膚色（appearance.skin）"],
    ["outfits.0.promptEn", "第 1 套服裝 promptEn（沒有服裝時會自動建）"],
    ["voice.desc", "音色描述（voice.desc）"],
  ],
  scene: [
    ["desc", "場景描述（desc）"],
    ["promptEn", "英文場景 prompt（promptEn）"],
    ["lighting", "光線（lighting）"],
    ["mood", "氛圍（mood）"],
    ["ambience.note", "環境音說明（ambience.note）"],
  ],
  // ── M3b：劇本場次／beat／鏡頭 ──────────────────────────────
  episodeScene: [
    ["summary", "場次摘要（summary）"],
    ["title", "場次標題（title）"],
  ],
  beat: [
    ["line", "台詞／動作（line）"],
    ["emotion", "情緒（emotion）"],
    ["notes", "備註（notes）"],
  ],
  shot: [
    ["promptEn", "英文提示（promptEn）"],
    ["promptZh", "中文提示（promptZh）"],
    ["spec.shotType", "鏡頭類型（spec.shotType，只能是 close_up／medium／wide／over_shoulder／pov）"],
    ["spec.angle", "角度（spec.angle）"],
    ["spec.cameraMove", "運鏡（spec.cameraMove）"],
    ["spec.composition", "構圖（spec.composition）"],
    ["spec.lighting", "光線（spec.lighting）"],
  ],
};

// ── M13a：多欄套用 ─────────────────────────────────────────
// 每個對象的「結構格子」：整份陣列（一次寫入）＋ 陣列內的單項欄位。
// multi:true 表示值是多行結構（textarea 大一點）；parse 表示「解析成多欄」預設勾這個。
const MULTI_APPLY_FIELDS = {
  story: () => [
    {
      path: "chapters", label: "整份章回（回覆是「第 N 回　標題」一段一段時用這個）",
      kind: "chapters", multi: true, mode: "replace", parse: true,
      hint: "每回會自動編 ch01、ch02…；「第一章」「第 1 回」「第2章」都認得。",
    },
    {
      path: "style", label: "風格四項（類型／基調／視覺風格／負向，一起寫）",
      kind: "style", multi: true, mode: "replace", parse: true,
      hint: "一行一項：「類型：奇幻冒險」「基調：壓抑」「視覺風格：寫實」「負向提示：卡通」。",
    },
  ],
  character: () => [
    {
      path: "outfits", label: "整份服裝（回覆是一套套服裝時用這個）",
      kind: "outfits", multi: true, mode: "replace", parse: true,
      hint: "會取代現有服裝；一行一項「名稱：…」「描述：…」「英文 prompt：…」。",
    },
  ],
  episodeScene: () => [
    {
      path: "beats", label: "整場台詞／動作（beats）",
      kind: "beats", multi: true, mode: "replace", parse: true,
      hint: "「角色名：台詞」→ 一句一個 beat（角色名自動對到人物 id）；「（動作：…）」→ 動作 beat。",
    },
  ],
  shot: () => [
    {
      path: "dialogue", label: "本鏡對白（dialogue，整份）",
      kind: "dialogue", multi: true, mode: "replace", parse: true,
      hint: "「角色名：台詞」一行一句。",
    },
  ],
  beat: () => [],
  scene: () => [],
};

// 某個 target 實際可套用的欄位（shot 會動態加 dialogue.N.line、章回與服裝逐項）
function applyFieldsFor(t) {
  const base = (APPLY_FIELDS[t.kind] || []).slice();
  const obj = chatTargetData(t);
  if (t.kind === "shot" && obj) base.push(...shotDialogueFields(obj));
  if (t.kind === "story" && obj) base.unshift(...chapterFields(obj));
  if (t.kind === "character" && obj) base.push(...outfitFields(obj));
  return base;
}

function multiApplyFieldsFor(t) {
  const fn = MULTI_APPLY_FIELDS[t && t.kind];
  if (!fn) return [];
  const obj = chatTargetData(t);
  return fn(t, obj) || [];
}

const chatState = {
  open: false,
  target: null,          // {kind, id, title}
  messages: [],          // [{role, content, meta?}]
  pending: false,
  llm: null,             // /api/llm/status 的結果
  llmChecking: false,
};

function chatTargetData(t) {
  const p = state.project;
  if (!p || !t) return null;
  if (t.kind === "story") return p.story;
  if (t.kind === "character") return (p.characters || []).find((x) => x.id === t.id) || null;
  if (t.kind === "scene") return (p.scenes || []).find((x) => x.id === t.id) || null;
  if (t.kind === "episodeScene") {
    const f = findEpisodeScene(t.id);
    return f ? f.es : null;
  }
  if (t.kind === "beat") {
    // beat id 只在場次內唯一 → 用 parentId（所屬場次）+ id 找
    const f = t.parentId ? findEpisodeScene(t.parentId) : null;
    if (f) return (f.es.beats || []).find((b) => b.id === t.id) || null;
    for (const ep of episodes()) {
      for (const es of ep.scenes || []) {
        const b = (es.beats || []).find((x) => x.id === t.id);
        if (b) return b;
      }
    }
    return null;
  }
  if (t.kind === "shot") return (p.shots || []).find((x) => x.id === t.id) || null;
  return null;
}

function beatLocation(t) {
  // beat 的「所屬場次／集」標示
  const f = t.parentId ? findEpisodeScene(t.parentId) : null;
  if (!f) return "";
  return `${esLabel(f.es)} · ${f.ep.id} ${f.ep.title || ""}`.trim();
}

function chatTargetTitle(t) {
  const obj = chatTargetData(t);
  if (!obj) return `${t.id || ""}（已不存在）`;
  if (t.kind === "story") return `${p_name()} · 故事`;
  if (t.kind === "episodeScene") return `場次 ${esLabel(obj)}`;
  if (t.kind === "beat") {
    const who = obj.characterId ? charNameById(obj.characterId) : "（未指定）";
    const line = (obj.line || "").trim();
    const short = line.length > 16 ? line.slice(0, 16) + "…" : line;
    return `${t.id} · ${who}：${short || "（空台詞）"}`;
  }
  if (t.kind === "shot") return `第 ${obj.no} 鏡（${obj.id}）`;
  return `${obj.name || obj.id}（${obj.id}）`;
}

const CHAT_KIND_LABEL = {
  story: "故事", character: "人物卡", scene: "場景卡",
  episodeScene: "劇本場次", beat: "台詞／動作 beat", shot: "鏡頭",
};
function p_name() { return state.project ? state.project.meta.name : ""; }

// 「▾ 與模型對話」按鈕（卡片標題列右上）；target 可帶 {kind,id,parentId}
function chatButton(kind, id, extraOpts) {
  const opts = extraOpts || {};
  return el("button", {
    class: "btn btn-small btn-chat", type: "button", title: "打開模型對話抽屜",
    onclick: () => openChat({ kind, id: id || null, parentId: opts.parentId || null }),
  }, "✦ 與模型對話");
}

// ── 抽屜外殼 ─────────────────────────────────────────────
function ensureDrawer() {
  let d = document.getElementById("chat-drawer");
  if (d) return d;
  d = el("aside", { id: "chat-drawer", class: "chat-drawer hidden" });
  const head = el("div", { class: "chat-head" },
    el("div", { class: "chat-title" },
      el("strong", { id: "chat-target-title", text: "與模型對話" }),
      el("span", { id: "chat-target-kind", class: "hint", text: "" }),
    ),
    el("button", { class: "chat-close", type: "button", title: "關閉（Esc）", onclick: closeChat }, "✕"),
  );
  const status = el("div", { id: "chat-status", class: "chat-status" },
    el("span", { class: "dot" }), el("span", { class: "txt", text: "檢查模型服務…" }),
  );
  const banner = el("div", { id: "chat-banner", class: "chat-banner hidden" });
  const tasks = el("div", { id: "chat-tasks", class: "chat-tasks" });
  const log = el("div", { id: "chat-log", class: "chat-log" });
  const inputWrap = el("div", { class: "chat-input" });
  const ta = el("textarea", { id: "chat-input", rows: "4", placeholder: "輸入要對模型說的話…（Ctrl+Enter 送出）" });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); }
  });
  inputWrap.append(ta);
  inputWrap.append(el("div", { class: "row wrap chat-actions" },
    el("button", { id: "chat-send", class: "btn btn-small btn-primary", type: "button", onclick: sendChat }, "送出"),
    el("button", { class: "btn btn-small", type: "button", onclick: clearChat }, "清空對話"),
    el("span", { class: "spacer" }),
    el("span", { id: "chat-model-name", class: "hint" }),
  ));
  d.append(head, status, banner, tasks, log, inputWrap);
  document.body.append(d);
  return d;
}

function openChat(target) {
  chatState.target = { kind: target.kind, id: target.id || null, parentId: target.parentId || null };
  chatState.messages = [];
  chatState.open = true;
  ensureDrawer();
  document.getElementById("chat-drawer").classList.remove("hidden");
  document.getElementById("content").classList.add("drawer-open");
  document.getElementById("chat-target-title").textContent = chatTargetTitle(chatState.target);
  const where = chatState.target.kind === "beat" ? beatLocation(chatState.target) : "";
  document.getElementById("chat-target-kind").textContent =
    `${CHAT_KIND_LABEL[target.kind] || target.kind}${where ? " · " + where : ""}`;
  renderChatTasks();
  renderChatLog();
  checkLLM();
  document.getElementById("chat-input").focus();
}

function closeChat() {
  chatState.open = false;
  const d = document.getElementById("chat-drawer");
  if (d) d.classList.add("hidden");
  const c = document.getElementById("content");
  if (c) c.classList.remove("drawer-open");
}

function clearChat() {
  chatState.messages = [];
  renderChatLog();
}

// ── 模型狀態 ─────────────────────────────────────────────
async function checkLLM(force) {
  const box = document.getElementById("chat-status");
  const banner = document.getElementById("chat-banner");
  const cfg = state.project && state.project.settings && state.project.settings.llm;
  const same = chatState.llm && cfg && chatState.llm.baseUrl === (cfg.baseUrl || "");
  if (chatState.llmChecking) return;
  chatState.llmChecking = true;
  if (box) box.className = "chat-status checking";
  if (box) box.querySelector(".txt").textContent = "檢查模型服務…";
  try {
    const q = new URLSearchParams();
    if (state.slug) q.set("slug", state.slug);
    const res = await api(`/api/llm/status?${q.toString()}`);
    chatState.llm = res;
  } catch (e) {
    chatState.llm = { ok: false, baseUrl: "", model: "", models: [], error: `檢查模型狀態失敗：${e.message}` };
  } finally {
    chatState.llmChecking = false;
  }
  const s = chatState.llm;
  if (box) {
    box.className = "chat-status " + (s.ok ? "ok" : "bad");
    const prov = s.providerLabel || s.provider || "";
    box.querySelector(".txt").textContent = s.ok
      ? `${prov} · 已連線 · ${s.model || "（未指定）"}${(s.models || []).length > 1 ? `（共 ${s.models.length} 個可用）` : ""}`
      : `${prov || "模型"} 未連線 · ${s.baseUrl || ""}`;
  }
  const mn = document.getElementById("chat-model-name");
  if (mn) mn.textContent = s.ok ? (s.model || "") : "";
  if (banner) {
    banner.textContent = "";
    if (s.ok) {
      banner.classList.add("hidden");
    } else {
      const online = s.provider === "online" || s.provider === "custom";
      banner.classList.remove("hidden");
      banner.append(
        el("strong", { text: `⚠ 連不上${online ? "線上" : "本機"}模型服務\n` }),
        el("span", { text: (s.error || "未知錯誤") + "\n" }),
        el("span", { text: `目前位址：${s.baseUrl || "（未設定）"}${s.hasApiKey ? " · API key：已設定（" + (s.apiKeyMasked || "****") + "）" : " · API key：未設定"}\n` }),
        el("div", { class: "chat-banner-steps",
          text: online
            ? "1. 到「設定 / 原始資料 → 模型服務」確認供應商＝online（DeepSeek）\n"
              + "2. 確認 hermes 的 .env 有 DEEPSEEK_API_KEY，或設環境變數 LLM_API_KEY\n"
              + "3. 確認 model 在帳號清單內（按「重新檢查」會列出可用模型）\n"
              + "4. 回來按下面「重新檢查」"
            : "1. 開啟本機 Strata（或其他 OpenAI 相容服務），載入任意模型\n"
              + "2. 確認它 listen 在 127.0.0.1:8080（`curl http://127.0.0.1:8080/v1/models` 要有回應）\n"
              + "3. 不想跑本機 → 到「設定」把供應商改成 online（用線上 DeepSeek）\n"
              + "4. 回來按下面「重新檢查」" }),
        el("button", { class: "btn btn-small", type: "button", onclick: () => checkLLM(true) }, "重新檢查"),
      );
    }
  }
  void same;
  // 沒有專案時：橫幅只提示
}

// ── 預設任務 ─────────────────────────────────────────────
function renderChatTasks() {
  const box = document.getElementById("chat-tasks");
  if (!box) return;
  box.textContent = "";
  const t = chatState.target;
  const data = chatTargetData(t) || {};
  const list = CHAT_TASKS[t.kind] || [];
  box.append(el("span", { class: "hint", text: "預設任務：" }));
  for (const task of list) {
    const to = task.applyHint ? `→ 回填：${applyHintLabel(task.applyHint)}` : (task.adviceOnly ? `→ ${task.adviceOnly}` : "");
    const btn = el("button", {
      class: "chip" + (task.applyHint ? " chip-hasapply" : "") + (task.applyHint === null ? " chip-advice" : ""),
      type: "button",
      title: task.applyHint
        ? `把這個任務的提示詞填進輸入框（可先改再送）；送完按「⇣ 回填到 ${applyHintLabel(task.applyHint)}」一鍵寫回`
        : (task.adviceOnly || "把這個任務的提示詞填進輸入框（可先改再送）"),
      onclick: () => {
        const ta = document.getElementById("chat-input");
        if (!ta) return;
        box.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === btn));
        ta.value = task.prompt(data);
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
      },
    }, task.label, to ? el("span", { class: "chip-to", text: to }) : null);
    btn.dataset.task = task.id;
    box.append(btn);
  }
  // M13a／M13b：把「多欄套用」的入口寫在任務列，使用者不用先找到那顆小鈕
  const tweakables = multiApplyFieldsFor(t);
  if (tweakables.length) {
    const lastAssistant = chatState.messages.map((m, i) => [m, i]).filter(([m]) => m.role === "assistant" && !(m.meta && m.meta.error)).pop();
    const lastTask = lastAssistant && lastAssistant[0].meta && lastAssistant[0].meta.task
      ? chatTaskById(t.kind, lastAssistant[0].meta.task) : null;
    const goal = lastTask && lastTask.applyHint ? applyHintLabel(lastTask.applyHint) : "";
    box.append(el("button", {
      class: "chip chip-multi", type: "button",
      title: goal ? `把這段回覆一鍵回填到「${goal}」（會先開面板讓你確認）`
        : "把最近一段回覆解析成多個欄位（章回／台詞／風格／身世…），先預覽再套用",
      onclick: () => {
        const idx = lastAssistant ? lastAssistant[1] : -1;
        if (idx < 0) { setSaveStatus("error", "還沒有模型回覆可以解析（先送一個任務）"); return; }
        const bubble = bubbleElByIndex(chatState.messages.slice(0, idx + 1).filter((m) => m.role === "assistant").length - 1);
        if (!bubble) { setSaveStatus("error", "還找不到回覆框，請先送一次訊息"); return; }
        const host = bubble.querySelector(".ma-host");
        if (!host) return;
        if (!host.childElementCount) renderMultiPanel(host, idx);
        host.scrollIntoView({ block: "nearest", behavior: "smooth" });
      },
    }, goal ? `⇣ 回填到 ${goal}` : "⇣ 解析成多欄適用欄位"));
  }
  // 章回數量的快速選項（只有故事卡有）
  if (t.kind === "story" && state.project && (state.project.story.chapters || []).length) {
    box.append(el("span", { class: "hint", text: "（直接按會覆蓋輸入框內容）" }));
  }
}

function currentTaskId() {
  const active = document.querySelector("#chat-tasks .chip.active");
  return active ? active.dataset.task || null : null;
}

// ── 對話氣泡 ─────────────────────────────────────────────
function renderChatLog() {
  const log = document.getElementById("chat-log");
  if (!log) return;
  log.textContent = "";
  if (!chatState.messages.length) {
    log.append(el("p", { class: "hint chat-empty", text: "選一個預設任務，或直接輸入你要模型做什麼。模型只會回文字，不會自動改你的資料。" }));
    return;
  }
  chatState.messages.forEach((m, idx) => {
    if (m.role === "user") {
      log.append(el("div", { class: "bubble user" }, el("div", { class: "bubble-text", text: m.content })));
      return;
    }
    const wrap = el("div", { class: "bubble assistant" });
    wrap.append(el("div", { class: "bubble-text", text: m.content }));
    const meta = el("div", { class: "bubble-meta" },
      el("span", { class: "hint", text: m.meta ? `${m.meta.model || ""}${m.meta.tokens ? ` · ${m.meta.tokens} tokens` : ""}` : "" }),
    );
    const acts = el("div", { class: "bubble-actions" });
    const multi = el("div", { class: "ma-host" });
    // M13b：回覆是回哪個任務的 → 按鈕直接寫「⇣ 回填到 <目標>」
    const replyTask = m.meta && m.meta.task ? chatTaskById(chatState.target.kind, m.meta.task) : null;
    const goal = replyTask && replyTask.applyHint ? applyHintLabel(replyTask.applyHint) : "";
    const isError = !!(m.meta && m.meta.error);
    const adviceOnly = !!(replyTask && replyTask.applyHint === null);
    const hintOk = !!(replyTask && replyTask.applyHint);
    if (adviceOnly) {
      acts.append(el("span", { class: "hint chip-advice", text: "僅建議，不回填（照建議自己改欄位）" }));
    } else if (hintOk) {
      acts.append(el("button", {
        class: "btn btn-small btn-primary ma-open", type: "button",
        title: `把這段回覆解析後一鍵寫回「${goal}」；面板只會展開這次任務的目標格子`,
        onclick: () => openReplyBackfill(multi, idx),
      }, `⇣ 回填到 ${goal}`));
      if (replyTask.applyHint.variants) {
        const pick = replyVariants(m.content, replyTask);
        if (pick.length >= 2) acts.append(variantButtons(multi, idx, replyTask, pick));
      }
    } else if (!isError) {
      acts.append(el("button", {
        class: "btn btn-small", type: "button",
        title: "把這段回覆解析成多個欄位（章回／台詞／風格／身世…），先預覽再套用",
        onclick: () => openReplyBackfill(multi, idx),
      }, "⇣ 解析成多欄"));
    }
    if (!isError) {
      acts.append(el("button", {
        class: "btn btn-small", type: "button",
        onclick: async (ev) => {
          try {
            await navigator.clipboard.writeText(m.content);
            ev.target.textContent = "已複製";
            setTimeout(() => { ev.target.textContent = "複製"; }, 1200);
          } catch (_) {
            const ta = document.getElementById("chat-input");
            if (ta) { ta.value = m.content; ta.focus(); }
            setSaveStatus("pending", "無法存取剪貼簿，已把內容放進輸入框");
          }
        },
      }, "複製"));
      acts.append(applyControls(idx, m));
    }
    wrap.append(meta, acts, multi);
    log.append(wrap);
  });
  log.scrollTop = log.scrollHeight;
}

// M13b：氣泡上的「⇣ 回填到 …」→ 開面板（只展開這次任務的目標格子）
function openReplyBackfill(multi, idx) {
  try {
    // 選取文字要在 onclick 最前面取一次：按鈕按下的那一下瀏覽器會收掉選取範圍
    const selNow = replySelectionFor(bubbleElByIndex(idx)) || cachedSelectionFor(idx);
    renderMultiPanel(multi, idx, selNow);
  } catch (e) {
    multi.textContent = "";
    multi.append(el("div", { class: "error", text: `回填面板開不起來：${e.message}` }));
  }
  if (!multi.childElementCount) return;
  multi.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ── M13b：多版本候選（三個版本台詞／三個音色建議）────────────────
// 「1. …／2. …／3. …」或三行各自獨立的句子 → 回 [{no, text}]
function replyVariants(text, task) {
  const s = stripChatWrapper(text);
  const lines = s.split("\n").map((l) => l.trim()).filter(Boolean);
  const out = [];
  const numbered = lines.filter((l) => /^\s*(?:[-*•]\s*)?(?:\d+[.)、]|[（(]\d+[）)]|[①②③④⑤⑥]|版本\s*\d)\s*/.test(l));
  const pool = numbered.length >= 2 ? numbered : (task && task.id === "character_voice" ? voiceCandidates(lines) : lines);
  for (const l of pool) {
    let v = l.replace(/^\s*(?:[-*•]\s*)?(?:\d+[.)、]|[（(]\d+[）)]|[①②③④⑤⑥])\s*/, "").trim();
    if (!v || v.length > 400) continue;
    v = v.replace(/\*{2,}/g, "");
    // 「台詞（更口語）」→ 風格名留著當標籤，但值只取台詞本體
    out.push({ style: (v.match(/[（(]([^）)]{1,10})[）)]\s*$/) || [])[1] || "", text: v });
  }
  return out.slice(0, 3).length >= 2 ? out.slice(0, 3) : [];
}

function voiceCandidates(lines) {
  // 「1. 低沉沙啞 · 敘事旁白型：演出方式…」或「音色一：…」或每行一個候選
  const out = [];
  for (const l of lines) {
    if (/^(以下是|建議|可以|這三個|綜合)/.test(l)) continue;
    out.push(l);
  }
  return out.slice(0, 3);
}

function variantValueFor(task, v) {
  if (task.id === "character_voice") {
    const style = v.style ? `${v.style}：` : "";
    return `${style}${v.text}`.trim();
  }
  // 台詞：去掉結尾的風格括號與引號、角色名
  let t = v.text.replace(/^[^：:]{1,8}\s*[：:]\s*/, "").replace(/[「」『』"]/g, "");
  t = t.replace(/[（(]([^）)]{1,10})[）)]\s*$/, "").trim();
  return t;
}

function variantButtons(multi, idx, task, pick) {
  const box = el("div", { class: "ma-variants" },
    el("span", { class: "hint", text: `多版本（${pick.length} 個）→ 直接寫進「${applyHintLabel(task.applyHint)}」：` }));
  pick.forEach((v, i) => {
    const b = el("button", {
      class: "btn btn-small btn-variant", type: "button",
      title: `把第 ${i + 1} 版直接寫進「${applyHintLabel(task.applyHint)}」（不蓋掉其他欄位）\n${v.text.slice(0, 120)}`,
      onclick: () => applyVariant(b, idx, task, i + 1, variantValueFor(task, v)),
    }, `用第 ${i + 1} 版`);
    if (v.style) b.append(el("span", { class: "ma-variant-tag", text: v.style }));
    box.append(b);
  });
  multi.before(box);
  return box;
}

async function applyVariant(btn, idx, task, no, value) {
  if (!state.slug || !value) return;
  const path = (task.applyHint && task.applyHint.fields && task.applyHint.fields[0]) || "line";
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "寫入中…";
  try {
    const res = await api(`/api/projects/${state.slug}/chat/apply-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: chatState.target, items: [{ path, value, mode: "replace" }] }),
    });
    syncTargetFromServer(res.targetAfter);
    const warns = (res.warnings || []).length ? `（提醒：${res.warnings.join("；")}）` : "";
    setSaveStatus(warns ? "pending" : "saved", `已寫入第 ${no} 版到 ${res.label} 的 ${path} · ${clockText()}${warns}`);
    btn.textContent = `已用第 ${no} 版 ✓`;
    const box = btn.parentElement;
    if (box) box.querySelectorAll(".btn-variant").forEach((b) => { if (b !== btn) b.disabled = true; });
  } catch (e) {
    btn.textContent = old;
    btn.disabled = false;
    setSaveStatus("error", `寫入失敗：${e.message}`);
  }
}

// 「套用到…」：下拉列出這張卡的目標欄位 + replace/append + 套用鈕
function applyControls(idx, msg) {
  const t = chatState.target;
  const fields = applyFieldsFor(t);
  const wrap = el("span", { class: "apply-row" });
  if (msg.meta && msg.meta.error) return wrap;   // 錯誤訊息不給套用
  if (!fields.length) {
    wrap.append(el("span", { class: "hint", text: "這個對象沒有可套用的欄位" }));
    return wrap;
  }
  const sel = el("select", { class: "inline-select apply-field" },
    ...fields.map(([path, label]) => el("option", { value: path, text: label })));
  const mode = el("select", { class: "inline-select apply-mode" },
    el("option", { value: "replace", text: "取代" }),
    el("option", { value: "append", text: "附加" }),
  );
  const btn = el("button", { class: "btn btn-small btn-primary", type: "button" }, "套用到…");
  btn.addEventListener("click", () => applyChatValue(idx, msg, sel.value, mode.value, btn));
  wrap.append(el("span", { class: "hint", text: "套用到：" }), sel, mode, btn);
  return wrap;
}

async function applyChatValue(idx, msg, path, mode, btn) {
  if (!state.slug) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "寫入中…";
  try {
    const res = await api(`/api/projects/${state.slug}/chat/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target: chatState.target,
        path,
        value: msg.content,
        mode,
      }),
    });
    // 後端已寫檔；把回傳的整張卡覆蓋回記憶體，畫面即時更新（不進 debounce，避免再次整份覆寫）
    syncTargetFromServer(res.targetAfter);
    const fieldLabel = path;
    setSaveStatus("saved", `已套用到 ${res.label} 的 ${fieldLabel} · ${clockText()}`);
    btn.textContent = "已套用 ✓";
    setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 1600);
  } catch (e) {
    btn.textContent = old;
    btn.disabled = false;
    setSaveStatus("error", `套用失敗：${e.message}`);
  }
}

function syncTargetFromServer(after) {
  if (!after) return;
  const t = chatState.target;
  const p = state.project;
  if (!p) return;
  if (t.kind === "story") {
    p.story = after;
  } else if (t.kind === "shot") {
    // 後端回整支鏡頭
    const i = (p.shots || []).findIndex((x) => x.id === after.id);
    if (i >= 0) p.shots[i] = after;
  } else if (t.kind === "episodeScene") {
    const f = findEpisodeScene(after.id) || findEpisodeScene(t.id);
    if (f) {
      const i = (f.ep.scenes || []).findIndex((x) => x.id === after.id);
      if (i >= 0) f.ep.scenes[i] = after;
    }
  } else if (t.kind === "beat") {
    const f = findEpisodeScene(t.parentId);
    if (f) {
      const i = (f.es.beats || []).findIndex((x) => x.id === (after.id || t.id));
      if (i >= 0) f.es.beats[i] = after;
    }
  } else {
    const pool = t.kind === "character" ? p.characters : p.scenes;
    const i = (pool || []).findIndex((x) => x.id === after.id);
    if (i >= 0) pool[i] = after;
  }
  clearTimeout(state.saveTimer);
  state.dirty = false;
  // 抽屜只在 openChat 時建立，這裡重畫只會動 #view-* 區塊，不會影響抽屜；
  // 套用後卡片上的欄位（例如 promptEn）立即反映新值。
  renderViews();
  const ta = document.getElementById("chat-input");
  if (ta && chatState.open) ta.focus();
}

// ══════════════════════════════════════════════════════════
// M13a：「解析成多欄」＋「多欄套用」
// 為什麼：模型一次回一份完整結構（三回正文、整場台詞、身世四欄、風格四項），
// 舊的回填只能一次貼一格 → 使用者要自己複製四次，回填形同虛設。
// 這裡把一段回覆解析成「一格一格」的結構，先顯示成可編輯清單，確認後一次打
// POST /chat/apply-batch（後端一次讀檔、驗完整批、原子寫檔一次）。
// 解析規則只放在前端（後端只負責驗證與寫入）。純函式、不碰 DOM，方便直接測。
// ══════════════════════════════════════════════════════════

const CN_DIGITS = { 零: 0, 一: 1, 二: 2, 兩: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
function cnNumber(s) {
  const t = String(s || "").trim();
  if (/^\d+$/.test(t)) return parseInt(t, 10);
  if (!t) return NaN;
  if (t === "十") return 10;
  const m = t.match(/^([零一二兩三四五六七八九])?十([零一二兩三四五六七八九])?$/);
  if (m) return (m[1] ? CN_DIGITS[m[1]] : 1) * 10 + (m[2] ? CN_DIGITS[m[2]] : 0);
  if (t.length === 1 && t in CN_DIGITS) return CN_DIGITS[t];
  return NaN;
}

// 模型很愛包 ``` 或說「以下是…」→ 去掉外框，只留正文
function stripChatWrapper(text) {
  let s = String(text || "").replace(/\r\n?/g, "\n").trim();
  const fence = s.match(/```[a-zA-Z]*\n?([\s\S]*?)```/);
  if (fence) s = fence[1].trim();
  return s;
}

function looksLikeJson(text) {
  const s = stripChatWrapper(text).trim();
  if (!/^[[{]/.test(s)) return false;
  try { JSON.parse(s); return true; } catch (_) { return false; }
}

// 「標籤：值」行（也認「**標籤**：值」「- 標籤: 值」「1. 標籤：值」）
function parseLabeled(text, labels) {
  const out = {};
  const re = /^\s*(?:[-*•]\s*|\d+[.)、]\s*)?\*{0,2}([^：:*]{2,14}?)\*{0,2}\s*[：:]\s*(.*)$/;
  for (const line of stripChatWrapper(text).split("\n")) {
    const m = line.match(re);
    if (!m) continue;
    // 先比對完全相同的標籤，再退而求其次找「含標籤」的（「視覺風格建議」→ 視覺風格）
    const exact = labels.filter((l) => m[1] === l);
    const key = exact[0] || labels.filter((l) => m[1].includes(l)).sort((a, b) => b.length - a.length)[0];
    if (!key) continue;
    const rest = m[2].trim();
    out[key] = out[key] ? `${out[key]}\n${rest}` : rest;
  }
  return out;
}

// 「第 N 回　標題」開頭分回；沒標號但有明顯多段時整段算一回
function parseChapters(text) {
  const s = stripChatWrapper(text);
  const lines = s.split("\n");
  const headRe = /^\s*(?:#{1,4}\s*)?\*{0,2}第\s*([0-9〇零一二兩三四五六七八九十百]+)\s*(回|章|話|篇)\*{0,2}(?=\s|$|[：:.、\-—])\s*[:：.、\-—]?\s*(.*)$/;
  // 行中間才出現的「第一回」也算分回（模型偶爾把前一段和下一回寫在同一行）
  const headReLoose = /^\s*(?:.*?[：:]\s*)?第\s*([0-9〇零一二兩三四五六七八九十百]+)\s*(回|章|話|篇)(?=\s|$|[：:.、\-—])\s*[:：.、\-—]?\s*(.*)$/;
  const items = [];
  let cur = null;
  for (const line of lines) {
    const m = line.match(headRe) || line.match(headReLoose);   // 句中的「第二段：第一回　雨夜」也認 section
    if (m) {
      if (cur) items.push(cur);
      cur = { title: `${m[3] || ""}`.replace(/\*{2,}/g, "").trim(), body: [] };
      if (!cur.title) cur.title = `${m[2]}（未命名）`;
      continue;
    }
    if (!cur) {
      if (!line.trim()) continue;
      cur = { title: "（未命名章回）", body: [] };
    }
    cur.body.push(line.replace(/\*{2,}/g, ""));
  }
  if (cur) items.push(cur);
  if (items.length === 1 && !items[0].title) items[0].title = "（未命名章回）";
  return items.map((it, i) => {
    let body = it.body.join("\n").trim();
    // 標題若在下一行（「第一章」單獨一行、標題在第二行）
    if ((!it.title || /^(回|章|話|篇)（未命名）$/.test(it.title)) && body) {
      const first = body.split("\n")[0].trim();
      if (first && first.length <= 30) {
        it.title = first;
        body = body.split("\n").slice(1).join("\n").trim();
      }
    }
    return { id: `ch${String(i + 1).padStart(2, "0")}`, title: it.title, body };
  }).filter((c) => c.title || c.body);
}

const CHAR_TRAIT_LABELS = ["出身", "個性", "性格", "目標", "動機", "秘密"];
const CHAR_TRAIT_KEYS = { 出身: "birth", 個性: "personality", 性格: "personality", 目標: "goal", 動機: "goal", 秘密: "secret" };
function parseCharacterTraits(text) {
  const got = parseLabeled(text, CHAR_TRAIT_LABELS);
  const out = {};
  for (const [label, val] of Object.entries(got)) {
    const key = CHAR_TRAIT_KEYS[label];
    if (key && val) out[key] = out[key] ? `${out[key]}\n${val}` : val;
  }
  return out;
}

// M13b：外型解析（character_look）。「臉型五官：…／髮型：…／體型：…／膚色：…／英文 prompt：…」
// 也認英文標籤（Face/Hair/Body/Skin/English prompt）與「**髮型**：」粗體標籤。
const APPEARANCE_LABELS = ["臉型五官", "臉型", "五官", "面部", "髮型髮色", "髮型", "髮色", "體型", "身材", "身型",
  "膚色", "皮膚", "辨識特徵", "特徵", "英文 prompt", "英文prompt", "英文提示", "英文", "promptEn",
  "Face", "Hair", "Body", "Skin", "English prompt", "English Prompt", "prompt"];
const APPEARANCE_KEYS = {
  臉型五官: "face", 臉型: "face", 五官: "face", 面部: "face", Face: "face",
  髮型髮色: "hair", 髮型: "hair", 髮色: "hair", Hair: "hair",
  體型: "body", 身材: "body", 身型: "body", Body: "body",
  膚色: "skin", 皮膚: "skin", Skin: "skin",
  "英文 prompt": "promptEn", 英文prompt: "promptEn", 英文提示: "promptEn", 英文: "promptEn",
  promptEn: "promptEn", "English prompt": "promptEn", "English Prompt": "promptEn", prompt: "promptEn",
};
function parseAppearance(text) {
  const got = parseLabeled(text, APPEARANCE_LABELS);
  const out = {};
  for (const [label, val] of Object.entries(got)) {
    const key = APPEARANCE_KEYS[label];
    const v = String(val || "").replace(/^[\s「『"]+|[\s」』"]+$/g, "").trim();
    if (!key || !v) continue;
    if (key === "promptEn") {
      // 英文 prompt 可能是多行：保留整段（標籤行之後的英文段落也算）
      out.promptEn = out.promptEn ? `${out.promptEn} ${v}` : v;
      continue;
    }
    out[key] = out[key] ? `${out[key]}；${v}` : v;
  }
  // 模型只回「英文 prompt：」單獨一行、內容在下一行時，parseLabeled 會留空 → 補抓
  if (!out.promptEn) {
    const m = stripChatWrapper(text).match(/^[ \t]*(?:[-*•]\s*)?\*{0,2}(?:英文\s*prompt|promptEn|English prompt)\*{0,2}\s*[：:][ \t]*\n([\s\S]+)$/im);
    if (m) {
      const block = m[1].split(/\n\s*\n/)[0].trim();
      if (block) out.promptEn = block.replace(/\n+/g, " ").trim();
    }
  }
  // 完全沒有標籤但整段是英文 → 視為 promptEn（模型「第二段寫英文 promptEn」常常不加標籤）
  if (!Object.keys(out).length) {
    const s = stripChatWrapper(text);
    if (s && !/[\u4e00-\u9fff]/.test(s)) out.promptEn = s.replace(/\n+/g, " ").trim();
  }
  return out;
}

const STYLE_LABELS = ["類型", "基調", "視覺風格", "視覺", "負向提示", "負面提示", "負向", "風格關鍵字"];
const STYLE_KEYS = { 類型: "genre", 基調: "tone", 視覺風格: "visualStyle", 視覺: "visualStyle",
  負向提示: "negative", 負面提示: "negative", 負向: "negative", 風格關鍵字: "keywords" };
function parseStoryStyle(text) {
  const got = parseLabeled(text, STYLE_LABELS);
  const out = {};
  for (const [label, val] of Object.entries(got)) {
    const key = STYLE_KEYS[label];
    if (!key || !val) continue;
    const clean = val.replace(/^["「『]|["」』]$/g, "").trim();
    if (key === "keywords") { out.negative = [out.negative, clean].filter(Boolean).join("，"); continue; }
    out[key] = out[key] ? `${out[key]}，${clean}` : clean;
  }
  return out;
}

// 角色名：用現有人物表比對，「角色名：台詞」→ characterId；對不到就保留名字（後端存 null＋warning）
function matchCharacterName(name, charMap) {
  // 先去掉動作括號再比對（「艾麗絲（動作）」就是艾麗絲；「艾麗絲（主角）」也一樣）
  const raw = String(name || "").replace(/[（(][^）)]*[）)]/g, "").trim();
  if (!raw) return { id: null, name: raw };
  // 打錯字（多一個字／少一個字）也算同一個人：「艾麗絲絲」→ 艾麗絲（只在唯一命中時採用）
  const near = charMap[raw] ? null : Object.keys(charMap).filter((k) => {
    if (!k || k.length < 2) return false;
    const t = raw;
    if (t.length === k.length + 1) return [...t].some((_, i) => t.slice(0, i) + t.slice(i + 1) === k);
    if (k.length === t.length + 1) return [...k].some((_, i) => k.slice(0, i) + k.slice(i + 1) === t);
    return false;
  });
  if (near && near.length === 1) return { id: charMap[near[0]], name: near[0] };
  if (charMap[raw]) return { id: charMap[raw], name: raw };
  // 「林旭（主角）」已經在上一步去掉括號；再試「去掉括號後包含」與全名/別名
  const hit = Object.keys(charMap).find((k) => k && (raw.includes(k) || k.includes(raw)));
  return hit ? { id: charMap[hit], name: hit } : { id: null, name: raw };
}

// 「角色名：台詞」「角色名（動作）：台詞」「（動作：…）」「【動作】…」
function parseBeats(text, characters) {
  const charMap = {};
  for (const c of characters || []) {
    if (c && c.name) charMap[c.name] = c.id;
    for (const a of c && c.aliases || []) if (a) charMap[a] = c.id;
  }
  const names = Object.keys(charMap).sort((a, b) => b.length - a.length);
  const items = [];
  const warnings = [];
  const lines = stripChatWrapper(text).split("\n");
  for (let raw of lines) {
    let line = raw.replace(/\*{2,}/g, "").trim();
    if (!line) continue;
    line = line.replace(/^\d+[.)、]\s*/, "");
    let m = line.match(/^[（(]\s*(?:動作|action)\s*[：:]\s*(.*?)[）)]\s*$/i);
    if (m) { items.push({ type: "action", characterId: null, line: m[1].trim(), emotion: "" }); continue; }
    m = line.match(/^[【\[]\s*(?:動作|action)\s*[】\]]\s*[：:]?\s*(.*)$/i);
    if (m) { items.push({ type: "action", characterId: null, line: m[1].trim(), emotion: "" }); continue; }
    // 角色名：台詞（角色名後面可能帶動作括號）
    const head = names.find((n) => line.startsWith(n) && /^\s*[：:]/.test(line.slice(n.length)));
    if (head) {
      let rest = line.slice(head.length).replace(/^\s*[：:]\s*/, "");
      const action = [];
      rest = rest.replace(/[（(]\s*(?:動作)?\s*[：:]?\s*([^（()）]*?)[）)]/g, (all, a) => {
        if (a) action.push(a.trim());
        return "";
      });
      if (rest.trim()) {
        items.push({ type: "dialogue", characterId: charMap[head], name: head, line: rest.trim(), emotion: "", notes: action.join("；") });
      } else if (action.length) {
        items.push({ type: "action", characterId: charMap[head], name: head, line: action.join("；"), emotion: "" });
      }
      continue;
    }
    // 通用「X：Y」找不到 X 是人名 → 仍視為台詞，但標示對不到
    const g = line.match(/^([^\s：:，。]{1,8})\s*[：:]\s*(.+)$/);
    if (g && /[\u4e00-\u9fff]/.test(g[1])) {
      items.push({ type: "dialogue", characterId: null, name: g[1], line: g[2].trim(), emotion: "" });
      warnings.push(`「${g[1]}」對不到現有人物，這句會存成沒有角色的台詞`);
      continue;
    }
    // 沒標角色的整行 → 動作
    items.push({ type: "action", characterId: null, line: line, emotion: "" });
  }
  return { beats: items, warnings };
}

function parseDialogue(text, characters) {
  const { beats, warnings } = parseBeats(text, characters);
  const out = beats.filter((b) => b.line).map((b) => ({ characterId: b.characterId || null, line: b.line, _name: b.name || "" }));
  return { dialogue: out, warnings };
}

function looksLikeBeats(text, kind) {
  const s = stripChatWrapper(text);
  return (kind === "beats" || kind === "dialogue") && /[：:]/.test(s) && s.split("\n").filter((l) => l.trim()).length >= 2;
}

// 統一入口：把一段回覆解析成「格子 → 值」。回 {patches, warnings, note}
// patches: { "欄位路徑或格子 key": 值 }；kind＝MULTI_APPLY_FIELDS 的 kind
function parseReplyIntoFields(text, kind, ctx) {
  const c = ctx || {};
  const src = stripChatWrapper(text);
  const characters = c.characters || [];
  const warn = [];

  // 模型直接回 patch JSON（進階）→ 原樣採用
  if (looksLikeJson(src)) {
    let obj;
    try { obj = JSON.parse(src); } catch (_) { obj = null; }
    if (Array.isArray(obj) && (kind === "beats" || kind === "dialogue")) obj = { [kind]: obj };
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      // 常見的「物件包陣列」JSON（{"chapters": [...]}）→ 照 key 套用；
      // 只有一個 key 而且 key 對不上這張卡時，直接拆開它（{"data": {...}}）
      let payload = obj;
      const keys = Object.keys(payload);
      const sub = keys.length === 1 ? payload[keys[0]] : null;
      if (sub && typeof sub === "object" && !Array.isArray(sub)) payload = sub;  // {"data": {...}}
      if (keys.length === 1 && Array.isArray(sub)) payload = { [keys[0]]: sub };  // {"beats": [...]}
      if (Object.keys(payload).length) {
        return { patches: payload, warnings: [], note: "偵測到 JSON：直接依欄位名稱套用" };
      }
    }
  }

  if (kind === "chapters") {
    const chapters = parseChapters(src);
    const bad = [];
    return { patches: { chapters }, warnings: warn, note: `辨識到 ${chapters.length} 回` };
  }
  if (kind === "style") {
    const style = parseStoryStyle(src);
    const n = Object.keys(style).length;
    if (!n) warn.push("沒有辨識到「類型：」「基調：」這種標籤行");
    return { patches: { style }, warnings: warn, note: `辨識到 ${n} 項風格` };
  }
  if (kind === "background") {
    const bg = parseCharacterTraits(src);
    const n = Object.keys(bg).length;
    if (!n) warn.push("沒有辨識到「出身：」「個性：」這種標籤行");
    return { patches: { background: bg }, warnings: warn, note: `辨識到 ${n} 欄身世` };
  }
  if (kind === "beats" || kind === "dialogue") {
    const r = kind === "beats" ? parseBeats(src, characters) : parseDialogue(src, characters);
    const items = kind === "beats" ? r.beats : r.dialogue;
    if (!items.length) {
      return { patches: { [kind]: [] }, warnings: [...(r.warnings || [])],
        note: "沒有辨識到「角色名：台詞」（模型若回 JSON 陣列，請貼成「角色名：台詞」一行一句）" };
    }
    const miss = items.filter((x) => !x.characterId && x.type !== "action" && (x._name || x.name));
    const w = [...r.warnings];
    if (kind === "beats") {
      for (const b of items) if (!b.characterId && b.name && b.type === "action") w.push(`動作 beat 的角色「${b.name}」對不到人物（動作不強制要角色）`);
    }
    return { patches: { [kind]: items }, warnings: w, note: `辨識到 ${items.length} 個${kind === "beats" ? " beat" : "對白"}${miss.length ? `，其中 ${miss.length} 個對不到人物` : ""}` };
  }
  if (kind === "outfits") {
    // 只有回覆真的在講服裝時才拆；否則回空（不要把人設／身世誤當成一套服裝）
    const clothWords = /(服裝|服飾|外套|雨衣|連帽|襯衫|裙|褲|靴|鞋|制服|披風|套裝|outfit|jacket|coat|dress)/i;
    const desc = parseLabeled(src, ["名稱", "描述", "英文 prompt", "英文prompt", "外觀", "材質", "promptEn", "英文"]);
    if (!Object.keys(desc).length && !clothWords.test(src)) {
      return { patches: { outfits: [] }, warnings: [], note: "回覆裡沒有服裝相關內容" };
    }
    const outfit = {
      name: desc["名稱"] || "新服裝",
      desc: desc["描述"] || desc["外觀"] || desc["材質"] || src,
      promptEn: desc["英文 prompt"] || desc["英文prompt"] || desc["promptEn"] || desc["英文"] || "",
    };
    if (!outfit.promptEn && !/[\u4e00-\u9fff]/.test(src) && /[a-zA-Z]{3}/.test(src)) outfit.promptEn = src.trim();
    return { patches: { outfits: [outfit] }, warnings: [], note: "整段視為一套服裝（可用「名稱：」「描述：」「英文 prompt：」分行）" };
  }
  return { patches: {}, warnings: ["這個對象沒有可解析的結構欄位，請直接勾單一欄位套用"], note: "" };
}

// ── 多欄套用：狀態與 UI ─────────────────────────────────────
const multiState = { msg: null, rows: [], sliders: [], previewOpen: false, busy: false };

function bubbleElByIndex(idx) {
  const log = document.getElementById("chat-log");
  if (!log) return null;
  return log.querySelectorAll(".bubble.assistant")[idx] || null;
}

// 使用者在氣泡裡選取過的字 → 優先用它（選取範圍＝這次要套用的內容）
// 為什麼要有快取：按下按鈕的那一下，瀏覽器會把選取範圍收起來（selectionchange 收到 isCollapsed），
// 等 onclick 進來才去讀 window.getSelection() 就永遠是空的（M13a 踩過）。
// 所以在 selectionchange 時就把「剛剛在氣泡裡選了什麼」記下來，按鈕只讀快取。
const bubbleSelCache = { idx: null, text: "", at: 0 };

document.addEventListener("selectionchange", () => {
  const sel = window.getSelection && window.getSelection();
  if (!sel || !sel.rangeCount || sel.isCollapsed) return;
  const text = (sel.toString() || "").trim();
  if (!text) return;
  const node = sel.anchorNode;
  const el0 = node ? (node.nodeType === 1 ? node : node.parentElement) : null;
  const bubble = el0 && el0.closest ? el0.closest(".bubble.assistant") : null;
  if (!bubble) return;
  const log = document.getElementById("chat-log");
  const idx = log ? Array.prototype.indexOf.call(log.querySelectorAll(".bubble.assistant"), bubble) : -1;
  if (idx < 0) return;
  bubbleSelCache.idx = idx;
  bubbleSelCache.text = text;
  bubbleSelCache.at = Date.now();
});

function cachedSelectionFor(idx) {
  if (bubbleSelCache.idx !== idx) return "";
  if (Date.now() - bubbleSelCache.at > 120000) return "";
  return bubbleSelCache.text;
}

function replySelectionFor(bubble) {
  if (!bubble) return "";
  const host = bubble.querySelector(".bubble-text") || bubble;
  const sel = window.getSelection && window.getSelection();
  if (!sel || !sel.rangeCount || sel.isCollapsed) return "";
  const range = sel.getRangeAt(0);
  if (!host.contains(range.commonAncestorContainer)) return "";
  return (sel.toString() || "").trim();
}

function replyTextFor(idx) {
  const m = chatState.messages[idx];
  const bubble = bubbleElByIndex(idx);
  const picked = replySelectionFor(bubble);
  if (picked) return { text: picked, fromSelection: true };
  return { text: (m && m.content) || "", fromSelection: false };
}

function chapterHints() {
  return ((state.project && state.project.story && state.project.story.chapters) || []).map((c) => `${c.id} ${c.title || "（無標題）"}`);
}

// 建立某個結構格子的列（勾選框＋textarea／結構白名單）
function buildSliderRow(spec, initialText, fromSelection) {
  const row = el("div", { class: "ma-slider" });
  const check = el("input", { type: "checkbox" });
  const rowState = { spec, check, ta: null, value: null, preview: el("div", { class: "ma-preview" }),
    parsedText: spec.parse ? initialText : null };
  const head = el("label", { class: "ma-slider-head" },
    check, el("span", { class: "ma-slider-label", text: spec.label }));
  row.append(head);
  if (spec.multi) {
    const ta = el("textarea", { class: "ma-input mono", rows: String(spec.kind === "style" || spec.kind === "background" ? 4 : 6), spellcheck: "false" });
    ta.value = "";
    ta.placeholder = spec.kind === "chapters"
      ? "第一回　雨夜\n（正文…）\n\n第二回　貨架後\n（正文…）"
      : "可以直接貼，或按下面的「解析成多欄」自動分配";
    ta.addEventListener("input", () => updateMultiPreview());
    rowState.ta = ta;
    row.append(ta);
  }
  const isArrayKind = ["chapters", "beats", "dialogue", "outfits"].includes(spec.kind);
  if (isArrayKind) {
    // 只選了回覆裡的一段（例如只選第 2 回）時預設「追加」，否則按下去會把其他章回／整場 beat 蓋掉
    const sel = el("select", { class: "ma-mode" },
      el("option", { value: "append", text: "追加到現有內容之後" }),
      el("option", { value: "replace", text: "整份取代（會蓋掉現有的）" }));
    sel.value = fromSelection ? "append" : "replace";
    rowState.modeSel = sel;
    row.append(el("label", { class: "ma-mode-row" }, el("span", { class: "hint", text: "寫入方式：" }), sel));
  }
  if (spec.hint) row.append(el("div", { class: "hint ma-hint", text: spec.hint }));
  row.append(rowState.preview);
  if (fromSelection) row.append(el("span", { class: "hint", text: "已帶入你在回覆裡選取的文字" }));
  rowState.row = row;
  check.addEventListener("change", () => {
    row.classList.toggle("checked", check.checked);
    updateMultiPreview();
  });
  // 結構格子預設都勾（來源就是這段回覆）；解析不到東西的格子不會進 items
  if (spec.parse) { check.checked = true; row.classList.add("checked"); }
  rowState.parsedFrom = initialText;
  return rowState;
}

// M13b：單欄格子的列（勾選框＋textarea）；spec = { path, label, hint, value }
function buildPlainRow(spec, text) {
  const c = el("input", { type: "checkbox" });
  const ta = el("textarea", { class: "ma-input", rows: "2" });
  ta.placeholder = `要寫進「${spec.label}」的內容（勾了才用；留空不套用）`;
  ta.addEventListener("input", () => updateMultiPreview());
  const line = el("div", { class: "ma-plain-row" },
    el("label", { class: "ma-slider-head" }, c, el("span", { text: spec.label })),
    ta);
  if (spec.hint) line.append(el("div", { class: "hint ma-hint", text: spec.hint }));
  c.addEventListener("change", () => {
    line.classList.toggle("checked", c.checked);
    if (c.checked && !ta.value.trim()) ta.value = text;
    updateMultiPreview();
  });
  // 先放值再判斷（textarea.value 要在設完才讀得到）
  ta.value = spec.value || "";
  // 預填了內容的格子預設勾起來（由 applyHint 指定的回填目標）
  if (String(ta.value || "").trim()) { c.checked = true; line.classList.add("checked"); }
  return { path: spec.path, check: c, ta, row: line };
}

function renderMultiPanel(host, idx, selArg, taskIdArg) {
  host.textContent = "";
  const t = chatState.target;
  const specs = multiApplyFieldsFor(t);
  const bubble = bubbleElByIndex(idx);
  const selNow = (selArg !== undefined ? selArg : replySelectionFor(bubble)) || "";
  const picked = selNow ? { text: selNow, fromSelection: true }
    : { text: (chatState.messages[idx] && chatState.messages[idx].content) || "", fromSelection: false };
  const text = picked.text;
  // M13b：這個氣泡是回哪個任務的？→ 決定「該開哪幾格」
  const taskId = taskIdArg !== undefined ? taskIdArg : ((chatState.messages[idx] && chatState.messages[idx].meta && chatState.messages[idx].meta.task) || null);
  const task = taskId ? chatTaskById(t.kind, taskId) : null;
  const hasTaskHint = !!(task && task.applyHint);
  const hint = hasTaskHint ? task.applyHint : null;
  const hintParts = hint ? splitHintFields(t, hint) : { multi: [], plain: [] };
  const hasHint = !!hint;
  const hintPaths = new Set((hint && hint.fields) || []);
  const hintSpecs = hintParts.multi;
  const hintPlain = hintParts.plain;
  multiState.msg = idx;
  multiState.rows = [];
  multiState.task = taskId;
  multiState.hint = hint;
  // 選取範圍很可能是使用者要精準套用的那一段；之後解析都優先用它（不要因焦點移動而丟失）
  multiState.selectedText = picked.fromSelection ? picked.text : "";
  multiState.note = "";
  multiState.summary = "";
  multiState.plainRows = [];
  // 有 applyHint 的任務：先把回覆解析成它宣告的那幾格，再拿來預填
  const hintParsed = {};
  const notes0 = [];
  function parseHintFields(src) {
    for (const k of Object.keys(hintParsed)) delete hintParsed[k];
    notes0.length = 0;
    if (!hasHint) return;
    const kinds = [...new Set(hintSpecs.map((s) => s.kind))];
    for (const kind of kinds) {
      const res = tidyParseResult(kind, parseReplyIntoFields(src, kind,
        { characters: (state.project && state.project.characters) || [] }));
      for (const [k, v] of Object.entries(res.patches || {})) hintParsed[k] = v;
      if (res.note) notes0.push(`${kind}：${res.note}`);
    }
    const look = (task && task.id === "character_look") || hintPaths.has("appearance.face");
    if (look) {
      const ap = parseAppearance(src);
      if (Object.keys(ap).length) {
        // 攤平成「appearance.face」這種路徑 key：回填格子一律用路徑取值
        for (const [k, v] of Object.entries(ap)) hintParsed[`appearance.${k}`] = v;
        hintParsed.appearance = { ...(hintParsed.appearance || {}), ...ap };
      } else notes0.push("外型：沒有辨識到「臉型五官：」「髮型：」這種標籤行");
    }
    const onlyPromptEn = hintPaths.size === 1 && hintPaths.has("appearance.promptEn");
    const singlePlain = hintPlain.length === 1 ? hintPlain[0] : null;
    if (singlePlain && singlePlain !== "appearance.promptEn" && !hintSpecs.length) {
      // 純文字回填（台詞／大綱／描述／音色）：整段回覆就是值
      hintParsed[singlePlain] = simplifyReplyText(singlePlain, src);
    } else if (singlePlain === "appearance.promptEn" && !hintSpecs.length) {
      const ap = parseAppearance(src);
      hintParsed["appearance.promptEn"] = ap.promptEn || firstEnglishBlock(src) || src.trim();
    } else if (onlyPromptEn && hintSpecs.length) {
      const ap = parseAppearance(src);
      hintParsed["appearance.promptEn"] = ap.promptEn || firstEnglishBlock(src) || src.trim();
    }
  }
  parseHintFields(text);
  const panel = el("div", { class: "ma-panel" });
  panel.append(el("div", { class: "ma-head" },
    el("strong", { text: hasHint ? `回填到 ${applyHintLabel(hint)}` : "多欄套用" }),
    el("span", { class: "hint ma-text", text: picked.fromSelection ? "（用你在回覆裡選取的文字）" : "（用整段回覆；先在回覆裡選一段就只套用那段）" })));

  const bodyWrap = el("div", { class: "ma-body" });
  if (hasHint) {
    bodyWrap.append(el("div", { class: "hint", text: `任務「${task.label}」的回填目標（已預填並勾選，確認無誤就按下面的套用）：` }));
    for (const spec of hintSpecs) {
      const rowState = buildSliderRow(spec, text, picked.fromSelection);
      if (hintParsed[spec.kind] !== undefined) rowState.value = { [spec.kind]: hintParsed[spec.kind] };
      rowState.check.checked = true;
      rowState.row.classList.add("checked");
      if (rowState.value) renderSliderPreview(rowState, { patches: rowState.value, warnings: notes0.filter((n) => n.startsWith(spec.kind)) });
      multiState.rows.push(rowState);
      bodyWrap.append(rowState.row);
    }
    const plainLabels = applyFieldsFor(t);
    for (const path of hintPlain) {
      const label = hintFieldLabel(plainLabels, path);
      const pr = buildPlainRow({ path, label, value: hintParsed[path] || "", hint: plainHintFor(path) }, text);
      multiState.plainRows.push(pr);
      bodyWrap.append(pr.row);
    }
    if (!hintSpecs.length && !hintPlain.length) {
      bodyWrap.append(el("div", { class: "hint", text: "這個任務沒有可回填的格子。" }));
    }
    if (notes0.length) bodyWrap.append(el("div", { class: "hint ma-hint", text: notes0.join("；") }));
  } else {
    bodyWrap.append(el("div", { class: "hint", text: "勾要套用的格子（整份結構一起寫）：" }));
    for (const spec of specs) {
      const rowState = buildSliderRow(spec, text, picked.fromSelection);
      if (hintParsed[spec.kind] !== undefined) rowState.value = { [spec.kind]: hintParsed[spec.kind] };
      multiState.rows.push(rowState);
      bodyWrap.append(rowState.row);
    }
  }
  panel.append(bodyWrap);

  // 其他欄位收在 details 裡；有 applyHint 時不要讓使用者以為「什麼都能填」
  const allPlain = applyFieldsFor(t);
  const others = allPlain.filter(([path]) => !hasHint || !hintPaths.has(path));
  const otherMulti = specs.filter((s) => !hasHint || !hintPaths.has(s.path));
  const plainHost = el("details", { class: "ma-plain" },
    el("summary", { class: "hint", text: `其他欄位（${others.length + otherMulti.length} 個；這次任務不一定要填）` }));
  if (hasHint) {
    for (const spec of otherMulti) {
      const rowState = buildSliderRow({ ...spec, parse: true }, text, picked.fromSelection);
      rowState.check.checked = false;
      rowState.row.classList.remove("checked");
      multiState.rows.push(rowState);
      plainHost.append(rowState.row);
    }
  }
  for (const [path, label] of others) {
    const c = el("input", { type: "checkbox" });
    const ta = el("textarea", { class: "ma-input", rows: "2" });
    ta.placeholder = `要寫進「${label}」的內容（勾了才用；留空不套用）`;
    ta.addEventListener("input", () => updateMultiPreview());
    const line = el("div", { class: "ma-plain-row" },
      el("label", { class: "ma-slider-head" }, c, el("span", { text: label })), ta);
    c.addEventListener("change", () => {
      line.classList.toggle("checked", c.checked);
      if (c.checked && !ta.value.trim()) ta.value = text;
      updateMultiPreview();
    });
    multiState.plainRows.push({ path, check: c, ta, other: true });
    plainHost.append(line);
  }
  panel.append(plainHost);

  const actions = el("div", { class: "ma-actions" });
  const applyBtn = el("button", { class: "btn btn-small btn-primary", type: "button" }, "套用已勾的欄位");
  const cancelBtn = el("button", { class: "btn btn-small", type: "button" }, "收起");
  if (!hasHint) {
    const parseBtn = el("button", { class: "btn btn-small btn-primary", type: "button",
      title: "把回覆自動分配到結構格子（不會直接寫檔，先看下方預覽）" }, "⇣ 解析成多欄");
    parseBtn.addEventListener("click", () => {
      // 解析目標＝還沒解析過的格子；已解析過、使用者也沒取消勾選的保留結果
      // 以使用者的勾選為準（預設全勾）；不要的格子先取消勾選再按解析
      let targets = multiState.rows.filter((r) => r.spec.parse && r.check.checked);
      if (!targets.length) { multiState.note = "這個對象沒有可解析的結構欄位"; updateMultiPreview(); return; }
      const notes = [];
      const srcText = multiState.selectedText || text;
      for (const r of targets) {
        const res = runParseForRow(r, srcText);
        if (res.note) notes.push(`${r.spec.label.split("（")[0]}：${res.note}`);
      }
      multiState.note = notes.join("；");
      multiState.previewOpen = true;
      updateMultiPreview();
    });
    actions.append(parseBtn);
  } else {
    const reparse = el("button", { class: "btn btn-small", type: "button",
      title: "用目前全文重新解析，覆蓋格子裡已預填的內容" }, "↻ 重新解析");
    reparse.addEventListener("click", () => {
      const src = multiState.selectedText || (chatState.messages[idx] && chatState.messages[idx].content) || "";
      parseHintFields(src);
      const notes = [];
      for (const r of multiState.rows) {
        if (!hintPaths.has(r.spec.path)) continue;
        if (hintParsed[r.spec.kind] !== undefined) {
          r.value = { [r.spec.kind]: hintParsed[r.spec.kind] };
          r.check.checked = true;
          r.row.classList.add("checked");
          renderSliderPreview(r, { patches: r.value, warnings: [] });
          if (r.spec.kind) notes.push(`${r.spec.kind}：${(hintParsed[r.spec.kind] || []).length || Object.keys(hintParsed[r.spec.kind] || {}).length} 項`);
        }
      }
      for (const p of multiState.plainRows) {
        if (!hintPaths.has(p.path)) continue;
        if (p.ta) p.ta.value = hintParsed[p.path] || "";
        if (p.check) { p.check.checked = !!String(p.ta.value || "").trim(); p.check.dispatchEvent(new Event("change")); }
      }
      multiState.note = notes.join("；");
      updateMultiPreview();
    });
    actions.append(reparse);
  }
  applyBtn.addEventListener("click", () => applyMulti(applyBtn));
  cancelBtn.addEventListener("click", () => { host.textContent = ""; multiState.rows = []; });
  actions.append(applyBtn, cancelBtn);
  panel.append(actions, el("div", { class: "ma-note" }));
  multiState.noteHost = panel.querySelector(".ma-note");
  multiState.host = host;
  host.append(panel);
  updateMultiPreview();
}

// 單欄回填的短提示
function plainHintFor(path) {
  if (path === "line") return "只改這一句台詞；系統會自動去掉引號與「1.」這種編號。";
  if (path === "synopsis") return "整段回覆寫進故事大綱（會蓋掉原本的大綱）。";
  if (path === "voice.desc") return "音色描述寫進人物卡的 voice.desc（三選一版本按鈕會直接寫入）。";
  if (path === "appearance.promptEn") return "只認英文段；中文描述請放上方的外型四欄。";
  if (path === "promptEn") return "整段回覆寫進 promptEn（可以直接編修）。";
  if (path === "desc") return "整段回覆寫進場景描述；想只套用一段，先在回覆裡選取。";
  return "";
}

// 單欄值收尾：台詞／音色去掉編號、引號、串場語
function simplifyReplyText(path, text) {
  let s = stripChatWrapper(text).trim();
  if (path === "line") {
    s = s.split("\n").map((l) => l.trim()).filter(Boolean)[0] || "";
    s = s.replace(/^\s*(?:\d+[.)、]|[-*•])\s*/, "");
    const m = s.match(/^[^：:]{1,8}\s*[：:]\s*(.+)$/);
    if (m) s = m[1];
    s = s.replace(/[「」『』"]/g, "").replace(/[（(][^）)]*[）)]\s*$/, "").trim();
    return s;
  }
  if (path === "voice.desc") {
    const lines = s.split("\n").filter((l) => l.trim() && !/^(以下是|建議|可以)/.test(l.trim()));
    if (lines.length >= 3) s = lines.slice(0, 3).join("\n");
  }
  return s;
}

// 回覆裡第一段「英文」行（給只有 promptEn 的任務用）
function firstEnglishBlock(text) {
  const s = stripChatWrapper(text);
  if (/[\u4e00-\u9fff]/.test(s)) return "";
  return s.trim();
}

// 對單一結構格子跑解析：來源＝使用者打過的 textarea，否則用「選取段落／整段回覆」
function runParseForRow(rowState, fallbackText) {
  const fallback = String(fallbackText || "");
  const own = ((rowState.ta && rowState.ta.value) || "").trim();
  const base = String(fallback || "");
  const src = own && own !== base.trim() ? own : base;
  const res = tidyParseResult(rowState.spec.kind, parseReplyIntoFields(
    src, rowState.spec.kind, { characters: (state.project && state.project.characters) || [] }));
  const count = countParsed(rowState.spec.kind, res.patches);
  // 解析出東西就幫使用者勾起來；解析不到就「唯讀預覽」＋不勾（不留下看起來像成功其實空手的格子）
  if (count > 0) {
    rowState.check.checked = true;
    rowState.row.classList.add("checked");
  } else if (rowState.parsedText) {
    // 解析過但一個都沒中 → 取消勾選（回覆裡沒有這種內容），避免按套用時空手
    rowState.check.checked = false;
    rowState.row.classList.remove("checked");
  }
  rowState.row.classList.toggle("parse-fail", count === 0 && !!rowState.parsedText);
  rowState.value = res.patches;
  rowState.parsedText = src;
  renderSliderPreview(rowState, res);
  return res;
}

function countParsed(kind, patches) {
  const p = patches || {};
  if (kind === "chapters") return (p.chapters || []).length;
  if (kind === "style" || kind === "background") return Object.keys(p[kind] || {}).length;
  if (kind === "beats" || kind === "dialogue") return (p[kind] || []).length;
  if (kind === "outfits") return (p.outfits || []).length;
  return 0;
}

// 解析結果整理：只留真的能寫進去的東西（例：章回開頭那句「以下是三回正文」不該變成第 1 回）
function tidyParseResult(kind, res) {
  const out = { patches: { ...(res.patches || {}) }, warnings: [...(res.warnings || [])], note: res.note || "" };
  const clean = (s) => String(s || "").trim();
  if (kind === "chapters") {
    let arr = (out.patches.chapters || []).map((c) => ({ title: clean(c.title), body: clean(c.body || c.content) }));
    // 開場白（很短、沒正文的一項）併進下一回或丟掉
    arr = arr.filter((c, i) => {
      if (/（未命名章回）/.test(c.title) && c.body.length < 40 && arr.length > 1) return false;
      return c.title || c.body;
    });
    arr.forEach((c, i) => { c.id = `ch${String(i + 1).padStart(2, "0")}`; });
    out.patches.chapters = arr;
    out.note = arr.length ? `辨識到 ${arr.length} 回` : "沒有辨識到「第 N 回」開頭的分段";
    if (!arr.length) out.warnings.push("回覆裡找不到「第一回　標題」這種開頭；可在 textarea 手動整理");
  } else if (kind === "style" || kind === "background") {
    const obj = {};
    for (const [k, v] of Object.entries(out.patches[kind] || {})) if (clean(v)) obj[k] = clean(v);
    out.patches = { [kind]: obj };
    out.note = Object.keys(obj).length ? `辨識到 ${Object.keys(obj).length} 項` : "沒有辨識到「標籤：內容」行";
  } else if (kind === "beats" || kind === "dialogue") {
    const key = kind;
    const arr = (out.patches[key] || []).filter((b) => clean(b.line));
    out.patches = { [key]: arr };
    const miss = arr.filter((b) => !b.characterId && (b.name || b._name));
    out.note = arr.length ? `辨識到 ${arr.length} 個${kind === "beats" ? " beat" : "對白"}${miss.length ? `，${miss.length} 個對不到人物` : ""}` : "沒有辨識到「角色名：台詞」";
  } else if (kind === "outfits") {
    const arr = (out.patches.outfits || []).filter((o) => clean(o.name) || clean(o.desc) || clean(o.promptEn));
    out.patches = { outfits: arr };
    out.note = arr.length ? `辨識到 ${arr.length} 套服裝` : "沒有內容";
  }
  return out;
}

function renderSliderPreview(rowState, res) {
  const p = rowState.preview;
  p.textContent = "";
  const patches = res.patches || {};
  const lines = [];
  if (rowState.spec.kind === "chapters") {
    for (const c of patches.chapters || []) lines.push(`${c.id}｜${c.title}｜${(c.body || "").slice(0, 40)}…`);
  } else if (rowState.spec.kind === "style") {
    for (const [k, v] of Object.entries(patches.style || {})) lines.push(`${k}：${v}`);
  } else if (rowState.spec.kind === "background") {
    for (const [k, v] of Object.entries(patches.background || {})) lines.push(`${k}：${String(v).slice(0, 40)}`);
  } else if (rowState.spec.kind === "beats" || rowState.spec.kind === "dialogue") {
    const items = patches[rowState.spec.kind] || [];
    items.forEach((b, i) => {
      const who = b.characterId ? charNameById(b.characterId) : (b.name || b._name ? `⚠ ${b.name || b._name}（對不到人物）` : "（動作／無角色）");
      lines.push(`${i + 1}. [${b.type || "dialogue"}] ${who}：${String(b.line || "").slice(0, 30)}`);
    });
  } else if (rowState.spec.kind === "outfits") {
    for (const o of patches.outfits || []) lines.push(`${o.name}｜${String(o.desc || "").slice(0, 40)}`);
  }
  if (!lines.length) { p.append(el("div", { class: "hint", text: "尚未解析" })); return; }
  if (res.warnings && res.warnings.length) {
    p.append(el("div", { class: "ma-warn", text: `⚠ ${res.warnings.join("；")}` }));
  }
  p.append(el("div", { class: "hint", text: `解析結果 ${lines.length} 條（確認無誤後按「套用已勾的欄位」）：` }),
    el("ol", { class: "ma-list" }, ...lines.slice(0, 12).map((l) => el("li", { text: l }))));
  if (lines.length > 12) p.append(el("div", { class: "hint", text: `…還有 ${lines.length - 12} 條` }));
  if (!String(res.note || "").trim()) p.append(el("div", { class: "hint", text: "（沒有可套用的結果：請確認回覆格式，或直接在 textarea 手動編修後再按套用）" }));
}

// 勾選狀態 → items（給 apply-batch 用）＋ 預覽文字
// 規矩：textarea 沒有被使用者改過 → 用解析結果；被改過 → 以 textarea 重新解析（人工改動優先）
function itemsFromRow(r, notes) {
  const spec = r.spec;
  const raw = ((r.ta && r.ta.value) || "").trim();
  const patchSrc = raw || (r.parsedText || "").trim();
  if (!patchSrc) { notes.push(`${spec.label}：內容是空的，略過`); return []; }
  const own = ((r.ta && r.ta.value) || "").trim();
  const changed = !r.value || (own && own !== (r.parsedText || "").trim());
  const patches = changed
    ? tidyParseResult(spec.kind, parseReplyIntoFields(patchSrc, spec.kind,
        { characters: (state.project && state.project.characters) || [] })).patches
    : r.value;
  const out = [];
  const clean = (s) => String(s || "").trim();
  if (spec.kind === "chapters") {
    const arr = (patches.chapters || []).map((c) => ({ title: clean(c.title), body: clean(c.body || c.content) })).filter((c) => c.title || c.body);
    if (!arr.length) notes.push("章回：沒有辨識到任何一回");
    else out.push({ path: "chapters", value: arr, mode: (r.modeSel && r.modeSel.value) || "replace" });
  } else if (spec.kind === "style" || spec.kind === "background") {
    const obj = patches[spec.kind] || {};
    const pairs = Object.entries(obj).filter(([, v]) => clean(v));
    if (!pairs.length) notes.push(`${spec.label}：沒有辨識到任何一項`);
    for (const [k, v] of pairs) {
      const path = spec.kind === "style" ? `style.${k}` : `background.${k}`;
      out.push({ path, value: clean(v), mode: "replace" });
    }
  } else if (spec.kind === "beats") {
    const arr = (patches.beats || []).filter((b) => clean(b.line)).map((b) => ({
      type: b.type === "action" ? "action" : "dialogue",
      characterId: b.characterId || null, line: clean(b.line), emotion: clean(b.emotion),
    }));
    if (!arr.length) notes.push("beats：沒有辨識到台詞或動作");
    else out.push({ path: "beats", value: arr, mode: (r.modeSel && r.modeSel.value) || "replace" });
  } else if (spec.kind === "dialogue") {
    const arr = (patches.dialogue || []).filter((d) => clean(d.line)).map((d) => ({ characterId: d.characterId || null, line: clean(d.line) }));
    if (!arr.length) notes.push("對白：沒有辨識到台詞");
    else out.push({ path: "dialogue", value: arr, mode: (r.modeSel && r.modeSel.value) || "replace" });
  } else if (spec.kind === "outfits") {
    const arr = (patches.outfits || []).map((o) => ({ name: clean(o.name), desc: clean(o.desc), promptEn: clean(o.promptEn) }))
      .filter((o) => o.name || o.desc || o.promptEn);
    if (!arr.length) notes.push("服裝：沒有內容");
    else out.push({ path: "outfits", value: arr, mode: (r.modeSel && r.modeSel.value) || "replace" });
  }
  return out;
}

function collectMultiItems() {
  const items = [];
  const notes = [];
  for (const r of multiState.rows || []) {
    if (!r.check.checked) continue;
    items.push(...itemsFromRow(r, notes));
  }
  for (const p of multiState.plainRows || []) {
    if (p.other) continue;   // 「其他欄位」= 只有使用者自己勾、自己打完才套用
    if (!p.check.checked) continue;
    const v = (p.ta.value || "").trim();
    if (!v) { continue; }    // 空的就略過，不洗版
    items.push({ path: p.path, value: v, mode: "replace" });
  }
  return { items, notes };
}

function updateMultiPreview() {
  const host = multiState.noteHost;
  if (!host) return;
  host.textContent = "";
  const { items, notes } = collectMultiItems();
  const all = [...(multiState.note ? [multiState.note] : []), ...notes];
  if (all.length) host.append(el("div", { class: "hint", text: all.join("；") }));
  const modeTxt = items.map((i) => (i.mode === "append" ? `${i.path}（追加）` : i.path)).join("、");
  host.append(el("div", { class: "ma-summary", text: items.length ? `將套用 ${items.length} 欄：${modeTxt}` : "尚未選擇要套用的欄位" }));
}

async function applyMulti(btn) {
  if (!state.slug || multiState.busy) return;
  const { items } = collectMultiItems();
  if (!items.length) { setSaveStatus("error", "沒有可套用的欄位（先勾選並填入內容）"); return; }
  multiState.busy = true;
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "寫入中…";
  try {
    const res = await api(`/api/projects/${state.slug}/chat/apply-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: chatState.target, items }),
    });
    syncTargetFromServer(res.targetAfter);
    const warns = (res.warnings || []).length ? `（提醒：${res.warnings.join("；")}）` : "";
    setSaveStatus(warns ? "pending" : "saved", `已套用 ${res.updated.length} 欄到 ${res.label} · ${clockText()}${warns}`);
    btn.textContent = `已套用 ${res.updated.length} 欄 ✓`;
    multiState.rows.forEach((r) => { r.value = null; r.parsedText = null; });
    setTimeout(() => { btn.textContent = oldText; btn.disabled = false; }, 1800);
  } catch (e) {
    btn.textContent = oldText;
    btn.disabled = false;
    setSaveStatus("error", `套用失敗（整批未寫入）：${e.message}`);
  } finally {
    multiState.busy = false;
  }
}

// ── 送出 ─────────────────────────────────────────────────
async function sendChat() {
  if (!state.slug) { openNewModal(); return; }
  if (chatState.pending) return;
  const ta = document.getElementById("chat-input");
  const text = (ta.value || "").trim();
  if (!text) { ta.focus(); return; }
  const task = currentTaskId();
  chatState.messages.push({ role: "user", content: text });
  ta.value = "";
  renderChatLog();
  chatState.pending = true;
  const log = document.getElementById("chat-log");
  const waitText = el("div", { class: "bubble-text", text: "模型思考中…（可能要幾分鐘，請勿關閉）" });
  const waiting = el("div", { class: "bubble assistant pending" }, waitText);
  const waitT0 = Date.now();
  const waitTimer = setInterval(() => {
    const secs = Math.round((Date.now() - waitT0) / 1000);
    waitText.textContent = `模型運算中… 已等 ${secs} 秒（可能要幾分鐘，請勿關閉視窗）`;
  }, 1000);
  log.append(waiting);
  log.scrollTop = log.scrollHeight;
  const sendBtn = document.getElementById("chat-send");
  if (sendBtn) sendBtn.disabled = true;
  try {
    const history = chatState.messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role, content: m.content }));
    const res = await api(`/api/projects/${state.slug}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task,
        target: chatState.target,
        messages: history,
      }),
    });
    chatState.messages.push({
      role: "assistant",
      content: res.reply,
      meta: { model: res.model, tokens: (res.usage || {}).total_tokens, task: task || null },
    });
    if (!chatState.llm || !chatState.llm.ok) { chatState.llm = { ok: true, baseUrl: "", model: res.model, models: [] }; checkLLM(true); }
  } catch (e) {
    chatState.messages.push({ role: "assistant", content: `⚠ 模型回覆失敗：${e.message}`, meta: { error: true } });
    checkLLM(true);
  } finally {
    clearInterval(waitTimer);
    chatState.pending = false;
    if (sendBtn) sendBtn.disabled = false;
    renderChatLog();
  }
}

// Esc 關抽屜（不影響燈箱的 Esc）
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (chatState.open && !document.querySelector(".lightbox:not(.hidden)")) { closeChat(); return; }
  const g = document.getElementById("modal-gen");
  if (g && !g.classList.contains("hidden")) closeGenModal();
});

// ══════════════════════════════════════════════════════════
// M3c：結構化一次生成（故事→整集劇本、場次→多鏡頭）
// 流程：填參數 → dryRun 產生預覽（write nothing）→ 可在 textarea 編修 → 「套用」才打 apply/bulk
// ══════════════════════════════════════════════════════════
const genState = { kind: null, params: null, preview: null, pending: false };

function ensureGenModal() {
  if ($("#modal-gen")) return;
  const box = el("div", { id: "modal-gen", class: "modal hidden" });
  box.append(el("div", { class: "modal-box gen-box" },
    el("h3", { class: "gen-title", text: "✦ 生成" }),
    el("div", { class: "gen-params row wrap" }),
    el("p", { class: "hint gen-hint", text: "" }),
    el("div", { class: "row wrap gen-actions" },
      el("button", { class: "btn btn-small gen-run", type: "button" }, "✦ 產生預覽"),
      el("span", { class: "spacer" }),
      el("button", { class: "btn gen-close", type: "button" }, "關閉"),
      el("button", { class: "btn btn-primary gen-apply", type: "button", disabled: "disabled" }, "套用"),
    ),
    el("p", { class: "error gen-error hidden" }),
    el("div", { class: "gen-raw hidden" }),
    el("label", { class: "field gen-preview-wrap hidden" },
      el("span", { text: "預覽 JSON（可直接編修，套用時以這裡的內容為準）" }),
      el("textarea", { class: "tall mono gen-preview", spellcheck: "false" }),
    ),
    el("p", { class: "hint gen-status" }),
  ));
  document.body.append(box);
  box.querySelector(".gen-close").addEventListener("click", closeGenModal);
  box.addEventListener("click", (e) => { if (e.target.id === "modal-gen" && !genState.pending) closeGenModal(); });
  box.querySelector(".gen-run").addEventListener("click", runGenDryRun);
  box.querySelector(".gen-apply").addEventListener("click", applyGenBulk);
}

function closeGenModal() {
  const box = $("#modal-gen");
  if (box && !genState.pending) box.classList.add("hidden");
}
function genNumInput(labelText, value, min, max, step) {
  const input = el("input", { type: "number", value, min, max, step: step || "1" });
  return { node: el("label", { class: "field" }, el("span", { text: labelText }), input), input };
}
function genTextInput(labelText, value, placeholder) {
  const input = el("input", { type: "text", value: value || "", placeholder: placeholder || "" });
  return { node: el("label", { class: "field" }, el("span", { text: labelText }), input), input };
}

// 劇本頁：由故事生成整集
function openGenScript() {
  if (!state.slug) { alert("請先選一個專案。"); return; }
  ensureGenModal();
  genState.kind = "script";
  genState.preview = null;
  const box = $("#modal-gen");
  box.querySelector(".gen-title").textContent = "✦ 由故事生成整集劇本";
  box.querySelector(".gen-hint").textContent =
    "先用「1 故事」的大綱當骨幹。模型只能用專案現有的人物卡／場景卡 id；產出後先看預覽，按「套用」才會寫入（追加成新的一集）。";
  const params = box.querySelector(".gen-params");
  params.textContent = "";
  const t = genTextInput("集名（可留空由模型命名）", "", "例如：第二話 · 霧裡的奶奶");
  const n = genNumInput("場次數", 3, 1, 12);
  const m = genNumInput("目標時長（分鐘）", 1.5, 0.2, 60, 0.5);
  params.append(t.node, n.node, m.node);
  genState.readParams = () => ({
    episodeTitle: t.input.value.trim(),
    sceneCount: Number(n.input.value) || 3,
    targetMinutes: Number(m.input.value) || 1.5,
    dryRun: true,
  });
  resetGenPreview();
  box.classList.remove("hidden");
}

// 鏡頭頁：單場次生成多鏡頭
function openGenShots(esId) {
  if (!state.slug) { alert("請先選一個專案。"); return; }
  const found = findEpisodeScene(esId);
  if (!found) { alert("請先選一個場次（劇本頁至少要有集與場次）。"); return; }
  ensureGenModal();
  genState.kind = "shots";
  genState.preview = null;
  const box = $("#modal-gen");
  box.querySelector(".gen-title").textContent = `✦ 生成此場鏡頭 · ${esLabel(found.es)}`;
  box.querySelector(".gen-hint").textContent =
    "依該場次的 beats（台詞與動作）拆成多鏡，含 spec／beats／dialogLineRefs／六段式 promptEn；台詞句號由模型填，套用時會再驗 coverage。";
  const params = box.querySelector(".gen-params");
  params.textContent = "";
  const sel = el("select", {}, ...episodeSceneOptions(false).map((o) => el("option", { value: o.value, text: o.text })));
  sel.value = found.es.id;
  const n = genNumInput("鏡數", 3, 1, 12);
  params.append(el("label", { class: "field" }, el("span", { text: "場次" }), sel), n.node);
  genState.readParams = () => ({
    episodeSceneId: sel.value,
    count: Number(n.input.value) || 3,
    dryRun: true,
  });
  resetGenPreview();
  box.classList.remove("hidden");
}

function resetGenPreview() {
  const box = $("#modal-gen");
  box.querySelector(".gen-error").classList.add("hidden");
  box.querySelector(".gen-raw").classList.add("hidden");
  box.querySelector(".gen-preview-wrap").classList.add("hidden");
  box.querySelector(".gen-preview").value = "";
  box.querySelector(".gen-apply").disabled = true;
  box.querySelector(".gen-status").textContent = "";
}

function genBusy(on, text) {
  const box = $("#modal-gen");
  genState.pending = on;
  box.querySelectorAll(".btn, input, select, textarea").forEach((b) => { b.disabled = on; });
  box.querySelector(".gen-run").textContent = on ? "模型生成中…（可能要 30–60 秒）" : "✦ 產生預覽";
  box.querySelector(".gen-close").disabled = on;
  if (on) { resetGenPreview(); box.querySelector(".gen-status").textContent = text || ""; }
}

function genShowError(msg, rawSnippet) {
  const box = $("#modal-gen");
  const err = box.querySelector(".gen-error");
  err.textContent = `⚠ ${msg}`;
  err.classList.remove("hidden");
  const raw = box.querySelector(".gen-raw");
  if (rawSnippet) {
    raw.textContent = `模型的原始回覆（前 500 字）：\n${rawSnippet}`;
    raw.classList.remove("hidden");
  }
}

async function runGenDryRun() {
  if (genState.pending || !state.slug) return;
  const body = { ...genState.readParams(), dryRun: true };
  const ep = genState.kind === "script" ? "script" : "shots";
  genBusy(true, "呼叫模型中：dryRun 只回預覽，不會寫入任何資料。");
  try {
    const res = await api(`/api/projects/${state.slug}/generate/${ep}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    genState.preview = res.preview;
    genState.params = body;
    genBusy(false);
    const box = $("#modal-gen");
    box.querySelector(".gen-preview").value = JSON.stringify(res.preview, null, 2);
    box.querySelector(".gen-preview-wrap").classList.remove("hidden");
    box.querySelector(".gen-apply").disabled = false;
    const warns = res.warnings || [];
    box.querySelector(".gen-status").textContent =
      `模型：${res.model || ""}（dryRun，尚未寫入）` + (warns.length ? `　⚠ ${warns.length} 則提醒：${warns.join("；")}` : "　✓ 結構檢查通過");
  } catch (e) {
    genBusy(false);
    $("#modal-gen").querySelector(".gen-status").textContent = "";
    genShowError(e.message, e.rawSnippet);
  }
}

async function applyGenBulk() {
  if (genState.pending || !state.slug) return;
  const box = $("#modal-gen");
  const text = box.querySelector(".gen-preview").value.trim();
  let data;
  try {
    data = JSON.parse(text);
  } catch (e) {
    genShowError(`預覽 JSON 有語法錯誤，請修正後再套用：${e.message}`);
    return;
  }
  const kind = genState.kind === "script" ? "episode" : "shots";
  genState.pending = true;
  box.querySelector(".gen-apply").disabled = true;
  box.querySelector(".gen-apply").textContent = "寫入中…";
  try {
    const res = await api(`/api/projects/${state.slug}/apply/bulk`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, data, mode: "append" }),
    });
    // 後端已寫檔 → 重新載入，畫面即時反映（並取消任何待儲存，避免整份覆寫蓋掉剛寫入的資料）
    clearTimeout(state.saveTimer);
    state.dirty = false;
    await reloadProject();
    const what = kind === "episode"
      ? `已新增 ${res.episodeId}「${res.title}」（場次 ${(res.episodeSceneIds || []).join("、")}）`
      : `已新增 ${(res.created || []).length} 個鏡頭（${(res.created || []).join("、")}；no ${(res.nos || []).join("/")}）`;
    box.querySelector(".gen-status").textContent = `✓ ${what}`;
    setSaveStatus("saved", what);
    setTimeout(() => { if (!genState.pending) closeGenModal(); }, 900);
  } catch (e) {
    genShowError(`套用失敗：${e.message}`);
    box.querySelector(".gen-apply").disabled = false;
  } finally {
    genState.pending = false;
    box.querySelector(".gen-apply").textContent = "套用";
  }
}

async function reloadProject() {
  const res = await api(`/api/projects/${state.slug}`);
  state.project = res.project;
  state.dirty = false;
  setSaveStatus("saved", `已載入 ${clockText()}`);
  renderViews();
}

// ── 啟動 ─────────────────────────────────────────────────
function init() {
  document.querySelectorAll(".nav-btn").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view)));
  $("#btn-new-project").addEventListener("click", openNewModal);
  $("#new-cancel").addEventListener("click", closeNewModal);
  $("#new-create").addEventListener("click", createProject);
  $("#new-name").addEventListener("keydown", (e) => { if (e.key === "Enter") createProject(); });
  $("#modal-new").addEventListener("click", (e) => { if (e.target.id === "modal-new") closeNewModal(); });
  window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });
  renderAll();          // 先畫出「尚未選擇專案」與各分頁佔位，避免右側一片空白
  loadPromptRules();    // M12b：三個引擎的官方提示詞規則卡（後端 app/promptrules.py 是唯一真相）
  loadHealth();         // 資料夾實際位置（frozen 時在 exe 旁，不能只寫死 projects/）
  loadProjectList();
  setSaveStatus("idle", "尚未載入");
  bindLightbox();
  refreshGpuStrip();                                         // 右下角 GPU 空閒指示
  setInterval(refreshGpuStrip, 15000);
}

document.addEventListener("DOMContentLoaded", init);
// 燈箱關閉行為（M2a；M10a 修正）
// 注意：app.js 在 <head> 尾端被載入，執行當下 #lightbox 還在 <body> 下方尚未解析，
// 因此不能在此時 getElementById 後直接綁定（會拿不到節點而靜默跳過）。
// 一律改成 document 層事件委派，目標節點存在與否都不影響。
document.addEventListener("click", (e) => {
  const t = e.target;
  if (!t || typeof t.closest !== "function") return;
  const box = document.getElementById("lightbox");
  if (!box || box.classList.contains("hidden")) return;
  // 關閉鈕或其內層、或直接點在燈箱背景（非圖片/說明文字）上 → 關閉
  if (t.closest(".lb-close") || t === box) box.classList.add("hidden");
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const box = document.getElementById("lightbox");
  if (box && !box.classList.contains("hidden")) box.classList.add("hidden");
});
// 頁內確認框（M2a）：刪人物/場景前列出引用明細；回傳 Promise<boolean>
(function svsConfirmModule() {
  function SVSConfirm({ title, refs, okText, plain } = {}) {
    return new Promise((resolve) => {
      const modal = document.getElementById("modal-confirm");
      if (!modal) { resolve(window.confirm(`要刪除「${title}」嗎？`)); return; }
      const tl = modal.querySelector(".cf-title");
      const body = modal.querySelector(".cf-body");
      const okBtn = modal.querySelector(".cf-ok");
      const cancelBtn = modal.querySelector(".cf-cancel");
      // plain=true：非刪除用途（例如一鍵佈署的「覆蓋」確認）。refs 當說明條目原樣列出，
      // 不加「刪除」「目前被 N 處引用」「會清成 null」那套刪除語意。
      tl.textContent = plain ? title : `刪除「${title}」`;
      body.textContent = "";
      okBtn.textContent = okText || "仍要刪除";
      const list = refs || [];
      if (plain) {
        list.forEach((r) => body.append(el("p", { class: "cf-warn", text: r })));
      } else if (list.length === 0) {
        body.append(el("p", { class: "hint", text: "目前沒有任何鏡頭、劇本場次或對白引用它，刪除不會影響其他內容。" }));
      } else {
        body.append(el("p", { class: "cf-warn", text: `目前被 ${list.length} 處引用：` }));
        const ul = el("ul", { class: "cf-list" });
        list.slice(0, 20).forEach((r) => ul.append(el("li", { text: r })));
        if (list.length > 20) ul.append(el("li", { class: "hint", text: `…另有 ${list.length - 20} 處` }));
        body.append(ul);
        body.append(el("p", { class: "hint", text: "按「仍要刪除」後，這些引用會清成 null（鏡頭與劇本不會被刪）。" }));
      }
      const done = (v) => {
        modal.classList.add("hidden");
        modal.style.zIndex = "";           // 還原動態抬升的層級
        window.__svsConfirmOpen = false;
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        document.removeEventListener("keydown", onKey);
        resolve(v);
      };
      const onOk = () => done(true);
      const onCancel = () => done(false);
      const onKey = (e) => { if (e.key === "Escape") done(false); };
      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      document.addEventListener("keydown", onKey);
      // 疊層：確認框必須壓在「目前開著的任何彈窗」之上。
      // #modal-deploy／#modal-gen 是執行時才 append 到 body，DOM 順序在 #modal-confirm 之後，
      // 若只靠 CSS 同一個 z-index，確認框會被蓋住 → 使用者點不到「繼續」，只看到底下的彈窗。
      const others = [...document.querySelectorAll(".modal")]
        .filter((m) => m !== modal && !m.classList.contains("hidden"));
      const topZ = others.reduce((mx, m) => Math.max(mx, Number(getComputedStyle(m).zIndex) || 50), 50);
      modal.style.zIndex = String(topZ + 10);
      window.__svsConfirmOpen = true;
      modal.classList.remove("hidden");
      cancelBtn.focus();
    });
  }
  window.SVSConfirm = SVSConfirm;
})();

// ══════════════════════════════════════════════════════════
// 一鍵佈署：故事 → 角色／場景／劇本／鏡頭（只建文字，圖片全空）
// 流程：彈窗填參數 → POST /deploy 建背景 job → 每 2 秒 poll → 完成摘要
// ══════════════════════════════════════════════════════════
const dpState = { jobId: null, timer: null, pending: false, confirming: false, existing: null, latest: null };

const DP_STEPS = [
  ["cast", "角色卡"], ["locations", "場景卡"], ["script", "劇本"], ["shots", "分鏡"],
];
const DP_STEP_LABEL = { cast: "角色卡", locations: "場景卡", script: "劇本", shots: "分鏡",
  write: "寫入專案", done: "完成", failed: "失敗", queued: "排隊中" };

function ensureDeployModal() {
  if ($("#modal-deploy")) return;
  const box = el("div", { id: "modal-deploy", class: "modal hidden" });
  box.append(el("div", { class: "modal-box dp-box" },
    el("h3", { text: "🚀 一鍵佈署到各頁面" }),
    el("p", { class: "hint dp-llm", text: "" }),
    el("p", { class: "hint dp-hint", text: "" }),
    el("div", { class: "row wrap dp-params" }),
    el("p", { class: "dp-warn hidden" }),
    el("div", { class: "dp-progress hidden" }),
    el("div", { class: "dp-summary hidden" }),
    el("p", { class: "error dp-error hidden" }),
    el("div", { class: "row wrap dp-actions" },
      el("button", { class: "btn btn-primary dp-run", type: "button" }, "開始佈署"),
      el("button", { class: "btn dp-close", type: "button" }, "關閉"),
    ),
  ));
  document.body.append(box);
  box.querySelector(".dp-close").addEventListener("click", closeDeployModal);
  box.querySelector(".dp-run").addEventListener("click", submitDeploy);
  box.addEventListener("click", (e) => {
    // 確認框開著時不要讓「點背景關閉」生效，否則使用者會以為參數窗被確認框吃掉
    if (e.target.id === "modal-deploy" && !dpState.pending && !window.__svsConfirmOpen) closeDeployModal();
  });
}

// 彈窗上方那一行小字：建議用線上模型＋目前 provider
function dpRenderLlmLine(llm) {
  const box = $("#modal-deploy");
  if (!box) return;
  const p = box.querySelector(".dp-llm");
  const prov = (llm && llm.provider) || "local";
  const label = (llm && llm.providerLabel) || ({ online: "線上", custom: "自訂" }[prov] || "本機");
  const model = (llm && llm.model) ? `　模型：${llm.model}` : "";
  p.textContent = "";
  p.append(el("span", { text: "建議使用線上模型（設定 → 模型服務 → 線上）：本機模型可能需重試多次" }));
  p.append(el("br"));
  p.append(el("span", { class: "dp-llm-cur" + (prov === "online" ? " dp-llm-ok" : " dp-llm-warn"),
    text: `目前 provider：${prov}（${label}）${model}${prov === "online" ? "" : "　← 建議切到 online"}` }));
}

function closeDeployModal() {
  if (dpState.pending) return;
  const box = $("#modal-deploy");
  if (box) box.classList.add("hidden");
}

async function openDeployModal() {
  if (!state.slug) { alert("請先選一個專案。"); return; }
  // 先把還沒存的編輯寫回，否則佈署會用舊大綱
  if (state.dirty) await saveNow();
  ensureDeployModal();
  const box = $("#modal-deploy");
  box.querySelector(".dp-progress").classList.add("hidden");
  box.querySelector(".dp-summary").classList.add("hidden");
  box.querySelector(".dp-error").classList.add("hidden");
  box.querySelector(".dp-warn").classList.add("hidden");
  const params = box.querySelector(".dp-params");
  params.textContent = "";
  const c = genNumInput("角色數", 3, 1, 8);
  const s = genNumInput("場景數", 2, 1, 8);
  const n = genNumInput("總鏡數（自動分配到各場）", 6, 1, 24);
  const sec = genNumInput("每鏡秒數", 5, 3, 15, 0.5);
  const owBox = el("input", { type: "checkbox" });
  owBox.checked = true;   // 預設覆蓋：append 會累積重複主題（送單前還會二次確認）
  const owLabel = el("label", { class: "field dp-check" }, owBox, el("span", { text: "覆蓋現有內容（清空角色／場景／劇本／鏡頭重建）" }));
  params.append(c.node, s.node, n.node, sec.node, owLabel);
  dpState.readParams = () => ({
    characterCount: Number(c.input.value) || 3,
    sceneCount: Number(s.input.value) || 2,
    shotCount: Number(n.input.value) || 6,
    shotSeconds: Number(sec.input.value) || 5,
    overwrite: !!owBox.checked,
    dryRun: false,
  });
  dpState.pending = false;
  dpState.confirming = false;
  box.querySelector(".dp-run").disabled = false;
  box.querySelector(".dp-run").textContent = "開始佈署";
  box.querySelector(".dp-close").disabled = false;
  box.classList.remove("hidden");

  // 問後端「已有多少內容」＋目前模型 provider
  const warn = box.querySelector(".dp-warn");
  try {
    const info = await api(`/api/projects/${state.slug}/deploy/info`);
    dpState.existing = info.existing;
    dpRenderLlmLine(info.llm);
    const ex = info.existing || {};
    const bits = [];
    if (ex.characters) bits.push(`${ex.characters} 角色`);
    if (ex.scenes) bits.push(`${ex.scenes} 場景`);
    if (ex.episodes) bits.push(`${ex.episodes} 集劇本`);
    if (ex.shots) bits.push(`${ex.shots} 鏡`);
    box.querySelector(".dp-hint").textContent = info.hasStory
      ? `會用這一頁的故事概念與大綱當骨幹（目前：${String(info.storyHint || "").slice(0, 60)}…）`
      : "故事頁還是空的：請先寫故事概念或大綱。";
    if (bits.length) {
      // 已勾覆蓋 → 預設講「會清空」；取消勾選時講「會接續新增、主題可能重複」
      const countLine = `目前已有 ${bits.join(" / ")}。`;
      const updateWarn = () => {
        warn.textContent = owBox.checked
          ? `⚠ ${countLine}已勾「覆蓋現有內容」：角色／場景／劇本／鏡頭會全部清空重建（舊媒體檔一併移出，不可復原）。`
          : `⚠ ${countLine}不勾「覆蓋現有內容」時會接續新增（角色／場景續編號、劇本多一集），主題可能與舊內容重複；重複佈署會一直累積。`;
        warn.classList.remove("hidden");
      };
      owBox.addEventListener("change", updateWarn);
      updateWarn();
    }
    if (!info.hasStory) box.querySelector(".dp-run").disabled = true;
  } catch (e) {
    dpRenderLlmLine(null);
    box.querySelector(".dp-hint").textContent = `讀取專案現況失敗：${e.message}`;
  }
}

function dpRenderProgress(job) {
  const box = $("#modal-deploy");
  const wrap = box.querySelector(".dp-progress");
  wrap.classList.remove("hidden");
  wrap.textContent = "";
  const cur = job.step || "queued";
  const order = ["cast", "locations", "script", "shots", "write", "done"];
  const curIdx = order.indexOf(cur);
  DP_STEPS.forEach(([key, label], i) => {
    let cls = "dp-step";
    if (job.status === "failed" && job.failedStep === key) cls += " dp-failed";
    else if (curIdx > i || job.status === "done") cls += " dp-ok";
    else if (cur === key) cls += " dp-doing";
    const t = (job.timings || {})[key];
    wrap.append(el("div", { class: cls },
      el("span", { class: "dp-step-name", text: `${i + 1}. ${label}` }),
      el("span", { class: "dp-step-state", text:
        cls.includes("dp-ok") ? (t ? `✓ ${t}s` : "✓")
        : cls.includes("dp-failed") ? "✗ 失敗"
        : cls.includes("dp-doing") ? "進行中…" : "等待" }),
    ));
  });
  const pct = Math.round((job.progress || 0) * 100);
  wrap.append(el("div", { class: "dp-bar" }, el("div", { class: "dp-bar-fill", style: `width:${pct}%` })));
  const wtxt = (() => {
    const w = job.written || {};
    const bits = [w.characters ? `${w.characters} 角色` : "", w.scenes ? `${w.scenes} 場景` : "",
                  w.episodeScenes ? `${w.episodeScenes} 場劇本` : "", w.shots ? `${w.shots} 鏡` : ""].filter(Boolean);
    return bits.length ? `　已寫入：${bits.join("、")}` : "";
  })();
  wrap.append(el("p", { class: "hint", text: `${pct}%　${DP_STEP_LABEL[cur] || cur}${job.elapsed ? `（已用 ${job.elapsed}s）` : ""}${wtxt}` }));
}

async function submitDeploy() {
  if (dpState.pending || !state.slug) return;
  const box = $("#modal-deploy");
  const body = dpState.readParams();
  if (body.overwrite) {
    if (dpState.confirming) return;   // 連按兩下不要開出兩個確認框
    dpState.confirming = true;
    const ex = dpState.existing || {};
    const has = (ex.characters || 0) + (ex.scenes || 0) + (ex.episodes || 0) + (ex.shots || 0) > 0;
    const ok = await (window.SVSConfirm
      ? SVSConfirm({ title: "覆蓋現有內容？（會先清空再重建）", plain: true, okText: "清空並重建", refs: [
        has ? `會永久清空：${ex.characters || 0} 角色、${ex.scenes || 0} 場景、${ex.episodes || 0} 集劇本、${ex.shots || 0} 個鏡頭`
          : "目前沒有舊內容，覆蓋等同直接建立",
        "角色／場景的參考圖、鏡頭的首尾幀與影片檔案也會一併移出（不可復原）",
        "不想清空的話按「取消」，再取消勾選「覆蓋現有內容」改成接續新增",
      ] })
      : Promise.resolve(confirm("會清空角色／場景／劇本／鏡頭（含已生成的圖片與影片）後重建，確定嗎？")));
    dpState.confirming = false;
    if (!ok) return;   // 取消：留在參數視窗，讓使用者改設定再送
  }
  dpState.pending = true;
  box.querySelector(".dp-error").classList.add("hidden");
  box.querySelector(".dp-summary").classList.add("hidden");
  box.querySelector(".dp-warn").classList.add("hidden");
  box.querySelector(".dp-run").disabled = true;
  box.querySelector(".dp-close").disabled = true;
  box.querySelector(".dp-run").textContent = "佈署中…";
  dpRenderProgress({ status: "running", step: "queued", progress: 0.01 });
  try {
    const res = await api(`/api/projects/${state.slug}/deploy`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    dpState.jobId = res.jobId;
    if (res.job) dpRenderProgress(res.job);
    clearInterval(dpState.timer);
    dpState.timer = setInterval(() => pollDeploy(res.jobId), 2000);
  } catch (e) {
    dpDone();
    dpShowError(`送出失敗：${e.message}`);
  }
}

function dpDone() {
  clearInterval(dpState.timer);
  dpState.timer = null;
  dpState.pending = false;
  const box = $("#modal-deploy");
  if (!box) return;
  box.querySelector(".dp-run").disabled = false;
  box.querySelector(".dp-close").disabled = false;
  box.querySelector(".dp-run").textContent = "再佈署一次";
}

function dpShowError(msg) {
  const box = $("#modal-deploy");
  const err = box.querySelector(".dp-error");
  err.textContent = `⚠ ${msg}`;
  err.classList.remove("hidden");
}

async function pollDeploy(jobId) {
  if (!dpState.jobId || dpState.jobId !== jobId) return;
  let j;
  try {
    const res = await api(`/api/projects/${state.slug}/deploy/${jobId}`);
    j = res.job || res;
  } catch (e) {
    dpDone();
    dpShowError(`查進度失敗：${e.message}`);
    return;
  }
  dpRenderProgress(j);
  if (j.status === "done") {
    dpDone();
    dpShowSummary(j);
    clearTimeout(state.saveTimer);
    state.dirty = false;
    await reloadProject();
  } else if (j.status === "failed" || j.status === "cancelled") {
    dpDone();
    const stepLabel = DP_STEP_LABEL[j.failedStep] || j.step || "";
    const w = j.written || {};
    const kept = [w.characters ? `${w.characters} 角色` : "", w.scenes ? `${w.scenes} 場景` : "",
                  w.episodeScenes ? `${w.episodeScenes} 場劇本` : "", w.shots ? `${w.shots} 鏡` : ""]
      .filter(Boolean).join("、");
    const head = j.status === "cancelled" ? "已取消" : `${stepLabel}這一步失敗`;
    // 逐步落地：前面已完成的層級留在專案裡，不是「白做」
    dpShowError(`${head}：${j.error || "原因不明"}`
      + (kept ? `\n已寫入的部分已存進專案（${kept}），可以到各頁直接查看或接續手動編輯。`
              : "\n這一輪還沒寫入任何資料。"));
    clearTimeout(state.saveTimer);
    state.dirty = false;
    await reloadProject();
  }
}

function dpShowSummary(job) {
  const box = $("#modal-deploy");
  const r = job.result || {};
  const wrap = box.querySelector(".dp-summary");
  wrap.classList.remove("hidden");
  wrap.textContent = "";
  wrap.append(el("h4", { text: "✓ 佈署完成" }));
  wrap.append(el("p", { text:
    `已建立 ${r.characters || 0} 位角色、${r.scenes || 0} 個場景、${r.episodes || 0} 集劇本、${r.shots || 0} 個鏡頭（圖片全部待補）` }));
  wrap.append(el("p", { class: "hint", text:
    "角色卡／場景卡／鏡頭的圖片欄位都是空的：可以到各頁上傳自己的圖，或按生成讓模型產圖。"
    + (r.mediaDeleted ? `（舊圖已移出 ${r.mediaDeleted} 個檔案）` : "") }));
  const t = job.timings || {};
  const timeBits = DP_STEPS.map(([k, l]) => `${l} ${t[k] != null ? t[k] + "s" : "—"}`).join("　");
  const plan = (job.params && job.params.shotPlan) || [];
  wrap.append(el("p", { class: "hint", text: `各步耗時：${timeBits}　總計 ${job.elapsed != null ? job.elapsed + "s" : "—"}`
    + (plan.length ? `　（總鏡數分配到各場：${plan.join(" / ")} 鏡）` : "") }));
  const prov = job.params && job.params.provider
    ? `　模型：${job.params.provider}（${job.params.providerLabel || ""}）${job.params.model ? "／" + job.params.model : ""}` : "";
  if (prov) wrap.append(el("p", { class: "hint", text: prov.trim() }));
  const warns = job.warnings || [];
  if (warns.length) {
    const ul = el("ul", { class: "dp-warn-list" });
    warns.slice(0, 6).forEach((w) => ul.append(el("li", { text: w })));
    if (warns.length > 6) ul.append(el("li", { class: "hint", text: `…另有 ${warns.length - 6} 則提醒` }));
    wrap.append(el("p", { class: "hint", text: `提醒 ${warns.length} 則：` }), ul);
  }
  // 四顆跳頁按鈕：關彈窗 → 切到對應分頁（分頁名以 nav 的 data-view 為準）
  const nav = el("div", { class: "row wrap dp-goto" });
  [[ "characters", "前往角色"], ["scenes", "前往場景"], ["script", "前往劇本"], ["shots", "前往分鏡"]].forEach(([view, text]) => {
    nav.append(el("button", { class: "btn btn-small btn-primary dp-go", type: "button", "data-view": view,
      onclick: () => { closeDeployModal(); setView(view); } }, text));
  });
  wrap.append(nav);
}

// 開專案／重新載入時，若有跑到一半或剛完成的佈署，接回進度（F5 不會斷）
async function reattachDeployJob() {
  if (!state.slug) return;
  try {
    const res = await api(`/api/projects/${state.slug}/deploy/latest`);
    const j = res.job;
    if (!j || !["queued", "running", "done", "failed"].includes(j.status)) return;
    dpState.latest = j;
    if (["queued", "running"].includes(j.status)) {
      dpState.jobId = j.jobId;
      ensureDeployModal();
      $("#modal-deploy").classList.remove("hidden");
      dpState.pending = true;
      $("#modal-deploy").querySelector(".dp-run").disabled = true;
      $("#modal-deploy").querySelector(".dp-run").textContent = "佈署中…";
      dpRenderProgress(j);
      clearInterval(dpState.timer);
      dpState.timer = setInterval(() => pollDeploy(j.jobId), 2000);
    }
  } catch (_) { /* 沒有任務或端點不通，靜默 */ }
}
