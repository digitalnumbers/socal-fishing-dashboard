/* SoCal Fishing Intelligence — dashboard app */
const D = window.DATA;
const $ = (s, r = document) => r.querySelector(s);
const el = (t, c, h) => { const n = document.createElement(t); if (c) n.className = c; if (h !== undefined) n.innerHTML = h; return n; };
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);
/* scores approach but never reach 100 (soft ceiling) — show a decimal at the top of the range
   so a 99.8 is not rounded into a fake perfect 100 */
const sTxt = v => (v === null || v === undefined || Number.isNaN(v)) ? '—'
  : (Number(v) >= 99 ? Number(v).toFixed(1) : String(Math.round(Number(v))));
const sign = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(d);
const ZM = Object.fromEntries(D.zones.map(z => [z.id, z]));
const SM = Object.fromEntries(D.species.map(s => [s.species_id, s]));
const BAND_ORDER = { inshore: 0, nearshore: 1, offshore: 2 };
const zoneList = [...D.zones].sort((a, b) => (BAND_ORDER[a.band] - BAND_ORDER[b.band]) || a.distance_nm - b.distance_nm);
const TODAY = D.meta.today || D.meta.dates[D.meta.dates.length - 7];
const charts = {};

const state = {
  date: TODAY,
  zone: 'pt_loma_kelp',
  species: 'yellowtail',
  band: 'all',
  tab: 'today'
};

/* ---------- lookups ---------- */
const condIdx = {}; D.conditions.forEach(c => { condIdx[c.zone_id + '|' + c.date] = c; });
const scoreIdx = {}; D.scores.forEach(s => { scoreIdx[s.zone_id + '|' + s.species_id + '|' + s.date] = s; });
const curveIdx = {};
D.curve.forEach(c => { (curveIdx[c.zone_id + '|' + c.species_id] ||= []).push(c); });
Object.values(curveIdx).forEach(a => a.sort((x, y) => x.doy - y.doy));
const cond = (z, d) => condIdx[z + '|' + d];
const sc = (z, s, d) => scoreIdx[z + '|' + s + '|' + d];
const ensoLabel = v => !v ? '—' : String(v).replace(/_/g, ' ').replace(/\bNino\b/gi, 'Niño').replace(/\bNina\b/gi, 'Niña')
  .replace(/^el nino/i, 'El Niño').replace(/^la nina/i, 'La Niña').replace(/\b\w/g, m => m.toUpperCase())
  .replace(/\bEl Niño\b/g, 'El Niño').replace(/\bLa Niña\b/g, 'La Niña');
const zonesInBand = () => state.band === 'all' ? zoneList : zoneList.filter(z => z.band === state.band);
const speciesFor = z => D.species.filter(s => s.zones.split(',').includes(z));
const doyOf = ds => { const dt = new Date(ds + 'T12:00:00Z'); return Math.floor((dt - Date.UTC(dt.getUTCFullYear(), 0, 0)) / 864e5); };
const dLabel = ds => new Date(ds + 'T12:00:00Z').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
const isFcst = ds => ds > TODAY;

/* ---------- score color ---------- */
function scoreColor(v, alpha = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return 'rgba(120,140,150,.25)';
  const stops = [[0, [37, 80, 95]], [35, [46, 122, 134]], [55, [55, 166, 142]], [70, [143, 211, 106]],
    [80, [246, 215, 70]], [90, [240, 145, 60]], [100, [232, 91, 77]]];
  let a = stops[0], b = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i++) if (v >= stops[i][0] && v <= stops[i + 1][0]) { a = stops[i]; b = stops[i + 1]; break; }
  const t = b[0] === a[0] ? 0 : (v - a[0]) / (b[0] - a[0]);
  const c = a[1].map((x, i) => Math.round(x + (b[1][i] - x) * t));
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
}
const deltaTag = v => {
  if (v === null || v === undefined || Number.isNaN(v)) return '<span class="delta flat">—</span>';
  const c = v > 2 ? '' : v < -2 ? ' neg' : ' flat';
  return `<span class="delta${c}">${sign(v)}</span>`;
};

/* ---------- chart defaults ---------- */
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function baseOpts(extra = {}) {
  const grid = css('--line-soft'), tick = css('--text-3'), text = css('--text-2');
  return Object.assign({
    responsive: true, maintainAspectRatio: false,
    animation: { duration: 220 }, animations: { colors: false },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: text, boxWidth: 10, boxHeight: 10, font: { size: 11 }, usePointStyle: true } },
      tooltip: {
        backgroundColor: css('--surface-2'), borderColor: css('--line'), borderWidth: 1,
        titleColor: css('--text'), bodyColor: css('--text-2'), padding: 10, cornerRadius: 8,
        titleFont: { size: 12, weight: '600' }, bodyFont: { size: 11.5 }
      }
    },
    scales: {
      x: { grid: { color: grid, drawTicks: false }, border: { display: false }, ticks: { color: tick, font: { size: 10.5 }, maxRotation: 0, autoSkipPadding: 12 } },
      y: { grid: { color: grid, drawTicks: false }, border: { display: false }, ticks: { color: tick, font: { size: 10.5 }, padding: 6 } }
    }
  }, extra);
}
function mk(id, cfg) {
  const c = document.getElementById(id);
  if (!c) { console.warn('mk: canvas not in document:', id); return; }
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(c, cfg);
}

/* ================================================== TODAY ================================================== */
function renderToday() {
  const host = $('#tab-today'); host.innerHTML = '';
  const zs = zonesInBand();
  const day = D.scores.filter(s => s.date === state.date && zs.some(z => z.id === s.zone_id));
  const top = [...day].sort((a, b) => b.score - a.score);
  const best = top[0];
  const c = cond(state.zone, state.date);
  const zone = ZM[state.zone];
  const mySc = sc(state.zone, state.species, state.date);

  /* KPI row */
  const kpi = el('div', 'grid g4');
  kpi.appendChild(el('div', 'card kpi', best ? `
    <div class="lab">Best bet · ${state.band === 'all' ? 'all zones' : state.band}</div>
    <div class="val">${sTxt(best.score)}<small> / 100</small></div>
    <div class="meta"><b>${SM[best.species_id].species}</b> — ${ZM[best.zone_id].name}<br>
      ${deltaTag(best.vs_norm)} vs typical for this date · ${fmt(best.annual_percentile, 0)}th pct of its own season</div>` :
    '<div class="lab">Best bet</div><div class="val">—</div>'));

  kpi.appendChild(el('div', 'card kpi', `
    <div class="lab">${zone.name} — water temp</div>
    <div class="val">${fmt(c?.sst_f)}<small> °F</small></div>
    <div class="meta">${deltaTag(c?.sst_anom_f)} vs ${fmt(c?.sst_norm)}°F normal ·
      ${fmt(c?.sst_pctile, 0)}th percentile<br><span class="note">${c?.sst_source || ''} · ${fmt(c?.sst_years, 0)} yrs of record</span></div>`));

  kpi.appendChild(el('div', 'card kpi', `
    <div class="lab">Fishability — ${zone.name}</div>
    <div class="val">${fmt(mySc?.fishability, 0)}<small> / 100</small></div>
    <div class="meta">${fmt(c?.wave_ft)} ft @ ${fmt(c?.wave_period_s, 0)} s · ${fmt(c?.wind_kt, 0)} kt wind<br>
      <span class="note">${mySc?.access_note || ''} · ${zone.distance_nm} nm run</span></div>`));

  const e = D.enso_current[0] || {};
  kpi.appendChild(el('div', 'card kpi', `
    <div class="lab">Climate regime</div>
    <div class="val" style="font-size:21px">${ensoLabel(e.regime)}</div>
    <div class="meta">ONI ${sign(e.oni, 2)} °C · 12-mo mean ${sign(e.trailing_12mo_mean_oni, 2)}<br>
      <span class="note">Warm-water species get an anomaly boost, cold-water species a penalty</span></div>`));
  host.appendChild(kpi);

  /* heatmap + rankings */
  const row = el('div', 'grid', ''); row.style.gridTemplateColumns = 'minmax(0,2.15fr) minmax(0,1fr)';
  row.style.marginTop = '14px';
  if (window.innerWidth < 1080) row.style.gridTemplateColumns = '1fr';

  const hmCard = el('div', 'card');
  hmCard.appendChild(el('div', 'card-hd', `<div><h3>Opportunity matrix — ${dLabel(state.date)}</h3>
    <div class="sub">Score 0–100 per species × zone. Click a cell to inspect its drivers.</div></div>`));
  const hm = el('div', 'hm');
  const tb = el('table');
  const spAll = D.species.filter(s => zs.some(z => s.zones.split(',').includes(z.id)));
  let head = '<thead><tr><th></th>' + zs.map(z => `<th class="zh">${z.name.replace(' / ', '<br>')}</th>`).join('') + '</tr></thead>';
  let body = '<tbody>' + spAll.map(s => '<tr><td class="sp">' + s.species + '</td>' + zs.map(z => {
    const v = sc(z.id, s.species_id, state.date);
    if (!v) return '<td><div class="cell na">·</div></td>';
    return `<td><div class="cell" style="background:${scoreColor(v.score)}" data-z="${z.id}" data-s="${s.species_id}"
      title="${s.species} · ${z.name}\nScore ${fmt(v.score)} (typical ${fmt(v.seasonal_norm_score)})">${sTxt(v.score)}</div></td>`;
  }).join('') + '</tr>').join('') + '</tbody>';
  tb.innerHTML = head + body;
  hm.appendChild(tb); hmCard.appendChild(hm);
  hmCard.appendChild(el('div', 'legend', `<span>weak</span><span class="bar"></span><span>prime</span>
    <span style="margin-left:auto">· = species not modeled in that zone</span>`));
  row.appendChild(hmCard);

  const side = el('div', 'grid'); side.style.gridTemplateColumns = '1fr'; side.style.alignContent = 'start';
  const rk = el('div', 'card');
  rk.appendChild(el('div', 'card-hd', `<div><h3>Top 10 combinations</h3><div class="sub">Ranked opportunity for the selected date</div></div>`));
  rk.insertAdjacentHTML('beforeend', top.slice(0, 10).map(s => `<div class="rank">
      <span class="l">${SM[s.species_id].species}<span class="note"> · ${ZM[s.zone_id].name}</span></span>
      <span class="r" style="color:${scoreColor(s.score)}">${sTxt(s.score)}</span></div>`).join(''));
  side.appendChild(rk);

  const dr = el('div', 'card');
  dr.id = 'driver-card';
  side.appendChild(dr);
  row.appendChild(side);
  host.appendChild(row);

  /* conditions strip */
  const cs = el('div', 'card'); cs.style.marginTop = '14px';
  cs.appendChild(el('div', 'card-hd', `<div><h3>Conditions snapshot — ${zone.name}</h3>
    <div class="sub">${zone.waters === 'mexico' ? 'Mexican waters — FMM permit required' : 'US waters'} ·
    ${zone.depth_ft} ft · tide station ${zone.tide_station} · buoy ${zone.buoys.split(',')[0]}</div></div>`));
  const items = [
    ['Water temp', fmt(c?.sst_f) + ' °F', sign(c?.sst_anom_f) + ' vs normal'],
    ['7-day SST trend', sign(c?.sst_trend_7d) + ' °F', 'week-over-week'],
    ['Swell', fmt(c?.wave_ft) + ' ft', fmt(c?.wave_period_s, 0) + ' s period'],
    ['Wind', fmt(c?.wind_kt, 0) + ' kt', 'gusts ' + fmt(c?.gust_kt, 0) + ' kt'],
    ['Pressure', fmt(c?.pressure_hpa, 0) + ' hPa', sign(c?.pressure_trend_24h) + ' / 24 h'],
    ['Tide range', fmt(c?.tide_range_ft) + ' ft', 'exchange ' + fmt(c?.max_exchange_rate_ft_h, 2) + ' ft/h'],
    ['Moon', fmt((c?.moon_illum ?? 0) * 100, 0) + '%', c?.moon_phase || ''],
    ['Daylight', fmt(c?.daylight_hours) + ' h', 'sunrise ' + fmt(c?.sunrise_hour) + 'h'],
  ];
  const strip = el('div', 'grid g4');
  items.forEach(([l, v, m]) => strip.appendChild(el('div', 'kpi', `<div class="lab">${l}</div>
    <div class="val" style="font-size:20px">${v}</div><div class="meta">${m}</div>`)));
  cs.appendChild(strip);
  host.appendChild(cs);

  hm.addEventListener('click', ev => {
    const t = ev.target.closest('.cell[data-z]'); if (!t) return;
    state.zone = t.dataset.z; state.species = t.dataset.s;
    syncControls(); render();
  });
  renderDrivers();
}

