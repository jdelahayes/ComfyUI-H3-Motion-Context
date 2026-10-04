import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Plan editor for H3 Motion Context Planner.
//
// The plan lives in the node's `plan` widget, hidden here and written as
// JSON when the editor applies. Every number shown (lengths, cuts, errors)
// comes from the /h3_motion_context/plan route, the same Python the node
// runs, so the table and the render cannot disagree. This file only
// draws, edits and stores.

const PLANNER = "MiniMaxH3MotionContextPlanner";
const SAVE = "MiniMaxH3MotionContextSaveLatent";
const LOAD = "MiniMaxH3MotionContextLoadLatent";
const CONTEXT = "MiniMaxH3MotionContext";
const DEFAULT_HEAD = 22;

const CSS = `
.h3p-view{display:flex;flex-direction:column;gap:4px;width:100%;box-sizing:border-box;padding:2px 0 4px;font:12px/1.35 sans-serif;color:var(--input-text,#ddd);}
.h3p-head{font-size:11px;opacity:.85;min-height:14px;}
.h3p-head.err{color:var(--error-text,#ff6b6b);opacity:1;}
.h3p-list{display:flex;flex-direction:column;gap:1px;overflow-y:auto;}
.h3p-item{display:grid;grid-template-columns:16px 22px 150px 1fr 18px;gap:4px;align-items:center;padding:2px 4px;border-radius:3px;cursor:pointer;white-space:nowrap;}
.h3p-item:hover{background:rgba(255,255,255,.07);}
.h3p-item.cur{background:rgba(111,139,189,.22);}
.h3p-item .p{overflow:hidden;text-overflow:ellipsis;opacity:.8;}
.h3p-item .t{font-variant-numeric:tabular-nums;opacity:.9;}
.h3p-ok{color:#6fbf73;}
.h3p-cur{color:#8fb3ff;}
.h3p-btn{cursor:pointer;border:1px solid var(--border-color,#555);background:var(--comfy-input-bg,#2a2a2a);color:var(--input-text,#ddd);border-radius:4px;padding:5px 10px;font:12px sans-serif;}
.h3p-btn:hover{filter:brightness(1.25);}
.h3p-btn.primary{border-color:#6f8bbd;background:#1f2a3a;color:#c5d4ee;}
.h3p-btn.small{padding:2px 6px;min-width:24px;}
.h3p-btn:disabled{opacity:.4;cursor:default;filter:none;}
.h3p-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;font:13px/1.4 sans-serif;color:var(--input-text,#ddd);}
.h3p-dialog{background:var(--comfy-menu-bg,#202020);border:1px solid var(--border-color,#444);border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.5);display:flex;flex-direction:column;max-width:96vw;max-height:94vh;box-sizing:border-box;}
.h3p-dialog.big{width:min(1420px,96vw);height:92vh;}
.h3p-dialog.mid{width:min(860px,94vw);height:min(640px,86vh);}
.h3p-titlebar{display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid var(--border-color,#444);}
.h3p-titlebar h3{margin:0;font-size:14px;font-weight:600;}
.h3p-titlebar .sum{flex:1;font-size:12px;opacity:.85;}
.h3p-titlebar .sum.err{color:var(--error-text,#ff6b6b);opacity:1;}
.h3p-body{flex:1;display:flex;flex-direction:column;gap:8px;padding:10px 14px;min-height:0;}
.h3p-label{font-size:11px;opacity:.7;margin-bottom:2px;}
.h3p-ta{width:100%;box-sizing:border-box;resize:vertical;background:var(--comfy-input-bg,#1a1a1a);color:var(--input-text,#ddd);border:1px solid var(--border-color,#444);border-radius:4px;padding:5px 7px;font:12px/1.4 ui-monospace,Consolas,monospace;}
.h3p-ta:focus,.h3p-in:focus{outline:1px solid #6f8bbd;}
.h3p-in{width:100%;box-sizing:border-box;background:var(--comfy-input-bg,#1a1a1a);color:var(--input-text,#ddd);border:1px solid var(--border-color,#444);border-radius:4px;padding:4px 6px;font:12px ui-monospace,Consolas,monospace;}
.h3p-in.bad,.h3p-row.bad .h3p-ta{border-color:var(--error-text,#ff6b6b);}
.h3p-table{flex:1;overflow:auto;min-height:120px;border:1px solid var(--border-color,#444);border-radius:4px;}
.h3p-item .s{font-size:11px;opacity:.8;}
.h3p-seed{display:flex;gap:3px;}
.h3p-seed .h3p-in{flex:1;min-width:0;}
.h3p-seed .h3p-in.drawn{opacity:.6;font-style:italic;}
.h3p-grid{display:grid;grid-template-columns:44px 104px minmax(240px,1fr) 66px 66px 84px 70px 172px 168px;gap:6px;align-items:start;padding:6px 8px;}
.h3p-thead{position:sticky;top:0;background:var(--comfy-menu-secondary-bg,var(--comfy-menu-bg,#202020));font-size:11px;opacity:.95;border-bottom:1px solid var(--border-color,#444);z-index:1;}
.h3p-thead .h3p-cell{padding-top:0;}
.h3p-row{border-bottom:1px solid rgba(255,255,255,.06);}
.h3p-row.cur{background:rgba(111,139,189,.12);}
.h3p-row.bad{background:rgba(255,80,80,.08);}
.h3p-num{padding-top:5px;font-variant-numeric:tabular-nums;}
.h3p-cell{padding-top:5px;font-variant-numeric:tabular-nums;text-align:right;}
.h3p-acts{display:flex;gap:3px;flex-wrap:wrap;}
.h3p-endrow{display:flex;align-items:center;gap:8px;}
.h3p-endrow .h3p-in{width:110px;}
.h3p-endrow .note{opacity:.75;font-size:12px;}
.h3p-split{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.h3p-foot{display:flex;align-items:center;gap:8px;padding:10px 14px;border-top:1px solid var(--border-color,#444);}
.h3p-foot .hint{flex:1;font-size:11px;opacity:.65;}
.h3p-foot .msg{flex:1;font-size:12px;color:var(--error-text,#ff6b6b);}
`;

