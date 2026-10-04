// Otto landing page: pinned scroll tour of the car's systems, live readouts, scroll reveals.
(() => {
  const $ = (id) => document.getElementById(id);
  const tour = $('tour');
  const steps = [...document.querySelectorAll('.step')];
  const car = document.querySelector('.car');
  const N = steps.length;

  // step -> system highlighted, zoom point (viewBox units), readout rows
  const SYSTEMS = [
    null,
    { sys: 'brain', at: [400, 240], label: 'Engine computer', rows: [['vin', 'VIN'], ['codes', 'Trouble codes'], ['checks', 'Code checks']] },
    { sys: 'engine', at: [220, 262], label: 'Engine', rows: [['rpm', 'RPM'], ['load_pct', 'Load'], ['timing_deg', 'Timing']] },
    { sys: 'fuel_air', at: [420, 268], label: 'Fuel & air', rows: [['stft_pct', 'Short trim'], ['ltft_pct', 'Long trim'], ['map_kpa', 'Intake pressure']] },
    { sys: 'cooling', at: [110, 264], label: 'Cooling', rows: [['coolant_c', 'Coolant'], ['oil_c', 'Oil'], ['intake_c', 'Intake air']] },
    { sys: 'electrical', at: [140, 276], label: 'Battery & charging', rows: [['ecu_voltage_v', 'Voltage'], ['rpm', 'Engine']] },
    { sys: 'throttle', at: [340, 258], label: 'Throttle & pedal', rows: [['pedal_d_pct', 'Gas pedal'], ['cmd_throttle_pct', 'Commanded'], ['throttle_pct', 'Throttle']] },
    { sys: 'emissions', at: [600, 312], label: 'Emissions', rows: [['cat_c', 'Catalyst'], ['o2_down_v', 'Rear O2'], ['lambda', 'Air-fuel λ']] },
  ];
  // What a healthy idling car typically shows, used when no capture is running
  const SAMPLE = { rpm: 742, load_pct: 21, timing_deg: 6.5, stft_pct: -0.8, ltft_pct: 2.3, map_kpa: 31, coolant_c: 90, oil_c: 86,
    intake_c: 34, ecu_voltage_v: 14.0, pedal_d_pct: 15, cmd_throttle_pct: 4, throttle_pct: 13, cat_c: 512, o2_down_v: 0.68,
    lambda: 1.0, vin: 'read from car', codes: 'none stored', checks: '4 of 4' };
  const FMT = {
    rpm: (v) => `${Math.round(v)}`, load_pct: (v) => `${Math.round(v)} %`, timing_deg: (v) => `${(+v).toFixed(1)}°`,
    stft_pct: (v) => `${v > 0 ? '+' : ''}${(+v).toFixed(1)} %`, ltft_pct: (v) => `${v > 0 ? '+' : ''}${(+v).toFixed(1)} %`,
    map_kpa: (v) => `${Math.round(v)} kPa`, coolant_c: F, oil_c: F, intake_c: F, cat_c: F,
    ecu_voltage_v: (v) => `${(+v).toFixed(1)} V`, pedal_d_pct: pct, cmd_throttle_pct: pct, throttle_pct: pct,
    o2_down_v: (v) => `${(+v).toFixed(2)} V`, lambda: (v) => `${(+v).toFixed(2)}`,
  };
  function F(v) { return `${Math.round(v * 9 / 5 + 32)} °F`; }
  function pct(v) { return `${Math.round(v)} %`; }

  let live = null, current = -1;

  function liveValue(key) {
    if (!live) return null;
    if (key === 'vin') return live.vin;
    if (key === 'codes') return live.trouble_codes.length ? live.trouble_codes.join(', ') : 'none';
    if (key === 'checks') return live.code_checks.length ? `${live.code_checks.length} of 4` : null;
    return live.readings[key];
  }

  function renderHud(i) {
    const hud = $('hud');
    const s = SYSTEMS[i];
    if (!s) { hud.classList.remove('on'); return; }
    let anyLive = false;
    $('hudLabel').textContent = s.label;
    $('hudRows').innerHTML = s.rows.map(([key, name]) => {
      let v = liveValue(key);
      if (v != null) anyLive = true; else v = SAMPLE[key];
      const shown = typeof v === 'number' && FMT[key] ? FMT[key](v) : v;
      return `<div><span>${name}</span><b>${String(shown).replace(/</g, '&lt;')}</b></div>`;
    }).join('');
    $('hudSrc').textContent = anyLive ? '● Live from your car' : 'Sample values · start a capture to see yours';
    $('hudSrc').className = 'hud-src' + (anyLive ? ' live' : '');
    hud.classList.add('on');
  }

  function setStep(i) {
    if (i === current) return;
    current = i;
    steps.forEach((el, k) => el.classList.toggle('on', k === i));
    const s = SYSTEMS[i];
    car.classList.toggle('focus', !!s);
    for (const g of car.querySelectorAll('.sys')) g.classList.toggle('on', !!s && g.dataset.sys === s.sys);
    if (s) {
      car.style.transformOrigin = `${s.at[0] / 10}% ${s.at[1] / 3.8}%`;
      car.style.transform = 'scale(1.32)';
    } else {
      car.style.transform = 'none';
    }
    renderHud(i);
  }

  function onScroll() {
    const r = tour.getBoundingClientRect();
    const total = tour.offsetHeight - innerHeight;
    const p = Math.min(1, Math.max(0, -r.top / total));
    setStep(Math.min(N - 1, Math.floor(p * N)));
    $('progressBar').style.width = (p * 100).toFixed(1) + '%';
    $('nav').classList.toggle('scrolled', scrollY > 20);
  }
  addEventListener('scroll', onScroll, { passive: true });
  addEventListener('resize', onScroll);
  // #step-3 opens the tour at that system (demos, and links from the dashboard)
  const deep = /^#step-(\d)$/.exec(location.hash);
  if (deep) {
    document.documentElement.style.scrollBehavior = 'auto';
    scrollTo(0, tour.offsetTop + (tour.offsetHeight - innerHeight) * (+deep[1] + 0.5) / N);
  }
  onScroll();

  $('seeInside').addEventListener('click', (e) => {
    e.preventDefault();
    scrollTo({ top: tour.offsetTop + (tour.offsetHeight - innerHeight) / N * 1.05, behavior: 'smooth' });
  });

  // Live values from a running capture (the landing page is served by the same Otto server)
  async function poll() {
    try {
      const r = await fetch('/api/state');
      const s = await r.json();
      live = s.session.frames > 0 && s.connection !== 'disconnected' ? s : null;
      if (current > 0) renderHud(current);
    } catch { live = null; }
  }
  poll();
  setInterval(() => { if (tour.getBoundingClientRect().bottom > 0) poll(); }, 2000);

  // Scroll reveals + health dial
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
  }, { threshold: 0.18 });
  document.querySelectorAll('.reveal').forEach((el) => io.observe(el));

  const dial = $('dial');
  new IntersectionObserver((entries, obs) => {
    if (!entries[0].isIntersecting) return;
    obs.disconnect();
    const target = 92, C = 540.4;
    dial.querySelector('.fill').style.strokeDashoffset = C * (1 - target / 100);
    const t0 = performance.now();
    (function tick(t) {
      const k = Math.min(1, (t - t0) / 2000);
      $('dialNum').textContent = Math.round(target * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(tick);
    })(t0);
  }, { threshold: 0.4 }).observe(dial);
})();
