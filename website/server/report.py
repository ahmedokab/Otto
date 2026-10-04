"""Printable mechanic report (HTML). The owner prints / saves as PDF from the browser."""
from html import escape as e

from .state import MODE_LABELS, READINGS

SEV = {"info": "Info", "caution": "Caution", "stop": "Stop driving"}
STATUS = {"ok": "OK", "watch": "Watch", "problem": "Problem", "unknown": "No data"}
URGENCY = {"now": "now (don't keep driving)", "soon": "soon", "when_convenient": "when convenient", "not_needed": "not needed"}
VERDICT = {"supports": "✓ supports", "against": "✕ contradicts", "neutral": "– neutral", "missing": "? not available"}


def _fmt(v, dp=1):
    return "—" if v is None else f"{v:.{dp}f}"


def render_html(ctx):
    sim = ctx["data_mode"] != "live"
    codes_html = ""
    for d in ctx["code_details"]:
        ev = "".join(
            f"<tr><td>{e(READINGS.get(x['key'], {}).get('label', x['key']))} ({e(x['stat'] or '')})</td>"
            f"<td>{_fmt(x['value'])}</td><td>{e(VERDICT[x['verdict']])}</td><td>{e(x['note'])}</td></tr>"
            for x in d["evidence"])
        codes_html += f"""
        <section class="code">
          <h3><span class="mono">{e(d['code'])}</span> {e(d['title'])}
              <span class="sev {e(d['severity'])}">{e(SEV.get(d['severity'], ''))}</span></h3>
          {('<p class="muted">Reported as: ' + e(', '.join(d['status'])) + '</p>') if d.get('status') else ''}
          <p>{e(d['meaning'])}</p>
          <p><b>Driving:</b> {e(d['driving'])}</p>
          {'<table><tr><th>Measurement</th><th>Value</th><th>Verdict</th><th>Note</th></tr>' + ev + '</table>' if ev else ''}
        </section>"""
    if not ctx["code_details"]:
        codes_html = "<p><b>No codes reported</b> by the ECU during this session. This does not by itself mean the vehicle has no faults.</p>"
    if ctx.get("code_checks"):
        codes_html += f"<p class='muted'>Checked: {e(', '.join(ctx['code_checks']))} codes.</p>"

    h = ctx.get("health") or {}
    systems = "".join(
        f"<tr><td>{e(x['name'])}</td><td class='st {e(x['status'])}'>{e(STATUS.get(x['status'], x['status']))}</td>"
        f"<td>{e('; '.join(x['reasons']))}</td></tr>" for x in h.get("systems", []))
    good = "".join(f"<li>{e(p)}</li>" for p in h.get("positives", []))
    health_html = f"""
    <h2>Vehicle health</h2>
    <div class="score"><b>{'—' if h.get('score') is None else h['score']}</b><span>/100 · {e(h.get('label', ''))}</span></div>
    {'<h4>Going well</h4><ul>' + good + '</ul>' if good else ''}
    <table><tr><th>System</th><th>Status</th><th>Why</th></tr>{systems}</table>"""

    d = ctx.get("diagnostics") or {}
    parts = []
    tests = d.get("readiness") or []
    if tests:
        open_ = [t["name"] for t in tests if not t["ready"]]
        verdict = "Yes, all self-tests complete" if not open_ else f"{len(open_)} of {len(tests)} not finished: {', '.join(open_)}"
        parts.append(f"<p><b>Ready for inspection:</b> {e(verdict)}</p>")
    if d.get("fuel_status"):
        parts.append(f"<p><b>Fuel control:</b> {e(d['fuel_status']['text'])}</p>")
    mis = d.get("misfires") or {}
    if mis:
        cells = "".join(f"<td>Cyl {e(c)}: <b>{v.get('recent', v.get('last_drive', 0))}</b></td>" for c, v in mis.items())
        parts.append(f"<p><b>Misfires by cylinder</b> (recent drives)</p><table><tr>{cells}</tr></table>")
    ff = d.get("freeze_frame") or {}
    if ff.get("code"):
        rows_ff = "".join(f"<tr><td>{e(READINGS.get(k, {}).get('label', k))}</td><td>{_fmt(v)} {e(READINGS.get(k, {}).get('unit', ''))}</td></tr>"
                          for k, v in (ff.get("readings") or {}).items())
        parts.append(f"<p><b>Snapshot when {e(ff['code'])} was set</b></p><table>{rows_ff}</table>")
    diag_html = ("<h2>Under the hood</h2>" + "".join(parts)) if parts else ""

    ex = ctx.get("explanation")
    ex_html = ""
    if ex:
        items = ""
        for f in ex.get("findings", []):
            causes = "".join(f"<li><b>{e(c.get('cause',''))}</b> ({e(c.get('likelihood',''))}) — {e(c.get('why',''))}</li>"
                             for c in f.get("likely_causes", []))
            diy = "".join(f"<li>{e(d.get('step',''))} <span class='muted'>({e(d.get('difficulty',''))}"
                          f"{'; ' + e(d['tools']) if d.get('tools') else ''})</span></li>" for d in f.get("diy_steps", []))
            items += (f"<h4><span class='mono'>{e(f.get('code',''))}</span> {e(f.get('title',''))}</h4>"
                      f"<p>{e(f.get('plain_meaning',''))}</p>"
                      f"{'<p><b>Likely causes</b></p><ul>' + causes + '</ul>' if causes else ''}"
                      f"{'<p><b>Try first</b></p><ol>' + diy + '</ol>' if diy else ''}"
                      f"{('<p><b>See a mechanic if:</b> ' + e(f['mechanic_needed_if']) + '</p>') if f.get('mechanic_needed_if') else ''}")
        qs = "".join(f"<li>{e(q)}</li>" for q in ex.get("questions_for_mechanic", []))
        well = "".join(f"<li>{e(w)}</li>" for w in ex.get("going_well", []))
        mech = ex.get("mechanic") or {}
        src = "AI analysis" if ex.get("source") == "ai" else "Offline reference"
        ex_html = f"""
        <h2>Analysis <small>{src}</small></h2>
        <p><b>{e(ex.get('headline',''))}</b></p>
        <p>{e(ex.get('health_summary',''))}</p>
        <p>Safety: <b>{e(ex.get('safety',{}).get('level','').upper())}</b> — {e(ex.get('safety',{}).get('message',''))}</p>
        {'<h4>What looks good</h4><ul>' + well + '</ul>' if well else ''}
        {items}
        {('<p><b>Mechanic:</b> ' + ('recommended, ' + e(URGENCY.get(mech.get('urgency'), '')) if mech.get('recommended') else 'not needed right now') + ' — ' + e(mech.get('why','')) + '</p>') if mech else ''}
        {'<h4>Questions to ask</h4><ul>' + qs + '</ul>' if qs else ''}
        <p class="muted">{e(ex.get('caveat',''))}</p>"""

    rows = ""
    whole = ctx.get("session_stats") or {}
    for k, meta in READINGS.items():
        st = whole.get(k) or ctx["stats_last_60s"].get(k)      # whole capture when known
        now = ctx["readings_now"].get(k)
        if st is None and now is None:
            continue
        rows += (f"<tr><td>{e(meta['label'])}</td><td>{e(meta['unit'])}</td><td>{_fmt(now)}</td>"
                 f"<td>{_fmt(st and st['min'])}</td><td>{_fmt(st and st['max'])}</td><td>{_fmt(st and st['mean'])}</td></tr>")
    markers = "".join(f"<li><span class='mono'>{e(m['t'])}</span> — {e(m['note'])} "
                      f"<span class='muted'>(rpm {_fmt(m['readings'].get('rpm'),0)}, coolant {_fmt(m['readings'].get('coolant_c'))} °C, "
                      f"{_fmt(m['readings'].get('ecu_voltage_v'),2)} V)</span></li>"
                      for m in ctx["markers"])

    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Otto report — {e(ctx['vehicle'] or 'vehicle')}</title>