let cssOnce = false;
function injectCss() {
  if (cssOnce) return;
  cssOnce = true;
  const s = document.createElement("style");
  s.textContent = CSS;
  document.head.appendChild(s);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function button(label, title, onClick, cls = "") {
  const b = el("button", "h3p-btn " + cls, label);
  if (title) b.title = title;
  b.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    onClick(e);
  };
  return b;
}

function swallow(e) {
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup",
                      "click", "dblclick", "contextmenu", "wheel"]) {
    e.addEventListener(type, (ev) => ev.stopPropagation());
  }
}

// ------------------------------------------------------------ graph lookup

function graphNodes(graph) {
  return graph?._nodes || graph?.nodes || [];
}

function single(cls) {
  const found = graphNodes(app.graph).filter((n) => n.comfyClass === cls);
  return found.length === 1 ? found[0] : null;
}

function widget(node, name) {
  return node?.widgets?.find((w) => w.name === name);
}

// mirrors planner.graph_position: the head is Motion Context's
// context_length, the clip being made is Save Latent's clip_index
function readHead() {
  const v = parseInt(widget(single(CONTEXT), "context_length")?.value, 10);
  return Number.isFinite(v) ? v : DEFAULT_HEAD;
}

function currentClip() {
  const s = single(SAVE);
  if (!s) return null;
  const v = parseInt(widget(s, "clip_index")?.value, 10);
  return Number.isFinite(v) && v > 0 ? v : null;
}

function latentPath() {
  const p = widget(single(LOAD), "latent_path")?.value;
  return p ? p : "h3_context";
}

