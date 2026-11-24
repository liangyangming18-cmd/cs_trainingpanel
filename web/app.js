/* ============================================================
   CS2 WORKBENCH — 前端
   ============================================================ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

let S = null;      // 服务端状态
let META = null;   // 模式/地图/游戏模式表
let HEALTH = null;
let PATHS = null;
let CATALOG = null;

const teamNames = { 2: "T 匪徒", 3: "CT 警察" };
const RARITY_ORDER = { covert: 5, classified: 4, restricted: 3, milSpec: 2, industrial: 1 };

const ui = { view: "overview", team: "2", cat: "weapons", id: null, search: "" };
let MODEL = { 2: { weapons: {} }, 3: { weapons: {} } };

/* ---------------- 工具 ---------------- */

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), bad ? 5200 : 3000);
}

/* ---------------- 服务连接守护 ---------------- */

const CONN = { online: true, checking: false, timer: null, manualStop: false };

function paintConn() {
  const bar = $("#conn-bar");
  if (!bar) return;
  bar.classList.toggle("is-on", !CONN.online);
  document.body.classList.toggle("is-offline", !CONN.online);
}

function markOnline() {
  if (CONN.online) return;
  CONN.online = true;
  if (CONN.timer) {
    clearInterval(CONN.timer);
    CONN.timer = null;
  }
  paintConn();
  toast("已重新连上工作台");
  // 重新拉一遍全部状态，页面自动恢复到断线前的样子
  setTimeout(() => boot().catch(() => {}), 60);
}

function markOffline(reason) {
  if (CONN.manualStop) {
    paintConn();
    return;
  }
  if (CONN.online) {
    CONN.online = false;
    paintConn();
    toast(`与工作台服务断开（${reason}），正在自动重连…`, true);
  }
  if (!CONN.timer) CONN.timer = setInterval(pingServer, 2500);
}

async function pingServer() {
  if (CONN.checking) return;
  if (CONN.manualStop) {
    if (CONN.timer) {
      clearInterval(CONN.timer);
      CONN.timer = null;
    }
    return;
  }
  CONN.checking = true;
  try {
    const res = await fetch("/api/ping?t=" + Date.now(), { cache: "no-store" });
    const data = await res.json();
    if (data && data.app === "CS2Workbench") markOnline();
  } catch (_) {
    /* 服务还没起来，继续等下一轮 */
  } finally {
    CONN.checking = false;
  }
}