function renderDrivers() {
  const card = $('#driver-card'); if (!card) return;
  const s = sc(state.zone, state.species, state.date);
  const sp = SM[state.species];
  card.innerHTML = '';
  card.appendChild(el('div', 'card-hd', `<div><h3>Why: ${sp.species}</h3>
    <div class="sub">${ZM[state.zone].name} · ${dLabel(state.date)}</div></div>`));
  if (!s) { card.appendChild(el('div', 'note', 'This species is not modeled in the selected zone.')); return; }
  const contrib = JSON.parse(s.contrib_json || '{}');
  const names = { sst: 'Water temp', season: 'Season', tide: 'Tide', moon: 'Moon', pressure: 'Pressure',
    swell: 'Swell', front: 'Temp break', chl: 'Chlorophyll' };
  const keys = Object.keys(contrib).sort((a, b) => Math.abs(contrib[b]) - Math.abs(contrib[a]));
  const max = Math.max(0.25, ...keys.map(k => Math.abs(contrib[k])));
  keys.forEach(k => {
    const v = contrib[k], w = Math.min(50, Math.abs(v) / max * 50);
    const col = v >= 0 ? css('--good') : css('--bad');
    card.insertAdjacentHTML('beforeend', `<div class="driver"><span class="dn">${names[k] || k}</span>
      <span class="db"><i style="${v >= 0 ? 'left:50%' : 'right:50%;left:auto'};width:${w}%;background:${col}"></i></span>
      <span class="dv">${fmt(s['term_' + k] * 100, 0)}</span></div>`);
  });
  card.insertAdjacentHTML('beforeend', `<div class="note" style="margin-top:10px">
    Bars show each driver's weighted push above (green) or below (red) neutral; the number is that driver's
    0–100 suitability. Core score ${fmt(s.core_score, 0)} × access ${fmt(s.fishability, 0)} → <b>${sTxt(s.score)}</b>.
    Typical for this date: ${fmt(s.seasonal_norm_score, 0)}.</div>
    <div class="chips">
      <span class="chip">Temp window ${fmt(sp.sst_opt_lo_f, 0)}–${fmt(sp.sst_opt_hi_f, 0)} °F (${sp.temp_reference})</span>
      <span class="chip">Tide: ${sp.tide_pref.replace(/_/g, ' ')}</span>
      <span class="chip">Moon: ${sp.moon_pref.replace(/_/g, ' ')}</span>
      <span class="chip">Swell tolerance ${fmt(sp.swell_tolerance_ft, 0)} ft</span>
      ${s.missing_drivers ? `<span class="chip">no data: ${s.missing_drivers}</span>` : ''}
    </div>`);
}

/* ================================================== FORECAST ================================================== */
function renderForecast() {
  const host = $('#tab-forecast'); host.innerHTML = '';
  const dates = D.meta.dates;
  const zs = zonesInBand().filter(z => SM[state.species].zones.split(',').includes(z.id));
  const sp = SM[state.species];

  const c1 = el('div', 'card');
  c1.appendChild(el('div', 'card-hd', `<div><h3>${sp.species} — 21-day opportunity track</h3>
    <div class="sub">14 days of observed conditions plus a 7-day forecast. Shaded region is the forecast window.</div></div>`));
  c1.appendChild(el('div', 'chart tall', '<canvas id="ch-fcst"></canvas>'));
  host.appendChild(c1);

  const palette = [css('--teal'), css('--amber'), css('--sky'), css('--violet'), css('--coral'), css('--good'), css('--warn'), '#8aa4b3', '#d17fb0'];
  const ds = zs.map((z, i) => ({
    label: z.name, borderColor: palette[i % palette.length], backgroundColor: palette[i % palette.length],
    data: dates.map(d => sc(z.id, state.species, d)?.score ?? null),
    tension: .35, borderWidth: 2, pointRadius: dates.map(d => isFcst(d) ? 3 : 0), pointHoverRadius: 4,
    segment: { borderDash: ctx => isFcst(dates[ctx.p1DataIndex]) ? [5, 4] : undefined }
  }));
  const norm = {
    label: 'Typical for date', borderColor: css('--text-3'), borderDash: [3, 3], borderWidth: 1.5,
    pointRadius: 0, data: dates.map(d => {
      const v = zs.map(z => sc(z.id, state.species, d)?.seasonal_norm_score).filter(x => x != null);
      return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
    }), tension: .35
  };
  mk('ch-fcst', {
    type: 'line', data: { labels: dates.map(dLabel), datasets: [...ds, norm] },
    options: baseOpts({ scales: { x: { grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), font: { size: 10 }, maxRotation: 45, minRotation: 45 } }, y: { min: 0, max: 100, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), stepSize: 20 }, title: { display: true, text: 'Opportunity score', color: css('--text-3'), font: { size: 10.5 } } } } })
  });

  /* best day per species table */
  const fdates = dates.filter(isFcst);
  const c2 = el('div', 'card'); c2.style.marginTop = '14px';
  c2.appendChild(el('div', 'card-hd', `<div><h3>Best day in the next 7 — by species</h3>
    <div class="sub">Peak zone and score across the forecast window${state.band === 'all' ? '' : ' · ' + state.band + ' only'}</div></div>`));
  const rows = D.species.map(s => {
    let best = null;
    zonesInBand().forEach(z => fdates.forEach(d => {
      const v = sc(z.id, s.species_id, d);
      if (v && (!best || v.score > best.score)) best = v;
    }));
    return best ? { s, best } : null;
  }).filter(Boolean).sort((a, b) => b.best.score - a.best.score);
  const t = el('div', 'tbl-wrap', `<table><thead><tr>
    <th>Species</th><th>Best day</th><th>Zone</th><th>Score</th><th>vs typical</th><th>Season pct</th>
    <th>Water °F</th><th>Anom</th><th>Swell ft</th><th>Wind kt</th><th>Access</th></tr></thead><tbody>` +
    rows.map(({ s, best }) => {
      const c = cond(best.zone_id, best.date);
      return `<tr><td>${s.species}</td><td>${dLabel(best.date)}</td><td>${ZM[best.zone_id].name}</td>
        <td style="color:${scoreColor(best.score)};font-weight:600">${sTxt(best.score)}</td>
        <td>${deltaTag(best.vs_norm)}</td><td>${fmt(best.annual_percentile, 0)}</td>
        <td>${fmt(c?.sst_f)}</td><td>${sign(c?.sst_anom_f)}</td><td>${fmt(c?.wave_ft)}</td>
        <td>${fmt(c?.wind_kt, 0)}</td><td>${fmt(best.fishability, 0)}</td></tr>`;
    }).join('') + '</tbody></table>');
  c2.appendChild(t);
  host.appendChild(c2);

  /* zone fishability forecast */
  const c3 = el('div', 'card'); c3.style.marginTop = '14px';
  c3.appendChild(el('div', 'card-hd', `<div><h3>Access window by zone</h3>
    <div class="sub">Small-boat fishability from forecast wind, swell height and period, scaled by run distance</div></div>`));
  c3.appendChild(el('div', 'chart', '<canvas id="ch-fish"></canvas>'));
  host.appendChild(c3);
  const zf = zonesInBand();
  mk('ch-fish', {
    type: 'bar',
    data: {
      labels: fdates.map(dLabel),
      datasets: zf.map((z, i) => ({
        label: z.name, backgroundColor: palette[i % palette.length], borderRadius: 3,
        data: fdates.map(d => {
          const v = D.scores.filter(s => s.zone_id === z.id && s.date === d);
          return v.length ? v[0].fishability : null;
        })
      }))
    },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { color: css('--text-3'), font: { size: 10 } } }, y: { min: 0, max: 100, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } } } })
  });
}

/* ================================================== 30-DAY OUTLOOK ==================================================
   Extended range, days 8-30. Everything in this section is deliberately presented at LOWER certainty
   than the 7-day tab: the near-term model (D.scores, built by build_dataset.py) is untouched and is
   still the only place daily wind/swell numbers are published beyond the deterministic model horizon.
   Tier definitions come from the pipeline (D.confidence), not from hardcoded UI strings.            */

const EXT = D.extended || [];
const EXTS = D.extended_scores || [];
const TIERS = D.confidence || [];
const EMETA = D.extended_meta || {};
const extIdx = {}; EXT.forEach(r => { extIdx[r.zone_id + '|' + r.date] = r; });
const extScIdx = {}; EXTS.forEach(r => { extScIdx[r.zone_id + '|' + r.species_id + '|' + r.date] = r; });
const extDates = [...new Set(EXT.map(r => r.date))].sort();
const ex = (z, d) => extIdx[z + '|' + d];
const exZone = z => EXT.filter(r => r.zone_id === z).sort((a, b) => a.lead_days - b.lead_days);
const exs = (z, s, d) => extScIdx[z + '|' + s + '|' + d];
const tierOf = d => (ex(state.zone, d) || EXT.find(r => r.date === d) || {}).confidence_tier
  || (d > TODAY ? 1 : 1);