async function postJson(url, body) {
  try {
    const r = await api.fetchApi(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;
  }
}

// ------------------------------------------------------------ plan helpers

function parseTc(text) {
  const s = String(text ?? "").trim().replace(",", ".");
  const parts = s.split(":");
  if (!s || parts.length > 3) return NaN;
  let total = Number(parts[parts.length - 1]);
  if (!Number.isFinite(total) || total < 0) return NaN;
  for (let i = parts.length - 2, mul = 60; i >= 0; i--, mul *= 60) {
    const w = Number(parts[i]);
    if (!Number.isInteger(w) || w < 0) return NaN;
    total += w * mul;
  }
  return total;
}

function fmtTc(seconds) {
  let ms = Math.round(seconds * 1000);
  const sign = ms < 0 ? "-" : "";
  ms = Math.abs(ms);
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const r = ms % 1000;
  const pad = (v, n) => String(v).padStart(n, "0");
  return h ? `${sign}${h}:${pad(m, 2)}:${pad(s, 2)}.${pad(r, 3)}`
           : `${sign}${m}:${pad(s, 2)}.${pad(r, 3)}`;
}

// seeds: same ranges as planner.MAX_SEED / RANDOM_SEEDS
const MAX_SEED = Number.MAX_SAFE_INTEGER;
const RANDOM_SEEDS = 2 ** 50;

function newSegment(start) {
  return { start, prompt: "", seed: null, random: true };
}

function drawSeed() {
  return Math.floor(Math.random() * RANDOM_SEEDS);
}

// what the seed box holds -> stored value: a number when it is one, the
// raw text otherwise so the server names the bad seed
function seedFromText(text) {
  const t = String(text ?? "").trim();
  if (!t) return null;
  const v = Number(t);
  return /^\d+$/.test(t) && v <= MAX_SEED ? v : t;
}

function emptyPlan() {
  return { prefix: "", suffix: "", end: "0:10",
           segments: [newSegment("0:00")] };
}

function clonePlan(p) {
  return JSON.parse(JSON.stringify(p));
}

// same output as planner.plan_to_text
function planToText(plan) {
  const out = [];
  if (plan.prefix) out.push("[prefix]", plan.prefix);
  if (plan.suffix) out.push("[suffix]", plan.suffix);
  for (const s of plan.segments || []) out.push(`[${s.start}${seedText(s)}]`, s.prompt || "");
  if (plan.end) out.push(`[end ${plan.end}]`);
  return out.join("\n") + "\n";
}

// same output as planner._seed_text
function seedText(s) {
  if (s.seed == null || s.seed === "") return "";
  return s.random !== false ? ` seed=random:${s.seed}` : ` seed=${s.seed}`;
}

function planToJson(plan) {
  return JSON.stringify({ v: 1, prefix: plan.prefix || "",
                          suffix: plan.suffix || "", end: plan.end || "",
                          segments: (plan.segments || []).map((s) => ({
                            start: s.start, prompt: s.prompt || "",
                            seed: s.seed ?? null, random: s.random !== false })) },
                        null, 1);
}

// same output as planner.compose_prompt
function composePrompt(plan, i) {
  return [plan.prefix, plan.segments[i]?.prompt, plan.suffix]
    .map((p) => (p || "").trim()).filter(Boolean).join("\n");
}

function firstLine(text) {
  const line = (text || "").split("\n").find((l) => l.trim());
  return line ? line.trim() : "(no prompt)";
}

// which row or field an error message from compute() is about
function errorTarget(message) {
  const m = /segment (\d+)/.exec(message || "");
  if (m) return { row: parseInt(m[1], 10) - 1 };
  if (/^end\b/.test(message || "")) return { end: true };
  return {};
}

// ------------------------------------------------------------ node state

const planners = new Set();

function planWidget(node) {
  return widget(node, "plan");
}

function hidePlanWidget(node) {
  const w = planWidget(node);
  if (!w) return;
  w.hidden = true;
  w.options = w.options || {};
  w.options.hidden = true;
  w.computeSize = () => [0, -4];
  const dom = w.element || w.inputEl;
  if (dom) dom.style.display = "none";
}

async function refresh(node, checkSlots = true) {
  const st = node._h3p;
  if (!st) return;
  const seq = ++st.seq;
  const head = readHead();
  const res = await postJson("/h3_motion_context/plan",
                             { plan: planWidget(node)?.value || "", head });
  if (seq !== st.seq) return;
  st.res = res;
  st.head = head;
  st.plan = res?.plan || null;
  // read by the Chain node to stop after the last clip
  node._h3plan = { ok: !!res?.ok, count: res?.ok ? res.clips.length : 0 };
  if (checkSlots && st.plan) {
    const n = st.plan.segments.length;
    const path = latentPath();
    const got = await Promise.all(Array.from({ length: n }, (_, i) =>
      postJson("/h3_motion_context/slot_exists",
               { latent_path: path, clip_index: i + 1 })));
    if (seq !== st.seq) return;
    st.done = new Set(got.map((r, i) => (r?.exists ? i + 1 : 0)).filter(Boolean));
  }
  render(node);
}

function render(node) {
  const st = node._h3p;
  if (!st) return;
  const { head, list } = st.view;
  const res = st.res;
  const cur = currentClip();
  st.current = cur;
  list.replaceChildren();
  head.classList.remove("err");
  if (!res) {
    head.textContent = "Plan server route not reachable.";
    head.classList.add("err");
    return;
  }
  const segs = st.plan?.segments || [];
  if (!res.ok) {
    head.textContent = res.error;
    head.classList.add("err");
  } else {
    const n = res.clips.length;
    const end = res.song_offset + res.clips[n - 1].target / res.fps;
    head.textContent = `${n} clip${n === 1 ? "" : "s"} · ${fmtTc(res.song_offset)}`
      + ` → ${fmtTc(end)} · head ${st.head}`
      + (cur ? ` · clip ${Math.min(cur, n)}/${n}` : "")
      + (cur && cur > n ? " (plan complete)" : "");
  }
  segs.forEach((s, i) => {
    const k = i + 1;
    const row = el("div", "h3p-item" + (cur === k ? " cur" : ""));
    const mark = st.done?.has(k)
      ? el("span", "h3p-ok", "✓")
      : el("span", cur === k ? "h3p-cur" : "", cur === k ? "▶" : "·");
    const clip = res.ok ? res.clips[i] : null;
    const span = clip
      ? `${fmtTc(res.song_offset + clip.start / res.fps)} → ${fmtTc(res.song_offset + clip.end / res.fps)}`
      : s.start;
    row.append(mark, el("span", "", String(k)), el("span", "t", span),
               el("span", "p", firstLine(s.prompt)),
               el("span", "s", s.random !== false ? "🎲" : "🔒"));
    row.title = "Edit this clip · seed "
      + (s.random !== false
         ? "random" + (s.seed != null ? ` (last ${s.seed})` : "")
         : s.seed ?? "missing");
    row.onclick = (e) => {
      e.stopPropagation();
      openEditor(node, i);
    };
    list.append(row);
  });
  node.setDirtyCanvas?.(true, true);
}

function commit(node, value) {
  const w = planWidget(node);
  if (!w) return;
  w.value = value;
  w.callback?.(value);
  app.graph?.setDirtyCanvas?.(true, true);
  try {
    app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
  } catch (e) { /* older frontend: the change is still saved with the graph */ }
}

// a random segment's seed is drawn by the node on every run; keep the one
// just used in the plan, so the take can be fixed afterwards
function recordSeed(node, info) {
  const st = node._h3p;
  const k = info?.current;
  const seed = Number(info?.seed);
  if (!st || !info?.random || !Number.isSafeInteger(seed) || !(k >= 1)) return;
  const raw = planWidget(node)?.value || "";
  let plan = null;
  if (raw.trim().startsWith("{")) {
    try { plan = JSON.parse(raw); } catch (e) { plan = null; }
  } else if (st.plan) {
    plan = clonePlan(st.plan);  // text plan: stored as JSON from now on
  }
  const seg = plan?.segments?.[k - 1];
  if (!seg || seg.random === false || seg.seed === seed) return;
  seg.seed = seed;
  commit(node, planToJson(plan));
  if (st.plan?.segments?.[k - 1]) st.plan.segments[k - 1].seed = seed;
  st.editorSeed?.(k - 1, seed);
  render(node);
}

// ------------------------------------------------------------ dialogs

let zTop = 10000;

function modal({ title, size = "mid", onKey, onClose }) {
  injectCss();
  const overlay = el("div", "h3p-overlay");
  overlay.style.zIndex = String(++zTop);
  const dialog = el("div", "h3p-dialog " + size);
  const bar = el("div", "h3p-titlebar");
  const h = el("h3", "", title);
  const sum = el("div", "sum");
  bar.append(h, sum);
  const body = el("div", "h3p-body");
  const foot = el("div", "h3p-foot");
  dialog.append(bar, body, foot);
  overlay.append(dialog);
  swallow(overlay);
  overlay.addEventListener("keydown", (e) => {
    e.stopPropagation();
    onKey?.(e);
  });
  overlay.addEventListener("keyup", (e) => e.stopPropagation());
  document.body.append(overlay);
  return { overlay, dialog, bar, sum, body, foot,
           close: () => { overlay.remove(); onClose?.(); } };
}

function textDialog({ title, value, readOnly, okLabel, onOk, extra }) {
  const m = modal({
    title,
    onKey: (e) => {
      if (e.key === "Escape") m.close();
      else if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && onOk) ok();
    },
  });
  const ta = el("textarea", "h3p-ta");
  ta.style.flex = "1";
  ta.style.resize = "none";
  ta.value = value || "";
  ta.readOnly = !!readOnly;
  ta.spellcheck = false;
  m.body.append(ta);
  const msg = el("div", "msg");
  const ok = () => {
    const err = onOk(ta.value, m);
    if (err instanceof Promise) {
      err.then((e) => { if (e) msg.textContent = e; else m.close(); });
    } else if (err) {
      msg.textContent = err;
    } else {
      m.close();
    }
  };
  extra?.(m, ta, msg);
  m.foot.append(msg);
  m.foot.append(button(onOk ? "Cancel" : "Close", "", () => m.close()));
  if (onOk) m.foot.append(button(okLabel || "OK", "Ctrl+Enter", ok, "primary"));
  setTimeout(() => ta.focus(), 0);
  return m;
}

