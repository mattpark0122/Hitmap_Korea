"use strict";
// 한국 증시 히트맵 — data/{market}/latest.json · YYYYMMDD.json · dates.json 을 읽어 그린다.

const MARKETS = { kospi: { name: "코스피", label: "KOSPI" }, kosdaq: { name: "코스닥", label: "KOSDAQ" } };
const $ = id => document.getElementById(id);
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const state = { market: "kospi", date: "latest", data: null, resolvedDate: null, news: null, ranked: [], openSector: null };
const cache = new Map();          // url -> Promise<json>
let loadSeq = 0;

// ---------- 저장된 보기 설정 (실패해도 무시) ----------
const PREF_KEY = "hitmap-korea:prefs";
function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
    for (const id of ["sort", "nsec", "topn", "range"]) {
      if (p[id] && [...$(id).options].some(o => o.value === p[id])) $(id).value = p[id];
    }
  } catch { /* ignore */ }
}
function savePrefs() {
  try { localStorage.setItem(PREF_KEY, JSON.stringify({ sort: $("sort").value, nsec: $("nsec").value, topn: $("topn").value, range: $("range").value })); }
  catch { /* ignore */ }
}

// ---------- 포맷 ----------
const fmtCap = eok => {
  if (eok >= 10000) { const jo = Math.floor(eok / 10000), r = Math.round(eok % 10000); return jo.toLocaleString() + "조" + (r ? " " + r.toLocaleString() + "억" : ""); }
  return Math.round(eok).toLocaleString() + "억";
};
const sign = c => (c > 0 ? "+" : c < 0 ? "−" : "");
const fmtChg = c => (c == null ? "—" : sign(c) + Math.abs(c).toFixed(2) + "%");
const cls = c => (c > 0 ? "upc" : c < 0 ? "dn" : "");
const WD = ["일", "월", "화", "수", "목", "금", "토"];
const fmtDate = s => {
  const d = new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8));
  return `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6, 8)} (${WD[d.getDay()]})`;
};
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- 주소(#kospi, #kosdaq/20261105) ----------
function parseHash() {
  const [m, d] = location.hash.replace(/^#/, "").toLowerCase().split("/");
  return { market: MARKETS[m] ? m : "kospi", date: /^\d{8}$/.test(d || "") ? d : "latest" };
}
function writeHash() {
  const h = "#" + state.market + (state.date !== "latest" ? "/" + state.date : "");
  if (location.hash !== h) history.replaceState(null, "", h);
}

// ---------- 데이터 ----------
function getJSON(url) {
  if (!cache.has(url)) {
    cache.set(url, fetch(url, { cache: "no-cache" }).then(r => {
      if (!r.ok) throw new Error(`${url} (${r.status})`);
      return r.json();
    }).catch(e => { cache.delete(url); throw e; }));
  }
  return cache.get(url);
}
async function getDates(market) {
  try { return (await getJSON(`data/${market}/dates.json`)).dates || []; } catch { return []; }
}

function showNote(html, err) {
  const n = $("note");
  n.hidden = !html; n.innerHTML = html || ""; n.classList.toggle("err", !!err);
}

async function load() {
  const seq = ++loadSeq;
  const { market } = state;
  syncMarketUI();
  const dates = await getDates(market);
  if (seq !== loadSeq) return;
  fillDateSelect(dates);

  // 선택 날짜가 이 시장에 없으면 그 이전 가장 가까운 날짜로
  let file = "latest", note = "";
  if (state.date !== "latest") {
    if (dates.includes(state.date)) file = state.date;
    else {
      const prev = dates.find(d => d < state.date);
      file = prev || "latest";
      note = `${MARKETS[market].name}에는 ${fmtDate(state.date)} 데이터가 없어 ` +
        (prev ? `직전 날짜 ${fmtDate(prev)}를 보여드려요.` : "최신 데이터를 보여드려요.");
    }
  }
  try {
    const data = await getJSON(`data/${market}/${file}.json`);
    if (seq !== loadSeq) return;
    state.data = data;
    state.resolvedDate = data.date;
    // 섹터 기사는 별도 파일. 없어도 히트맵은 그대로 그린다.
    state.news = await getJSON(`data/${market}/news/${data.date}.json`).catch(() => null);
    if (seq !== loadSeq) return;
    showNote(note);
    renderHeader();
    render();
    if (state.openSector && !state.ranked.some(s => s.name === state.openSector)) closeNews();
    else renderNews();
  } catch (e) {
    if (seq !== loadSeq) return;
    state.data = null; state.news = null; closeNews();
    $("map").innerHTML = ""; $("map").style.height = "200px";
    $("idx").innerHTML = ""; $("shown").textContent = ""; $("chips").innerHTML = ""; $("stats").textContent = "";
    $("stamp").textContent = "데이터 없음";
    const local = location.protocol === "file:";
    showNote(local
      ? "브라우저 보안 때문에 파일을 직접 열면 데이터를 읽지 못해요. 프로젝트 폴더에서 <code>python -m http.server 8000</code> 실행 후 <code>http://localhost:8000</code>으로 열어 주세요."
      : `데이터를 불러오지 못했어요 (${esc(e.message)}). <code>python scripts/collect.py</code>로 먼저 수집해 주세요.`, true);
  }
}

function fillDateSelect(dates) {
  const sel = $("date");
  const opts = [`<option value="latest">최신${dates[0] ? " · " + fmtDate(dates[0]) : ""}</option>`];
  for (const d of dates) opts.push(`<option value="${d}">${fmtDate(d)}</option>`);
  // 다른 시장에서 고른 날짜가 이 시장에 없더라도 선택은 유지한다
  if (state.date !== "latest" && !dates.includes(state.date))
    opts.push(`<option value="${state.date}">${fmtDate(state.date)} (없음)</option>`);
  sel.innerHTML = opts.join("");
  sel.value = state.date;
}

function syncMarketUI() {
  for (const b of document.querySelectorAll(".mkt button"))
    b.setAttribute("aria-pressed", String(b.dataset.mkt === state.market));
  $("h1").textContent = MARKETS[state.market].name + " 히트맵";
  document.title = MARKETS[state.market].name + " 히트맵";
  $("map").setAttribute("aria-label", MARKETS[state.market].name + " 섹터별 시가총액 트리맵");
}

// ---------- 계산 ----------
function marketStats(stocks) {
  const known = stocks.filter(s => s.chg != null);
  const cap = d3.sum(known, s => s.cap);
  return {
    count: stocks.length,
    up: known.filter(s => s.chg > 0).length,
    down: known.filter(s => s.chg < 0).length,
    flat: known.filter(s => s.chg === 0).length,
    wavg: cap ? d3.sum(known, s => s.cap * s.chg) / cap : null,
    cap: d3.sum(stocks, s => s.cap),
  };
}

// 섹터 정렬 기준. 등락률은 섹터 전체 종목의 시총가중 평균이고, 값이 없는 섹터는 맨 뒤로 보낸다.
// 상승률 순은 등락률 내림차순이라 오른 섹터가 하나도 없으면 자연히 덜 떨어진 섹터부터 나온다(하락률 순도 대칭).
// 그때는 none 문구로 그 사실을 알려 준다.
const SORTS = {
  move: { label: "변동폭", key: s => (s.avg == null ? -Infinity : Math.abs(s.avg)) },
  up: { label: "상승률", key: s => (s.avg == null ? -Infinity : s.avg), hit: s => s.avg > 0, none: "오른 섹터가 없어 덜 떨어진 순" },
  down: { label: "하락률", key: s => (s.avg == null ? -Infinity : -s.avg), hit: s => s.avg < 0, none: "내린 섹터가 없어 덜 오른 순" },
};
const sortMode = () => SORTS[$("sort").value] || SORTS.move;

// 섹터 전체 종목 기준으로 시총가중 평균 등락률을 계산해 고른 기준으로 순위를 매긴다.
// 상위 N개 종목으로 자르기 전에 계산하므로 '섹터별 종목 수'를 바꿔도 순위가 변하지 않는다.
function rankSectors(stocks, mode) {
  const groups = d3.group(stocks.filter(s => s.cap > 0), s => s.sector || "기타");
  const out = [];
  for (const [name, list] of groups) {
    const known = list.filter(s => s.chg != null);
    const kcap = d3.sum(known, s => s.cap);
    const avg = kcap ? d3.sum(known, s => s.cap * s.chg) / kcap : null;
    out.push({ name, stocks: list.sort((a, b) => b.cap - a.cap), avg, cap: d3.sum(list, s => s.cap) });
  }
  return out.sort((a, b) => mode.key(b) - mode.key(a) || b.cap - a.cap);
}

// ---------- 색 ----------
function colorFn() {
  const R = +$("range").value, iu = d3.interpolateRgb(css("--flat"), css("--up")), id = d3.interpolateRgb(css("--flat"), css("--down"));
  return c => { const e = Math.pow(Math.min(1, Math.abs(c) / R), 0.8); return c >= 0 ? iu(e) : id(e); };
}
function updateLegend() {
  const R = +$("range").value, col = colorFn(), stops = [];
  for (let i = 0; i <= 10; i++) stops.push(col(-R + (2 * R * i) / 10) + " " + i * 10 + "%");
  $("grad").style.background = "linear-gradient(90deg," + stops.join(",") + ")";
  $("lgL").textContent = "−" + R + "%"; $("lgR").textContent = "+" + R + "%";
}

// ---------- 렌더 ----------
function renderHeader() {
  const d = state.data, ix = d.index || {}, st = marketStats(d.stocks), mk = MARKETS[state.market];
  $("stamp").textContent = `${fmtDate(d.date)} 종가 · ${st.count.toLocaleString()}종목`;
  const arrow = ix.change > 0 ? "▲" : ix.change < 0 ? "▼" : "";
  $("idx").innerHTML =
    `<div><span class="k">${mk.label}</span><span class="v">${ix.close != null ? ix.close.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}</span>` +
    `<span class="c ${cls(ix.change)}">${ix.change != null ? arrow + Math.abs(ix.change).toFixed(2) + " " : ""}${fmtChg(ix.change_pct)}</span></div>` +
    `<div><span class="k">상승 / 하락</span><span class="v"><span class="upc">${st.up}</span> / <span class="dn">${st.down}</span></span><span class="c" style="color:var(--muted)">보합 ${st.flat}</span></div>` +
    `<div><span class="k">시총가중 평균</span><span class="v ${cls(st.wavg)}">${fmtChg(st.wavg)}</span><span class="c" style="color:var(--muted)">시총 ${fmtCap(st.cap)}</span></div>`;
}

function render() {
  const el = $("map");
  if (!state.data) return;
  const W = el.clientWidth; if (!W) return;
  const H = W < 560 ? Math.round(W * 1.5) : Math.round(Math.max(460, Math.min(800, W * 0.6)));
  el.style.height = H + "px";
  el.innerHTML = "";

  const mode = sortMode();
  const ranked = rankSectors(state.data.stocks, mode);
  const nsecV = $("nsec").value, K = nsecV === "all" ? ranked.length : Math.min(+nsecV, ranked.length);
  const N = +$("topn").value;
  const shown = ranked.slice(0, K);
  const rankOf = new Map(ranked.map((s, i) => [s.name, i + 1]));
  state.ranked = ranked;

  // 선택된 섹터만, 각 섹터는 시총 상위 N개(종목이 N개보다 적으면 있는 만큼). 배치는 시총 크기 기준.
  const root = d3.hierarchy({ children: shown.map(s => ({ name: s.name, avg: s.avg, children: s.stocks.slice(0, N) })) })
    .sum(d => (d.children ? 0 : d.cap)).sort((a, b) => b.value - a.value);
  d3.treemap().size([W, H]).tile(d3.treemapSquarify.ratio(1.2))
    .paddingOuter(d => (d.depth === 0 ? 0 : 2)).paddingTop(d => (d.depth === 1 ? 17 : 0)).paddingInner(1).round(true)(root);

  const col = colorFn(), frag = document.createDocumentFragment();
  for (const s of root.children || []) {
    const w = s.x1 - s.x0; if (w < 24) continue;
    const h = document.createElement("div"); h.className = "sec" + (s.data.name === state.openSector ? " active" : "");
    h.dataset.sec = s.data.name; h.tabIndex = 0; h.setAttribute("role", "button");
    Object.assign(h.style, { left: s.x0 + "px", top: s.y0 + "px", width: w + "px" });
    h.innerHTML = `<span class="nm">${esc(s.data.name)}</span>` +
      (w >= 72 ? `<span class="pc ${cls(s.data.avg)}">${fmtChg(s.data.avg)}</span>` : "");
    h.title = `${s.data.name} ${fmtChg(s.data.avg)} · ${mode.label} ${rankOf.get(s.data.name)}위 · 눌러서 관련 기사 보기`;
    frag.appendChild(h);
  }
  for (const leaf of root.leaves()) {
    const d = leaf.data, w = leaf.x1 - leaf.x0, h = leaf.y1 - leaf.y0;
    if (w <= 0 || h <= 0) continue;
    const t = document.createElement("div"); t.className = "tile" + (d.chg == null ? " nodata" : "");
    Object.assign(t.style, { left: leaf.x0 + "px", top: leaf.y0 + "px", width: w + "px", height: h + "px" });
    if (d.chg != null) t.style.background = col(d.chg);
    const fs = Math.max(9, Math.min(26, Math.sqrt(w * h) / 6.5));
    if (w > 34 && h > 18) {
      const n = document.createElement("div"); n.className = "n"; n.style.fontSize = fs + "px"; n.textContent = d.name; t.appendChild(n);
      if (h > fs * 2.4) { const p = document.createElement("div"); p.className = "p"; p.style.fontSize = Math.max(9, fs * 0.72) + "px"; p.textContent = fmtChg(d.chg); t.appendChild(p); }
    }
    t._d = { ...d, secAvg: leaf.parent.data.avg, secRank: rankOf.get(leaf.parent.data.name) };
    frag.appendChild(t);
  }
  el.appendChild(frag);

  // 표시 범위 안내
  const hidden = ranked.length - K;
  const noHit = mode.hit && ranked.length && !ranked.some(mode.hit);
  const order = noHit ? mode.none : `${mode.label} 순`;
  $("shown").innerHTML = nsecV === "all" || hidden === 0
    ? `전체 <b>${ranked.length}</b>개 섹터 표시 중${noHit ? ` <span class="h">· ${order}</span>` : ""}`
    : `${order}으로 상위 <b>${K}</b>개 섹터 표시 중 <span class="h">· ${hidden}개 섹터 숨김</span>`;
  $("chips").innerHTML = shown.map((s, i) => {
    const n = newsFor(s.name);
    return `<li><button type="button" data-sec="${esc(s.name)}" class="${s.name === state.openSector ? "active" : ""}" title="${esc(s.name)} 관련 기사 보기">` +
      `<span class="r">${i + 1}</span>${esc(s.name)}<span class="pc ${cls(s.avg)}">${fmtChg(s.avg)}</span>` +
      (n ? `<span class="nc">기사 ${n.length}</span>` : "") + `</button></li>`;
  }).join("");
  const leaves = root.leaves();
  const shownCap = d3.sum(leaves, l => l.data.cap), allCap = d3.sum(state.data.stocks, s => s.cap);
  $("stats").innerHTML =
    `<span>표시 <b>${leaves.length.toLocaleString()}</b> / ${state.data.stocks.length.toLocaleString()}종목</span>` +
    `<span>표시 시총 <b>${fmtCap(shownCap)}</b> (${allCap ? ((shownCap / allCap) * 100).toFixed(1) : 0}%)</span>` +
    `<span>섹터별 상위 <b>${N}</b>종목</span>` +
    `<span>업종: ${esc(sectorSourceLabel(state.data.source))}</span>`;
}

function sectorSourceLabel(src) {
  const s = (src && src.sectors) || {};
  const names = { krx: "KRX 업종분류", naver: "네이버 업종", "preferred-parent": "보통주 업종", reference: "참고 매핑", unclassified: "미분류" };
  const parts = Object.entries(s).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${names[k] || k} ${v}`);
  return parts.join(", ") || "—";
}

// ---------- 섹터 기사 패널 ----------
const newsFor = name => (state.news && state.news.sectors ? state.news.sectors[name] || [] : null);
const fmtDT = s => `${s.slice(4, 6)}.${s.slice(6, 8)} ${s.slice(8, 10)}:${s.slice(10, 12)}`;

function openNews(name) {
  state.openSector = name;
  renderNews();
  markActive();
  $("newsClose").focus({ preventScroll: true });
}
function closeNews() {
  if (!state.openSector) return;
  state.openSector = null;
  $("news").hidden = true;
  markActive();
}
function markActive() {
  for (const e of document.querySelectorAll(".sec[data-sec], .chips button[data-sec]"))
    e.classList.toggle("active", e.dataset.sec === state.openSector);
}
function renderNews() {
  const name = state.openSector;
  if (!name || !state.data) { $("news").hidden = true; return; }
  const idx = state.ranked.findIndex(s => s.name === name), sec = state.ranked[idx];
  if (!sec) { closeNews(); return; }
  $("newsTitle").innerHTML = `${esc(name)}<span class="pc ${cls(sec.avg)}">${fmtChg(sec.avg)}</span>`;
  $("newsMeta").textContent = `${MARKETS[state.market].name} · ${fmtDate(state.data.date)} · ${sortMode().label} ${idx + 1}위 / ${state.ranked.length} · ${sec.stocks.length}종목`;
  // 시총 상위 + 크게 움직인 종목
  const top = sec.stocks.slice(0, 3);
  const movers = sec.stocks.filter(s => s.chg != null && !top.includes(s)).sort((a, b) => Math.abs(b.chg) - Math.abs(a.chg)).slice(0, 2);
  $("newsStocks").innerHTML = [...top, ...movers].map(s => `<span>${esc(s.name)}<b class="${cls(s.chg)}">${fmtChg(s.chg)}</b></span>`).join("");
  const items = newsFor(name);
  $("newsList").innerHTML = items == null
    ? `<li class="empty">이 날짜(${fmtDate(state.data.date)})의 기사 데이터가 없어요. <code>python scripts/news.py</code>로 수집할 수 있어요.</li>`
    : !items.length
      ? `<li class="empty">이 섹터의 관련 기사를 찾지 못했어요. 대표 종목 뉴스 중 시황·공시 모음 기사는 제외했어요.</li>`
      : items.map(a => `<li><a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">` +
          `<span class="t">${esc(a.title)}</span>` +
          `<span class="m">${esc(a.office || "")} · ${fmtDT(a.dt)}${a.stocks && a.stocks.length ? " · " + a.stocks.map(esc).join(", ") : ""}</span></a></li>`).join("");
  $("newsList").scrollTop = 0;
  $("news").hidden = false;
}
$("newsClose").addEventListener("click", closeNews);
addEventListener("keydown", e => { if (e.key === "Escape") closeNews(); });
$("map").addEventListener("click", e => { const h = e.target.closest(".sec[data-sec]"); if (h) openNews(h.dataset.sec); });
$("map").addEventListener("keydown", e => {
  const h = e.target.closest(".sec[data-sec]");
  if (h && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openNews(h.dataset.sec); }
});
$("chips").addEventListener("click", e => { const b = e.target.closest("button[data-sec]"); if (b) openNews(b.dataset.sec); });

// ---------- 툴팁 ----------
const tip = $("tip");
function showTip(e) {
  const t = e.target.closest(".tile"); if (!t) { tip.hidden = true; return; }
  const d = t._d;
  tip.innerHTML = `<div class="t">${esc(d.name)} <span style="font-family:var(--mono);font-size:11px;color:var(--muted);font-weight:400">${d.code}</span></div>
    <div class="s">${esc(d.sector)} <span class="${cls(d.secAvg)}">${fmtChg(d.secAvg)}</span> · ${sortMode().label} ${d.secRank}위</div>
    <div class="row"><span>등락률</span><span class="${cls(d.chg)}">${fmtChg(d.chg)}</span></div>
    ${d.close != null ? `<div class="row"><span>종가</span><span>${Math.round(d.close).toLocaleString()}원</span></div>` : ""}
    <div class="row"><span>시가총액</span><span>${fmtCap(d.cap)}</span></div>`;
  tip.hidden = false;
  const r = tip.getBoundingClientRect(), x = e.clientX + 14, y = e.clientY + 14;
  tip.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + "px";
  tip.style.top = (y + r.height > innerHeight - 8 ? Math.max(8, e.clientY - r.height - 10) : y) + "px";
}
$("map").addEventListener("pointermove", showTip);
$("map").addEventListener("pointerdown", showTip);
$("map").addEventListener("pointerleave", e => { if (e.pointerType === "mouse") tip.hidden = true; });
document.addEventListener("pointerdown", e => { if (!e.target.closest("#map")) tip.hidden = true; });
addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });

// ---------- 이벤트 ----------
for (const b of document.querySelectorAll(".mkt button")) {
  b.addEventListener("click", () => {
    if (state.market === b.dataset.mkt) return;
    state.market = b.dataset.mkt;   // 날짜(state.date)는 그대로 유지
    writeHash(); load();
  });
}
$("date").addEventListener("change", e => { state.date = e.target.value; writeHash(); load(); });
$("sort").addEventListener("change", () => { savePrefs(); render(); renderNews(); });
$("nsec").addEventListener("change", () => { savePrefs(); render(); });
$("topn").addEventListener("change", () => { savePrefs(); render(); });
$("range").addEventListener("change", () => { savePrefs(); updateLegend(); render(); });
addEventListener("hashchange", () => {
  const h = parseHash();
  if (h.market !== state.market || h.date !== state.date) { Object.assign(state, h); load(); }
});
let rzW = 0;
new ResizeObserver(() => { const w = $("map").clientWidth; if (w !== rzW) { rzW = w; render(); } }).observe($("map"));
const rerender = () => { updateLegend(); render(); };
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerender);
new MutationObserver(rerender).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

loadPrefs();
Object.assign(state, parseHash());
writeHash();
updateLegend();
load();