const tierMeta = t => TIERS.find(x => x.tier === t) || {};
const TIER_COLOR = t => css(t === 1 ? '--t1' : t === 2 ? '--t2' : '--t3');
const addDays = (ds, n) => { const t = new Date(ds + 'T12:00:00Z'); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
const TREND_CLASS = {
  'well above typical': 'up2', 'above typical': 'up1', 'near typical': 'flat',
  'below typical': 'dn1', 'well below typical': 'dn2'
};
const trendTag = t => t ? `<span class="trend ${TREND_CLASS[t] || 'flat'}">${t}</span>` : '—';
const bandTxt = zid => { const g = exZone(zid); if (!g.length) return 'range unavailable'; const a = g[0], b = g[g.length - 1];
  const h = r => (r.sst_proj_hi_f - r.sst_proj_f); return `\u00b1${fmt(h(a))} \u00b0F at day ${a.lead_days}, \u00b1${fmt(h(b))} \u00b0F at day ${b.lead_days}`; };
const cBadge = t => `<span class="cbadge t${t}"><span class="cd"></span>${tierMeta(t).short || 'Days ?'}</span>`;

// Score values as a tinted chip rather than coloured digits. Colouring the glyphs themselves put
// pale mid-scale greens and yellows at ~1.5:1 against the light-theme card, which is unreadable;
// this keeps the same colour scale as a background plate and leaves the number at full contrast.
function svChip(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '<span class="np">n/f</span>';
  return `<span class="sv" style="background:${scoreColor(v, 0.22)};border-color:${scoreColor(v, 0.6)}">${sTxt(v)}</span>`;
}

function renderOutlook() {
  const host = $('#tab-outlook'); host.innerHTML = '';
  const sp = SM[state.species], z = ZM[state.zone];
  const hasSp = sp && sp.zones.split(',').includes(state.zone);

  /* ---------- 1. confidence tier legend: the primary honesty element ---------- */
  const c0 = el('div', 'card');
  c0.appendChild(el('div', 'card-hd', `<div><h3>How far ahead this can actually see</h3>
    <div class="sub">The 30-day view is three different products stitched together, not one forecast.
    Certainty falls sharply with lead time — read the tier before reading the number.</div></div>`));
  c0.appendChild(el('div', 'tierleg', TIERS.map(t => `
    <div class="tiercard t${t.tier}">
      <div class="th"><span class="tt">${t.label}</span><span class="tr">${t.short}</span></div>
      <div class="tb">${t.basis}</div>
      <div class="ts"><b>Shows:</b> ${t.shows}</div>
    </div>`).join('')));
  host.appendChild(c0);

  /* ---------- 2. the 30-day track with widening uncertainty ---------- */
  const nearDates = D.meta.dates.filter(d => d >= addDays(TODAY, -6));
  const allDates = [...nearDates, ...extDates];
  const c1 = el('div', 'card'); c1.style.marginTop = '14px';
  c1.appendChild(el('div', 'card-hd', `<div><h3>${sp.species} — ${z.name} · 30-day opportunity outlook</h3>
    <div class="sub">Solid = observed and short-range forecast. Dashed = moderate confidence.
    Dotted = trend only. The shaded envelope is the range of scores this species would get across the
    plausible water-temperature range for that day. Its modelled half-width never narrows as lead time
    grows, but the drawn band can still look tighter where it runs into the top of the 0&#8209;100 scale.</div></div>
    <div>${cBadge(1)} ${cBadge(2)} ${cBadge(3)}</div>`));
  c1.appendChild(el('div', 'chart tall', '<canvas id="ch-outlook"></canvas>'));
  const stripe = el('div', 'stripe');
  allDates.forEach(d => { const t = d <= addDays(TODAY, 7) ? 1 : tierOf(d); stripe.appendChild(el('i', 't' + t)); });
  c1.appendChild(stripe);
  c1.appendChild(el('div', 'striplab', `<span>${dLabel(allDates[0])}</span>
    <span>today</span><span>${dLabel(allDates[allDates.length - 1])} · +30 d</span>`));
  host.appendChild(c1);

  if (hasSp) {
    /* three separate series so each can carry its own dash pattern, with the boundary
       point duplicated into the neighbouring series so the line reads as continuous */
    const nearAt = d => sc(state.zone, state.species, d)?.score ?? null;
    const extAt = (d, t) => { const r = exs(state.zone, state.species, d); return r && r.confidence_tier === t ? r.score_outlook : null; };
    const lastNear = nearDates[nearDates.length - 1];
    const firstT3 = extDates.find(d => tierOf(d) === 3);
    const lastT2 = [...extDates].reverse().find(d => tierOf(d) === 2);

    const s1 = allDates.map(d => nearDates.includes(d) ? nearAt(d) : null);
    const s2 = allDates.map(d => d === lastNear ? nearAt(d) : extAt(d, 2));
    const s3 = allDates.map(d => d === lastT2 ? extAt(d, 2) : extAt(d, 3));
    const lo = allDates.map(d => exs(state.zone, state.species, d)?.score_lo ?? null);
    const hi = allDates.map(d => exs(state.zone, state.species, d)?.score_hi ?? null);
    const norm = allDates.map(d => {
      const r = exs(state.zone, state.species, d);
      if (r) return r.climatological_score;
      return sc(state.zone, state.species, d)?.seasonal_norm_score ?? null;
    });

    mk('ch-outlook', {
      type: 'line',
      data: {
        labels: allDates.map(dLabel),
        datasets: [
          {
            label: 'Uncertainty band', data: hi, borderColor: 'transparent', pointRadius: 0,
            backgroundColor: 'rgba(157,127,234,.13)', fill: '+1', tension: .35, order: 9
          },
          { label: '_lo', data: lo, borderColor: 'transparent', pointRadius: 0, fill: false, tension: .35, order: 9 },
          {
            label: 'Typical for date (climatology)', data: norm, borderColor: css('--text-3'),
            borderDash: [3, 3], borderWidth: 1.5, pointRadius: 0, tension: .35, order: 5
          },
          {
            label: 'Days 1–7 · high confidence', data: s1, borderColor: css('--t1'),
            backgroundColor: css('--t1'), borderWidth: 2.4, pointRadius: 0, tension: .35, order: 1
          },
          {
            label: 'Days 8–14 · moderate', data: s2, borderColor: css('--t2'),
            backgroundColor: css('--t2'), borderWidth: 2.2, borderDash: [6, 4], pointRadius: 2.5, tension: .35, order: 2
          },
          {
            label: 'Days 15–30 · outlook only', data: s3, borderColor: css('--t3'),
            backgroundColor: css('--t3'), borderWidth: 2, borderDash: [2, 4], pointRadius: 2, tension: .35, order: 3
          }
        ]
      },
      options: baseOpts({
        plugins: {
          legend: {
            labels: {
              color: css('--text-2'), boxWidth: 10, boxHeight: 10, font: { size: 11 }, usePointStyle: true,
              filter: it => it.text !== '_lo'
            }
          },
          tooltip: {
            backgroundColor: css('--surface-2'), borderColor: css('--line'), borderWidth: 1,
            titleColor: css('--text'), bodyColor: css('--text-2'), padding: 10, cornerRadius: 8,
            titleFont: { size: 12, weight: '600' }, bodyFont: { size: 11.5 },
            filter: it => it.dataset.label !== '_lo' && it.parsed.y !== null,
            callbacks: {
              afterBody: it => {
                const d = allDates[it[0].dataIndex], r = exs(state.zone, state.species, d);
                if (!r) return '';
                return [`${tierMeta(r.confidence_tier).label} · lead +${r.lead_days} d`,
                `range ${sTxt(r.score_lo)}–${sTxt(r.score_hi)} · ${r.trend}`];
              }
            }
          }
        },
        scales: {
          x: { grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), font: { size: 9.5 }, maxRotation: 45, minRotation: 45, autoSkip: true, maxTicksLimit: 18 } },
          y: {
            min: 0, max: 100, grid: { color: css('--line-soft') },
            ticks: { color: css('--text-3'), stepSize: 20 },
            title: { display: true, text: 'Opportunity score', color: css('--text-3'), font: { size: 10.5 } }
          }
        }
      })
    });
  } else {
    c1.appendChild(el('div', 'callout', `${sp.species} is not modelled in ${z.name}. Pick another zone or species.`));
  }

  /* ---------- 2b. water temperature projection: the cleanest view of growing uncertainty ----------
     Unlike the score, SST is unbounded in the range we care about, so its band widens monotonically
     and honestly shows how much less the model knows at day 30 than at day 8. */
  const cT = el('div', 'card'); cT.style.marginTop = '14px';
  cT.appendChild(el('div', 'card-hd', `<div><h3>Projected water temperature — ${z.name}</h3>
    <div class="sub">Persistence of the current anomaly decaying toward the day-of-year climatology, weighted
    with the ENSO analog composite and nudged by the CPC outlook. The envelope widens monotonically
    with lead time — ${bandTxt(state.zone)}.</div></div>`));
  cT.appendChild(el('div', 'chart', '<canvas id="ch-outsst"></canvas>'));
  host.appendChild(cT);
  {
    const zx = exZone(state.zone);
    const nd = nearDates;
    const lab = [...nd, ...zx.map(r => r.date)];
    const obs = lab.map(d => nd.includes(d) ? (cond(state.zone, d)?.sst_f ?? null) : null);
    const lastO = cond(state.zone, nd[nd.length - 1])?.sst_f ?? null;
    const proj = lab.map((d, i) => i === nd.length - 1 ? lastO : (zx.find(r => r.date === d)?.sst_proj_f ?? null));
    const plo = lab.map((d, i) => i === nd.length - 1 ? lastO : (zx.find(r => r.date === d)?.sst_proj_lo_f ?? null));
    const phi = lab.map((d, i) => i === nd.length - 1 ? lastO : (zx.find(r => r.date === d)?.sst_proj_hi_f ?? null));
    const nrm = lab.map(d => zx.find(r => r.date === d)?.sst_norm_f ?? (cond(state.zone, d)?.sst_norm ?? null));
    mk('ch-outsst', {
      type: 'line',
      data: {
        labels: lab.map(dLabel),
        datasets: [
          { label: 'Uncertainty envelope', data: phi, borderColor: 'transparent', pointRadius: 0, backgroundColor: 'rgba(91,192,235,.16)', fill: '+1', tension: .35, order: 9 },
          { label: '_lo', data: plo, borderColor: 'transparent', pointRadius: 0, fill: false, tension: .35, order: 9 },
          { label: 'Seasonal normal', data: nrm, borderColor: css('--text-3'), borderDash: [3, 3], borderWidth: 1.5, pointRadius: 0, tension: .35 },
          { label: 'Observed / forecast', data: obs, borderColor: css('--t1'), borderWidth: 2.4, pointRadius: 0, tension: .35 },
          { label: 'Projection', data: proj, borderColor: css('--sky'), borderWidth: 2.2, borderDash: [6, 4], pointRadius: 1.8, tension: .35 }
        ]
      },
      options: baseOpts({
        plugins: {
          legend: { labels: { color: css('--text-2'), boxWidth: 10, boxHeight: 10, font: { size: 11 }, usePointStyle: true, filter: it => it.text !== '_lo' } },
          tooltip: { backgroundColor: css('--surface-2'), borderColor: css('--line'), borderWidth: 1, titleColor: css('--text'), bodyColor: css('--text-2'), padding: 10, cornerRadius: 8, titleFont: { size: 12, weight: '600' }, bodyFont: { size: 11.5 }, filter: it => it.dataset.label !== '_lo' && it.parsed.y !== null }
        },
        scales: {
          x: { grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), font: { size: 9.5 }, maxRotation: 45, minRotation: 45, maxTicksLimit: 18 } },
          y: { grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') }, title: { display: true, text: 'Water temperature °F', color: css('--text-3'), font: { size: 10.5 } } }
        }
      })
    });
  }

  /* ---------- 3. regime context driving the extended range ---------- */
  const e = D.enso_current[0] || {};
  const cpcT = (D.cpc || []).filter(r => r.kind === 'temperature');
  const kpis = el('div', 'grid g4'); kpis.style.marginTop = '14px';
  const wk = EXT.find(r => r.zone_id === state.zone && r.confidence_tier === 3) || {};
  [
    ['ENSO regime', ensoLabel(e.regime), `ONI <b>${sign(e.oni, 2)}</b> · ${e.season || ''} ${e.year || ''}.
      Weighted more heavily as lead time grows — ${fmt((wk.w_enso_analog || 0) * 100, 0)}% of the day-15+ signal here.`],
    ['Analog years', (EMETA.analog_years || []).slice(0, 4).join(', ') + ((EMETA.analog_years || []).length > 4 ? '…' : ''),
      `${(EMETA.analog_years || []).length} years with a comparable ONI in this calendar window, from the 1950-present record.`],
    ['CPC outlook signal', cpcT.length ? `${cpcT[0].cat} ${fmt(cpcT[0].prob, 0)}%` : '—',
      cpcT.map(r => `${r.product.replace('cpc_', '').replace('_temp', '')}: ${r.cat} ${fmt(r.prob, 0)}%`).join(' · ')
      + '. Probabilistic tercile, land-based CONUS product.'],
    ['Model horizon', EMETA.model_horizon_last_date ? dLabel(EMETA.model_horizon_last_date) : '—',
      `Last date any deterministic weather model reaches. Past this, no daily wind or swell numbers are published.`]
  ].forEach(([l, v, m]) => {
    const k = el('div', 'card kpi');
    k.innerHTML = `<div class="lab">${l}</div><div class="val">${v}</div><div class="meta">${m}</div>`;
    kpis.appendChild(k);
  });
  host.appendChild(kpis);

  /* ---------- 4. day-by-day extended table for the selected zone ---------- */
  const c2 = el('div', 'card'); c2.style.marginTop = '14px';
  c2.appendChild(el('div', 'card-hd', `<div><h3>Day-by-day extended outlook — ${z.name}</h3>
    <div class="sub">Water temperature is a projection with an uncertainty range. Tide and moon are astronomical
    and exact at any lead. Swell and wind are shown only where a real model reaches; elsewhere they are blank
    rather than filled with a seasonal average dressed up as a forecast.</div></div>`));
  const zr = EXT.filter(r => r.zone_id === state.zone);
  c2.appendChild(el('div', 'tbl-wrap scroll-tall', `<table><thead><tr>
    <th>Date</th><th>Lead</th><th>Confidence</th><th>Water °F</th><th>Range °F</th><th>vs normal</th>
    <th>Swell ft</th><th>Wind kt</th><th>Tide ft</th><th>Moon</th><th>CPC temp</th>
    ${hasSp ? '<th>Score</th><th>Score range</th><th>Trend</th>' : ''}</tr></thead><tbody>` +
    zr.map(r => {
      const s = hasSp ? exs(state.zone, state.species, r.date) : null;
      return `<tr>
        <td>${dLabel(r.date)}</td><td>+${r.lead_days}</td><td>${cBadge(r.confidence_tier)}</td>
        <td style="font-weight:600" title="${r.sst_basis || ''}">${fmt(r.sst_proj_f)}</td>
        <td class="note" style="font-size:11.5px">${fmt(r.sst_proj_lo_f)}–${fmt(r.sst_proj_hi_f)}</td>
        <td>${deltaTag(r.sst_anom_proj_f)}</td>
        <td${r.wave_proj_ft == null ? ' class="np"' : ''} title="${r.wave_basis || ''}">${r.wave_proj_ft == null ? 'n/f' : fmt(r.wave_proj_ft)}</td>
        <td${r.wind_proj_kt == null ? ' class="np"' : ''} title="${r.wind_basis || ''}">${r.wind_proj_kt == null ? 'n/f' : fmt(r.wind_proj_kt, 0)}</td>
        <td>${fmt(r.tide_range_ft)}</td>
        <td>${fmt(r.moon_illum * 100, 0)}%<span class="note"> ${r.moon_phase || ''}</span></td>
        <td>${r.cpc_temp_cat ? r.cpc_temp_cat + ' ' + fmt(r.cpc_temp_prob, 0) + '%' : '—'}</td>
        ${hasSp ? `<td>${svChip(s?.score_outlook)}</td>
          <td class="note" style="font-size:11.5px" title="${s?.score_band_hits_ceiling ? 'Upper edge clipped at the top of the 0-100 scale — modelled uncertainty is wider than shown' : s?.score_band_is_ratcheted ? 'Widened so the band never implies more certainty at longer lead' : ''}">${sTxt(s?.score_lo)}–${sTxt(s?.score_hi)}${s?.score_band_hits_ceiling ? ' <span class="pill mx">clipped</span>' : ''}</td>
          <td>${trendTag(s?.trend)}</td>` : ''}
      </tr>`;
    }).join('') + '</tbody></table>'));
  c2.appendChild(el('div', 'note', `<b>n/f</b> = not forecastable at this lead. Swell and wind are published only
    inside the deterministic model horizon (through ${EMETA.model_horizon_last_date ? dLabel(EMETA.model_horizon_last_date) : '—'}
    for wind, and earlier still for swell, since the marine model runs shorter than the atmospheric one).`));
  host.appendChild(c2);

  /* ---------- 5. where the extended trend is strongest ---------- */
  const c3 = el('div', 'card'); c3.style.marginTop = '14px';
  c3.appendChild(el('div', 'card-hd', `<div><h3>Strongest extended windows by species</h3>
    <div class="sub">Best day 8-30 opportunity per species, against that day's seasonal norm.
    Treat the date as indicative of a favourable stretch, not a specific bookable day.</div></div>`));
  const rows = D.species.map(s => {
    let best = null;
    zonesInBand().forEach(zz => extDates.forEach(d => {
      const v = exs(zz.id, s.species_id, d);
      if (v && (!best || v.score_outlook > best.score_outlook)) best = v;
    }));
    return best ? { s, best } : null;
  }).filter(Boolean).sort((a, b) => b.best.score_outlook - a.best.score_outlook);
  c3.appendChild(el('div', 'tbl-wrap', `<table><thead><tr>
    <th>Species</th><th>Best window</th><th>Lead</th><th>Zone</th><th>Confidence</th>
    <th>Score</th><th>Range</th><th>Typical</th><th>Trend</th><th>Water °F</th></tr></thead><tbody>` +
    rows.map(({ s, best }) => `<tr>
      <td>${s.species}</td><td>${dLabel(best.date)}</td><td>+${best.lead_days}</td>
      <td>${ZM[best.zone_id].name}</td><td>${cBadge(best.confidence_tier)}</td>
      <td>${svChip(best.score_outlook)}</td>
      <td class="note" style="font-size:11.5px">${sTxt(best.score_lo)}–${sTxt(best.score_hi)}</td>
      <td>${sTxt(best.climatological_score)}</td><td>${trendTag(best.trend)}</td>
      <td>${fmt(ex(best.zone_id, best.date)?.sst_proj_f)}</td></tr>`).join('') + '</tbody></table>'));
  host.appendChild(c3);

  /* ---------- 6. how each extended field is actually derived ---------- */
  const g = el('div', 'grid g2'); g.style.marginTop = '14px';

  const c4 = el('div', 'card pad0');
  c4.appendChild(el('div', 'card-hd', `<div><h3>Forecast vs historical pattern</h3>
    <div class="sub">Which extended fields are a real forecast and which are a seasonal-average or
    analog-year estimate. Also in Data &amp; Sources.</div></div>`));
  $('.card-hd', c4).style.padding = '16px 16px 0';
  const basisOrder = ['forecast', 'blend', 'enso_analog', 'climatology', 'astronomical', 'metadata'];
  const prov = [...(D.extended_provenance || [])]
    .sort((a, b) => basisOrder.indexOf(a.basis) - basisOrder.indexOf(b.basis));
  c4.appendChild(el('div', 'tbl-wrap scroll-tall', `<table><thead><tr>
    <th>Field</th><th>Basis</th><th>Source</th></tr></thead><tbody>` +
    prov.map(r => `<tr><td><code>${r.field}</code></td>
      <td><span class="basis ${r.basis}">${r.basis.replace('_', ' ')}</span></td>
      <td class="wrap-ok">${r.source}</td></tr>`).join('') + '</tbody></table>'));
  g.appendChild(c4);

  const c5 = el('div', 'card');
  c5.appendChild(el('div', 'card-hd', `<div><h3>Limits of the 15–30 day range</h3>
    <div class="sub">Read these before acting on anything past two weeks</div></div>`));
  c5.appendChild(el('div', 'callout', `<b>No true extended marine forecast exists</b> at public-API level for this
    range. Deterministic swell and wind stop around
    ${EMETA.model_horizon_last_date ? dLabel(EMETA.model_horizon_last_date) : 'day 15'}, so days 15–30 publish
    <b>no daily wind or swell numbers at all</b>. What remains is the seasonal norm, the ENSO analog signal,
    and exactly-calculable tide and moon.`));
  const lims = [
    ['CPC outlooks are land products', `The 8-14 day, week 3-4 and monthly tercile outlooks are CONUS
      <i>air temperature</i> forecasts. No polygon covers the offshore zones, so an onshore San Diego proxy point is
      sampled for all ${D.zones.length} zones — including Cortez Bank at 95 nm. Treat it as a regional warm/cool lean, not a marine SST forecast.`],
    ['Small analog sample', `Observed analog-year SST anomalies can only be computed for years inside the satellite
      record, so the primary ENSO signal comes from the zone composite rather than from directly observed analog years.`],
    ['Monthly product is stale', `The CPC monthly-update product was not current at build time; the September
      monthly outlook came from the seasonal lead-14 file instead.`],
    ['Pressure driver excluded', `Barometric trend exists only inside the model horizon. Feeding it into days 8-14
      and dropping it at day 15 renormalized the driver weights and created a false cliff at the boundary, so it is
      excluded from the extended score at both tiers and shown as reference only.`],
    ['Fewer drivers than near-term', `Frontal structure and chlorophyll are unavailable in the extended range, so
      those drivers are dropped and the remaining weights renormalize. Extended scores are directionally comparable
      to near-term scores but not identically constructed.`]
  ];
  lims.forEach(([t, b]) => {
    const d = el('div'); d.style.marginTop = '11px';
    d.innerHTML = `<div style="font-size:12.5px;font-weight:600;color:var(--text)">${t}</div>
      <div class="note" style="margin-top:3px">${b}</div>`;
    c5.appendChild(d);
  });
  g.appendChild(c5);
  host.appendChild(g);
}

