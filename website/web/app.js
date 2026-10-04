// Otto dashboard. Plain JS, no build step, no internet needed.
// Data flow: server pushes one snapshot every 200 ms over /ws (falls back to polling).

// range = [min, max] in metric units for the level bar under each value
const KEYS = {
  rpm:           { label: 'Engine RPM', hint: 'How fast the engine spins',        unit: 'rpm',  dp: 0, primary: true, icon: 'engine', range: [0, 7000] },
  coolant_c:     { label: 'Engine temp', hint: 'Coolant temperature',        unit: '°C',   dp: 0, primary: true, temp: true, icon: 'thermometer', range: [40, 125] },
  ecu_voltage_v: { label: 'Battery', hint: '13.5\u201314.7 V when running',         unit: 'V',    dp: 1, primary: true, icon: 'ecu', range: [10, 15] },
  speed_kph:     { label: 'Speed', hint: 'How fast the car is moving',       unit: 'km/h', dp: 0, speed: true, icon: 'speedometer', range: [0, 200] },
  throttle_pct:  { label: 'Throttle', hint: 'How far the engine\'s air valve is open',            unit: '%',    dp: 0, icon: 'helmet', range: [0, 100] },
  stft_pct:      { label: 'Fuel adjust (now)', hint: 'Fuel the computer is adding (+) or cutting (\u2212) right now', unit: '%',   dp: 1, signed: true, icon: 'pump', range: [-25, 25] },
  ltft_pct:      { label: 'Fuel adjust (learned)', hint: 'Long-term fuel correction; beyond \u00b110% is worth checking', unit: '%',    dp: 1, signed: true, icon: 'fuelclock', range: [-25, 25] },
  maf_gs:        { label: 'Air flow', hint: 'Air entering the engine',       unit: 'g/s',  dp: 1, icon: 'airflow', range: [0, 60] },
  intake_c:      { label: 'Intake air temp', hint: 'Temperature of the air going into the engine',     unit: '°C',   dp: 0, temp: true, icon: 'intake', range: [-10, 70] },
  // extra: only shown once the car actually reports it. more: in the collapsed "More readings" section
  load_pct:      { label: 'Engine load', hint: 'How hard the engine is working',         unit: '%',    dp: 0, extra: true, icon: 'engine', range: [0, 100] },
  map_kpa:       { label: 'Intake pressure', hint: 'Low at idle, rises when you accelerate',     unit: 'kPa',  dp: 0, extra: true, icon: 'gauge', range: [0, 250] },
  pedal_d_pct:   { label: 'Gas pedal', hint: 'How far the pedal is pressed',           unit: '%',    dp: 0, extra: true, icon: 'pedal', range: [0, 100] },
  cmd_throttle_pct: { label: 'Throttle target', hint: 'Where the computer wants the throttle to be', unit: '%',  dp: 0, extra: true, icon: 'helmet', range: [0, 100] },
  timing_deg:    { label: 'Spark timing', hint: 'When the spark plugs fire',      unit: '°',    dp: 1, extra: true, signed: true, icon: 'spark', range: [-20, 40] },
  oil_c:         { label: 'Oil temp', hint: 'Engine oil temperature',            unit: '°C',   dp: 0, extra: true, temp: true, icon: 'oil', range: [40, 140] },
  cat_c:         { label: 'Catalytic converter', hint: 'Exhaust cleaner temperature',       unit: '°C',   dp: 0, extra: true, temp: true, icon: 'thermometer', range: [200, 900] },
  lambda:        { label: 'Air-fuel mix', hint: '1.00 is the ideal balance of air and fuel',  unit: 'λ',    dp: 2, extra: true, icon: 'airflow', range: [0.7, 1.3] },
  cmd_lambda:    { label: 'Air-fuel target', hint: 'The mix the computer is aiming for',         unit: 'λ',    dp: 2, extra: true, icon: 'airflow', range: [0.7, 1.3] },
  o2_up_v:       { label: 'Oxygen sensor (front)', hint: 'Exhaust oxygen before the converter',   unit: 'V',    dp: 2, extra: true, icon: 'gauge', range: [0, 1.1] },
  o2_down_v:     { label: 'Oxygen sensor (rear)', hint: 'Exhaust oxygen after the converter',    unit: 'V',    dp: 2, extra: true, icon: 'gauge', range: [0, 1.1] },
  rail_kpa:      { label: 'Fuel pressure', hint: 'Pressure feeding the fuel injectors',  unit: 'kPa',  dp: 0, extra: true, icon: 'pump', range: [0, 20000] },
  fuel_pct:      { label: 'Fuel tank', hint: 'How full the tank is',          unit: '%',    dp: 0, extra: true, icon: 'pump', range: [0, 100] },
  throttle_b_pct:{ label: 'Throttle sensor 2', hint: 'Backup throttle reading; should track the first',   unit: '%',    dp: 0, extra: true, more: true, icon: 'helmet', range: [0, 100] },
  pedal_e_pct:   { label: 'Gas pedal sensor 2', hint: 'Backup pedal reading; should track the first',  unit: '%',    dp: 0, extra: true, more: true, icon: 'pedal', range: [0, 100] },
  fuel_rate_lph: { label: 'Fuel use', hint: 'Fuel burned per hour',            unit: 'L/h',  dp: 1, extra: true, more: true, icon: 'pump', range: [0, 30] },
  ambient_c:     { label: 'Outside temp', hint: 'Air temperature outside the car',    unit: '°C',   dp: 0, extra: true, more: true, temp: true, icon: 'thermometer', range: [-20, 45] },
  baro_kpa:      { label: 'Air pressure', hint: 'Outside air pressure; lower at high altitude', unit: 'kPa',  dp: 0, extra: true, more: true, icon: 'gauge', range: [70, 110] },
  runtime_min:   { label: 'Time running', hint: 'Since the engine was started',     unit: 'min',  dp: 1, extra: true, more: true, icon: 'clock', range: [0, 120] },
  warmups:       { label: 'Warm-ups since cleared', hint: 'Engine warm-ups since codes were last cleared', unit: '', dp: 0, extra: true, more: true, icon: 'clock', range: [0, 255] },
  clear_dist_km: { label: 'Driven since cleared', hint: 'Distance since codes were last cleared', unit: 'km', dp: 0, extra: true, more: true, dist: true, icon: 'road', range: [0, 5000] },
  clear_time_min:{ label: 'Run time since cleared', hint: 'Engine time since codes were last cleared', unit: 'min', dp: 0, extra: true, more: true, icon: 'clock', range: [0, 6000] },
  mil_dist_km:   { label: 'Driven with warning light', hint: 'Distance with the check-engine light on', unit: 'km', dp: 0, extra: true, more: true, dist: true, icon: 'road', range: [0, 1000] },
  mil_time_min:  { label: 'Run time with warning light', hint: 'Engine time with the check-engine light on', unit: 'min', dp: 0, extra: true, more: true, icon: 'clock', range: [0, 6000] },
};

