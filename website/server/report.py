"""Printable mechanic report (HTML). The owner prints / saves as PDF from the browser."""
from html import escape as e

from .state import MODE_LABELS, READINGS

SEV = {"info": "Info", "caution": "Caution", "stop": "Stop driving"}
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
          <p>{e(d['meaning'])}</p>
          <p><b>Driving:</b> {e(d['driving'])}</p>
          {'<table><tr><th>Measurement</th><th>Value</th><th>Verdict</th><th>Note</th></tr>' + ev + '</table>' if ev else ''}
        </section>"""
    if not ctx["code_details"]:
        codes_html = "<p><b>No codes reported</b> by the ECU during this session. This does not by itself mean the vehicle has no faults.</p>"

    ex = ctx.get("explanation")
    ex_html = ""
    if ex:
        items = ""
        for f in ex.get("findings", []):
            causes = "".join(f"<li><b>{e(c.get('cause',''))}</b> ({e(c.get('likelihood',''))}) — {e(c.get('why',''))}</li>"
                             for c in f.get("likely_causes", []))
            items += f"<h4 class='mono'>{e(f.get('code',''))}</h4><ul>{causes}</ul>"
        qs = "".join(f"<li>{e(q)}</li>" for q in ex.get("questions_for_mechanic", []))
        src = "AI-assisted (" + e(ex.get("model") or "") + ")" if ex.get("source") == "ai" else "Offline reference"
        ex_html = f"""
        <h2>Analysis <small>{src}</small></h2>
        <p><b>{e(ex.get('headline',''))}</b></p>
        <p>Safety: <b>{e(ex.get('safety',{}).get('level','').upper())}</b> — {e(ex.get('safety',{}).get('message',''))}</p>
        {items}
        {'<h4>Questions to ask</h4><ul>' + qs + '</ul>' if qs else ''}
        <p class="muted">{e(ex.get('caveat',''))}</p>"""

    rows = ""
    for k, meta in READINGS.items():
        st = ctx["stats_last_60s"].get(k)
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
<title>Otto report — {e(ctx['vehicle'])}</title>
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
 @media print{{.print{{display:none}} body{{margin:0}}}}
</style></head><body>
<button class="print" onclick="window.print()">Print / Save PDF</button>
<div class="top"><h1>Vehicle diagnostic report</h1></div>
<div class="muted">Generated by Otto (FREE-WILi OBD-II capture, read-only) · {e(ctx['generated'])}</div>
{'<p class="banner">SIMULATED / SAVED-HISTORY DATA — for demonstration, not a fresh reading from the vehicle.</p>' if sim else ''}
<div class="meta">
 <div><b>Vehicle</b>{e(ctx['vehicle'])}</div>
 <div><b>Data source</b>{e(MODE_LABELS.get(ctx['data_mode'], ctx['data_mode'].upper()))}{(' · ' + e(ctx['scenario'])) if ctx.get('scenario') and ctx['data_mode']=='simulator' else ''}</div>
 <div><b>Session start</b>{e(ctx.get('session_start') or '—')}</div>
 <div><b>Check-engine lamp</b>{'ON' if ctx['mil'] else 'Off'}</div>
</div>
{('<h2>Owner-reported symptoms</h2><p>' + e(ctx['symptoms']) + '</p>') if ctx.get('symptoms') else ''}
<h2>Trouble codes</h2>{codes_html}
{ex_html}
{('<h2>Symptom markers</h2><ul>' + markers + '</ul>') if markers else ''}
<h2>Readings <small>last 60 s of capture</small></h2>
<table><tr><th>Measurement</th><th>Unit</th><th>Latest</th><th>Min</th><th>Max</th><th>Avg</th></tr>{rows or '<tr><td colspan=6>No readings captured</td></tr>'}</table>
<p class="muted">This report summarizes data read from the vehicle's OBD-II port. A trouble code identifies a condition the engine computer detected; it does not by itself prove which part has failed. Please have a qualified technician confirm the cause.</p>
</body></html>"""