/* ================================================== CONDITIONS ================================================== */
function renderConditions() {
  const host = $('#tab-conditions'); host.innerHTML = '';
  const dates = D.meta.dates, z = ZM[state.zone];
  const series = dates.map(d => cond(state.zone, d));

  const c1 = el('div', 'card');
  c1.appendChild(el('div', 'card-hd', `<div><h3>Water temperature vs historical norm — ${z.name}</h3>
    <div class="sub">Observed/forecast SST against the day-of-year climatology and its 10th–90th percentile envelope
    (${fmt(series.find(s => s?.sst_years)?.sst_years, 0)} years of record, ±7-day window)</div></div>`));
  c1.appendChild(el('div', 'chart tall', '<canvas id="ch-sst"></canvas>'));
  host.appendChild(c1);
  mk('ch-sst', {
    type: 'line',
    data: {
      labels: dates.map(dLabel),
      datasets: [
        { label: 'p90', data: series.map(s => s?.sst_p90 ?? null), borderColor: 'transparent', backgroundColor: 'rgba(46,196,182,.12)', fill: '+1', pointRadius: 0 },
        { label: 'p10', data: series.map(s => s?.sst_p10 ?? null), borderColor: 'transparent', backgroundColor: 'transparent', pointRadius: 0 },
        { label: 'Normal', data: series.map(s => s?.sst_norm ?? null), borderColor: css('--text-3'), borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0, tension: .3 },
        { label: 'Observed / forecast', data: series.map(s => s?.sst_f ?? null), borderColor: css('--amber'), backgroundColor: css('--amber'), borderWidth: 2.5, pointRadius: dates.map(d => isFcst(d) ? 3 : 0), tension: .3, segment: { borderDash: ctx => isFcst(dates[ctx.p1DataIndex]) ? [5, 4] : undefined } }
      ]
    },
    options: baseOpts({ plugins: { legend: { labels: { color: css('--text-2'), filter: i => i.text !== 'p10', usePointStyle: true, boxWidth: 10 } } }, scales: { x: { grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), font: { size: 10 }, maxRotation: 45, minRotation: 45 } }, y: { grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), callback: v => v + '°' } } } })
  });

  const g = el('div', 'grid g2'); g.style.marginTop = '14px';
  host.appendChild(g); /* attach before mk() so the canvases are in the document */
  const c2 = el('div', 'card');
  c2.appendChild(el('div', 'card-hd', `<div><h3>Swell &amp; wind vs normal</h3>
    <div class="sub">Bars are observed/forecast; dashed lines are buoy ${z.buoys.split(',')[0]} day-of-year climatology</div></div>`));
  c2.appendChild(el('div', 'chart', '<canvas id="ch-wave"></canvas>'));
  g.appendChild(c2);
  mk('ch-wave', {
    type: 'bar',
    data: {
      labels: dates.map(dLabel),
      datasets: [
        { type: 'bar', label: 'Swell ft', data: series.map(s => s?.wave_ft ?? null), backgroundColor: 'rgba(91,192,235,.6)', borderRadius: 3, yAxisID: 'y' },
        { type: 'line', label: 'Normal swell ft', data: series.map(s => s?.wave_norm ?? null), borderColor: css('--sky'), borderDash: [4, 3], borderWidth: 1.5, pointRadius: 0, yAxisID: 'y' },
        { type: 'line', label: 'Wind kt', data: series.map(s => s?.wind_kt ?? null), borderColor: css('--coral'), borderWidth: 2, pointRadius: 0, tension: .3, yAxisID: 'y1' },
        { type: 'line', label: 'Normal wind kt', data: series.map(s => s?.wind_norm ?? null), borderColor: css('--coral'), borderDash: [4, 3], borderWidth: 1.2, pointRadius: 0, yAxisID: 'y1' }
      ]
    },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { color: css('--text-3'), font: { size: 9.5 }, maxRotation: 45, minRotation: 45 } }, y: { position: 'left', beginAtZero: true, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } }, y1: { position: 'right', beginAtZero: true, grid: { display: false }, ticks: { color: css('--text-3') } } } })
  });

  const c3 = el('div', 'card');
  c3.appendChild(el('div', 'card-hd', `<div><h3>Tide exchange, pressure &amp; moon</h3>
    <div class="sub">Tide range at ${z.tide_station}; barometric trend drives the pressure term; moon sets spring/neap timing</div></div>`));
  c3.appendChild(el('div', 'chart', '<canvas id="ch-tide"></canvas>'));
  g.appendChild(c3);
  mk('ch-tide', {
    type: 'bar',
    data: {
      labels: dates.map(dLabel),
      datasets: [
        { type: 'bar', label: 'Tide range ft', data: series.map(s => s?.tide_range_ft ?? null), backgroundColor: 'rgba(46,196,182,.55)', borderRadius: 3, yAxisID: 'y' },
        { type: 'line', label: 'Moon illum %', data: series.map(s => s ? s.moon_illum * 100 : null), borderColor: css('--violet'), borderWidth: 2, pointRadius: 0, tension: .3, yAxisID: 'y1' },
        { type: 'line', label: 'Pressure trend hPa/24h', data: series.map(s => s?.pressure_trend_24h ?? null), borderColor: css('--warn'), borderWidth: 1.8, pointRadius: 0, tension: .3, yAxisID: 'y' }
      ]
    },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { color: css('--text-3'), font: { size: 9.5 }, maxRotation: 45, minRotation: 45 } }, y: { position: 'left', grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } }, y1: { position: 'right', min: 0, max: 100, grid: { display: false }, ticks: { color: css('--text-3') } } } })
  });

  /* anomaly table across zones */
  const c4 = el('div', 'card'); c4.style.marginTop = '14px';
  c4.appendChild(el('div', 'card-hd', `<div><h3>All zones vs normal — ${dLabel(state.date)}</h3>
    <div class="sub">Every zone's current departure from its own day-of-year climatology</div></div>`));
  c4.appendChild(el('div', 'tbl-wrap', `<table><thead><tr><th>Zone</th><th>Band</th><th>nm</th><th>Water °F</th>
    <th>Normal °F</th><th>Anomaly</th><th>Pctile</th><th>7-day Δ</th><th>Swell ft</th><th>vs normal</th>
    <th>Wind kt</th><th>vs normal</th><th>Source</th></tr></thead><tbody>` +
    zoneList.map(z2 => {
      const c = cond(z2.id, state.date); if (!c) return '';
      return `<tr><td>${z2.name}${z2.waters === 'mexico' ? ' <span class="pill mx">MX</span>' : ''}</td>
        <td>${z2.band}</td><td>${z2.distance_nm}</td><td>${fmt(c.sst_f)}</td><td>${fmt(c.sst_norm)}</td>
        <td>${deltaTag(c.sst_anom_f)}</td><td>${fmt(c.sst_pctile, 0)}</td><td>${sign(c.sst_trend_7d)}</td>
        <td>${fmt(c.wave_ft)}</td><td>${sign(c.wave_anom_ft)}</td><td>${fmt(c.wind_kt, 0)}</td>
        <td>${sign(c.wind_anom_kt, 0)}</td><td class="note">${c.sst_source}</td></tr>`;
    }).join('') + '</tbody></table>'));
  host.appendChild(c4);
}