// Line icons on a 24×24 grid, drawn in currentColor (blue via CSS).
const ICONS = {
  engine: '<path d="M8 5h6M11 5v3M6 8h9l2 2h2V8.5h2V17h-2v-1.5h-2L15 19H9l-3-3z"/><path d="M3 11v4M3 13h3"/>',
  thermometer: '<path d="M10 14.3V5a2 2 0 0 1 4 0v9.3a4 4 0 1 1-4 0z"/><path d="M12 9v7.5"/><circle cx="12" cy="17.5" r="1.4" fill="currentColor"/><path d="M16.5 6h2M16.5 9h2M16.5 12h2"/>',
  ecu: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M9.5 4v3M12 4v3M14.5 4v3M9.5 17v3M12 17v3M14.5 17v3M4 9.5h3M4 12h3M4 14.5h3M17 9.5h3M17 12h3M17 14.5h3"/><path d="M12.6 9l-2 3.2h2.8L11.4 15" stroke-width="1.6"/>',
  speedometer: '<path d="M3.5 17a8.5 8.5 0 1 1 17 0"/><path d="M12 16l4.2-5"/><circle cx="12" cy="16" r="1.5" fill="currentColor"/><path d="M6.2 10.9l1.2 1M12 7.5V9M17.8 10.9l-1.2 1M4.6 15.3h1.5M17.9 15.3h1.5"/>',
  helmet: '<path d="M3.5 15.5C3.5 9.5 7.5 5.5 13 5.5c4 0 6.8 2.4 7.8 6l.7 2.5V17a1.5 1.5 0 0 1-1.5 1.5H6.5a3 3 0 0 1-3-3z"/><path d="M11.5 10.5h9.4l.6 3.5h-8a2 2 0 0 1-2-2z"/><path d="M4.6 12.4C6.4 9 9.2 7.3 12.8 7.2M8 18.5v-3h3.5"/>',
  pump: '<path d="M5 20V5a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v15M3.5 20h12"/><rect x="7" y="6.5" width="5" height="4" rx=".5"/><path d="M14 9h1.5l2.5 2.5v5.5a1.5 1.5 0 0 0 3 0V9.5L19 7.5"/>',
  fuelclock: '<path d="M12 3.5s-6 6.4-6 10.8a6 6 0 0 0 12 0C18 9.9 12 3.5 12 3.5z"/><path d="M12 11v3.3l2.2 1.4"/>',
  airflow: '<path d="M3 8.5h10a2.5 2.5 0 1 0-2.5-2.5M3 12.5h15a2.5 2.5 0 1 1-2.5 2.5M3 16.5h7"/>',
  intake: '<path d="M5.4 13.6V6a1.6 1.6 0 0 1 3.2 0v7.6a3.2 3.2 0 1 1-3.2 0z"/><path d="M7 9v6.2"/><path d="M12 8h6a2 2 0 1 0-2-2M12 12h8.5M12 16h5a2 2 0 1 1-2 2"/>',
  gauge: '<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l3.5-4.5"/><circle cx="12" cy="16" r="1.4" fill="currentColor"/>',
  spark: '<path d="M13 3L6 13.5h5L10 21l7-10.5h-5z"/>',
  oil: '<path d="M3 10h4l2-2h5l1.5 1.5L21 7.5V9l-5 6H7.5L5 12H3z"/><path d="M19.5 17c0 .9-.7 1.6-1.5 1.6s-1.5-.7-1.5-1.6c0-.9 1.5-2.6 1.5-2.6s1.5 1.7 1.5 2.6z"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.2 2"/>',
  road: '<path d="M8.5 4L5 20M15.5 4L19 20M12 5v2.5M12 10.5v3M12 16.5V19"/>',
  pedal: '<path d="M8 3.5l7 2.2-2.4 12.8a2 2 0 0 1-2.3 1.6l-1.6-.3a2 2 0 0 1-1.6-2.3z"/><path d="M9 20.5h6"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;

const WINDOW_S = 60;
const MODE_LABEL = { live: 'LIVE', simulator: 'SIMULATOR', replay: 'HISTORY' };
const CONN_LABEL = { connected: 'Connected', idle: 'Ready', waiting: 'Not connected yet', disconnected: 'Connection lost' };
const ICON = { supports: '✓', against: '✕', neutral: '–', missing: '?' };
const SEV_LABEL = { stop: 'Stop driving', caution: 'Caution', info: 'Info' };

const S = {
  snap: null, meta: null, buf: {}, lastUpd: {},
  units: load('units', 'metric'),
  expl: null, explVersion: -1, findingsKey: '', findingsAt: 0, explaining: false,
};
for (const k in KEYS) S.buf[k] = [];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function load(k, d) { try { return localStorage.getItem('pitstop.' + k) || d; } catch { return d; } }
function save(k, v) { try { localStorage.setItem('pitstop.' + k, v); } catch {} }

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ---------- units ----------
function conv(key, v) {
  if (v == null) return null;
  const m = KEYS[key];
  if (S.units === 'imperial') { if (m.temp) return v * 9 / 5 + 32; if (m.speed || m.dist) return v * 0.621371; }
  return v;
}
function unitOf(key) {
  const m = KEYS[key];
  if (S.units === 'imperial') { if (m.temp) return '°F'; if (m.speed) return 'mph'; if (m.dist) return 'mi'; }
  return m.unit;
}
function fmt(key, v, stat) {
  if (v == null) return '—';
  if (stat === 'roughness') return `±${v.toFixed(0)} ${unitOf(key)}`;   // a spread, not a temperature
  const c = conv(key, v);
  const s = c.toFixed(KEYS[key]?.dp ?? 1);
  return (KEYS[key]?.signed && c > 0 ? '+' : '') + s;
}

// ---------- readings ----------
function buildGauges() {
  for (const [k, m] of Object.entries(KEYS)) {
    const el = document.createElement('div');
    el.className = 'gauge' + (m.primary ? '' : ' small');
    el.id = 'g-' + k;
    const value = '<div class="value"><span class="num">—</span><span class="unit"></span></div>';
    // Primary: stat card with a progress ring (icon in the middle). Others: icon tile + level bar.
    el.innerHTML = m.primary
      ? `<div class="g-main">
          <div class="label">${esc(m.label)}</div><div class="hint">${esc(m.hint)}</div>${value}<div class="fresh">Not reported</div>
          <div class="ring"><svg viewBox="0 0 64 64" aria-hidden="true"><circle class="track" cx="32" cy="32" r="${RING_R}"/><circle class="fill" cx="32" cy="32" r="${RING_R}"/></svg><span class="g-icon">${icon(m.icon)}</span></div>
        </div><canvas></canvas>`
      : `<div class="g-top"><span class="g-icon">${icon(m.icon)}</span><div><div class="label">${esc(m.label)}</div><div class="hint">${esc(m.hint)}</div></div></div>
        ${value}<div class="meter${m.signed ? ' signed' : ''}"><i></i></div><div class="fresh">Not reported</div>`;
    $(m.primary ? 'primary' : m.more ? 'more' : 'secondary').appendChild(el);
  }
}

function flagFor(k, v, snap) {
  if (v == null) return '';
  const running = (snap.readings.rpm ?? 0) > 500;
  if (k === 'coolant_c') return v >= 112 ? 'flag bad' : v >= 105 ? 'flag' : '';
  if (k === 'ecu_voltage_v' && running) return v < 12.0 ? 'flag bad' : v < 12.8 ? 'flag' : '';
  // Short-term trim swings ±10% in normal driving, so only the long-term trim is flagged
  if (k === 'ltft_pct') return Math.abs(v) > 20 ? 'flag bad' : Math.abs(v) > 10 ? 'flag' : '';
  return '';
}

function updateReadings(snap) {
  const now = Date.parse(snap.server_time) / 1000;
  for (const k in KEYS) {
    const v = snap.readings[k];
    const upd = snap.reading_updated[k];
    if (upd && upd !== S.lastUpd[k] && v != null) {
      S.lastUpd[k] = upd;
      S.buf[k].push([Date.parse(upd) / 1000, v]);
    }
    S.buf[k] = S.buf[k].filter(([t]) => t >= now - WINDOW_S);

    const el = $('g-' + k);
    // Only show what this car can report: hide unsupported PIDs, and extras until they arrive
    el.hidden = (snap.supported && !snap.supported.includes(k)) || (KEYS[k].extra && !upd);
    const stale = snap.stale.includes(k) || (snap.capturing && snap.connection === 'disconnected');
    el.querySelector('.num').textContent = fmt(k, v);
    el.querySelector('.unit').textContent = v == null ? '' : unitOf(k);
    let fresh = 'Not reported', cls = '';
    if (upd && v == null) fresh = 'Not supported by vehicle';
    else if (upd && stale) { fresh = `Last seen ${Math.round(now - Date.parse(upd) / 1000)}s ago`; cls = 'stale'; }
    else if (upd) { fresh = snap.capturing ? 'Live' : 'Paused'; cls = snap.capturing ? 'live' : ''; }
    el.className = `gauge${KEYS[k].primary ? '' : ' small'} ${cls} ${flagFor(k, v, snap)}`;
    el.querySelector('.fresh').textContent = fresh;
    if (KEYS[k].primary) setRing(el.querySelector('.ring .fill'), k, v);
    else setMeter(el.querySelector('.meter i'), k, v);
    if (KEYS[k].primary) drawSpark(el.querySelector('canvas'), k, now, snap.markers, stale);
  }
  const shown = [...$('more').children].filter((c) => !c.hidden).length;
  $('moreBox').hidden = !shown;
  $('moreCount').textContent = shown;
}

// Level bar: fills from the left, or from the centre for signed values like fuel trim.
function setMeter(bar, key, v) {
  const [lo, hi] = KEYS[key].range;
  if (v == null) { bar.style.left = '0'; bar.style.width = '0'; return; }
  const pos = (x) => Math.min(100, Math.max(0, ((x - lo) / (hi - lo)) * 100));
  const from = KEYS[key].signed ? pos(0) : 0;
  const to = pos(v);
  bar.style.left = Math.min(from, to) + '%';
  bar.style.width = Math.abs(to - from) + '%';
}

const RING_R = 27, RING_C = 2 * Math.PI * RING_R;
function setRing(arc, key, v) {
  const [lo, hi] = KEYS[key].range;
  const f = v == null ? 0 : Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
  arc.style.strokeDasharray = RING_C;
  arc.style.strokeDashoffset = RING_C * (1 - f);
}

function drawSpark(cv, key, now, markers, stale) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w) return;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.documentElement);
  const pts = S.buf[key];
  const x = (t) => ((t - (now - WINDOW_S)) / WINDOW_S) * w;

  g.strokeStyle = css.getPropertyValue('--line');
  g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, h - 0.5); g.lineTo(w, h - 0.5); g.stroke();

  g.setLineDash([3, 3]);
  g.strokeStyle = css.getPropertyValue('--orange');
  for (const m of markers || []) {
    if (m.epoch < now - WINDOW_S) continue;
    g.beginPath(); g.moveTo(x(m.epoch), 0); g.lineTo(x(m.epoch), h); g.stroke();
  }
  g.setLineDash([]);
  if (pts.length < 2) return;

  let lo = Infinity, hi = -Infinity;
  for (const [, v] of pts) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const pad = Math.max((hi - lo) * 0.15, Math.abs(hi) * 0.02, 0.5);
  lo -= pad; hi += pad;
  const y = (v) => h - 4 - ((v - lo) / (hi - lo)) * (h - 8);

  const color = (stale ? css.getPropertyValue('--dim') : css.getPropertyValue('--orange')).trim();
  g.beginPath();
  pts.forEach(([t, v], i) => (i ? g.lineTo(x(t), y(v)) : g.moveTo(x(t), y(v))));
  g.strokeStyle = color; g.lineWidth = 2; g.lineJoin = 'round'; g.stroke();
  // soft area fill under the line
  g.lineTo(x(pts[pts.length - 1][0]), h); g.lineTo(x(pts[0][0]), h); g.closePath();
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color + '38'); grad.addColorStop(1, color + '00');
  g.fillStyle = grad; g.fill();
  const [lt, lv] = pts[pts.length - 1];
  g.fillStyle = color; g.beginPath(); g.arc(x(lt), y(lv), 2.6, 0, Math.PI * 2); g.fill();
}

