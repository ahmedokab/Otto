// Vehicle search box. Type anything — whatever you type is saved as-is.
// Suggestions come from the bundled top-20 list (vehicles.js) and, when online, from NHTSA's
// free public vehicle database (vPIC) for every other make and model sold in the US.
// They step through Make → Model → Year → Trim, matching the "Make Model Year Trim" format.

const VPIC = 'https://vpic.nhtsa.dot.gov/api/vehicles';
const ALIASES = { vw: 'Volkswagen', chevy: 'Chevrolet', mercedes: 'Mercedes-Benz', benz: 'Mercedes-Benz', merc: 'Mercedes-Benz' };
const POPULAR = ['Volkswagen Beetle', 'Toyota Camry', 'Honda Civic', 'Ford F-150', 'Chevrolet Silverado 1500', 'Toyota RAV4', 'Honda CR-V', 'Tesla Model Y'];
const MAX_SUGGESTIONS = 8;
const NEWEST_YEAR = new Date().getFullYear() + 1;   // model years run a year ahead

const VS = { makes: Object.keys(VEHICLES), onlineModels: {}, pending: {}, online: null, refresh: null };

const lc = (s) => s.toLowerCase();
const tokensOf = (s) => lc(s).split(/\s+/).filter(Boolean);

// "LAND ROVER" -> "Land Rover", "MERCEDES-BENZ" -> "Mercedes-Benz", short acronyms like "BMW" stay as-is
function titleCase(s) {
  return s.trim().split(' ').map((w) => (w.length <= 3 && w === w.toUpperCase() ? w
    : w.toLowerCase().replace(/(^|-)\p{L}/gu, (c) => c.toUpperCase()))).join(' ');
}

function addUnique(list, items) {
  const seen = new Set(list.map(lc));
  for (const it of items) if (it && !seen.has(lc(it))) { seen.add(lc(it)); list.push(it); }
  return list;
}

async function loadOnlineMakes() {
  try {
    const types = ['car', 'multipurpose passenger vehicle (mpv)'];
    const res = await Promise.all(types.map((t) =>
      fetch(`${VPIC}/GetMakesForVehicleType/${encodeURIComponent(t)}?format=json`).then((r) => r.json())));
    const names = res.flatMap((r) => r.Results.map((m) => titleCase(m.MakeName))).sort();
    addUnique(VS.makes, names);
    VS.online = true;
  } catch {
    VS.online = false;   // no internet: the bundled list still works
  }
  VS.refresh?.();
}

function fetchModels(make) {
  const key = lc(make);
  if (VS.onlineModels[key] || VS.pending[key] || VS.online === false) return;
  VS.pending[key] = fetch(`${VPIC}/GetModelsForMake/${encodeURIComponent(make)}?format=json`)
    .then((r) => r.json())
    .then((r) => { VS.onlineModels[key] = addUnique([], r.Results.map((m) => m.Model_Name.trim())).sort(); VS.refresh?.(); })
    .catch(() => {})
    .finally(() => { delete VS.pending[key]; });
}

function modelsFor(make) {
  return addUnique(Object.keys(VEHICLES[make] || {}), VS.onlineModels[lc(make)] || []);
}

function yearsFor(make, model) {
  const known = VEHICLES[make]?.[model] && vehicleYears(make, model);
  if (known) return known;
  const out = [];
  for (let y = NEWEST_YEAR; y >= 1996; y--) out.push(String(y));   // OBD-II era
  return out;
}

// Every typed token must start a word of the text. Tokens with digits may also match with spaces/dashes removed ("f150" finds "F-150").
function matches(text, tokens) {
  const words = lc(text).split(/[\s-]+/);
  const flat = lc(text).replace(/[\s-]/g, '');
  return tokens.every((t) => words.some((w) => w.startsWith(t)) || (/\d/.test(t) && flat.includes(t)));
}

