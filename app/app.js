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

/* ================================================== DATA & SOURCES ================================================== */
function renderData() {
  const host = $('#tab-data'); host.innerHTML = '';
  const c0 = el('div', 'card');
  c0.appendChild(el('div', 'card-hd', `<div><h3>Downloads</h3>
    <div class="sub">Full cleaned dataset, every intermediate table, and the written documentation</div></div>`));
  c0.insertAdjacentHTML('beforeend', `<div class="chips" style="gap:10px">
    <a class="dl" href="downloads/socal_fishing_dataset.xlsx" download>⬇ socal_fishing_dataset.xlsx</a>
    <a class="dl" href="downloads/SOURCE_REGISTRY.md" download>⬇ SOURCE_REGISTRY.md</a>
    <a class="dl" href="downloads/DATA_DICTIONARY.md" download>⬇ DATA_DICTIONARY.md</a></div>
    <div class="sub" style="margin-top:14px">Individual tables as CSV</div>
    <div class="chips">` +
    [...new Set(D.dictionary.map(r => r.table))].map(t =>
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
  ({ today: renderToday, forecast: renderForecast, conditions: renderConditions, species: renderSpecies,
    enso: renderEnso, validation: renderValidation, data: renderData }[state.tab])();
}
function boot() {
  $('#f-date').innerHTML = D.meta.dates.map(d => `<option value="${d}"${d === state.date ? ' selected' : ''}>${dLabel(d)}${isFcst(d) ? ' · forecast' : d === TODAY ? ' · today' : ''}</option>`).join('');
  $('#f-zone').innerHTML = zoneList.map(z => `<option value="${z.id}"${z.id === state.zone ? ' selected' : ''}>${z.name} · ${z.band}</option>`).join('');
  syncControls();
  const e = D.enso_current[0] || {};
  $('#badge-enso').innerHTML = `<span class="dot" style="background:${e.simple_regime === 'el_nino' ? css('--coral') : e.simple_regime === 'la_nina' ? css('--sky') : css('--teal')}"></span>
    <span>${e.regime ? ensoLabel(e.regime) : 'ENSO'} · ONI <b>${sign(e.oni, 2)}</b></span>`;
  $('#badge-run').textContent = 'Built ' + new Date(D.meta.generated).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' · static snapshot';
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