// ---------- header / source bar ----------
function updateChrome(snap) {
  const pill = $('modePill');
  pill.textContent = MODE_LABEL[snap.source] + (snap.source === 'simulator' ? ' · SIMULATED DATA' : '');
  pill.className = 'mode-pill ' + snap.source;

  $('connDot').className = 'dot ' + snap.connection;
  $('connText').textContent = CONN_LABEL[snap.connection] || snap.connection;
  $('connDetail').textContent = snap.connection_detail || '';

  const btn = $('captureBtn');
  btn.classList.toggle('on', snap.capturing);
  $('captureLabel').textContent = snap.capturing ? 'Stop capture' : 'Start capture';

  S.vsearch.setValue(snap.vehicle);

  for (const b of $('modeSeg').children) b.classList.toggle('on', b.dataset.mode === snap.source);
  $('scenarioBox').hidden = snap.source !== 'simulator';
  $('replayBox').hidden = snap.source !== 'replay';
  $('liveBox').hidden = snap.source !== 'live';
  for (const c of $('scenarioBox').children) c.classList.toggle('on', c.dataset.s === snap.scenario);
  if (snap.source === 'replay' && snap.recording) $('recordingSel').value = snap.recording;

  $('milBadge').hidden = !snap.mil;
  $('vinText').hidden = !snap.vin;
  $('vinText').textContent = snap.vin ? `Detected from car · VIN ${snap.vin}` : '';

  const ml = $('markerList');
  const key = snap.markers.map((m) => m.t).join();
  if (ml.dataset.key !== key) {
    ml.dataset.key = key;
    ml.innerHTML = snap.markers.length
      ? snap.markers.slice().reverse().map((m) => `<li class="${esc(m.origin)}"><time>${new Date(m.t).toLocaleTimeString()}</time><span>${esc(m.note)}</span></li>`).join('')
      : '<li class="empty">No markers yet. They show up as dashed lines on the charts and in the report.</li>';
  }
}