// Longest entry that the text starts with, followed by a space or the end of the text.
function findPrefix(list, text) {
  const t = lc(text);
  let best = null;
  for (const it of list) {
    const l = lc(it);
    if ((t === l || t.startsWith(l + ' ')) && (!best || it.length > best.length)) best = it;
  }
  return best;
}

function suggest(query) {
  let q = query.replace(/\s+/g, ' ').trimStart();
  const first = lc(q.split(' ')[0]);
  if (ALIASES[first]) q = ALIASES[first] + q.slice(first.length);

  const make = findPrefix(VS.makes, q);
  if (make) {
    fetchModels(make);
    const rest = q.slice(make.length).trim();
    const models = modelsFor(make);
    const model = findPrefix(models, rest);
    if (!model) {
      const tokens = tokensOf(rest);
      return models.filter((m) => matches(m, tokens)).slice(0, MAX_SUGGESTIONS)
        .map((m) => ({ text: `${make} ${m}`, hint: 'pick year' }));
    }
    const after = rest.slice(model.length).trim();
    const years = yearsFor(make, model);
    const trims = VEHICLES[make]?.[model] ? vehicleTrims(make, model) : [];
    const m = after.match(/^(\d{1,4})(?:\s+(.*))?$/);
    if (!after || (m && !m[2] && !years.includes(m[1]))) {
      return years.filter((y) => y.startsWith(m ? m[1] : '')).slice(0, MAX_SUGGESTIONS)
        .map((y) => ({ text: `${make} ${model} ${y}`, hint: trims.length ? 'pick trim' : '', final: !trims.length }));
    }
    if (m && years.includes(m[1]) && !trims.length) {
      return m[2] ? [] : [{ text: `${make} ${model} ${m[1]}`, final: true }];   // no trim data: confirm as typed
    }
    if (m && years.includes(m[1])) {
      const tokens = tokensOf(m[2] || '');
      return trims.filter((t) => matches(t, tokens))
        .map((t) => ({ text: `${make} ${model} ${m[1]} ${t}`, final: true }));
    }
    return [];   // beyond what we know: free text
  }

  const tokens = tokensOf(q);
  if (!tokens.length) return POPULAR.map((p) => ({ text: p, hint: 'pick year' }));
  const year = tokens.find((t) => /^(19|20)\d\d$/.test(t));
  const words = tokens.filter((t) => t !== year);
  const phrase = words.join(' ');

  const makeHits = words.length ? VS.makes.filter((mk) => matches(mk, words)).slice(0, 3)
    .map((mk) => ({ text: mk, hint: 'pick model' })) : [];
  const modelHits = [];
  const consider = (mk, mo) => {
    if (!matches(`${mk} ${mo}`, words) || (year && !yearsFor(mk, mo).includes(year))) return;
    modelHits.push({ text: `${mk} ${mo}${year ? ' ' + year : ''}`, hint: year ? 'pick trim' : 'pick year',
      rank: lc(mo).startsWith(phrase) ? 0 : 1 });
  };
  for (const mk of Object.keys(VEHICLES)) for (const mo of Object.keys(VEHICLES[mk])) consider(mk, mo);
  for (const [key, models] of Object.entries(VS.onlineModels)) {
    const mk = VS.makes.find((x) => lc(x) === key);
    for (const mo of models) if (!VEHICLES[mk]?.[mo]) consider(mk, mo);
  }
  modelHits.sort((a, b) => a.rank - b.rank);
  return addUniqueBy([...makeHits, ...modelHits]).slice(0, MAX_SUGGESTIONS);
}