/* ================================================== SPECIES MODEL ================================================== */
function renderSpecies() {
  const host = $('#tab-species'); host.innerHTML = '';
  const sp = SM[state.species];
  const zid = sp.zones.split(',').includes(state.zone) ? state.zone : sp.zones.split(',')[0];
  const cv = curveIdx[zid + '|' + state.species] || [];
  const todayDoy = doyOf(state.date);
  const s = sc(zid, state.species, state.date);

  const g = el('div', 'grid g2');
  host.appendChild(g); /* attach before mk() so the canvases are in the document */
  const c1 = el('div', 'card');
  c1.appendChild(el('div', 'card-hd', `<div><h3>Seasonal opportunity curve — ${sp.species}</h3>
    <div class="sub">${ZM[zid].name} · climatological score for every day of the year, with today marked</div></div>`));
  c1.appendChild(el('div', 'chart tall', '<canvas id="ch-curve"></canvas>'));
  g.appendChild(c1);
  mk('ch-curve', {
    type: 'line',
    data: {
      labels: cv.map(c => c.doy),
      datasets: [
        { type: 'line', label: 'Typical score', data: cv.map(c => ({ x: c.doy, y: c.climatological_score })), borderColor: css('--teal'), backgroundColor: 'rgba(46,196,182,.14)', fill: true, borderWidth: 2, pointRadius: 0, tension: .35, yAxisID: 'y' },
        { type: 'line', label: 'Normal water °F', data: cv.map(c => ({ x: c.doy, y: c.sst_norm_f })), borderColor: css('--amber'), borderWidth: 1.4, borderDash: [4, 3], pointRadius: 0, tension: .35, yAxisID: 'y1' },
        { type: 'scatter', label: 'Today', data: s ? [{ x: cv.reduce((p, c) => Math.abs(c.doy - todayDoy) < Math.abs(p.doy - todayDoy) ? c : p, cv[0] || { doy: 1 }).doy, y: s.score }] : [], parsing: false, backgroundColor: css('--coral'), pointRadius: 6, pointStyle: 'circle', yAxisID: 'y' }
      ]
    },
    options: baseOpts({
      scales: {
        x: { type: 'linear', min: 1, max: 366, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), stepSize: 30.5, autoSkip: false, callback: v => ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Math.round(v / 30.5)] || '' } },
        y: { min: 0, max: 100, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } },
        y1: { position: 'right', suggestedMin: 52, suggestedMax: 78, grid: { display: false }, ticks: { color: css('--text-3'), callback: v => Math.round(v) + '°F' } }
      }
    })
  });

  const c2 = el('div', 'card');
  c2.appendChild(el('div', 'card-hd', `<div><h3>Temperature response &amp; zone spread</h3>
    <div class="sub">Trapezoidal suitability window (${sp.temp_reference} temperature) with each zone's current reading</div></div>`));
  c2.appendChild(el('div', 'chart tall', '<canvas id="ch-resp"></canvas>'));
  g.appendChild(c2);
  const xs = []; for (let t = Math.max(40, sp.sst_min_f - 4); t <= sp.sst_max_f + 4; t += 0.5) xs.push(t);
  const trap = t => t <= sp.sst_min_f || t >= sp.sst_max_f ? 2 :
    t < sp.sst_opt_lo_f ? 2 + 98 * (t - sp.sst_min_f) / (sp.sst_opt_lo_f - sp.sst_min_f) :
      t <= sp.sst_opt_hi_f ? 100 : 2 + 98 * (sp.sst_max_f - t) / (sp.sst_max_f - sp.sst_opt_hi_f);
  const pts = sp.zones.split(',').map(z2 => { const c = cond(z2, state.date); return c && c.sst_f != null ? { x: c.sst_f, y: trap(c.sst_f), z: ZM[z2].name } : null; }).filter(Boolean);
  mk('ch-resp', {
    data: {
      datasets: [
        { type: 'line', label: 'Suitability', data: xs.map(t => ({ x: t, y: trap(t) })), parsing: false, borderColor: css('--teal'), backgroundColor: 'rgba(46,196,182,.12)', fill: true, pointRadius: 0, borderWidth: 2 },
        { type: 'scatter', label: 'Zones today', data: pts, parsing: false, backgroundColor: css('--coral'), pointRadius: 5 }
      ]
    },
    options: baseOpts({
      plugins: { tooltip: { callbacks: { label: ctx => ctx.dataset.type === 'scatter' ? `${ctx.raw.z}: ${fmt(ctx.raw.x)} °F → ${fmt(ctx.raw.y, 0)}` : `${fmt(ctx.parsed.x)} °F → ${fmt(ctx.parsed.y, 0)}` } }, legend: { labels: { color: css('--text-2'), usePointStyle: true, boxWidth: 10 } } },
      scales: { x: { type: 'linear', grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), callback: v => v + '°F' } }, y: { min: 0, max: 105, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } } }
    })
  });

  const c3 = el('div', 'card'); c3.style.marginTop = '14px';
  c3.appendChild(el('div', 'card-hd', `<div><h3>Model parameters — all species</h3>
    <div class="sub">Response-curve inputs and driver weights. Every row is reproduced in the dataset and data dictionary.</div></div>`));
  c3.appendChild(el('div', 'tbl-wrap', `<table><thead><tr><th>Species</th><th>Group</th><th>Temp ref</th>
    <th>Min °F</th><th>Opt lo</th><th>Opt hi</th><th>Max °F</th><th>Anom pref</th><th>Tide</th><th>Moon</th>
    <th>Swell tol ft</th><th>Depth ft</th><th>Zones</th></tr></thead><tbody>` +
    D.species.map(s2 => `<tr><td>${s2.species}</td><td>${s2.group}</td><td>${s2.temp_reference}</td>
      <td>${fmt(s2.sst_min_f, 0)}</td><td>${fmt(s2.sst_opt_lo_f, 0)}</td><td>${fmt(s2.sst_opt_hi_f, 0)}</td>
      <td>${fmt(s2.sst_max_f, 0)}</td><td>${sign(s2.anomaly_pref, 1)}</td><td>${s2.tide_pref.replace(/_/g, ' ')}</td>
      <td>${s2.moon_pref.replace(/_/g, ' ')}</td><td>${fmt(s2.swell_tolerance_ft, 0)}</td>
      <td>${s2.depth_min_ft}–${s2.depth_max_ft}</td><td class="note">${s2.zones.split(',').length}</td></tr>`).join('') +
    '</tbody></table>'));
  host.appendChild(c3);

  const c4 = el('div', 'card'); c4.style.marginTop = '14px';
  c4.appendChild(el('div', 'card-hd', `<div><h3>${sp.species} — field notes</h3></div>`));
  c4.insertAdjacentHTML('beforeend', `<div class="note" style="font-size:12.5px;color:var(--text-2)">${sp.notes}</div>
    <div class="chips">${sp.sources.split(' ; ').map(u => `<a class="chip" href="${u}" target="_blank" rel="noopener">${new URL(u).hostname.replace('www.', '')}</a>`).join('')}</div>`);
  host.appendChild(c4);
}

/* ================================================== ENSO ================================================== */
function renderEnso() {
  const host = $('#tab-enso'); host.innerHTML = '';
  const e = D.enso_current[0] || {};
  const g = el('div', 'grid g3');
  g.appendChild(el('div', 'card kpi', `<div class="lab">Current regime</div>
    <div class="val" style="font-size:22px">${ensoLabel(e.regime)}</div>
    <div class="meta">${e.season || ''} ${e.year || ''} · ONI ${sign(e.oni, 2)} °C</div>`));
  g.appendChild(el('div', 'card kpi', `<div class="lab">Trailing 12-month ONI</div>
    <div class="val">${sign(e.trailing_12mo_mean_oni, 2)}<small> °C</small></div>
    <div class="meta">Smoothed regime persistence</div>`));
  const sens = D.enso_sensitivity.filter(s => s.zone_id === state.zone);
  const mo = new Date(state.date + 'T12:00:00Z').getUTCMonth() + 1;
  const sm = sens.find(s => s.month === mo);
  g.appendChild(el('div', 'card kpi', `<div class="lab">${ZM[state.zone].name} ENSO sensitivity</div>
    <div class="val">${sm ? sign(sm.anom_f_per_oni, 2) : '—'}<small> °F per +1 ONI</small></div>
    <div class="meta">${sm ? `month ${mo}, r = ${fmt(sm.r, 2)}, n = ${sm.n_days} days` : 'insufficient overlap for this month'}</div>`));
  host.appendChild(g);

  const c1 = el('div', 'card'); c1.style.marginTop = '14px';
  c1.appendChild(el('div', 'card-hd', `<div><h3>Oceanic Niño Index, 1980–present</h3>
    <div class="sub">CPC ONI 3-month running mean. Above +0.5 °C is El Niño, below −0.5 °C is La Niña.</div></div>`));
  c1.appendChild(el('div', 'chart tall', '<canvas id="ch-oni"></canvas>'));
  host.appendChild(c1);
  const o = D.oni;
  mk('ch-oni', {
    type: 'bar',
    data: {
      labels: o.map(r => `${r.year}-${String(r.month).padStart(2, '0')}`),
      datasets: [{
        label: 'ONI °C', data: o.map(r => r.oni), borderRadius: 1,
        backgroundColor: o.map(r => r.oni >= 0.5 ? 'rgba(232,105,95,.85)' : r.oni <= -0.5 ? 'rgba(91,192,235,.85)' : 'rgba(140,160,172,.5)')
      }]
    },
    options: baseOpts({ plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: css('--text-3'), font: { size: 9.5 }, maxTicksLimit: 14 } }, y: { grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), callback: v => v + '°' } } } })
  });

  const g2 = el('div', 'grid g2'); g2.style.marginTop = '14px';
  const c2 = el('div', 'card');
  c2.appendChild(el('div', 'card-hd', `<div><h3>Regime composites — SST anomaly by zone</h3>
    <div class="sub">Mean satellite SST departure in each ENSO regime for the current month, from the project's own record</div></div>`));
  const cm = D.enso_composite.filter(r => r.month === mo);
  c2.appendChild(el('div', 'tbl-wrap', cm.length ? `<table><thead><tr><th>Zone</th><th>Regime</th>
    <th>Mean anomaly °F</th><th>SD</th><th>Days</th></tr></thead><tbody>` +
    cm.map(r => `<tr><td>${ZM[r.zone_id]?.name || r.zone_id}</td><td>${ensoLabel(r.simple_regime)}</td>
      <td>${deltaTag(r.mean_anom_f)}</td><td>${fmt(r.sd_anom_f)}</td><td>${r.n_days}</td></tr>`).join('') +
    '</tbody></table>' : '<div class="note">Composite not yet available for this month.</div>'));
  g2.appendChild(c2);

  const c3 = el('div', 'card');
  c3.appendChild(el('div', 'card-hd', `<div><h3>How the regime moves the forecast</h3></div>`));
  c3.insertAdjacentHTML('beforeend', `<div class="note" style="font-size:12.5px;color:var(--text-2);line-height:1.6">
    Each species carries an <b>anomaly preference</b> from −1 (cold-water specialist) to +1 (warm-water visitor).
    The score is multiplied by <span class="mono">1 + pref × (anomaly / 2.5 °F) × 0.45</span>, clamped to 0.35–1.35,
    so a +2 °F warm anomaly lifts a tropical species like dorado about 35% and trims a cold-water rockfish bite
    by a similar amount. The anomaly itself is measured against this project's own day-of-year climatology, so the
    adjustment is empirical rather than a fixed El Niño rule of thumb. Zone-level ENSO sensitivity (°F of SST
    anomaly per +1 °C ONI) is regressed from the satellite record and shown above as a diagnostic.</div>
    <div class="tbl-wrap" style="margin-top:12px"><table><thead><tr><th>Species</th><th>Anomaly preference</th>
    <th>Effect of +2 °F</th></tr></thead><tbody>` +
    D.species.map(s => `<tr><td>${s.species}</td><td>${sign(s.anomaly_pref, 1)}</td>
      <td>${deltaTag(Math.round((s.anomaly_pref * (2 / 2.5) * 0.45) * 100))}%</td></tr>`).join('') +
    '</tbody></table></div>');
  g2.appendChild(c3);
  host.appendChild(g2);
}