function buildSourceBar() {
  const box = $('scenarioBox');
  Object.entries(S.meta.scenarios).forEach(([id, s], i) => {
    const b = document.createElement('button');
    b.className = 'chip'; b.dataset.s = id; b.title = s.description;
    b.innerHTML = `${esc(s.label)}${s.code ? `<b>${s.code}</b>` : ''}`;
    b.onclick = () => setSource('simulator', { scenario: id });
    box.appendChild(b);
  });
  fillRecordings(S.meta.recordings);
  $('liveBox').innerHTML = `<ol class="steps-inline">
      <li><b>1</b>Plug the OBD-II cable into your car's port: under the dashboard on the driver's side, near the pedals below the steering wheel</li>
      <li><b>2</b>Check the other end of the cable is connected to the FREE-WILi, and the FREE-WILi to this laptop</li>
      <li><b>3</b>Turn the car fully on (start the engine)</li>
      <li><b>4</b>Press <em>Start capture</em></li>
    </ol>`;
  for (const b of $('modeSeg').children) b.onclick = () => setSource(b.dataset.mode);
  $('recordingSel').onchange = (e) => setSource('replay', { recording: e.target.value });
  $('refreshRec').onclick = async () => fillRecordings((await api('/api/meta')).recordings);
}

// "simulator-lean-20261003-204512.jsonl" -> "Lean condition · Oct 3, 8:45 PM"
function recordingLabel(name) {
  const sample = name.match(/^sample-(.+)\.jsonl$/);
  if (sample) return `Sample · ${S.meta?.scenarios[sample[1]]?.label || sample[1]}`;
  const m = name.match(/^(live|simulator)-(.+)-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}\.jsonl$/);
  if (!m) return name;
  const [, mode, scen, y, mo, d, h, mi] = m;
  const what = mode === 'live' ? 'Real car' : (S.meta?.scenarios[scen]?.label || scen);
  const when = new Date(+y, mo - 1, +d, +h, +mi)
    .toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  return `${what} · ${when}`;
}