async function api(path, body, opts = {}) {
  const timeout = opts.timeout || (body === undefined ? 12000 : 90000);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
      cache: "no-store",
    });
    const data = await res.json();
    markOnline();
    if (!data.ok) {
      toast(data.error || "操作失败", true);
      if (data.state) applyState(data.state);
      return null;
    }
    return data;
  } catch (err) {
    const reason = err && err.name === "AbortError" ? "请求超时" : "网络中断";
    markOffline(reason);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function applyState(payload) {
  S = payload.state;
  META = payload.meta;
  HEALTH = payload.health;
  PATHS = payload.paths;
  if (payload.detectedSteamId && !S.steamId) S.steamId = payload.detectedSteamId;
  MODEL = normalizeModel(S.inventory);
  renderAll();
}

function normalizeModel(inv) {
  const out = { 2: { weapons: {} }, 3: { weapons: {} } };
  for (const team of ["2", "3"]) {
    const src = (inv && inv[team]) || {};
    out[team] = {
      weapons: src.weapons && typeof src.weapons === "object" ? { ...src.weapons } : {},
      knife: src.knife || null,
      gloves: src.gloves || null,
    };
  }
  return out;
}

/* ---------------- 加载 ---------------- */

async function boot() {
  const data = await api("/api/state");
  if (!data) return;
  applyState(data);
  CATALOG = await (await fetch("/data/catalog.json")).json();
  renderSkinList();
  if (ui.id) renderInspector();
}

/* ---------------- 渲染总入口 ---------------- */

function renderAll() {
  renderRail();
  renderOverview();
  renderMatch();
  renderDeploy();
  renderClean();
  renderLogs();
  renderActionbar();
  renderSkinsHeader();
  if (CATALOG) renderSkinList();
}

function renderRail() {
  const ok = HEALTH.found && HEALTH.metamod && HEALTH.cssharp;
  const dot = $("#rail-dot");
  dot.className = "dot " + (ok ? (HEALTH.invsim ? "ok" : "warn") : "bad");
  $("#rail-health").textContent = ok
    ? HEALTH.invsim ? "环境就绪" : "缺少换肤插件"
    : HEALTH.found ? "模组未部署" : "未找到 CS2";
  $("#rail-path").textContent = PATHS.csgo || "未定位安装目录";
  $("#top-mode").textContent = (META.modes[S.mode] || {}).label || S.mode;
  $("#top-diff").textContent = (META.difficulty[S.difficulty] || S.difficulty) + " 难度";
}

/* ---------------- 概览 ---------------- */

function renderOverview() {
  const cards = [
    {
      label: "CS2 安装目录",
      value: HEALTH.found ? "已定位" : "未找到",
      sub: PATHS.cs2Root || "请到「部署」页指定",
      state: HEALTH.found ? "ok" : "bad",
    },
    {
      label: "Metamod:Source",
      value: HEALTH.metamod ? "已安装" : "缺失",
      sub: HEALTH.metamod ? "addons/metamod" : "未找到 addons/metamod",
      state: HEALTH.metamod ? "ok" : "bad",
    },
    {
      label: "CounterStrikeSharp",
      value: HEALTH.cssharp ? "v" + (HEALTH.cssharpVersion || "?") : "缺失",
      sub: HEALTH.cssharp ? `${HEALTH.plugins.length} 个插件` : "core 未就绪",
      state: HEALTH.cssharp ? "ok" : "bad",
    },
    {
      label: "换肤插件",
      value: HEALTH.invsim ? "InventorySimulator" : "未安装",
      sub: (S.skinSource || "site") === "site"
        ? "皮肤源 inventory.cstrike.app" + (S.siteCache && S.siteCache.at ? ` · ${S.siteCache.at} 同步` : "")
        : "皮肤源 本地库存" + (S.lastServedAt ? ` · ${S.lastServedAt} 已下发` : ""),
      state: HEALTH.invsim ? ((S.skinSource === "site" || HEALTH.inventories) ? "ok" : "warn") : "bad",
    },
    {
      label: "当前挂载",
      value: HEALTH.gameinfo === "offline" ? "离线全功能" : HEALTH.gameinfo === "skin" ? "仅换肤" : "在线原版",
      sub: HEALTH.gameinfo === "offline" ? "含 botprofile.vpk 覆盖" : HEALTH.gameinfo === "skin" ? "机器人插件已停用" : "官方匹配，无模组",
      state: HEALTH.gameinfo === "online" ? "warn" : "ok",
    },
  ];

  $("#status-grid").innerHTML = cards.map((c) => `
    <div class="status-card">
      <div class="sc-top">
        <span class="sc-label">${esc(c.label)}</span>
        <span class="sc-flag ${c.state}">${c.state === "ok" ? "OK" : c.state === "warn" ? "CHECK" : "MISS"}</span>
      </div>
      <div class="sc-value">${esc(c.value)}</div>
      <div class="sc-sub">${esc(c.sub)}</div>
    </div>`).join("");

  $("#mode-grid").innerHTML = Object.entries(META.modes).map(([key, m], i) => `
    <button class="mode-card ${S.mode === key ? "is-active" : ""}" data-mode="${key}">
      <div class="mc-no">MODE 0${i + 1}</div>
      <div class="mc-title">${esc(m.label)}</div>
      <div class="mc-desc">${esc(m.desc)}</div>
    </button>`).join("");

  const checks = [
    ["CS2 目录已定位", HEALTH.found, PATHS.cs2Root || "—"],
    ["Metamod 已安装", HEALTH.metamod, "addons/metamod"],
    ["gameinfo 已挂载 addons", HEALTH.gameinfo !== "online", "csgo/gameinfo.gi"],
    ["强化 AI 插件在位", HEALTH.plugins.filter((p) => p !== "InventorySimulator").length >= 8, `${HEALTH.plugins.length} 个插件`],
    ["InventorySimulator 已安装", HEALTH.invsim, "plugins/InventorySimulator"],
    [(S.skinSource || "site") === "site" ? "官网库存已同步" : "本地库存已保存",
      (S.skinSource || "site") === "site" ? !!(S.siteCache && S.siteCache.at) : HEALTH.inventories,
      (S.skinSource || "site") === "site" ? "inventory.cstrike.app" : "本地接口 /api/equipped/v5"],
    ["对局配置已写入", HEALTH.cfg, "cfg/wb_match.cfg"],
    ["难度覆盖已就位", !!HEALTH.difficultyVpk, HEALTH.difficultyVpk ? `overrides/botprofile.vpk (${HEALTH.difficultyVpk})` : "未应用"],
  ];
  $("#checklist").innerHTML = checks.map(([text, ok, hint]) => `
    <div class="check ${ok ? "ok" : "bad"}">
      <span class="check-mark">${ok ? "✓" : "·"}</span>
      <span class="check-text">${esc(text)}</span>
      <span class="check-hint">${esc(hint)}</span>
    </div>`).join("");
}

/* ---------------- 对局 ---------------- */

function renderMatch() {
  const m = S.match;
  const botDir = (META && META.botDir) || "";
  const botBtn = $("#open-bot-dir");
  if (botBtn) {
    botBtn.disabled = !botDir;
    botBtn.title = botDir || "未找到 CS2BotImprover 目录";
  }
  const botPath = $("#bot-dir-path");
  if (botPath) botPath.textContent = botDir || "未找到 CS2BotImprover 目录";

  $("#gamemode-row").innerHTML = Object.entries(META.gamemodes).map(([key, g]) => `
    <button class="chip ${m.gamemode === key ? "is-active" : ""}" data-gamemode="${key}">${esc(g.label)}</button>`).join("");

  $("#map-grid").innerHTML = META.maps.map(([id, cn]) => `
    <button class="map-cell ${m.map === id ? "is-active" : ""}" data-map="${id}">
      <b>${esc(id.replace("de_", "").replace("cs_", ""))}</b><span>${esc(cn)}</span>
    </button>`).join("");

  $("#diff-row").innerHTML = Object.entries(META.difficulty).map(([key, label]) => `
    <button class="chip ${S.difficulty === key ? "is-active" : ""}" data-diff="${key}">${esc(label)}</button>`).join("");
  $("#diff-note").textContent = {
    Low: "botprofile.vpk = Low：反应慢、精度低，适合练枪与熟悉地图",
    Medium: "botprofile.vpk = Medium：按 HLTV 数据混合，默认推荐",
    High: "botprofile.vpk = High：极限反应与准度，压枪与预瞄训练",
  }[S.difficulty] || "";

  $$("#aim-row .chip").forEach((b) => b.classList.toggle("is-active", b.dataset.aim === S.aim));
  $$("#nades-row .chip").forEach((b) => b.classList.toggle("is-active", b.dataset.nades === S.nades));

  $("#quota").value = m.botQuota;
  $("#quota-val").textContent = m.botQuota;
  $("#roundtime").value = m.roundTime;
  $("#roundtime-val").textContent = m.roundTime;
  $("#freezetime").value = m.freezeTime;
  $("#freezetime-val").textContent = m.freezeTime;
  $("#startmoney").value = m.startMoney;
  $("#startmoney-val").textContent = m.startMoney;
  $("#c4timer").value = m.c4timer ?? 40;
  $("#c4timer-val").textContent = m.c4timer ?? 40;
  $("#extraargs").value = m.extraArgs || "";
  $("#ff").classList.toggle("is-on", !!m.friendlyFire);
  $("#automap").classList.toggle("is-on", !!m.autoMap);
  $("#applycmds").classList.toggle("is-on", !!m.applyPluginCmds);

  renderBotSync();
}

function renderBotSync() {
  const m = S.match;
  const presets = (META && META.presets) || {};
  const loadouts = (META && META.loadouts) || {};
  const row = $("#preset-row");
  if (!row) return;

  row.innerHTML = Object.entries(presets).map(([key, p]) => `
    <button class="chip ${m.preset === key ? "is-active" : ""}" data-preset="${key}">${esc(p.label)}</button>`).join("");
  $("#preset-note").textContent = (presets[m.preset] || {}).desc || "";

  $("#loadout-row").innerHTML = Object.entries(loadouts).map(([key, l]) => `
    <button class="chip ${m.loadout === key ? "is-active" : ""}" data-loadout="${key}">${esc(l.label)}</button>`).join("");

  $("#ecolimit").value = m.ecoLimit ?? 2800;
  $("#ecolimit-val").textContent = m.ecoLimit ?? 2800;
  $("#botnades").classList.toggle("is-on", !!m.botGrenades);

  const info = (META && META.botcfg) || {};
  const sync = (META && META.botSync) || {};
  const dot = $("#sync-dot");
  const ok = !!info.exists;
  if (dot) dot.className = "sync-dot" + (ok ? " is-on" : " is-off");
  $("#sync-text").textContent = ok
    ? (sync.at
        ? `与插件 cfg 已同步（${sync.at} · ${sync.newline === "CRLF" ? "CRLF" : "LF"}）`
        : `当前生效：${(presets[m.preset] || {}).label || m.preset}，点「写入插件配置」落盘`)
    : "还没找到插件的预设 cfg，先点「安装 / 更新模组」";
  $("#sync-path").textContent = info.source || "—";
}

function collectMatch() {
  return {
    gamemode: S.match.gamemode,
    map: S.match.map,
    botQuota: Number($("#quota").value),
    roundTime: Number($("#roundtime").value),
    freezeTime: Number($("#freezetime").value),
    startMoney: Number($("#startmoney").value),
    c4timer: Number($("#c4timer").value),
    ecoLimit: Number($("#ecolimit").value),
    preset: S.match.preset,
    loadout: S.match.loadout,
    botGrenades: $("#botnades").classList.contains("is-on"),
    extraArgs: $("#extraargs").value.trim(),
    friendlyFire: $("#ff").classList.contains("is-on"),
    autoMap: $("#automap").classList.contains("is-on"),
    applyPluginCmds: $("#applycmds").classList.contains("is-on"),
    botDifficulty: S.match.botDifficulty,
  };
}

/* ---------------- 换肤 ---------------- */

function weaponSkinLabel(weapon, entry) {
  if (!entry || !entry.paint) return "";
  const paint = weapon.paints.find((p) => p.kit === Number(entry.paint));
  if (!paint) return "#" + entry.paint;
  return paint.name ? paint.name.split(" ")[0] : "#" + paint.kit;
}

function renderSkinsHeader() {
  $$("#team-seg .seg-item").forEach((b) => b.classList.toggle("is-active", b.dataset.team === ui.team));
  $$("#cat-seg .seg-item").forEach((b) => b.classList.toggle("is-active", b.dataset.cat === ui.cat));
  const sid = $("#steamid");
  if (document.activeElement !== sid) sid.value = S.steamId || "";
  const count = countItems();
  $("#skin-count").textContent = count + " 件";
  renderSource();
}

const SOURCE_SITE = "https://inventory.cstrike.app";

function renderSource() {
  const site = (S.skinSource || "site") === "site";
  $$("#source-seg .seg-item").forEach((b) =>
    b.classList.toggle("is-active", (b.dataset.source === "site") === site));

  $("#skin-lead").textContent = site
    ? "皮肤直接取自 inventory.cstrike.app 上你账号已保存的库存，进游戏自动加载"
    : "使用工作台本地库存，完全离线；保存后游戏内 !ws 立即生效";

  const cache = S.siteCache || {};
  const c = cache.counts || {};
  const total = (c.t || 0) + (c.ct || 0) + (c.knife || 0) + (c.gloves || 0);
  $("#site-state").textContent = total
    ? `${cache.at || ""} 同步 · T ${c.t} / CT ${c.ct} / 刀 ${c.knife} / 手套 ${c.gloves}`
    : "尚未从官网拉取";

  const hint = $("#save-hint");
  if (site) {
    hint.innerHTML = `游戏里读的是官网库存，这里保存只作本地备份 · <a href="${SOURCE_SITE}" target="_blank" rel="noreferrer">去网站改皮肤</a>`;
  } else {
    hint.innerHTML = "保存后游戏内 <code>!ws</code> 立即生效 · 期间保持工作台运行";
  }
  $("#skin-save").textContent = site ? "另存为本地库存" : "保存到下局库存";
}

/* ---------------- 清理 / 还原 ---------------- */

function renderClean() {
  const L = HEALTH.ledger || { count: 0, sizeMb: 0, groups: [] };
  const clean = HEALTH.clean || { clean: true, issues: [] };
  const modded = L.owned > 0 || HEALTH.gameinfo !== "online";

  $("#clean-title").textContent = clean.clean
    ? "当前是官方原版状态，可以安全进在线匹配"
    : modded
      ? "当前装有模组，先清理再打官方匹配"
      : "检测到残留文件，建议清理";
  $("#clean-title").className = "cs-main" + (clean.clean ? "" : " is-bad");
  $("#clean-sub").textContent = clean.clean
    ? "gameinfo.gi 未被修改，addons / overrides 均不存在"
    : clean.issues.length
      ? clean.issues.slice(0, 3).join(" · ") + (clean.issues.length > 3 ? ` · 等 ${clean.issues.length} 项` : "")
      : "—";

  const stats = [
    ["台账文件", L.count, "个"],
    ["占用空间", L.sizeMb, "MB"],
    ["被改写", L.modifiedCount || 0, "个"],
    ["部署时间", L.installedAt ? L.installedAt.slice(5) : "—", ""],
  ];
  $("#clean-stats").innerHTML = stats.map(([k, v, u]) => `
    <div class="clean-stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}${u ? `<small> ${esc(u)}</small>` : ""}</div></div>`).join("");

  $("#clean-count").textContent = `${L.count} 个文件 / ${L.sizeMb} MB`;
  const rows = (L.groups || []).map((g) => `
    <div class="file-row ok">
      <span class="check-mark">−</span>
      <span class="p">${esc(g.path)}</span>
      <span class="v">${g.files} 个 · ${g.sizeMb} MB</span>
    </div>`).join("");
  const mods = (L.modified || []).map((rel) => `
    <div class="file-row ok">
      <span class="check-mark">↺</span>
      <span class="p">${esc(rel)}</span>
      <span class="v">将还原</span>
    </div>`).join("");
  $("#clean-list").innerHTML = rows + mods ||
    `<div class="empty" style="color:var(--mute)">台账为空，说明最近没有部署记录</div>`;
}

/* ---------------- 日志 ---------------- */

function countItems() {
  let n = 0;
  for (const team of ["2", "3"]) {
    const t = MODEL[team];
    n += Object.keys(t.weapons || {}).length;
    if (t.knife) n += 1;
    if (t.gloves) n += 1;
  }
  return n;
}

function renderSkinList() {
  if (!CATALOG) return;
  const q = ui.search.trim().toLowerCase();
  const team = MODEL[ui.team];
  let html = "";

  if (ui.cat === "weapons") {
    const list = CATALOG.weapons.filter((w) => {
      if (!q) return true;
      const label = `${w.nameCn} ${w.nameEn} ${w.def}`.toLowerCase();
      if (label.includes(q)) return true;
      return w.paints.some((p) => (p.name && p.name.toLowerCase().includes(q)) || String(p.kit) === q);
    });
    html = list.map((w) => {
      const entry = team.weapons[String(w.def)];
      const label = weaponSkinLabel(w, entry);
      return `<div class="skin-row ${ui.id === "w" + w.def ? "is-sel" : ""}" data-kind="w" data-def="${w.def}">
        <div><div class="sr-name">${esc(w.nameCn)}</div><div class="sr-sub">${w.def} · ${esc(w.nameEn)}</div></div>
        <div class="sr-right"><div class="sr-skin ${label ? "" : "is-empty"}">${esc(label || "未设置")}</div>
        <div class="sr-kit">${entry ? "#" + entry.paint : "—"}</div></div>
      </div>`;
    }).join("");
  } else if (ui.cat === "knife") {
    html = CATALOG.knives.map((k) => {
      const entry = team.knife && team.knife.def === k.def ? team.knife : null;
      const fin = entry ? k.finishes.find((f) => f.kit === Number(entry.paint)) : null;
      return `<div class="skin-row ${ui.id === "k" + k.def ? "is-sel" : ""}" data-kind="k" data-def="${k.def}">
        <div><div class="sr-name">${esc(k.nameCn)}</div><div class="sr-sub">${k.def} · ${esc(k.nameEn)}</div></div>
        <div class="sr-right"><div class="sr-skin ${fin ? "" : "is-empty"}">${esc(fin ? fin.name : entry ? "#" + entry.paint : "未设置")}</div>
        <div class="sr-kit">${entry ? "#" + entry.paint : "—"}</div></div>
      </div>`;
    }).join("");
  } else {
    html = CATALOG.gloves.map((g) => {
      const entry = team.gloves && team.gloves.def === g.def ? team.gloves : null;
      const fin = entry ? g.finishes.find((f) => f.kit === Number(entry.paint)) : null;
      return `<div class="skin-row ${ui.id === "g" + g.def ? "is-sel" : ""}" data-kind="g" data-def="${g.def}">
        <div><div class="sr-name">${esc(g.nameCn)}</div><div class="sr-sub">${g.def} · ${esc(g.nameEn)}</div></div>
        <div class="sr-right"><div class="sr-skin ${fin ? "" : "is-empty"}">${esc(fin ? fin.name : entry ? "#" + entry.paint : "默认手套")}</div>
        <div class="sr-kit">${entry ? "#" + entry.paint : "—"}</div></div>
      </div>`;
    }).join("");
  }
  $("#skin-list").innerHTML = html || `<div class="empty" style="padding:40px;text-align:center;color:var(--mute)">没有匹配的物品</div>`;
  renderInspector();
}

function currentEntry() {
  const team = MODEL[ui.team];
  if (!ui.id) return null;
  const kind = ui.id[0];
  const def = Number(ui.id.slice(1));
  if (kind === "w") return { kind, def, entry: team.weapons[String(def)] || null, target: team.weapons };
  if (kind === "k") return { kind, def, entry: team.knife && team.knife.def === def ? team.knife : null, setter: (v) => (team.knife = v) };
  return { kind, def, entry: team.gloves && team.gloves.def === def ? team.gloves : null, setter: (v) => (team.gloves = v) };
}

function paintListFor(ctx) {
  if (ctx.kind === "w") {
    const w = CATALOG.weapons.find((x) => x.def === ctx.def);
    return { title: w.nameCn, sub: `DEF ${w.def} · ${w.nameEn}`, options: w.paints.map((p) => ({ kit: p.kit, name: p.name, rarity: p.rarityCn, min: p.wearMin, max: p.wearMax })) };
  }
  if (ctx.kind === "k") {
    const k = CATALOG.knives.find((x) => x.def === ctx.def);
    return { title: k.nameCn, sub: `DEF ${k.def} · ${k.nameEn}`, options: k.finishes.map((f) => ({ kit: f.kit, name: f.name, rarity: "", min: f.wearMin, max: f.wearMax })) };
  }
  const g = CATALOG.gloves.find((x) => x.def === ctx.def);
  return { title: g.nameCn, sub: `DEF ${g.def} · ${g.nameEn}`, options: g.finishes.map((f) => ({ kit: f.kit, name: f.name, rarity: "", min: f.wearMin, max: f.wearMax })) };
}

function condition(wear) {
  if (wear < 0.07) return "崭新出厂 FN";
  if (wear < 0.15) return "略有磨损 MW";
  if (wear < 0.38) return "久经沙场 FT";
  if (wear < 0.45) return "破损不堪 WW";
  return "战痕累累 BS";
}

function renderInspector() {
  const box = $("#inspector");
  const ctx = currentEntry();
  if (!ctx || !CATALOG) {
    box.innerHTML = `<div class="empty">从左侧选择一个物品</div>`;
    return;
  }
  const info = paintListFor(ctx);
  const entry = ctx.entry || {};
  const opts = info.options.map((o) => {
    const label = `#${o.kit}${o.name ? " · " + o.name : ""}${o.rarity ? " · " + o.rarity : ""}`;
    return `<option value="${o.kit}" ${Number(entry.paint) === o.kit ? "selected" : ""}>${esc(label)}</option>`;
  }).join("");
  const selected = info.options.find((o) => o.kit === Number(entry.paint)) || info.options[0];
  const wear = entry.wear !== undefined && entry.wear !== null ? Number(entry.wear) : Math.min(selected.max, selected.min + 0.03);

  box.innerHTML = `
    <div class="insp-head">
      <div><div class="insp-title">${esc(info.title)}</div><div class="insp-def">${esc(info.sub)}</div></div>
      <button class="icon-btn" id="insp-remove">移除</button>
    </div>
    <div class="insp-fields">
      <div class="field">
        <label>${ctx.kind === "w" ? "皮肤" : ctx.kind === "k" ? "刀型涂装" : "手套配色"} <span class="hint mono">${info.options.length} 种</span></label>
        <select class="input mono" id="insp-paint">
          <option value="">— 不设置 —</option>
          ${opts}
        </select>
      </div>
      <div class="field">
        <label>磨损 <span class="hint mono" id="insp-wear-val">${wear.toFixed(3)}</span></label>
        <input type="range" id="insp-wear" min="${selected.min}" max="${selected.max}" step="0.001" value="${wear}">
        <div class="wear-line"><span>FN</span><span id="insp-cond">${condition(wear)}</span><span>BS</span></div>
      </div>
      ${ctx.kind === "w" ? `
      <div class="field">
        <label>StatTrak <span class="hint">留空则不启用</span></label>
        <input class="input mono" id="insp-st" type="number" min="0" placeholder="0" value="${entry.stattrak ?? ""}">
      </div>
      <div class="field">
        <label>图案模板 Seed</label>
        <input class="input mono" id="insp-seed" type="number" min="0" max="1000" placeholder="0" value="${entry.seed ?? ""}">
      </div>
      <div class="field">
        <label>改名标签</label>
        <input class="input" id="insp-name" maxlength="20" placeholder="20 字符以内" value="${esc(entry.nametag || "")}">
      </div>` : ""}
    </div>`;

  $("#insp-paint").addEventListener("change", (ev) => {
    const kit = Number(ev.target.value);
    if (!kit) {
      removeEntry(ctx);
      return;
    }
    const opt = info.options.find((o) => o.kit === kit);
    const next = { paint: kit, wear: Math.min(opt.max, Math.max(opt.min, Number($("#insp-wear").value || opt.min))) };
    if (ctx.kind === "w" && ctx.entry) Object.assign(next, ctx.entry);
    next.paint = kit;
    writeEntry(ctx, next);
  });

  $("#insp-wear").addEventListener("input", (ev) => {
    const v = Number(ev.target.value);
    $("#insp-wear-val").textContent = v.toFixed(3);
    $("#insp-cond").textContent = condition(v);
    if (ctx.entry) ctx.entry.wear = v;
  });
  $("#insp-wear").addEventListener("change", () => {
    renderSkinList();
  });

  const st = $("#insp-st");
  if (st) st.addEventListener("change", () => {
    const el = currentEntry();
    if (!el.entry) return;
    el.entry.stattrak = st.value === "" ? null : Number(st.value);
    renderSkinList();
  });
  const seed = $("#insp-seed");
  if (seed) seed.addEventListener("change", () => {
    const el = currentEntry();
    if (!el.entry) return;
    el.entry.seed = seed.value === "" ? null : Number(seed.value);
  });
  const name = $("#insp-name");
  if (name) name.addEventListener("change", () => {
    const el = currentEntry();
    if (!el.entry) return;
    el.entry.nametag = name.value.trim() || null;
    renderSkinList();
  });
  $("#insp-remove").addEventListener("click", () => removeEntry(ctx));
}

function writeEntry(ctx, value) {
  if (ctx.kind === "w") {
    MODEL[ui.team].weapons[String(ctx.def)] = value;
  } else {
    ctx.setter(value);
  }
  renderSkinList();
  renderSkinsHeader();
}

function removeEntry(ctx) {
  if (ctx.kind === "w") delete MODEL[ui.team].weapons[String(ctx.def)];
  else ctx.setter(null);
  renderSkinList();
  renderSkinsHeader();
}

/* ---------------- 部署 ---------------- */

function renderDeploy() {
  const input = $("#cs2root");
  if (document.activeElement !== input) input.value = PATHS.cs2Root || "";
  $("#path-note").textContent = HEALTH.found
    ? `已识别：${PATHS.csgo}`
    : "未找到 CS2，请手动填写到 Counter-Strike Global Offensive 这一层";

  const rows = [
    ["Metamod", HEALTH.metamod, "addons/metamod"],
    ["CounterStrikeSharp", HEALTH.cssharp, "v" + (HEALTH.cssharpVersion || "?")],
    ["强化 AI 插件", HEALTH.plugins.length >= 8, HEALTH.plugins.filter((p) => p !== "InventorySimulator").join(" / ")],
    ["InventorySimulator", HEALTH.invsim, "plugins/InventorySimulator"],
    ["gamedata", HEALTH.invsim, "gamedata/inventory-simulator.json"],
    ["gameinfo.gi", HEALTH.gameinfo !== "online", HEALTH.gameinfo === "offline" ? "含 botprofile 覆盖" : HEALTH.gameinfo === "skin" ? "仅挂载 metamod" : "官方原版"],
    ["难度覆盖 vpk", !!HEALTH.difficultyVpk, HEALTH.difficultyVpk || "未应用"],
    ["库存来源", true, (S.skinSource || "site") === "site" ? "inventory.cstrike.app" : "本地接口"],
    ["wb_match.cfg", HEALTH.cfg, "cfg/wb_match.cfg"],
  ];
  $("#file-list").innerHTML = rows.map(([name, ok, hint]) => `
    <div class="file-row ${ok ? "ok" : ""}">
      <span class="check-mark">${ok ? "✓" : "·"}</span>
      <span class="p">${esc(name)}</span>
      <span class="v">${esc(hint)}</span>
    </div>`).join("");

  $("#backup-note").textContent = HEALTH.found
    ? `原始 gameinfo.gi 已备份到 ${"CS2-Workbench\\backup\\gameinfo.original.gi"}（仅首次部署时生成）`
    : "—";

  renderPersist();
}

function renderPersist() {
  const p = (META && META.persist) || {};
  const stateEl = $("#persist-state");
  const noteEl = $("#persist-note");
  const actions = $("#persist-actions");
  if (!stateEl || !actions) return;

  let label = "未设置常驻";
  let note = "现在只在你双击 exe 时运行，宿主进程一退出服务就会停。";
  let buttons = [
    ["install", "设为常驻运行", "btn-primary"],
    ["startup", "打开启动文件夹", "btn-ghost"],
  ];

  if (p.installed) {
    label = "已设为常驻 · 登录自启";
    note = "启动文件夹里已有 CS2 Workbench.lnk，登录 Windows 后自动在后台运行（由 explorer 拉起，不挂在任何宿主进程下）。";
    buttons = [
      ["startup", "打开启动文件夹", "btn-ghost"],
      ["remove", "取消常驻", "btn-ghost"],
    ];
  }

  stateEl.textContent = label;
  stateEl.classList.toggle("is-on", !!p.installed);
  noteEl.textContent = note;
  actions.innerHTML = buttons.map(([action, text, cls]) => `
    <button class="btn ${cls}" data-persist="${action}">${esc(text)}</button>`).join("");
}

/* ---------------- 日志 ---------------- */

function renderLogs() {
  const logs = S.log || [];
  $("#log-list").innerHTML = logs.length
    ? logs.map((l) => `<div class="log-line ${l.level === "error" ? "error" : ""}">
        <span class="log-t">${esc(l.t)}</span>
        <span class="log-lv ${l.level === "error" ? "error" : ""}">${l.level === "error" ? "ERROR" : "INFO"}</span>
        <span class="log-msg">${esc(l.msg)}</span>
      </div>`).join("")
    : `<div style="color:var(--mute);padding:10px 0">暂无记录</div>`;
}

/* ---------------- 底部条 ---------------- */

function renderActionbar() {
  const modeLabel = (META.modes[S.mode] || {}).label || S.mode;
  $("#ab-mode").textContent = modeLabel;
  const m = S.match;
  $("#ab-meta").textContent = `${m.map} · ${(META.gamemodes[m.gamemode] || {}).label || m.gamemode} · ${META.difficulty[S.difficulty] || S.difficulty} · -insecure`;
  const btn = $("#ab-launch");
  const online = S.mode === "online";
  btn.disabled = online;
  btn.textContent = online ? "先切到离线模式" : "启动 CS2";
  btn.title = online ? "在线原版模式不加载模组，请先在概览页切换模式" : "";
}

/* ---------------- 事件 ---------------- */

function switchView(name) {
  ui.view = name;
  $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === "view-" + name));
  const active = $(".nav-item.is-active");
  if (active) $("#crumb-view").textContent = active.textContent.replace(/^\d+/, "").trim();
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
}