/* ================================================== VALIDATION ================================================== */
function renderValidation() {
  const host = $('#tab-validation'); host.innerHTML = '';
  const v = D.validation;
  const nDays = Math.max(...v.map(r => r.n_days), 0);
  const pos = v.filter(r => r.spearman_rho > 0).length;

  host.appendChild(el('div', 'callout', `<b>Read this first.</b> Skill is measured against ${D.trips.length.toLocaleString()}
    species-rows from ${nDays} days of public dock totals (${D.meta.dates[0]} → ${TODAY}) — a single late-summer window.
    Dock totals reflect where boats chose to go and how long they fished as much as how the fish bit, and effort is
    normalized only by anglers and trip length. With ${nDays} days, rank correlations have wide confidence intervals:
    ${pos} of ${v.length} species show positive rank agreement. Treat this as a preliminary sanity check, not a
    validated forecast skill score. A defensible skill estimate needs a full seasonal cycle of catch data.`));

  const c1 = el('div', 'card'); c1.style.marginTop = '14px';
  c1.appendChild(el('div', 'card-hd', `<div><h3>Modeled score vs observed catch per angler-day</h3>
    <div class="sub">Spearman rank correlation between each day's best modeled score and reported CPUE</div></div>`));
  c1.appendChild(el('div', 'chart', '<canvas id="ch-val"></canvas>'));
  host.appendChild(c1);
  mk('ch-val', {
    type: 'bar',
    data: {
      labels: v.map(r => r.species),
      datasets: [{
        label: 'Spearman ρ', data: v.map(r => r.spearman_rho), borderRadius: 3,
        backgroundColor: v.map(r => r.spearman_rho >= 0 ? 'rgba(65,214,155,.8)' : 'rgba(232,105,95,.8)')
      }]
    },
    options: baseOpts({ indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { min: -1, max: 1, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } }, y: { grid: { display: false }, ticks: { color: css('--text-2'), font: { size: 11 } } } } })
  });

  const c2 = el('div', 'card'); c2.style.marginTop = '14px';
  c2.appendChild(el('div', 'card-hd', `<div><h3>Validation detail</h3>
    <div class="sub">CPUE normalized by anglers × trip length; raw (un-normalized) correlation shown for comparison</div></div>`));
  c2.appendChild(el('div', 'tbl-wrap', `<table><thead><tr><th>Species</th><th>Days</th><th>Spearman ρ</th>
    <th>Pearson r</th><th>ρ raw CPUE</th><th>Mean fish / angler-day</th><th>Mean score</th><th>Fish</th>
    <th>Anglers</th></tr></thead><tbody>` +
    v.map(r => `<tr><td>${r.species}</td><td>${r.n_days}</td>
      <td style="color:${r.spearman_rho >= 0 ? css('--good') : css('--bad')};font-weight:600">${fmt(r.spearman_rho, 2)}</td>
      <td>${fmt(r.pearson_r, 2)}</td><td>${fmt(r.spearman_rho_raw_cpue, 2)}</td>
      <td>${fmt(r.mean_cpue_per_angler_day, 2)}</td><td>${fmt(r.mean_score, 0)}</td>
      <td>${r.total_fish.toLocaleString()}</td><td>${r.total_anglers.toLocaleString()}</td></tr>`).join('') +
    '</tbody></table>'));
  host.appendChild(c2);

  const c3 = el('div', 'card'); c3.style.marginTop = '14px';
  c3.appendChild(el('div', 'card-hd', `<div><h3>Observed catch per angler-day</h3>
    <div class="sub">Public dock totals aggregated across San Diego landings</div></div>`));
  c3.appendChild(el('div', 'chart', '<canvas id="ch-cpue"></canvas>'));
  host.appendChild(c3);
  const dts = [...new Set(D.cpue.map(r => r.date))].sort();
  const top = [...new Set(D.cpue.map(r => r.species_id))]
    .map(s => ({ s, tot: D.cpue.filter(r => r.species_id === s).reduce((a, b) => a + b.kept, 0) }))
    .sort((a, b) => b.tot - a.tot).slice(0, 6).map(x => x.s);
  const pal = [css('--teal'), css('--amber'), css('--sky'), css('--violet'), css('--coral'), css('--good')];
  mk('ch-cpue', {
    type: 'line',
    data: {
      labels: dts.map(dLabel),
      datasets: top.map((s, i) => ({
        label: SM[s]?.species || s, borderColor: pal[i], backgroundColor: pal[i], borderWidth: 2, tension: .3, pointRadius: 2,
        data: dts.map(d => D.cpue.find(r => r.date === d && r.species_id === s)?.cpue_per_angler_day ?? null)
      }))
    },
    options: baseOpts({ scales: { x: { grid: { display: false }, ticks: { color: css('--text-3'), font: { size: 9.5 }, maxRotation: 45, minRotation: 45 } }, y: { beginAtZero: true, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } } } })
  });

  const c4 = el('div', 'card'); c4.style.marginTop = '14px';
  c4.appendChild(el('div', 'card-hd', '<div><h3>How the score is computed</h3>\n    <div class="sub">Every number in this dashboard comes from these five steps — no hidden tuning</div></div>'));
  c4.insertAdjacentHTML('beforeend', `<div class="meth">
    <ol>
      <li><b>Driver suitabilities.</b> Water temperature, season, swell, wind, tide state, moon phase, barometric
        trend, thermal-break strength and depth are each mapped to a 0–1 suitability from that species'
        published preference window. Rockfish and sheephead use a bottom-temperature proxy,
        <code>sst − 14·(1 − e<sup>−depth/160</sup>)</code>, rather than surface temperature.</li>
      <li><b>Weighted geometric mean.</b> <code>core = exp( Σ wᵢ·ln(termᵢ) / Σ wᵢ )</code>. A geometric mean is used so
        one disqualifying driver — water far outside the window, or a closed season — suppresses the score instead
        of being averaged away by favourable drivers.</li>
      <li><b>Climate-regime adjustment.</b> <code>anomaly_term = clamp(1 + pref · (sst_anom/2.5) · 0.45, 0.35, 1.35)</code>.
        Warm-water species gain in a positive anomaly, cold-water species lose. The anomaly itself is measured
        against the day-of-year baseline for that exact zone: ${D.meta.baseline_satellite || '—'} for satellite SST,
        ${D.meta.baseline_shore || '—'} for the shore thermistors and ${D.meta.baseline_buoy || '—'} for buoy wave and wind.
        The satellite record is shorter than the full MUR era (2002–present) because each zone-year is a separate
        sequential ERDDAP request; buoy history carries the long baseline.</li>
      <li><b>Fishability.</b> The result is multiplied by <code>0.35 + 0.65 · fishability</code>, a sea-state term for
        whether a small boat can reasonably work the zone. It never zeroes a score, because conditions can
        improve inside a day.</li>
      <li><b>Soft ceiling.</b> Values above 0.90 are compressed, not clipped:
        <code>0.90 + 0.10 · (1 − e<sup>−(v−0.90)/0.10</sup>)</code>. Clipping made several species tie at exactly 100 in a
        strong warm anomaly and destroyed the ranking at the top; this mapping is strictly monotonic, so order is
        preserved and 100 is approached but never reached.</li>
    </ol>
    <p class="note"><b>Comparison against normal</b> re-runs the identical model on climatological temperature, wave
      and wind with neutral tide, moon and pressure terms, which is what makes “better or worse than typical for this
      date” a like-for-like comparison rather than a comparison of two different formulas.</p>
  </div>`);
  host.appendChild(c4);
}

/* ================================================== FORECAST ACCURACY ================================================== */
const pct = v => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Math.round(v * 100) + '%';
const MODEL_NAME = { v1: 'Model v1 (production)', baseline_climatology: 'A · Seasonal climatology', baseline_enso: 'B · ENSO/SST regime' };
const mName = id => MODEL_NAME[id] || (id + ' (challenger)');
const FLAG = {
  insufficient: ['Insufficient sample', 'status-bad'],
  preliminary: ['Preliminary', 'status-warn'],
  reportable: ['Reportable', '']
};
const flagPill = f => `<span class="badge ${FLAG[f]?.[1] || ''}" style="padding:2px 8px;font-size:11px">${FLAG[f]?.[0] || f || '—'}</span>`;
const WIN = { rolling_30d: 'Rolling 30 days', rolling_90d: 'Rolling 90 days', season_to_date: 'Season to date', all: 'All verified' };
const HG = { short_0_7: 'Short-term (days 0–7)', outlook_8_30: 'Outlook (days 8–30)' };