function fillRecordings(list) {
  $('recordingSel').innerHTML = list.length
    ? list.map((r) => `<option value="${esc(r)}">${esc(recordingLabel(r))}</option>`).join('')
    : '<option value="">No saved sessions yet — capture something first</option>';
}

async function setSource(mode, extra = {}) {
  S.expl = null; $('aiOut').innerHTML = ''; S.findingsKey = '';
  for (const k in KEYS) { S.buf[k] = []; delete S.lastUpd[k]; }
  await api('/api/source', { mode, ...extra });
}

// ---------- findings ----------
function renderFindings(snap) {
  const ex = S.expl;
  // Re-render immediately on structural change (codes, verdicts, mode...);
  // evidence numbers alone refresh at most once a second so text doesn't flicker.
  const structural = JSON.stringify([snap.code_details.map((d) => [d.code, d.status, d.evidence.map((e) => e.verdict)]),
    snap.capturing, snap.session.frames > 0, snap.explanation.version, S.units, snap.source, snap.code_checks]);
  const values = JSON.stringify(snap.code_details.map((d) => d.evidence.map((e) => e.value)));
  const now = performance.now();
  const key = structural + values;
  if (key === S.findingsKey) return;
  if (S.findingsKey.startsWith(structural) && now - S.findingsAt < 1000) return;
  S.findingsKey = key; S.findingsAt = now;

  const aiByCode = {}, fixByCode = {};
  for (const f of ex?.findings || []) fixByCode[f.code] = f;          // DIY steps: AI or offline
  if (ex && ex.source === 'ai') for (const f of ex.findings || []) aiByCode[f.code] = f;

  // safety banner: AI's if present, else worst offline severity
  const sb = $('safety');
  const worst = snap.code_details[0];
  if (ex && ex.safety && snap.code_details.length) {
    sb.hidden = false; sb.className = 'safety ' + ex.safety.level;
    sb.innerHTML = `<span class="lvl">${esc(ex.safety.level.toUpperCase())}</span><span>${esc(ex.safety.message)}</span>`;
  } else if (worst && worst.severity !== 'info') {
    sb.hidden = false; sb.className = 'safety ' + worst.severity;
    sb.innerHTML = `<span class="lvl">${esc(SEV_LABEL[worst.severity].toUpperCase())}</span><span>${esc(worst.driving)}</span>`;
  } else sb.hidden = true;

  const box = $('codes');
  if (!snap.code_details.length) {
    const started = snap.session.frames > 0;
    box.innerHTML = started
      ? `<div class="empty-state clear"><h3>No codes reported</h3><p>The engine computer isn't reporting any trouble codes right now. That's not a guarantee nothing is wrong — if you notice something, mark it.</p>${checksHtml(snap.code_checks)}</div>`
      : `<div class="empty-state"><h3>Start a capture to read your car</h3><p>Press Start capture above, or the green button on the FREE-WILi. Readings and trouble codes appear here.</p></div>`;
    return;
  }

  box.innerHTML = snap.code_details.map((d) => {
    const a = aiByCode[d.code];
    const evidence = d.evidence.map((e) => `<li>
        <span class="ic ${e.verdict}" title="${esc(e.verdict)}">${ICON[e.verdict]}</span>
        <span>${esc(KEYS[e.key]?.label || e.key)}${e.stat && e.stat !== 'latest' ? ` <small>${esc(e.stat)} over last 60 s</small>` : ''}<small>${esc(e.note)}</small></span>
        <span class="v">${e.value == null ? '—' : esc(fmt(e.key, e.value, e.stat))}${e.value == null || e.stat === 'roughness' ? '' : ' ' + esc(unitOf(e.key))}</span>
      </li>`).join('');
    const aiShows = a?.what_the_data_shows?.length
      ? `<ul class="causes">${a.what_the_data_shows.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>` : '';
    const causes = a?.likely_causes?.length
      ? `<h4>Likely causes <span class="ai-note">· ranked by AI using your data</span></h4><ol class="causes">${a.likely_causes.map((c) =>
          `<li><span class="lik ${esc(c.likelihood)}">${esc(c.likelihood)}</span>${esc(c.cause)}<br><small>${esc(c.why)}</small></li>`).join('')}</ol>`
      : (d.common_causes.length ? `<h4>Common causes</h4><ul class="causes">${d.common_causes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>` : '');
    const fix = fixByCode[d.code];
    const steps = fix?.diy_steps?.length ? fix.diy_steps : d.next_checks.map((step) => ({ step, difficulty: '', tools: '' }));
    return `<article class="code">
      <div class="code-head">
        <span class="code-id">${esc(d.code)}</span>
        <h3>${esc(d.title)}<span class="sys">${esc(d.system)}</span></h3>
        <span class="sev ${esc(d.severity)}">${esc(SEV_LABEL[d.severity])}</span>
      </div>
      ${d.status?.length ? `<div class="code-tags">${d.status.map((t) => `<span class="tag ${TAG_CLASS[t] || ''}" title="${esc(TAG_HELP[t] || '')}">${esc(t)}</span>`).join('')}</div>` : ''}
      <div class="code-body">
        <p>${esc(a?.plain_meaning || d.meaning)}</p>
        ${evidence ? `<div><h4>What your data shows</h4><ul class="evidence">${evidence}</ul>${aiShows}</div>` : ''}
        <div>${causes}</div>
        <div><h4>${fix ? 'Try this first' : 'Next checks'}</h4><ol class="diy">${steps.map((s) =>
          `<li><span>${esc(s.step)}${s.tools ? `<small>Tools: ${esc(s.tools)}</small>` : ''}</span>${s.difficulty ? `<span class="diff ${esc(s.difficulty)}">${esc(s.difficulty)}</span>` : '<span></span>'}</li>`).join('')}</ol>
          ${fix?.mechanic_needed_if ? `<p class="muted" style="margin:8px 0 0;font-size:13px"><b>See a mechanic if:</b> ${esc(fix.mechanic_needed_if)}</p>` : ''}</div>
        <div class="driving"><b>Driving:</b> ${esc(d.driving)}</div>
      </div>
    </article>`;
  }).join('');
}