// ------------------------------------------------------------ the editor

function openEditor(node, focusIndex = null) {
  const st = node._h3p;
  let draft;
  let pendingImport = null;
  if (st.plan) {
    draft = clonePlan(st.plan);
  } else {
    draft = emptyPlan();
    const raw = (planWidget(node)?.value || "").trim();
    // a plan the server could not even parse: open it in the import box
    // with the error, rather than dropping it on the floor
    if (raw && raw !== "[0:00]\n\n[end 0:10]".trim()) pendingImport = raw;
  }
  let initial = planToJson(draft);
  const head = readHead();
  const cur = currentClip();
  const done = st.done || new Set();
  let latest = null;
  let seq = 0;
  let timer = null;

  const m = modal({
    title: "H3 chain plan",
    size: "big",
    onKey: (e) => {
      if (e.key === "Escape") cancel();
      else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) apply();
    },
    onClose: () => { if (st.editorSeed === takeSeed) st.editorSeed = null; },
  });

  // a seed drawn by a run while the editor is open goes into the draft
  // too, and into the baseline so it alone does not count as an edit
  function takeSeed(i, seed) {
    const seg = draft.segments[i];
    if (!seg || seg.random === false) return;
    seg.seed = seed;
    if (rowEls[i]) rowEls[i].seedIn.value = seed;
    const base = JSON.parse(initial);
    if (base.segments[i] && base.segments[i].random !== false) {
      base.segments[i].seed = seed;
      initial = planToJson(base);
    }
  }
  st.editorSeed = takeSeed;

  // prefix / suffix
  const fixes = el("div", "h3p-split");
  const prefixBox = el("div");
  prefixBox.append(el("div", "h3p-label", "Prefix: added before every prompt"));
  const prefixTa = el("textarea", "h3p-ta");
  prefixTa.rows = 3;
  prefixTa.value = draft.prefix;
  prefixTa.oninput = () => { draft.prefix = prefixTa.value; schedule(); };
  prefixBox.append(prefixTa);
  const suffixBox = el("div");
  suffixBox.append(el("div", "h3p-label", "Suffix: added after every prompt"));
  const suffixTa = el("textarea", "h3p-ta");
  suffixTa.rows = 3;
  suffixTa.value = draft.suffix;
  suffixTa.oninput = () => { draft.suffix = suffixTa.value; schedule(); };
  suffixBox.append(suffixTa);
  fixes.append(prefixBox, suffixBox);

  // table
  const table = el("div", "h3p-table");
  const thead = el("div", "h3p-grid h3p-thead");
  for (const [t, tip] of [
    ["#", "✓ generated · ▶ being made (Save Latent clip_index)"],
    ["Start", "Where this segment starts: 62.5, 1:02.5 or 0:01:02.500"],
    ["Prompt", "This segment's prompt. Prefix and suffix are added around it."],
    ["Render", "Frames rendered, pinned head included"],
    ["Keep", "Frames delivered after the Trim node removes the head"],
    ["Cut", "Where the clip really ends"],
    ["Error", "Cut minus the next timecode"],
    ["Seed", "Sampler seed (Planner seed output). 🎲 random: a new seed every run, "
      + "the last one drawn shown in grey. 🔒 fixed: this seed every run. "
      + "Typing a seed fixes it."],
    ["", ""],
  ]) {
    const c = el("div", ["#", "Start", "Prompt", "Seed"].includes(t) ? "" : "h3p-cell", t);
    c.title = tip;
    thead.append(c);
  }
  const rowsBox = el("div");
  table.append(thead, rowsBox);

  // end row
  const endRow = el("div", "h3p-endrow");
  const endIn = el("input", "h3p-in");
  endIn.value = draft.end;
  endIn.title = "Where the last segment ends";
  endIn.oninput = () => { draft.end = endIn.value; schedule(); };
  const addBtn = button("+ Add segment", "Add a segment after the last one; the end moves 10 s later",
                        () => {
    const end = parseTc(draft.end);
    const start = Number.isFinite(end) ? end : 0;
    draft.segments.push(newSegment(fmtTc(start)));
    draft.end = fmtTc(start + 10);
    endIn.value = draft.end;
    buildRows(draft.segments.length - 1);
    schedule(0);
  });
  const tailNote = el("span", "note");
  endRow.append(el("span", "h3p-label", "End"), endIn, addBtn, tailNote);

  m.body.append(fixes, table, endRow);

  // footer
  const hint = el("div", "hint",
    "Edits apply from the next queued clip. Ctrl+Enter applies, Esc cancels.");
  m.foot.append(
    button("Import text", "Replace the plan with plan text", () => openImport()),
    button("Export text", "Copy or save the plan as text", () => openExport()),
    hint,
    button("Cancel", "", () => cancel()),
    button("Apply", "Ctrl+Enter", () => apply(), "primary"));

  let rowEls = [];

  function autosize(ta) {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight + 2, 320) + "px";
  }

  function buildRows(focus = null) {
    rowsBox.replaceChildren();
    rowEls = draft.segments.map((seg, i) => {
      const k = i + 1;
      const row = el("div", "h3p-grid h3p-row" + (cur === k ? " cur" : ""));
      const num = el("div", "h3p-num");
      const mark = done.has(k) ? "✓ " : cur === k ? "▶ " : "";
      num.textContent = mark + k;
      if (done.has(k)) num.classList.add("h3p-ok");
      else if (cur === k) num.classList.add("h3p-cur");
      const start = el("input", "h3p-in");
      start.value = seg.start;
      start.oninput = () => { seg.start = start.value; schedule(); };
      const prompt = el("textarea", "h3p-ta");
      prompt.rows = 2;
      prompt.value = seg.prompt;
      prompt.spellcheck = false;
      prompt.oninput = () => { seg.prompt = prompt.value; autosize(prompt); };
      const gen = el("div", "h3p-cell", "—");
      const del = el("div", "h3p-cell", "—");
      const cut = el("div", "h3p-cell", "—");
      const err = el("div", "h3p-cell", "—");
      const seedBox = el("div", "h3p-seed");
      const seedIn = el("input", "h3p-in");
      seedIn.value = seg.seed ?? "";
      const seedBtn = button("", "", () => {
        if (seg.random !== false) {
          // keep the take: the last drawn seed, or a new one if none yet
          seg.random = false;
          if (seg.seed == null || seg.seed === "") seg.seed = drawSeed();
          seedIn.value = seg.seed;
        } else {
          seg.random = true;
        }
        paintSeed();
        schedule(0);
      }, "small");
      const paintSeed = () => {
        const rnd = seg.random !== false;
        seedBtn.textContent = rnd ? "🎲" : "🔒";
        seedBtn.title = rnd
          ? "Random: a new seed every run. Click to fix the seed shown (keeps that take)."
          : "Fixed: this seed every run. Click to go back to random.";
        seedIn.placeholder = rnd ? "random" : "seed";
        seedIn.title = rnd ? "Last seed drawn for this clip" : "Seed used every run";
        seedIn.classList.toggle("drawn", rnd);
      };
      seedIn.oninput = () => {
        seg.seed = seedFromText(seedIn.value);
        if (seg.seed != null) seg.random = false;
        paintSeed();
        schedule();
      };
      paintSeed();
      seedBox.append(seedIn, seedBtn);
      const acts = el("div", "h3p-acts");
      acts.append(
        button("⤢", "Edit this prompt full size", () => {
          textDialog({
            title: `Clip ${k} prompt`, value: seg.prompt, okLabel: "OK",
            onOk: (v) => { seg.prompt = v; prompt.value = v; autosize(prompt); },
          });
        }, "small"),
        button("👁", "Show the final prompt sent to H3 (prefix + prompt + suffix)", () => {
          textDialog({ title: `Clip ${k}: final prompt`,
                       value: composePrompt(draft, i), readOnly: true });
        }, "small"),
        button("↑", "Swap this prompt with the one above (timecodes stay)", () => swap(i, i - 1), "small"),
        button("↓", "Swap this prompt with the one below (timecodes stay)", () => swap(i, i + 1), "small"),
        button("+", "Insert a segment after this one, halfway to the next cut", () => insertAfter(i), "small"),
        button("✕", "Delete this segment; its time joins the one before", () => remove(i), "small"),
      );
      acts.children[2].disabled = i === 0;
      acts.children[3].disabled = i === draft.segments.length - 1;
      acts.children[5].disabled = draft.segments.length === 1;
      row.append(num, start, prompt, gen, del, cut, err, seedBox, acts);
      rowsBox.append(row);
      return { row, start, prompt, gen, del, cut, err, seedIn };
    });
    requestAnimationFrame(() => {
      rowEls.forEach((r) => autosize(r.prompt));
      if (focus != null && rowEls[focus]) {
        rowEls[focus].row.scrollIntoView({ block: "center" });
        rowEls[focus].prompt.focus();
      }
    });
    paintComputed();
  }

  function swap(a, b) {
    if (b < 0 || b >= draft.segments.length) return;
    const s = draft.segments;
    [s[a].prompt, s[b].prompt] = [s[b].prompt, s[a].prompt];
    rowEls[a].prompt.value = s[a].prompt;
    rowEls[b].prompt.value = s[b].prompt;
    autosize(rowEls[a].prompt);
    autosize(rowEls[b].prompt);
    rowEls[b].prompt.focus();
  }

  function insertAfter(i) {
    const a = parseTc(draft.segments[i].start);
    const next = i + 1 < draft.segments.length
      ? parseTc(draft.segments[i + 1].start) : parseTc(draft.end);
    const mid = Number.isFinite(a) && Number.isFinite(next) ? (a + next) / 2 : a;
    draft.segments.splice(i + 1, 0,
      newSegment(Number.isFinite(mid) ? fmtTc(mid) : ""));
    buildRows(i + 1);
    schedule(0);
  }

  function remove(i) {
    const s = draft.segments;
    if (s.length === 1) return;
    // the piece keeps its start: deleting the first segment hands its
    // start to the next one instead of moving the song offset
    if (i === 0) s[1].start = s[0].start;
    s.splice(i, 1);
    buildRows(Math.max(0, i - 1));
    schedule(0);
  }

  function paintComputed() {
    const res = latest;
    m.sum.classList.remove("err");
    endIn.classList.remove("bad");
    tailNote.textContent = "";
    rowEls.forEach((r) => {
      r.row.classList.remove("bad");
      r.start.classList.remove("bad");
      r.seedIn.classList.remove("bad");
      for (const c of [r.gen, r.del, r.cut, r.err]) c.textContent = "—";
    });
    if (!res) {
      m.sum.textContent = "Computing…";
      return;
    }
    if (!res.ok) {
      m.sum.textContent = res.error || "Plan server route not reachable.";
      m.sum.classList.add("err");
      const t = errorTarget(res.error);
      if (t.row != null && rowEls[t.row]) {
        rowEls[t.row].row.classList.add("bad");
        rowEls[t.row].start.classList.add("bad");
        if (/seed/.test(res.error)) {
          rowEls[t.row].start.classList.remove("bad");
          rowEls[t.row].seedIn.classList.add("bad");
        }
      }
      if (t.end) endIn.classList.add("bad");
      return;
    }
    const fps = res.fps;
    res.clips.forEach((c, i) => {
      const r = rowEls[i];
      if (!r) return;
      r.gen.textContent = String(c.generated);
      r.del.textContent = String(c.delivered);
      r.cut.textContent = fmtTc(res.song_offset + c.end / fps);
      const ms = Math.round(c.error * 1000 / fps);
      r.err.textContent = (ms > 0 ? "+" : "") + ms + " ms";
    });
    const inner = res.clips.slice(0, -1).map((c) => Math.abs(c.error));
    const worst = inner.length ? Math.max(...inner) : 0;
    const n = res.clips.length;
    m.sum.textContent = `${n} clip${n === 1 ? "" : "s"} · head ${head}`
      + (inner.length ? ` · cuts within ±${Math.round(worst * 1000 / fps)} ms` : "");
    if (res.tail_trim) {
      tailNote.textContent = `The last clip runs ${res.tail_trim} frame(s)`
        + ` (${Math.round(res.tail_trim * 1000 / fps)} ms) past the end;`
        + " tail_trim removes them.";
    }
  }

  function schedule(delay = 250) {
    clearTimeout(timer);
    timer = setTimeout(compute, delay);
  }

  async function compute() {
    const mine = ++seq;
    const res = await postJson("/h3_motion_context/plan",
                               { plan: planToJson(draft), head });
    if (mine !== seq) return;
    latest = res || { ok: false, error: "Plan server route not reachable." };
    paintComputed();
    return latest;
  }

  function openImport(text = "", error = "") {
    textDialog({
      title: "Import plan text",
      value: text,
      okLabel: "Replace plan",
      extra: (dm, ta, msg) => {
        msg.textContent = error;
        const file = el("input");
        file.type = "file";
        file.accept = ".txt,.md,text/plain";
        file.style.display = "none";
        file.onchange = async () => {
          const f = file.files?.[0];
          if (f) ta.value = await f.text();
        };
        dm.foot.append(file, button("Open file…", "Load plan text from a file", () => file.click()));
      },
      onOk: async (value) => {
        const res = await postJson("/h3_motion_context/plan", { plan: value, head });
        if (!res) return "Plan server route not reachable.";
        if (!res.plan) return res.error;
        draft = res.plan;
        prefixTa.value = draft.prefix;
        suffixTa.value = draft.suffix;
        endIn.value = draft.end;
        buildRows();
        schedule(0);
        return null;
      },
    });
  }

  function openExport() {
    textDialog({
      title: "Plan text",
      value: planToText(draft),
      readOnly: true,
      extra: (dm, ta, msg) => {
        dm.foot.append(
          button("Copy", "Copy to the clipboard", async () => {
            try {
              await navigator.clipboard.writeText(ta.value);
              msg.style.color = "inherit";
              msg.textContent = "Copied.";
            } catch (e) {
              ta.select();
              msg.textContent = "Select and copy with Ctrl+C.";
            }
          }),
          button("Download", "Save as a .txt file", () => {
            const a = el("a");
            a.href = URL.createObjectURL(new Blob([ta.value], { type: "text/plain" }));
            a.download = "h3_plan.txt";
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 1000);
          }));
      },
    });
  }

  function cancel() {
    if (planToJson(draft) !== initial
        && !confirm("Discard the changes to the plan?")) return;
    m.close();
  }

  async function apply() {
    const value = planToJson(draft);
    if (value === initial) {
      m.close();
      return;
    }
    clearTimeout(timer);
    const res = await compute();
    if (!res?.ok && !confirm(`The plan has an error:\n\n${res?.error}\n\n`
        + "Apply anyway? The Planner will refuse to render until it is fixed.")) {
      return;
    }
    // clips already on disk whose timing the edit moves no longer match
    // the plan, and the Planner refuses to continue from them
    const before = st.res?.ok ? st.res.clips : [];
    const after = res?.ok ? res.clips : [];
    const moved = [...done].sort((a, b) => a - b).filter((k) => {
      const a = before[k - 1];
      const b = after[k - 1];
      return a && (!b || a.start !== b.start || a.generated !== b.generated);
    });
    if (moved.length && !confirm(
        `Clip${moved.length > 1 ? "s" : ""} ${moved.join(", ")} already `
        + `generated will no longer match the plan: the edit moves `
        + `${moved.length > 1 ? "their" : "its"} timing. The Planner will `
        + `refuse to continue from ${moved.length > 1 ? "them until they are" : "it until it is"}`
        + " regenerated.\n\n"
        + "Apply anyway?")) {
      return;
    }
    commit(node, value);
    m.close();
    refresh(node);
  }

  buildRows(focusIndex);
  schedule(0);
  if (pendingImport) {
    openImport(pendingImport, st.res?.error
      || "This plan could not be read. Fix it here and replace.");
  }
}