function bind() {
  $("#conn-retry").addEventListener("click", () => {
    if (CONN.timer) {
      clearInterval(CONN.timer);
      CONN.timer = null;
    }
    pingServer();
  });

  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => switchView(btn.dataset.view)));
  const initial = (location.hash || "").replace("#", "");
  if (["overview", "match", "skins", "deploy", "clean", "logs"].includes(initial)) switchView(initial);
  const item = new URLSearchParams(location.search).get("item");
  if (item) ui.id = item;

  $("#mode-grid").addEventListener("click", async (ev) => {
    const card = ev.target.closest("[data-mode]");
    if (!card) return;
    const mode = card.dataset.mode;
    if (mode === S.mode) return;
    const data = await api("/api/mode", { mode });
    if (data) {
      applyState(data.state);
      toast("已切换：" + (META.modes[mode] || {}).label);
    }
  });

  // 对局
  $("#open-bot-dir").addEventListener("click", async () => {
    const data = await api("/api/open", { target: "bot" });
    if (data) toast("已在资源管理器中打开 CS2BotImprover");
  });

  $("#gamemode-row").addEventListener("click", (ev) => {
    const chip = ev.target.closest("[data-gamemode]");
    if (!chip) return;
    S.match.gamemode = chip.dataset.gamemode;
    renderMatch();
    renderActionbar();
  });
  $("#map-grid").addEventListener("click", (ev) => {
    const cell = ev.target.closest("[data-map]");
    if (!cell) return;
    S.match.map = cell.dataset.map;
    renderMatch();
    renderActionbar();
  });
  $("#diff-row").addEventListener("click", async (ev) => {
    const chip = ev.target.closest("[data-diff]");
    if (!chip) return;
    const data = await api("/api/difficulty", { level: chip.dataset.diff });
    if (data) {
      applyState(data.state);
      toast("难度已切换，下次启动生效");
    }
  });
  $("#aim-row").addEventListener("click", (ev) => {
    const chip = ev.target.closest("[data-aim]");
    if (!chip) return;
    S.aim = chip.dataset.aim;
    renderMatch();
  });
  $("#nades-row").addEventListener("click", (ev) => {
    const chip = ev.target.closest("[data-nades]");
    if (!chip) return;
    S.nades = chip.dataset.nades;
    renderMatch();
  });

  $("#quota").addEventListener("input", (ev) => ($("#quota-val").textContent = ev.target.value));
  $("#roundtime").addEventListener("input", (ev) => ($("#roundtime-val").textContent = Number(ev.target.value).toFixed(2)));
  $("#freezetime").addEventListener("input", (ev) => ($("#freezetime-val").textContent = ev.target.value));
  $("#startmoney").addEventListener("input", (ev) => ($("#startmoney-val").textContent = ev.target.value));
  $("#c4timer").addEventListener("input", (ev) => ($("#c4timer-val").textContent = ev.target.value));
  $("#ecolimit").addEventListener("input", (ev) => ($("#ecolimit-val").textContent = ev.target.value));
  $("#botnades").addEventListener("click", () => $("#botnades").classList.toggle("is-on"));

  // 插件 cfg 同步（预设 / 机器人物品 / 经济）
  async function pushBotCfg(patch, msg) {
    const data = await api("/api/botcfg", { action: "save", ...patch });
    if (!data) return null;
    applyState(data.state);
    if (msg) toast(msg);
    return data;
  }
  $("#preset-row").addEventListener("click", (ev) => {
    const chip = ev.target.closest("[data-preset]");
    if (!chip || chip.dataset.preset === S.match.preset) return;
    pushBotCfg({ preset: chip.dataset.preset }, "已切换预设并写回插件 cfg");
  });
  $("#loadout-row").addEventListener("click", (ev) => {
    const chip = ev.target.closest("[data-loadout]");
    if (!chip || chip.dataset.loadout === S.match.loadout) return;
    pushBotCfg({ loadout: chip.dataset.loadout }, "机器人装备已同步到 bot_loadout");
  });
  $("#botcfg-save").addEventListener("click", () => {
    pushBotCfg({
      preset: S.match.preset,
      loadout: S.match.loadout,
      botGrenades: $("#botnades").classList.contains("is-on"),
      ecoLimit: Number($("#ecolimit").value),
      c4timer: Number($("#c4timer").value),
    }, "已写入 CS2BotImprover 的 cfg");
  });
  $("#botcfg-read").addEventListener("click", async () => {
    const data = await api("/api/botcfg", { action: "read" });
    if (!data) return;
    applyState(data.state);
    toast("已从插件 cfg 读回当前设置");
  });
  $("#open-bot-cfg").addEventListener("click", () => api("/api/open", { target: "botcfgdir" }));

  ["#ff", "#automap", "#applycmds"].forEach((id) =>
    $(id).addEventListener("click", () => $(id).classList.toggle("is-on")));

  async function saveMatch(silent) {
    const data = await api("/api/match", { match: collectMatch(), aim: S.aim, nades: S.nades });
    if (!data) return null;
    applyState(data.state);
    if (!silent) toast("配置已写入 cfg/wb_match.cfg");
    return data;
  }
  $("#match-save").addEventListener("click", () => saveMatch(false));
  $("#match-preview").addEventListener("click", async () => {
    const data = await saveMatch(true);
    if (!data) return;
    const text = [
      "// cfg/wb_match.cfg（启动时执行）",
      ...(data.result && data.result.cfg ? data.result.cfg : []),
    ].join("\n");
    showDialog("生成的配置", text || "配置已写入，请在 game/csgo/cfg/wb_match.cfg 查看");
  });

  $("#ab-cfg").addEventListener("click", () => saveMatch(false));

  // 启动
  async function launch() {
    const btn = $("#ab-launch");
    btn.disabled = true;
    const data = await api("/api/launch", { match: collectMatch(), useSteam: true });
    btn.disabled = false;
    if (data) toast("已发送启动命令，等待 CS2 加载模组…");
  }
  $("#ab-launch").addEventListener("click", launch);

  // 换肤
  $("#team-seg").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-team]");
    if (!btn) return;
    ui.team = btn.dataset.team;
    ui.id = null;
    renderSkinsHeader();
    renderSkinList();
  });
  $("#cat-seg").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-cat]");
    if (!btn) return;
    ui.cat = btn.dataset.cat;
    ui.id = null;
    renderSkinsHeader();
    renderSkinList();
  });
  $("#skin-search").addEventListener("input", (ev) => {
    ui.search = ev.target.value;
    renderSkinList();
  });
  $("#skin-list").addEventListener("click", (ev) => {
    const row = ev.target.closest("[data-kind]");
    if (!row) return;
    ui.id = row.dataset.kind + row.dataset.def;
    renderSkinList();
  });
  $("#skin-random").addEventListener("click", async () => {
    const data = await api("/api/preset", { preset: { rarity: "restricted" } });
    if (!data) return;
    MODEL = normalizeModel(data.result.inventory);
    S.inventory = data.result.inventory;
    renderSkinList();
    renderSkinsHeader();
    toast("已生成随机套装，记得保存");
  });
  $("#skin-clear").addEventListener("click", () => {
    MODEL = { 2: { weapons: {} }, 3: { weapons: {} } };
    ui.id = null;
    renderSkinList();
    renderSkinsHeader();
    toast("已清空（未保存）");
  });
  $("#skin-save").addEventListener("click", async () => {
    const steamId = $("#steamid").value.trim();
    const site = (S.skinSource || "site") === "site";
    const data = await api(site ? "/api/site/save-local" : "/api/inventory", { inventory: MODEL, steamId });
    if (data) {
      applyState(data.state);
      toast(site
        ? "已存为本地库存，并切到「本地工作台」来源"
        : `已保存 ${data.result.counts.t + data.result.counts.ct} 件武器皮肤`);
    }
  });

  // 皮肤来源
  $("#source-seg").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-source]");
    if (!btn) return;
    const source = btn.dataset.source;
    if (source === (S.skinSource || "site")) return;
    const data = await api("/api/source", { source });
    if (data) {
      applyState(data.state);
      toast(source === "site" ? "已切到官网库存，进游戏按 !ws 刷新" : "已切到本地库存");
    }
  });
  $("#site-pull").addEventListener("click", async () => {
    const btn = $("#site-pull");
    btn.disabled = true;
    const steamId = $("#steamid").value.trim() || S.steamId;
    const data = await api("/api/site/pull", { steamId });
    btn.disabled = false;
    if (!data) return;
    MODEL = normalizeModel(data.result.inventory);
    applyState(data.state);
    const c = data.result.counts;
    toast(`已同步官网库存：T ${c.t} / CT ${c.ct} / 刀 ${c.knife} / 手套 ${c.gloves}`);
  });
  $("#site-open").addEventListener("click", () => api("/api/site/open", {}));

  // 清理 / 还原
  $("#clean-run").addEventListener("click", async () => {
    if (!confirm("确定要删除 game/csgo 下所有模组文件并恢复官方 gameinfo.gi 吗？\n\n· 游戏本体文件不会被删除\n· 建议先关闭 CS2\n· 之后可随时「重新导入」")) return;
    const btn = $("#clean-run");
    btn.disabled = true;
    const data = await api("/api/uninstall", {});
    btn.disabled = false;
    if (!data) return;
    const r = data.result;
    $("#clean-result").innerHTML = `
      <div class="ok-line">已删除 ${r.removedFiles} 个模组文件，释放约 ${r.freedMb} MB</div>
      <div>游戏本体文件：还原 ${r.restoredFiles} 个（cfg / gameinfo.gi）</div>
      <div>gameinfo.gi：${r.restoredGameinfo ? "已还原为官方版本" : "未找到备份，请用 Steam 验证完整性"}</div>
      ${r.leftovers.length ? `<ul>${r.leftovers.map((x) => `<li>仍存在 ${esc(x)}</li>`).join("")}</ul>` : "<div>没有残留目录。</div>"}
      <div>现在可以从 Steam 正常启动 CS2，启动项里不要再带 -insecure。</div>`;
    applyState(data.state);
    toast("已清理完成，可以正常进在线对局");
  });
  $("#clean-verify").addEventListener("click", async () => {
    const data = await api("/api/verify", {});
    if (!data) return;
    const r = data.result;
    $("#clean-result").innerHTML = r.clean
      ? `<div class="ok-line">校验通过：没有模组残留，gameinfo.gi 未被修改。</div>`
      : `<div>发现 ${r.issues.length} 处残留：</div><ul>${r.issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    applyState(data.state);
  });
  $("#clean-reinstall").addEventListener("click", async () => {
    const btn = $("#clean-reinstall");
    btn.disabled = true;
    let data = await api("/api/install", {});
    if (data) data = await api("/api/mode", { mode: "offline" });
    btn.disabled = false;
    if (!data) return;
    $("#clean-result").innerHTML = `<div class="ok-line">已重新导入模组并切到「离线人机」，可以启动 CS2 练枪了。</div>`;
    applyState(data.state);
    toast("模组已重新导入");
  });
  $("#clean-open-csgo").addEventListener("click", () => api("/api/open", { target: "csgo" }));

  $("#ab-quit").addEventListener("click", async () => {
    const data = await api("/api/shutdown", {});
    // 主动退出：不再自动重连，直接告诉用户可以关页面
    CONN.manualStop = true;
    CONN.online = false;
    if (CONN.timer) {
      clearInterval(CONN.timer);
      CONN.timer = null;
    }
    paintConn();
    $("#conn-text").textContent = "工作台已退出";
    const persistOn = !!(META && META.persist && META.persist.installed);
    showDialog("工作台已退出",
      "本地服务已停止，可以直接关闭此页面。\n" +
      (persistOn
        ? "注意：常驻已开启，下次登录 Windows 仍会自动在后台启动；\n要彻底停用，请先在部署页点「取消常驻」。"
        : "下次双击 CS2Workbench.exe 即可重新打开。"));
    if (!data) toast("服务已停止", false);
  });

  // 部署
  $("#path-save").addEventListener("click", async () => {
    const data = await api("/api/path", { cs2Root: $("#cs2root").value.trim() });
    if (data) {
      applyState(data.state);
      toast("路径已更新");
    }
  });
  $("#path-detect").addEventListener("click", async () => {
    const data = await api("/api/detect", {});
    if (data) {
      applyState(data.state);
      toast("已重新探测");
    }
  });
  $("#deploy-install").addEventListener("click", async () => {
    const btn = $("#deploy-install");
    btn.disabled = true;
    const data = await api("/api/install", {});
    btn.disabled = false;
    if (data) {
      applyState(data.state);
      toast(`部署完成，共写入 ${data.result.copied} 个文件`);
    }
  });
  $("#deploy-repair").addEventListener("click", async () => {
    const btn = $("#deploy-repair");
    btn.disabled = true;
    const data = await api("/api/repair", {});
    btn.disabled = false;
    if (!data) return;
    applyState(data.state);
    const r = data.result || {};
    const fixed = r.fixed || [];
    const missing = r.missing || [];
    if (fixed.length) {
      showDialog("自检完成，已自动修复", "· " + fixed.join("\n· "));
      toast(`自检修复 ${fixed.length} 项`);
    } else if (missing.length) {
      showDialog("自检发现问题", "· " + missing.join("\n· ") + "\n\n先点「安装 / 更新模组」补文件。");
    } else {
      showDialog("自检通过", "gameinfo 挂载、插件目录、难度 vpk、插件 cfg 全部正常。");
      toast("环境正常");
    }
  });
  $("#deploy-online").addEventListener("click", async () => {
    const data = await api("/api/mode", { mode: "online" });
    if (data) {
      applyState(data.state);
      toast("已恢复在线模式，记得从 Steam 启动项移除 -insecure");
    }
  });
  $("#open-csgo").addEventListener("click", () => api("/api/open", { target: "csgo" }));
  $("#deploy-clean").addEventListener("click", () => switchView("clean"));
  $("#open-inv").addEventListener("click", () => api("/api/open", { target: "inventory" }));

  $("#make-shortcut").addEventListener("click", async () => {
    const data = await api("/api/shortcut", {});
    if (data) toast("桌面已生成「CS2 人机对局」快捷方式");
  });

  $("#persist-actions").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-persist]");
    if (!btn) return;
    const action = btn.dataset.persist;
    if (action === "startup") {
      const data = await api("/api/open", { target: "startup" });
      if (data) toast("已打开启动文件夹");
      return;
    }
    btn.disabled = true;
    const data = await api("/api/persist", { action });
    btn.disabled = false;
    if (!data) return;
    applyState(data.state);
    if (action === "install") {
      showDialog("已设为常驻运行",
        "1. 启动文件夹已放入 CS2 Workbench.lnk，登录 Windows 后自动在后台启动。\n" +
        "2. 工作台正在把服务交给独立进程接管，页面会短暂断一下再自动连上。\n\n" +
        "之后关掉终端、关掉任何宿主程序，服务都不会停。");
      toast("常驻已开启，服务正在交接…");
      return;
    }
    toast("已取消常驻（开机不再自动启动）");
  });

  $("#log-clear").addEventListener("click", async () => {
    const data = await api("/api/log/clear", {});
    if (data) applyState(data.state);
  });

  $("#dialog-close").addEventListener("click", () => $("#overlay").classList.remove("is-open"));
  $("#overlay").addEventListener("click", (ev) => {
    if (ev.target.id === "overlay") $("#overlay").classList.remove("is-open");
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") $("#overlay").classList.remove("is-open");
  });
}

function showDialog(title, body) {
  $("#dialog-title").textContent = title;
  $("#dialog-body").textContent = body;
  $("#overlay").classList.add("is-open");
}

bind();
boot();