const TAG_CLASS = { 'warning light': 'hot', active: 'hot', stored: 'on', pending: 'soft', permanent: 'on', intermittent: 'soft' };
const TAG_HELP = {
  'warning light': 'The engine computer has a dashboard warning light on for this fault',
  active: 'Failing right now',
  stored: 'Confirmed and saved in the engine computer',
  pending: 'Seen once; becomes stored if it happens again',
  permanent: 'Stays until the car confirms the repair, even if codes are cleared',
  intermittent: 'Failed at some point since codes were last cleared, not right now',
};
const CHECK_LABELS = { stored: 'Stored', pending: 'Pending', permanent: 'Permanent', manufacturer: 'Manufacturer fault memory' };

// Which code checks the car answered, so "no codes" is honest about what was looked at
function checksHtml(checks) {
  if (!checks?.length) return '';
  const items = Object.entries(CHECK_LABELS).map(([k, label]) =>
    `<li class="${checks.includes(k) ? 'ok' : 'na'}">${checks.includes(k) ? '✓' : '–'} ${label}</li>`).join('');
  const note = checks.includes('manufacturer') ? ''
    : '<p class="muted">No module has answered the fault-memory request yet, so faults some cars keep only in a module\'s own memory may not show here.</p>';
  return `<ul class="checks">${items}</ul>${note}`;
}

// ---------- AI ----------
async function explain() {
  if (S.explaining) return;
  S.explaining = true;
  $('explainBtn').disabled = true;
  $('aiStatus').innerHTML = '<span class="spinner"></span> Analyzing codes, readings and your notes…';
  try {
    S.expl = await api('/api/explain', { symptoms: $('symptoms').value });
    renderExplanation();
  } catch (e) {
    $('aiStatus').textContent = 'Could not reach the server: ' + e.message;
  } finally {
    S.explaining = false; $('explainBtn').disabled = false; S.findingsKey = '';
  }
}

function renderExplanation() {
  const ex = S.expl;
  if (!ex || !ex.headline) { $('aiOut').innerHTML = ''; $('aiStatus').textContent = ''; return; }
  const src = ex.source === 'ai' ? 'AI analysis' : 'Offline reference';
  $('aiStatus').innerHTML = `${src}${ex.note ? ` — ${esc(ex.note)}` : ''}`;
  const outdated = S.snap?.explanation?.outdated ? '<div class="outdated">Codes changed since this analysis — run it again.</div>' : '';
  const qs = (ex.questions_for_mechanic || []).map((q) => `<li>${esc(q)}</li>`).join('');
  const well = (ex.going_well || []).map((w) => `<li>${esc(w)}</li>`).join('');
  const m = ex.mechanic || {};
  const urgency = { now: 'See a mechanic now', soon: 'See a mechanic soon', when_convenient: 'A mechanic visit is worth booking', not_needed: 'No mechanic needed right now' }[m.urgency] || '';
  $('aiOut').innerHTML = `<div class="ai-summary">
      ${outdated}
      <div class="headline">${esc(ex.headline)}</div>
      ${ex.health_summary ? `<p style="margin:0">${esc(ex.health_summary)}</p>` : ''}
      ${well ? `<div><h4 class="ai-h">What looks good</h4><ul class="well">${well}</ul></div>` : ''}
      ${urgency ? `<div class="mech-callout ${m.recommended ? 'yes' : ''}"><div><b>${esc(urgency)}</b><br><small class="muted">${esc(m.why || '')}${m.shop_type && m.recommended ? ` · ${esc(m.shop_type)}` : ''}</small></div>
        ${m.recommended ? `<a class="btn primary" href="${esc(mechanicUrl(m.shop_type))}" target="_blank" rel="noopener">Find one near me</a>` : ''}</div>` : ''}
      ${qs ? `<div><h4 class="ai-h">Ask your mechanic</h4><ul>${qs}</ul></div>` : ''}
      <div class="muted" style="font-size:12px">${esc(ex.caveat || '')}</div>
    </div>`;
  updateMechanicPanel();
}