function renderAccuracy() {
  const host = $('#tab-accuracy'); host.innerHTML = '';
  const L = D.learning;
  if (!L || !L.evaluation || !L.evaluation.headline) {
    host.appendChild(el('div', 'callout', '<b>Forecast accuracy is not available in this build.</b> The learning ledger has not produced an evaluation yet.'));
    return;
  }
  const E = L.evaluation, lab = E.labels || {}, pr = E.pairs || {}, led = E.ledger || {};
  const H = (w, dim, st, m) => E.headline.find(r => r.window === w && r.dimension === dim && r.stratum === st && r.model_id === m);
  const prod = L.registry?.production_model || 'v1';

  host.appendChild(el('div', 'callout', `<b>Read this first — what this page can and cannot tell you.</b>
    Every forecast is frozen in an append-only ledger at the moment it is issued and is only scored after the day has
    been fished and reported. Outcomes come from public San Diego dock totals: they measure where boats chose to go and
    how many anglers fished as much as how the fish bit. A day with no reports is <b>not</b> scored as a poor bite — a
    Poor label needs enough effort (≥2 eligible trips and ≥30 angler-days) and a species that was demonstrably being
    caught in that region. Labels are provisional for 3 days after the trip date because boats update counts late.
    The ledger currently holds <b>${led.issuances || 0} issuance${led.issuances === 1 ? '' : 's'}</b>
    (${led.first_issue || '—'} → ${led.last_issue || '—'}); <b>${pr.issuances || 0}</b> of them can be verified so far.
    Every stratum below carries a sample-size flag and strong conclusions are suppressed until it is
    <i>Reportable</i> (≥100 pairs, ≥30 target days and ≥10 issuances). Pairs from one issuance are highly correlated, so
    the current numbers are a pipeline check, not a skill estimate.`));

  /* KPI matrix: windows x horizon groups */
  const kc = el('div', 'card'); kc.style.marginTop = '14px';
  kc.appendChild(el('div', 'card-hd', `<div><h3>Production model accuracy</h3>
    <div class="sub">${mName(prod)} · exact class hit, within-one-class, MAE and Brier (P(Good or better)) · skill vs seasonal climatology</div></div>`));
  const kg = el('div', 'grid g3');
  ['rolling_30d', 'rolling_90d', 'season_to_date'].forEach(w => {
    const cells = ['short_0_7', 'outlook_8_30'].map(hg => {
      const r = H(w, 'horizon_group', hg, prod);
      if (!r) return `<div class="meta" style="margin-top:8px"><b>${HG[hg]}</b> · no verified pairs yet</div>`;
      const strong = r.sample_flag === 'reportable';
      return `<div style="margin-top:10px"><div class="lab" style="display:flex;justify-content:space-between;gap:6px;align-items:center">
        <span>${HG[hg]}</span>${flagPill(r.sample_flag)}</div>
        <div class="val" style="${strong ? '' : 'opacity:.6'}">${pct(r.exact_hit)} <small>exact · ${pct(r.within_one)} ±1 class</small></div>
        <div class="meta">MAE ${fmt(r.mae, 1)} pts · Brier ${fmt(r.brier, 3)} · skill ${r.brier_skill_vs_clim == null ? '—' : sign(r.brier_skill_vs_clim * 100, 0) + '%'}
        · n=${r.n_pairs} pairs / ${r.n_target_dates} days / ${r.n_issuances} issuance${r.n_issuances === 1 ? '' : 's'}</div></div>`;
    }).join('');
    kg.appendChild(el('div', 'kpi', `<div class="lab"><b>${WIN[w]}</b></div>${cells}`));
  });
  kc.appendChild(kg);
  kc.appendChild(el('p', 'note', `Values are shown faded until the stratum is Reportable. Skill = 1 − Brier(model)/Brier(climatology); positive means better than the seasonal baseline.
    Lead-0 forecasts issued after local noon are excluded (the day was already being fished).`));
  host.appendChild(kc);

  /* Model comparison by lead bucket */
  const mc = el('div', 'card'); mc.style.marginTop = '14px';
  mc.appendChild(el('div', 'card-hd', `<div><h3>Model comparison by forecast lead</h3>
    <div class="sub">Walk-forward over as-issued records only · A = seasonal climatology · B = ENSO/SST-regime baseline · C = production v1 · D = shadow challengers</div></div>`));
  const leads = ['0-3', '4-7', '8-14', '15-30'];
  const models = E.models || [];
  const BL = (lb, m) => (E.by_lead || []).find(r => r.stratum === lb && r.model_id === m);
  mc.appendChild(el('div', 'tbl-wrap', `<table><thead><tr><th>Lead (days)</th><th class="tl">Model</th><th class="tl">Sample</th><th>Exact</th><th>±1 class</th>
    <th>MAE</th><th>Brier</th><th>Skill vs A</th><th>Precision G+</th><th>Recall G+</th></tr></thead><tbody>` +
    leads.flatMap(lb => models.map((m, i) => {
      const r = BL(lb, m);
      if (!r) return i === 0 ? `<tr><td>${lb}</td><td colspan="9" class="note">No verified pairs yet</td></tr>` : '';
      const dim = r.sample_flag === 'insufficient' ? ' style="opacity:.55"' : '';
      return `<tr${dim}><td>${i === 0 ? '<b>' + lb + '</b>' : ''}</td><td class="tl">${mName(m)}</td><td class="tl">${flagPill(r.sample_flag)} <span class="note">${r.n_pairs}/${r.n_target_dates}d</span></td>
        <td>${pct(r.exact_hit)}</td><td>${pct(r.within_one)}</td><td>${fmt(r.mae, 1)}</td><td>${fmt(r.brier, 3)}</td>
        <td>${m === 'baseline_climatology' ? '—' : (r.brier_skill_vs_clim == null ? '—' : sign(r.brier_skill_vs_clim * 100, 0) + '%')}</td>
        <td>${r.precision_good == null ? '<span class="note">n&lt;10</span>' : pct(r.precision_good)}</td>
        <td>${r.recall_good == null ? '<span class="note">n&lt;10</span>' : pct(r.recall_good)}</td></tr>`;
    })).join('') + '</tbody></table>'));
  mc.appendChild(el('p', 'note', `Days 15–30 are scored as a probabilistic seasonal/regime outlook (calendar seasonality, ENSO analogs, tide/moon timing);
    no deterministic daily swell or wind is issued or evaluated beyond day 14.`));
  host.appendChild(mc);

  /* Observed vs forecast timeline + reliability */
  const g = el('div', 'grid g2'); g.style.marginTop = '14px';
  const regionOf = Object.entries(L.regions || {}).find(([, r]) => r.zones.includes(state.zone));
  const rid = regionOf ? regionOf[0] : null;
  const tl = (E.timeline || []).filter(r => r.region_id === rid && r.species_id === state.species);
  const t1 = el('div', 'card');
  t1.appendChild(el('div', 'card-hd', `<div><h3>Forecast vs observed · ${SM[state.species]?.species || state.species}</h3>
    <div class="sub">${regionOf ? regionOf[1].name : 'This zone has no outcome region'} · shortest-lead issued forecast vs effort-normalized dock index (0–100)</div></div>`));
  if (tl.length) {
    t1.appendChild(el('div', 'chart', '<canvas id="ch-acc-tl"></canvas>'));
  } else {
    t1.appendChild(el('p', 'note', regionOf && !regionOf[1].labelled ? 'Bays and surf have no reliable public effort signal, so they are not scored.'
      : 'No verified forecast/outcome pairs yet for this species and region. Choose another species or zone, or check back as the ledger grows.'));
  }
  g.appendChild(t1);
  const t2 = el('div', 'card');
  t2.appendChild(el('div', 'card-hd', `<div><h3>Confidence calibration</h3>
    <div class="sub">Forecast P(Good or better) vs observed frequency · production model · all verified pairs</div></div>`));
  t2.appendChild(el('div', 'chart', '<canvas id="ch-acc-rel"></canvas>'));
  g.appendChild(t2);
  host.appendChild(g);
  if (tl.length) {
    mk('ch-acc-tl', {
      type: 'line',
      data: {
        labels: tl.map(r => dLabel(r.target_date)),
        datasets: [
          { label: 'Observed', data: tl.map(r => r.observed_score), borderColor: css('--amber'), backgroundColor: css('--amber'), showLine: false, pointRadius: 4 },
          { label: 'v1 forecast', data: tl.map(r => r.bite_score), borderColor: css('--teal'), backgroundColor: css('--teal'), borderWidth: 2, tension: .3, pointRadius: 1.5 },
          { label: 'Climatology (A)', data: tl.map(r => r.baseline_clim_score), borderColor: css('--text-3'), borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0 }
        ]
      },
      options: baseOpts({
        plugins: { legend: { labels: { color: css('--text-2'), boxWidth: 10, font: { size: 11 } } },
          tooltip: { callbacks: { afterBody: items => { const r = tl[items[0].dataIndex]; return `lead ${r.lead_days} d · label ${r.label_status}`; } } } },
        scales: { x: { grid: { display: false }, ticks: { color: css('--text-3'), font: { size: 9.5 } } }, y: { min: 0, max: 100, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3') } } }
      })
    });
  }
  const rel = E.reliability || [];
  const pal = { short_0_7: css('--teal'), outlook_8_30: css('--violet') };
  mk('ch-acc-rel', {
    type: 'scatter',
    data: {
      datasets: [{ label: 'Perfect calibration', type: 'line', data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], borderColor: css('--text-3'), borderDash: [4, 4], pointRadius: 0, borderWidth: 1 }]
        .concat(['short_0_7', 'outlook_8_30'].map(hg => ({
          label: HG[hg], data: rel.filter(r => r.horizon_group === hg).map(r => ({ x: r.mean_p, y: r.obs_rate, n: r.n })),
          backgroundColor: pal[hg], borderColor: pal[hg], clip: false, pointRadius: c => Math.max(3, Math.min(11, Math.sqrt(c.raw?.n || 1)))
        })))
    },
    options: baseOpts({
      layout: { padding: { top: 10, right: 12 } },
      interaction: { mode: 'nearest', intersect: true },
      plugins: { tooltip: { callbacks: { label: c => c.raw?.n ? `${c.dataset.label}: forecast ${pct(c.raw.x)} → observed ${pct(c.raw.y)} (n=${c.raw.n})` : '' } } },
      scales: { x: { type: 'linear', min: 0, max: 1, title: { display: true, text: 'forecast probability', color: css('--text-3') }, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), callback: v => pct(v) } },
        y: { type: 'linear', min: 0, max: 1, title: { display: true, text: 'observed frequency', color: css('--text-3') }, grid: { color: css('--line-soft') }, ticks: { color: css('--text-3'), callback: v => pct(v) } } }
    })
  });

  /* Why this forecast changed */
  const wc = el('div', 'card'); wc.style.marginTop = '14px';
  const cm = L.changes_meta;
  wc.appendChild(el('div', 'card-hd', `<div><h3>Why this forecast changed · ${ZM[state.zone]?.name || state.zone} · ${SM[state.species]?.species || state.species}</h3>
    <div class="sub">${cm ? `Latest issuance ${cm.current} vs prior ${cm.prior}` : 'Compares the active forecast with the prior issuance'} · driver contributions in score points, summing to the change</div></div>`));
  const allRows = (L.changes || []).filter(r => r.zone_id === state.zone && r.species_id === state.species);
  const material = r => Math.abs(r.delta || 0) >= 1 || r.class_prior !== r.class_now || r.tier_prior !== r.tier_now || r.notes;
  const rows = state.chgAll ? allRows : allRows.filter(material);
  if (!allRows.length) {
    wc.appendChild(el('p', 'note', (L.changes || []).length
      ? 'This zone/species pair has no overlapping target days between the last two issuances.'
      : `Not available yet: the issuances in the ledger (${(L.ledger_index || []).map(r => r.issue_local_date).join(', ') || '—'}) do not cover any of the same target days.
         From the next daily refresh, every day that both issuances forecast will be compared here.`));
  } else {
    const nMat = allRows.filter(material).length;
    const big = [...allRows].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))[0];
    const bar = el('div', '', `<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
      <div class="note">${nMat} of ${allRows.length} target days changed by ≥1 point, class or confidence tier${nMat ? ` · largest ${dLabel(big.target_date)} ${sign(big.delta)} pts` : ''}</div>
      <div class="seg sm" role="group"><button data-chg="0" aria-pressed="${!state.chgAll}">Material changes</button><button data-chg="1" aria-pressed="${!!state.chgAll}">All days</button></div></div>`);
    bar.querySelectorAll('button[data-chg]').forEach(b => b.onclick = () => { state.chgAll = b.dataset.chg === '1'; renderAccuracy(); });
    wc.appendChild(bar);
    if (!rows.length) {
      wc.appendChild(el('p', 'note', 'No target day changed materially between the two issuances for this zone and species.'));
    } else {
      wc.appendChild(el('div', 'tbl-wrap scroll-tall', `<table><thead><tr><th>Target day</th><th>Lead</th><th>Prior</th><th>Now</th><th>Δ</th>
        <th>Class</th><th class="tl">Main drivers (pts)</th><th class="tl">Input changes</th><th class="tl">Notes</th></tr></thead><tbody>` +
        rows.map(r => `<tr><td>${dLabel(r.target_date)}</td><td>${r.lead_prior}→${r.lead_now} d</td><td>${sTxt(r.score_prior)}</td><td>${sTxt(r.score_now)}</td>
          <td>${deltaTag(r.delta)}</td><td>${r.class_prior === r.class_now ? r.class_now : `${r.class_prior} → <b>${r.class_now}</b>`}</td>
          <td style="white-space:normal;text-align:left;min-width:180px">${r.top_drivers || '—'}</td><td class="note" style="white-space:normal;text-align:left;min-width:160px">${r.input_changes || '—'}</td><td class="note" style="white-space:normal;text-align:left;min-width:160px">${r.notes || ''}</td></tr>`).join('') + '</tbody></table>'));
    }
  }
  host.appendChild(wc);

  /* Strata */
  const sc2 = el('div', 'card'); sc2.style.marginTop = '14px';
  sc2.appendChild(el('div', 'card-hd', `<div><h3>Accuracy by species, region, month, season and ENSO regime</h3>
    <div class="sub">Production model vs climatology, all verified pairs · strata below the minimum sample are greyed and their skill is withheld</div></div>`));
  const DIM = { species_id: 'Species', region_id: 'Region', month: 'Month', season: 'Season', enso_regime: 'ENSO regime', label_status: 'Label status' };
  const strata = (E.strata || []).filter(r => r.window === 'all');
  const nameOf = (dim, s) => dim === 'species_id' ? (SM[s]?.species || s) : dim === 'region_id' ? (L.regions?.[s]?.name || s) : dim === 'enso_regime' ? ensoLabel(s) : s;
  sc2.appendChild(el('div', 'tbl-wrap scroll-tall', `<table><thead><tr><th>Dimension</th><th class="tl">Stratum</th><th class="tl">Sample</th><th>Exact</th><th>±1</th><th>MAE</th>
    <th>Brier</th><th>Clim. Brier</th><th>Skill vs A</th></tr></thead><tbody>` +
    Object.keys(DIM).flatMap(dim => {
      const ss = [...new Set(strata.filter(r => r.dimension === dim).map(r => r.stratum))].sort();
      return ss.map(s => {
        const r = strata.find(x => x.dimension === dim && x.stratum === s && x.model_id === prod);
        const c = strata.find(x => x.dimension === dim && x.stratum === s && x.model_id === 'baseline_climatology');
        if (!r) return '';
        const weak = r.sample_flag === 'insufficient';
        return `<tr${weak ? ' style="opacity:.55"' : ''}><td>${DIM[dim]}</td><td class="tl">${nameOf(dim, s)}</td><td class="tl">${flagPill(r.sample_flag)} <span class="note">${r.n_pairs}/${r.n_target_dates}d</span></td>
          <td>${pct(r.exact_hit)}</td><td>${pct(r.within_one)}</td><td>${fmt(r.mae, 1)}</td><td>${fmt(r.brier, 3)}</td><td>${fmt(c?.brier, 3)}</td>
          <td>${weak ? '<span class="note">withheld</span>' : (r.brier_skill_vs_clim == null ? '—' : sign(r.brier_skill_vs_clim * 100, 0) + '%')}</td></tr>`;
      });
    }).join('') + '</tbody></table>'));
  host.appendChild(sc2);

  /* Ledger, labels, governance */
  const g3 = el('div', 'grid g2'); g3.style.marginTop = '14px';
  const lc = el('div', 'card');
  lc.appendChild(el('div', 'card-hd', `<div><h3>Ledger and outcome status</h3><div class="sub">Immutable forecasts · append-only raw reports · versioned labels (${E.label_version || 'L1'})</div></div>`));
  lc.appendChild(el('div', 'tbl-wrap', `<table class="wraptxt"><tbody>
    <tr><td>Forecast issuances</td><td><b>${led.issuances || 0}</b> · ${(led.forecast_rows || 0).toLocaleString()} rows · ${led.first_issue || '—'} → ${led.last_issue || '—'}</td></tr>
    <tr><td>Outcome label rows</td><td>${(lab.rows || 0).toLocaleString()} (${lab.first_fishing_date || '—'} → ${lab.last_fishing_date || '—'})</td></tr>
    <tr><td>Labelled</td><td>${lab.labelled || 0} · ${lab.final || 0} final · ${lab.provisional || 0} provisional</td></tr>
    <tr><td>Not labelled</td><td>${lab.insufficient_effort || 0} insufficient effort · others have no species-region reference</td></tr>
    <tr><td>Effort-adjusted zero catch (Poor)</td><td>${lab.effort_adjusted_zero || 0}</td></tr>
    <tr><td>Reporting lag (trip → report date)</td><td>median ${fmt(lab.median_reporting_lag_days, 0)} d · labels final after 3 d</td></tr>
    <tr><td>Collection lag</td><td>${lab.median_publication_lag_days_live == null ? `live: not yet measured · ${lab.backfilled || 0} labels were backfilled from archived pages` : `median ${fmt(lab.median_publication_lag_days_live, 0)} d`}</td></tr>
    <tr><td>Verified pairs</td><td>${pr.total || 0} (${pr.target_dates || 0} target days · ${pr.excluded_late_lead0_rows || 0} late lead-0 rows excluded)</td></tr>
    </tbody></table>`));
  lc.appendChild(el('div', 'chips', ['model_registry.json', 'model_evaluation.csv', 'outcome_ledger.csv', 'index.csv', 'update_log.json', 'FORECAST_LEARNING.md']
    .map(f => `<a class="chip" href="downloads/learning/${f}" download>${f === 'index.csv' ? 'forecast_ledger index.csv' : f}</a>`).join('')));
  g3.appendChild(lc);
  const rc = el('div', 'card');
  const gov = L.registry?.governance || {};
  rc.appendChild(el('div', 'card-hd', `<div><h3>Model registry and challengers</h3><div class="sub">Live model changes need a passing review, a human approval flag and a reviewed code change · auto-retrain ${gov.auto_retrain ? 'ON' : 'off'}</div></div>`));
  (L.registry?.models || []).forEach(m => {
    const ch = (E.challengers || []).find(c => c.model_id === m.model_id);
    const dec = ch ? ch.recommendation : (m.promotion_decision?.decision || '—');
    const fails = ch ? ch.checks.filter(x => !x.passed) : [];
    rc.appendChild(el('div', 'reg', `<div class="rn">${m.model_id}${m.role ? ` <span class="note" style="font-weight:400">· ${m.role}</span>` : ''}</div>
      <div class="rs"><span class="pill">${m.status}</span><span class="pill">${dec.replace(/_/g, ' ')}</span>
        <span class="pill">${m.human_approval?.approved ? 'approved' : 'not approved'}</span></div>
      <div class="rd">${m.description || ''}${fails.length ? `<ul>${fails.map(x => `<li>${x.check}: ${x.detail}</li>`).join('')}</ul>` : ''}</div>`));
  });
  const crit = gov.promotion_criteria || {};
  rc.appendChild(el('p', 'note', `Promotion is only recommended when a challenger has ≥${crit.min_pairs} pairs over ≥${crit.min_target_dates} target days and ≥${crit.min_issuances} issuances,
    improves aggregate Brier by ≥${Math.round((crit.min_aggregate_brier_improvement || 0) * 100)}% with bootstrap P ≥ ${crit.min_bootstrap_p_positive},
    does not worsen MAE by more than ${crit.max_mae_degradation_pts} pts, does not degrade priority species (${(gov.priority_species || []).map(s => SM[s]?.species || s).join(', ')})
    or regions (${(gov.priority_regions || []).map(r => L.regions?.[r]?.name || r).join(', ')}) and keeps 0–7 day accuracy.`));
  g3.appendChild(rc);
  host.appendChild(g3);
}