<style>
 body{{font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#13294B;background:#fff;max-width:860px;margin:32px auto;padding:0 20px}}
 h1{{margin:0 0 4px;font-size:26px;font-weight:900}} .top{{border-top:8px solid #13294B;box-shadow:inset 0 4px 0 #FF5F05;padding-top:18px}} h2{{margin-top:28px;border-bottom:3px solid #FF5F05;padding-bottom:4px}} h2 small{{font-weight:500;color:#667;font-size:13px}}
 .mono{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}} .muted{{color:#667}}
 table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}} td,th{{border-bottom:1px solid #dde;padding:6px 8px;text-align:left}}
 .sev{{font-size:12px;padding:2px 8px;border-radius:99px;margin-left:8px;vertical-align:middle}}
 .sev.stop{{background:#fde2e1;color:#a11}} .sev.caution{{background:#fff1d6;color:#8a5a00}} .sev.info{{background:#e3efff;color:#1d4f91}}
 .banner{{background:repeating-linear-gradient(45deg,#fff1d6,#fff1d6 12px,#ffe4a8 12px,#ffe4a8 24px);border:1px solid #e0b040;padding:10px 14px;border-radius:8px;font-weight:600}}
 .meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:12px 0}} .meta div{{background:#f4f5f7;border-radius:8px;padding:8px 10px}}
 .meta b{{display:block;font-size:12px;color:#667;font-weight:600;text-transform:uppercase;letter-spacing:.04em}}
 .print{{position:fixed;top:16px;right:16px;padding:8px 14px;border-radius:8px;border:0;background:#FF5F05;color:#fff;font-weight:700;cursor:pointer}}
 .score{{display:flex;align-items:baseline;gap:8px}} .score b{{font-size:44px;line-height:1;color:#FF5F05}} .score span{{font-weight:600}}
 .st.ok{{color:#1f8a5b;font-weight:600}} .st.watch{{color:#8a5a00;font-weight:600}} .st.problem{{color:#a11;font-weight:700}} .st.unknown{{color:#889}}
 .foot{{margin-top:32px;padding-top:12px;border-top:3px solid #13294B;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:13px}}
 @media print{{.print{{display:none}} body{{margin:0}}}}
</style></head><body>
<button class="print" onclick="window.print()">Print / Save PDF</button>
<div class="top"><h1>Otto vehicle report</h1></div>
<div class="muted">One less trip to the mechanic · made by the UofI Car Guys · {e(ctx['generated'])}</div>
{'<p class="banner">SIMULATED / SAVED-HISTORY DATA — for demonstration, not a fresh reading from the vehicle.</p>' if sim else ''}
<div class="meta">
 <div><b>Vehicle</b>{e(ctx['vehicle'] or 'Not identified')}{('<br><span class="mono">VIN ' + e(ctx['vin']) + '</span>') if ctx.get('vin') else ''}</div>
 <div><b>Data source</b>{e(MODE_LABELS.get(ctx['data_mode'], ctx['data_mode'].upper()))}{(' · ' + e(ctx['scenario'])) if ctx.get('scenario') and ctx['data_mode']=='simulator' else ''}</div>
 <div><b>Session start</b>{e(ctx.get('session_start') or '—')}</div>
 <div><b>Check-engine lamp</b>{'ON' if ctx['mil'] else 'Off'}</div>
</div>
{('<h2>Owner-reported symptoms</h2><p>' + e(ctx['symptoms']) + '</p>') if ctx.get('symptoms') else ''}
{health_html}
<h2>Trouble codes</h2>{codes_html}
{diag_html}
{ex_html}
{('<h2>Symptom markers</h2><ul>' + markers + '</ul>') if markers else ''}
<h2>Readings <small>{"whole capture" if whole else "last 60 s of capture"}{(" · full recording: " + e(ctx["recording_file"])) if ctx.get("recording_file") else ""}</small></h2>
<table><tr><th>Measurement</th><th>Unit</th><th>Latest</th><th>Min</th><th>Max</th><th>Avg</th></tr>{rows or '<tr><td colspan=6>No readings captured</td></tr>'}</table>
<p class="muted">This report summarizes data read from the vehicle's OBD-II port. A trouble code identifies a condition the car's computer detected; it does not by itself prove which part has failed. Please have a qualified technician confirm the cause.</p>
<div class="foot"><b>Otto · One less trip to the mechanic</b><span>Made by the UofI Car Guys · read via FREE-WILi, read-only OBD-II</span></div>
</body></html>"""