// ------------------------------------------------------------ registration

function attach(node) {
  if (node._h3p) return;
  injectCss();
  hidePlanWidget(node);
  const root = el("div", "h3p-view");
  const head = el("div", "h3p-head", "Loading plan…");
  const list = el("div", "h3p-list");
  list.style.maxHeight = "260px";
  const edit = button("Edit plan", "Open the plan editor", () => openEditor(node));
  root.append(head, list, edit);
  swallow(root);
  node.addDOMWidget("h3_plan_view", "H3PLAN", root, {
    serialize: false,
    getMinHeight: () => 60 + Math.min(13, node._h3p?.plan?.segments?.length || 1) * 20,
  });
  node._h3p = { view: { head, list }, seq: 0, res: null, plan: null,
                done: new Set(), head: null, current: null };
  planners.add(node);
  const size = node.computeSize?.() || [440, 220];
  node.setSize?.([Math.max(440, size[0]), Math.max(220, size[1])]);
}

// head and clip_index live on other nodes and change without telling us
// (the Chain node writes clip_index directly), so look every so often
setInterval(() => {
  for (const node of planners) {
    if (!node.graph) {
      planners.delete(node);
      continue;
    }
    const st = node._h3p;
    if (readHead() !== st.head && st.res) refresh(node, false);
    else if (currentClip() !== st.current) render(node);
  }
}, 1500);

api.addEventListener("execution_success", () => {
  for (const node of planners) refresh(node);
});

app.registerExtension({
  name: "h3_motion_context.planner",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== PLANNER) return;
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      attach(this);
      setTimeout(() => refresh(this), 0);
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      attach(this);
      hidePlanWidget(this);
      setTimeout(() => refresh(this), 0);
      return r;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (output) {
      const r = onExecuted?.apply(this, arguments);
      recordSeed(this, output?.h3_plan?.[0]);
      return r;
    };
    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      planners.delete(this);
      return onRemoved?.apply(this, arguments);
    };
  },
});