// ---------- under the hood ----------
const FUEL_NOTE = {
  1: 'Normal right after starting; it switches to normal once the engine warms up.',
  2: 'The engine is fine-tuning fuel with its oxygen sensors, as it should.',
  4: 'Temporary and normal when accelerating hard or coasting.',
  8: 'Worth checking: the computer stopped fine-tuning fuel because of a fault.',
  16: 'Worth checking: one of the oxygen sensors is reporting a problem.',
};

function renderDiagnostics(snap) {
  const d = snap.diagnostics || {};
  const key = JSON.stringify([d, S.units]);
  if (key === S.diagKey) return;
  S.diagKey = key;
  $('diagPanel').hidden = !Object.keys(d).length;

  const tests = d.readiness || [];
  $('dReady').hidden = !tests.length;
  if (tests.length) {
    const open = tests.filter((t) => !t.ready);
    const [verdict, cls, note] = !open.length ? ['Yes', 'ok', `All ${tests.length} emissions self-tests are complete.`]
      : open.length === 1 ? ['Probably', 'watch', 'One test is still running. Most states allow one unfinished test (older cars: two).']
      : ['Not yet', 'bad', `${open.length} tests haven't finished. They complete on their own after a few days of normal driving. This is common after a battery change or cleared codes, and isn't a fault.`];
    $('dReady').querySelector('.d-big').innerHTML = `<span class="${cls}">${verdict}</span>`;
    $('dReady').querySelector('.d-note').textContent = note;
    $('dReady').querySelector('.d-tests').innerHTML = tests.map((t) =>
      `<li class="${t.ready ? 'ok' : 'na'}">${t.ready ? '✓' : '…'} ${esc(t.name)}</li>`).join('');
  }

  const fuel = d.fuel_status;
  $('dFuel').hidden = !fuel;
  if (fuel) {
    const cls = fuel.code === 2 ? 'ok' : fuel.code >= 8 ? 'bad' : 'watch';
    $('dFuel').querySelector('.d-big').innerHTML = `<span class="${cls}">${esc(fuel.text)}</span>`;
    $('dFuel').querySelector('.d-note').textContent = FUEL_NOTE[fuel.code] || '';
  }

  const mis = Object.entries(d.misfires || {}).map(([c, v]) => [c, v.recent ?? v.last_drive ?? 0]);
  $('dMis').hidden = !mis.length;
  if (mis.length) {
    const top = Math.max(10, ...mis.map(([, n]) => n));
    const [worstCyl, worst] = mis.reduce((a, b) => (b[1] > a[1] ? b : a));
    const others = Math.max(0, ...mis.filter(([c]) => c !== worstCyl).map(([, n]) => n));
    const standout = worst >= 10 && worst > 3 * others;
    $('dMis').querySelector('.d-bars').innerHTML = mis.map(([c, n]) => `<div class="bar ${standout && c === worstCyl ? 'hot' : ''}">
        <span>Cyl ${esc(c)}</span><i style="width:${Math.max(2, (n / top) * 100)}%"></i><b>${n}</b></div>`).join('');
    $('dMis').querySelector('.d-note').textContent = standout
      ? `Cylinder ${worstCyl} stands out. Its spark plug, ignition coil or fuel injector is the first thing to check.`
      : !worst ? 'No misfires recorded on any cylinder.'
      : !others ? `Only cylinder ${worstCyl} has misfired so far (${worst}). Worth watching if the number keeps climbing.`
      : 'A few misfires spread across cylinders; normal in small numbers.';
  }

  const ff = d.freeze_frame;
  $('dFreeze').hidden = !(ff && ff.code);
  if (ff && ff.code) {
    $('dFreeze').querySelector('.d-big').innerHTML = `<span class="code-chip">${esc(ff.code)}</span>`;
    $('dFreeze').querySelector('.d-note').textContent = 'What the car was doing the moment this code was set:';
    $('dFreeze').querySelector('.d-snap').innerHTML = Object.entries(ff.readings || {}).filter(([k]) => KEYS[k]).map(([k, v]) =>
      `<dt>${esc(KEYS[k].label)}</dt><dd>${esc(fmt(k, v))} ${esc(unitOf(k))}</dd>`).join('');
  }
}

// ---------- health ----------
const HEALTH_RING = 2 * Math.PI * 52;
function renderHealth(snap) {
  const h = snap.health || {};
  const key = JSON.stringify(h);
  if (key === S.healthKey) return;
  S.healthKey = key;
  const score = h.score;
  $('healthScore').textContent = score == null ? '—' : score;
  $('healthLabel').textContent = h.label || 'No data yet';
  const ring = $('health').querySelector('.h-score');
  ring.className = 'h-score ' + (score == null ? '' : score >= 75 ? 'ok' : score >= 50 ? 'watch' : 'bad');
  const arc = ring.querySelector('.fill');
  arc.style.strokeDasharray = HEALTH_RING;
  arc.style.strokeDashoffset = HEALTH_RING * (1 - (score ?? 0) / 100);
  const label = { ok: 'OK', watch: 'Watch', problem: 'Problem', unknown: 'No data' };
  $('healthSystems').innerHTML = (h.systems || []).map((x) =>
    `<li class="${esc(x.status)}" title="${esc(label[x.status])}${x.reasons.length ? ': ' + esc(x.reasons.join('; ')) : ''}">${esc(x.name)}</li>`).join('');
  $('healthGood').innerHTML = (h.positives || []).slice(0, 5).map((p) => `<li>${esc(p)}</li>`).join('');
  updateMechanicPanel();
}