/* ================================================== DATA & SOURCES ================================================== */
function renderData() {
  const host = $('#tab-data'); host.innerHTML = '';
  const SS = D.source_status || {};
  const SR = SS.sources || [];
  const sc = SS.summary || {};
  const cS = el('div', 'card');
  cS.appendChild(el('div', 'card-hd', `<div><h3>Build and source freshness</h3>
    <div class="sub">Build time is separate from each source's newest observation or forecast issue time</div></div>
    <a class="chip lnk" href="downloads/csv/source_status.json" download>source_status.json</a>`));
  cS.insertAdjacentHTML('beforeend', '<div class="status-summary">' +
    ['fresh', 'delayed', 'stale', 'failed', 'cached', 'not_due'].filter(k => sc[k])
      .map(k => `<span class="status-count ${k}">${k.replace('_', ' ')} ${sc[k]}</span>`).join('') +
    '</div><div class="tbl-wrap"><table><thead><tr><th>Source</th><th>Status</th><th>Newest data</th><th>Last success</th><th>Cadence</th><th>Note</th></tr></thead><tbody>' +
    SR.map(r => `<tr><td>${r.display_name}</td><td><span class="pill">${r.freshness}${r.used_cached_data ? ' · cached' : ''}</span></td>
      <td>${r.newest_valid_source_timestamp || '—'}</td><td>${r.last_successful_fetch_local || '—'}</td>
      <td>${r.expected_cadence}</td><td class="wrap-ok status-note">${r.error || r.note || '—'}</td></tr>`).join('') +
    '</tbody></table></div>');
  host.appendChild(cS);

  const c0 = el('div', 'card');
  c0.style.marginTop = '14px';
  c0.appendChild(el('div', 'card-hd', `<div><h3>Downloads</h3>
    <div class="sub">Full cleaned dataset, every intermediate table, and the written documentation</div></div>`));
  c0.insertAdjacentHTML('beforeend', `<div class="chips" style="gap:10px">
    <a class="dl" href="downloads/socal_fishing_dataset.xlsx" download>⬇ socal_fishing_dataset.xlsx</a>
    <a class="dl" href="downloads/SOURCE_REGISTRY.md" download>⬇ SOURCE_REGISTRY.md</a>
    <a class="dl" href="downloads/DATA_DICTIONARY.md" download>⬇ DATA_DICTIONARY.md</a></div>
    <div class="sub" style="margin-top:14px">Individual tables as CSV</div>
    <div class="chips">` +
    [...new Set(D.dictionary.map(r => r.table))].map(t =>
      `<a class="chip lnk" href="downloads/csv/${t}.csv" download>${t}.csv</a>`).join('') +
    // The extended-range tables are new and are not described in D.dictionary, which was baked by
    // the original build, so list them explicitly rather than leaving them undownloadable.
    ['extended_outlook', 'extended_scores', 'extended_field_provenance', 'forecast_confidence',
      'enso_analog_years', 'enso_analog_zone_anomaly', 'cpc_outlook_current'].map(t =>
      `<a class="chip lnk" href="downloads/csv/${t}.csv" download>${t}.csv</a>`).join('') + '</div>');
  host.appendChild(c0);

  const c1 = el('div', 'card'); c1.style.marginTop = '14px';
  c1.appendChild(el('div', 'card-hd', `<div><h3>Source registry</h3>
    <div class="sub">Every feed behind this dashboard, with cadence, coverage and known limitations</div></div>`));
  c1.insertAdjacentHTML('beforeend', '<div class="srcgrid">' + D.registry.map(r => `<div class="src">
      <a class="src-t" href="${r.url}" target="_blank" rel="noopener">${r.name}</a>
      <div class="src-p">${r.provider}</div>
      <div class="src-u">${r.used_for}</div>
      <dl class="src-dl">
        <dt>Coverage</dt><dd>${r.coverage}</dd>
        <dt>Cadence</dt><dd>${r.cadence}</dd>
        <dt>Access</dt><dd>${r.access}</dd>
        <dt>Units</dt><dd>${r.units}</dd>
        <dt>Licence</dt><dd>${r.license}</dd>
      </dl>
      <div class="src-lim"><b>Limitations</b> ${r.limitations}</div>
      <code class="src-ep">${r.endpoint.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</code>
    </div>`).join('') + '</div>');
  host.appendChild(c1);

  const c2 = el('div', 'card'); c2.style.marginTop = '14px';
  c2.appendChild(el('div', 'card-hd', `<div><h3>Data dictionary</h3>
    <div class="sub">${D.dictionary.length} fields across ${new Set(D.dictionary.map(r => r.table)).size} tables · filter to find one</div></div>
    <input id="dict-q" class="srch" type="search" placeholder="Filter table, field or definition…" aria-label="Filter the data dictionary">`));
  c2.appendChild(el('div', 'tbl-wrap scroll-tall', `<table><thead><tr><th>Table</th><th>Field</th><th>Type</th><th>Units</th>
    <th>Definition</th></tr></thead><tbody id="dict-body">` +
    D.dictionary.map(r => `<tr><td>${r.table}</td><td class="mono">${r.field}</td><td>${r.type}</td>
      <td>${r.units}</td><td class="wrap-ok">${r.definition}</td></tr>`).join('') + '</tbody></table>'));
  host.appendChild(c2);
  const q = $('#dict-q'), body = $('#dict-body');
  q.addEventListener('input', () => {
    const v = q.value.trim().toLowerCase();
    let shown = 0;
    [...body.rows].forEach(tr => {
      const hit = !v || tr.textContent.toLowerCase().includes(v);
      tr.style.display = hit ? '' : 'none';
      if (hit) shown++;
    });
    $('#dict-count').textContent = shown === D.dictionary.length ? '' : `${shown} of ${D.dictionary.length} fields shown`;
  });
  c2.insertAdjacentHTML('beforeend', '<div class="note" id="dict-count" style="padding:8px 14px 12px"></div>');

  // Requirement: make it unambiguous which extended-range fields are real forecasts and which are
  // climatology or astronomy. Same table the 30-Day Outlook tab shows, kept here so the source
  // registry is self-contained.
  const cP = el('div', 'card'); cP.style.marginTop = '14px';
  const PB = ['forecast', 'blend', 'enso_analog', 'climatology', 'astronomical', 'metadata'];
  const PROV = (D.extended_provenance || []).slice()
    .sort((a, b) => PB.indexOf(a.basis) - PB.indexOf(b.basis) || a.field.localeCompare(b.field));
  const pc = {}; PROV.forEach(r => pc[r.basis] = (pc[r.basis] || 0) + 1);
  cP.appendChild(el('div', 'card-hd', `<div><h3>Extended-range field provenance</h3>
    <div class="sub">${PROV.length} fields in the 8-30 day tables, tagged forecast vs historical pattern</div></div>
    <div class="chips">` + PB.filter(b => pc[b]).map(b =>
    `<span class="basis ${b}">${b.replace('_', ' ')} ${pc[b]}</span>`).join('') + '</div>'));
  cP.appendChild(el('div', 'tbl-wrap scroll-tall', `<table><thead><tr><th>Field</th><th>Table</th>
    <th>Basis</th><th>Source</th><th class="wrap-ok">Note</th></tr></thead><tbody>` +
    PROV.map(r => `<tr><td class="mono">${r.field}</td><td>${r.table}</td>
      <td><span class="basis ${r.basis}">${String(r.basis).replace('_', ' ')}</span></td>
      <td class="wrap-ok">${r.source}</td><td class="wrap-ok">${r.notes}</td></tr>`).join('') +
    '</tbody></table>'));
  host.appendChild(cP);

  const c3 = el('div', 'card'); c3.style.marginTop = '14px';
  c3.appendChild(el('div', 'card-hd', `<div><h3>Known gaps</h3></div>`));
  c3.insertAdjacentHTML('beforeend', '<ul class="note" style="margin:0;padding-left:18px;font-size:12.5px;line-height:1.7">' +
    D.gaps.map(g => `<li>${g}</li>`).join('') + '</ul>');
  host.appendChild(c3);
}

/* ================================================== shell ================================================== */
function syncControls() {
  $('#f-date').value = state.date;
  $('#f-zone').value = state.zone;
  const spSel = $('#f-species');
  spSel.innerHTML = D.species.map(s => `<option value="${s.species_id}"${s.species_id === state.species ? ' selected' : ''}>${s.species}</option>`).join('');
}
function render() {
  document.querySelectorAll('nav.tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === state.tab));
  document.querySelectorAll('section.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + state.tab));
  ({ today: renderToday, forecast: renderForecast, outlook: renderOutlook, conditions: renderConditions, species: renderSpecies,
    enso: renderEnso, validation: renderValidation, accuracy: renderAccuracy, data: renderData }[state.tab])();
}
function boot() {
  $('#f-date').innerHTML = D.meta.dates.map(d => `<option value="${d}"${d === state.date ? ' selected' : ''}>${dLabel(d)}${isFcst(d) ? ' · forecast' : d === TODAY ? ' · today' : ''}</option>`).join('');
  $('#f-zone').innerHTML = zoneList.map(z => `<option value="${z.id}"${z.id === state.zone ? ' selected' : ''}>${z.name} · ${z.band}</option>`).join('');
  syncControls();
  const e = D.enso_current[0] || {};
  $('#badge-enso').innerHTML = `<span class="dot" style="background:${e.simple_regime === 'el_nino' ? css('--coral') : e.simple_regime === 'la_nina' ? css('--sky') : css('--teal')}"></span>
    <span>${e.regime ? ensoLabel(e.regime) : 'ENSO'} · ONI <b>${sign(e.oni, 2)}</b></span>`;
  const ss = D.source_status || {}, sm = ss.summary || {};
  const degraded = (sm.failed || 0) + (sm.stale || 0) + (sm.delayed || 0) + (sm.cached || 0);
  const sb = $('#badge-sources');
  const hasStatus = (ss.sources || []).length > 0;
  sb.textContent = !hasStatus ? 'Freshness unavailable' :
    (degraded ? `${degraded} source${degraded === 1 ? '' : 's'} degraded` : 'Sources current');
  sb.classList.toggle('status-bad', (sm.failed || 0) + (sm.stale || 0) > 0);
  sb.classList.toggle('status-warn', !sb.classList.contains('status-bad') && (!hasStatus || degraded > 0));
  const built = ss.build_completed_at_utc || ss.build_started_at_utc || D.meta.generated;
  $('#badge-run').textContent = 'Built ' + new Date(built).toLocaleString('en-US', {
    timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    timeZoneName: 'short'
  }) + ' · static snapshot';
  $('#hd-sub').textContent = `${D.zones.length} zones · ${D.species.length} species · ${D.meta.dates.length}-day window`;
  $('#foot').innerHTML = `<b>SoCal Fishing Intelligence</b> — built for San Diego inshore, nearshore and offshore waters.
    Model scores are decision support, not a guarantee: they combine public environmental feeds with published
    species-behavior literature. Verify regulations before fishing (CDFW seasons and limits, and a Mexican FMM permit
    plus fishing licence for Coronado Islands and Mexican-side banks). Data build ${new Date(D.meta.generated).toISOString().slice(0, 16).replace('T', ' ')} UTC ·
    ${D.conditions.length} zone-days · ${D.scores.length} species-zone-day scores · ${D.oni.length} months of ONI.`;
  document.querySelectorAll('nav.tabs button').forEach(b => b.onclick = () => { state.tab = b.dataset.tab; render(); });
  $('#f-date').onchange = e2 => { state.date = e2.target.value; render(); };
  $('#f-zone').onchange = e2 => { state.zone = e2.target.value; render(); };
  $('#f-species').onchange = e2 => { state.species = e2.target.value; render(); };
  $('#f-band').onclick = e2 => {
    const b = e2.target.closest('button[data-band]'); if (!b) return;
    state.band = b.dataset.band;
    $('#f-band').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b));
    render();
  };
  $('#theme').onclick = () => {
    const h = document.documentElement;
    h.dataset.theme = h.dataset.theme === 'dark' ? 'light' : 'dark';
    Object.values(charts).forEach(c => c.destroy()); Object.keys(charts).forEach(k => delete charts[k]);
    render();
  };
  render();
}
boot();