// "Volkswagen Beetle 2019 SE" -> [{value:'Volkswagen', label:'Make'}, {…'Model'}, {…'Year'}, {…'Trim'}].
// Works for free text too: the 4-digit year splits model (before) from trim (after).
function vehicleParts(text) {
  let q = text.replace(/\s+/g, ' ').trim();
  if (!q) return [];
  const first = lc(q.split(' ')[0]);
  if (ALIASES[first]) q = ALIASES[first] + q.slice(first.length);
  const yearRe = /(?:^|\s)((?:19|20)\d\d)(?=\s|$)/;
  let make = findPrefix(VS.makes, q);
  if (!make) {
    const y0 = q.match(yearRe);
    if (!y0 || y0.index === 0) return [{ value: text.trim(), label: 'Vehicle' }];
    make = q.split(' ')[0];   // unknown make: assume it's the first word
  }
  const rest = q.slice(make.length).trim();
  const y = rest.match(yearRe);
  let model = rest, year = '', trim = '';
  if (y) {
    model = rest.slice(0, y.index).trim();
    year = y[1];
    trim = rest.slice(y.index + y[0].length).trim();
  }
  return [[make, 'Make'], [model, 'Model'], [year, 'Year'], [trim, 'Trim']]
    .filter(([v]) => v).map(([value, label]) => ({ value, label }));
}

function addUniqueBy(items) {
  const seen = new Set();
  return items.filter((s) => !seen.has(lc(s.text)) && seen.add(lc(s.text)));
}

// Wires the input + list. onCommit(name) fires when the user settles on a vehicle.
function initVehicleSearch(input, list, onCommit) {
  let items = [], active = -1, committed = input.value;
  const escHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const partsBox = input.parentElement.querySelector('.vparts');

  // Labelled view shown while not editing: each part with its caption underneath.
  function renderParts() {
    partsBox.innerHTML = vehicleParts(input.value)
      .map((p) => `<span><b>${escHtml(p.value)}</b><small>${p.label}</small></span>`).join('');
  }

  function render() {
    if (document.activeElement !== input) { list.hidden = true; renderParts(); return; }
    items = suggest(input.value);
    active = -1;
    const source = Object.keys(VS.pending).length ? 'Loading models…' : VS.online ? 'Top sellers + NHTSA database' : VS.online === false ? 'Offline · top 20 brands only' : 'Loading more makes…';
    list.innerHTML = items.map((s, i) => `<li role="option" data-i="${i}"><span>${escHtml(s.text)}</span>${s.hint ? `<small>${s.hint} ›</small>` : ''}</li>`).join('')
      + `<li class="vs-foot">${items.length ? '' : 'Press Enter to use exactly what you typed · '}${source}</li>`;
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  function highlight(i) {
    active = (i + items.length) % items.length;
    list.querySelectorAll('li[data-i]').forEach((li) => li.classList.toggle('on', +li.dataset.i === active));
    list.querySelector('li.on')?.scrollIntoView({ block: 'nearest' });
  }

  function commit() {
    const v = input.value.replace(/\s+/g, ' ').trim();
    if (!v) { input.value = committed; return; }
    input.value = v;
    if (v !== committed) { committed = v; onCommit(v); }
  }

  function choose(s) {
    input.value = s.text + (s.final ? '' : ' ');
    if (s.final) { commit(); input.blur(); } else render();
  }

  function close() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); }

  input.addEventListener('focus', () => { input.select(); render(); });
  input.addEventListener('input', render);
  input.addEventListener('blur', () => { close(); commit(); renderParts(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' && items.length) { e.preventDefault(); highlight(active + 1); }
    else if (e.key === 'ArrowUp' && items.length) { e.preventDefault(); highlight(active - 1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (active >= 0) choose(items[active]); else { commit(); input.blur(); }
    } else if (e.key === 'Escape') { input.value = committed; input.blur(); }
  });
  input.parentElement.querySelector('.vs-btn')?.addEventListener('mousedown', (e) => { e.preventDefault(); input.focus(); });
  list.addEventListener('mousedown', (e) => {
    const li = e.target.closest('li[data-i]');
    e.preventDefault();   // keep focus in the input
    if (li) choose(items[+li.dataset.i]);
  });

  VS.refresh = render;
  loadOnlineMakes();
  return {
    setValue(v) { if (document.activeElement !== input && v !== committed) { input.value = v; committed = v; renderParts(); } },
  };
}