// ---------- mechanics + saved reports ----------
function mechanicUrl(shopType) {
  // Google Maps finds shops near the viewer's location; dealer/specialist advice searches for the car's make
  const make = (S.snap?.vehicle || '').split(' ')[0];
  const specialist = /dealer|special/i.test(shopType || '') && make;
  const q = specialist ? `${make} repair near me` : 'auto repair near me';
  return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(q);
}

function updateMechanicPanel() {
  const m = S.expl?.mechanic;
  const worst = S.snap?.code_details?.[0]?.severity;
  const urgent = m ? m.urgency === 'now' : worst === 'stop';
  $('mechanicPanel').classList.toggle('urgent', !!urgent || !!m?.recommended);
  $('mechanicBtn').href = mechanicUrl(m?.shop_type);
  $('mechanicWhy').textContent = m?.recommended ? `${m.why} Bring your saved report.`
    : urgent ? 'This code means the car should not be driven far. Find a shop near you.'
    : 'If a fix is beyond a driveway job, find a shop near you. Bring your saved report.';
}

async function loadReports() {
  const list = await api('/api/reports');
  $('reportList').innerHTML = list.slice(0, 5).map((r) => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">
      <span>${esc(new Date(r.generated).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }))} · ${esc(r.vehicle || 'Vehicle')}</span>
      <span>${r.score == null ? '' : `<b>${r.score}</b>/100 · `}${r.codes.length ? esc(r.codes.join(', ')) : 'no codes'}</span></a></li>`).join('');
}

async function saveReport() {
  const b = $('saveReportBtn');
  b.disabled = true; b.textContent = 'Saving…';
  try {
    const r = await api('/api/reports', {});
    b.textContent = 'Saved ✓';
    await loadReports();
    window.open(r.url, '_blank', 'noopener');
  } catch (e) {
    b.textContent = 'Save failed';
  } finally {
    setTimeout(() => { b.disabled = false; b.textContent = 'Save report'; }, 1800);
  }
}

async function syncExplanation(snap) {
  if (snap.explanation.version === S.explVersion) return;
  S.explVersion = snap.explanation.version;
  if (S.explaining) return;
  S.expl = snap.explanation.available ? await api('/api/explanation') : null;
  renderExplanation(); S.findingsKey = '';
}

// ---------- main loop ----------
function onSnapshot(snap) {
  S.snap = snap;
  updateChrome(snap);
  updateReadings(snap);
  renderHealth(snap);
  renderDiagnostics(snap);
  renderFindings(snap);
  syncExplanation(snap).catch(() => {});
  if (snap.explanation.running && !S.explaining) $('aiStatus').innerHTML = '<span class="spinner"></span> Analysis requested from FREE-WILi…';
}

function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { $('wsState').textContent = 'Dashboard connected'; };
  ws.onmessage = (e) => onSnapshot(JSON.parse(e.data));
  ws.onclose = () => {
    $('wsState').textContent = 'Dashboard reconnecting…';
    $('connDot').className = 'dot disconnected'; $('connText').textContent = 'Server offline';
    setTimeout(connect, 1000);
  };
}

async function backfill() {
  const h = await api('/api/history?seconds=' + WINDOW_S);
  for (const k in KEYS) if (h[k]) S.buf[k] = h[k];
}

function bindControls() {
  $('captureBtn').onclick = () => api('/api/capture', { action: 'toggle' });
  $('explainBtn').onclick = explain;
  $('saveReportBtn').onclick = saveReport;
  $('markerForm').onsubmit = (e) => {
    e.preventDefault();
    api('/api/marker', { note: $('markerNote').value }).then(() => { $('markerNote').value = ''; });
  };
  S.vsearch = initVehicleSearch($('vehicle'), $('vehicleList'), (name) => api('/api/vehicle', { name }));
  $('dataBtn').onclick = () => {
    const v = (S.snap?.vehicle || 'vehicle').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '');
    $('dataBtn').download = `otto-${v}-${new Date().toISOString().slice(0, 10)}.json`;
  };
  for (const b of $('unitSeg').children) {
    b.classList.toggle('on', b.dataset.units === S.units);
    b.onclick = () => {
      S.units = b.dataset.units; save('units', S.units);
      for (const x of $('unitSeg').children) x.classList.toggle('on', x === b);
      S.findingsKey = '';
      if (S.snap) onSnapshot(S.snap);
    };
  }
  window.addEventListener('resize', () => S.snap && updateReadings(S.snap));
}

(async function init() {
  buildGauges();
  S.meta = await api('/api/meta');
  buildSourceBar();
  bindControls();
  const b = $('aiBadge');
  b.textContent = S.meta.ai.available ? 'AI ready' : 'Offline mode';
  b.className = 'badge' + (S.meta.ai.available ? ' on' : '');
  b.title = S.meta.ai.available ? 'AI analysis is available' : S.meta.ai.detail;
  await backfill().catch(() => {});
  loadReports().catch(() => {});
  onSnapshot(await api('/api/state'));
  connect();
})();
