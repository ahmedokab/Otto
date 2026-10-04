"""The FREE-WILi status screen, drawn as an image.

The device's text command only shows one line, so Otto draws a full 320x240
screen (logo, connection, health ring, battery, engine temp, codes, button
hints) and the live source uploads it as a .fwi image when it changes.
"""
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 320, 240
ASSETS = Path(__file__).resolve().parent / "assets"
METRIC = os.getenv("OTTO_UNITS", "imperial") == "metric"

NAVY, NAVY_2, INK = (11, 26, 51), (19, 41, 75), (30, 58, 107)
ORANGE, WHITE, MUTED = (255, 95, 5), (241, 244, 249), (143, 160, 186)
GREEN, AMBER, RED, BLUE = (60, 203, 138), (242, 179, 61), (255, 93, 74), (91, 155, 255)


def _font(weight, size):
    try:
        return ImageFont.truetype(str(ASSETS / f"jakarta-{weight}.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def _round(d, box, r, fill):
    d.rounded_rectangle(box, radius=r, fill=fill)


def health_color(score):
    return MUTED if score is None else GREEN if score >= 75 else AMBER if score >= 50 else RED


def render(s):
    """s: {"mode", "status", "status_level", "score", "label", "volts", "running", "coolant_c", "codes", "capturing"}"""
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)

    # header band: logo, name, mode
    d.rectangle((0, 0, W, 50), fill=NAVY_2)
    d.rectangle((0, 50, W, 52), fill=ORANGE)
    try:
        logo = Image.open(ASSETS / "beetle.png").convert("RGBA").resize((68, 36), Image.LANCZOS)
        im.paste(logo, (8, 7), logo)
    except OSError:
        pass
    d.text((82, 25), "Otto", font=_font(800, 26), fill=WHITE, anchor="lm")
    mode = {"live": "LIVE", "simulator": "SIM", "replay": "HISTORY"}.get(s.get("mode"), "")
    if mode:
        f = _font(800, 11)
        tw = d.textlength(mode, font=f)
        _round(d, (W - tw - 26, 15, W - 10, 35), 10, ORANGE if mode == "LIVE" else INK)
        d.text((W - 18 - tw / 2, 25), mode, font=f, fill=WHITE, anchor="mm")

    # status line
    level = s.get("status_level", "idle")
    dot = {"ok": GREEN, "warn": AMBER, "bad": RED}.get(level, BLUE)
    d.ellipse((14, 64, 26, 76), fill=dot)
    d.text((34, 70), s.get("status", ""), font=_font(700, 16), fill=WHITE, anchor="lm")

    # health ring
    cx, cy, r = 66, 140, 44
    score = s.get("score")
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=INK, width=9)
    if score is not None:
        d.arc((cx - r, cy - r, cx + r, cy + r), start=-90, end=-90 + 360 * score / 100, fill=health_color(score), width=9)
    d.text((cx, cy - 4), "--" if score is None else str(score), font=_font(800, 30), fill=WHITE, anchor="mm")
    d.text((cx, cy + 20), "HEALTH", font=_font(700, 9), fill=MUTED, anchor="mm")
    label = s.get("label") or ""
    d.text((cx, 199), label if len(label) <= 16 else label.split(":")[0], font=_font(700, 12), fill=health_color(score), anchor="mm")

    # battery + engine temp tiles
    def tile(y, title, value, note, color):
        _round(d, (130, y, W - 10, y + 54), 10, NAVY_2)
        d.text((142, y + 13), title, font=_font(700, 9), fill=MUTED, anchor="lm")
        d.text((142, y + 34), value, font=_font(800, 22), fill=WHITE, anchor="lm")
        if note:
            d.text((W - 20, y + 36), note, font=_font(700, 10), fill=color, anchor="rm")

    v = s.get("volts")
    if v is None:
        tile(94, "BATTERY", "--", "", MUTED)
    else:
        if s.get("running"):
            note, col = ("Charging", GREEN) if 13.2 <= v <= 14.9 else ("Low", RED) if v < 12.8 else ("Check", AMBER)
        else:
            note, col = ("Good", GREEN) if v >= 12.4 else ("Low", AMBER)
        tile(94, "BATTERY", f"{v:.1f} V", note, col)
    t = s.get("coolant_c")
    if t is None:
        tile(154, "ENGINE TEMP", "--", "", MUTED)
    else:
        shown = f"{t:.0f} °C" if METRIC else f"{t * 9 / 5 + 32:.0f} °F"
        note, col = ("Hot", RED) if t >= 112 else ("Warm", AMBER) if t >= 105 else ("Warming up", BLUE) if t < 70 else ("Normal", GREEN)
        tile(154, "ENGINE TEMP", shown, note, col)

    # footer: codes + button hints
    d.rectangle((0, 214, W, H), fill=NAVY_2)
    codes = s.get("codes") or []
    if codes:
        f = _font(800, 11)
        txt = codes[0] + (f" +{len(codes) - 1}" if len(codes) > 1 else "")
        tw = d.textlength(txt, font=f)
        _round(d, (10, 219, 22 + tw, 235), 8, ORANGE)
        d.text((16 + tw / 2, 227), txt, font=f, fill=WHITE, anchor="mm")
    elif s.get("capturing"):
        d.text((12, 227), "No trouble codes", font=_font(700, 11), fill=GREEN, anchor="lm")
    hints = [(RED, "stop"), ((170, 178, 190), "mark")] if s.get("capturing") else [(GREEN, "start")]
    x = W - 10
    for color, word in reversed(hints):
        f = _font(700, 11)
        x -= d.textlength(word, font=f)
        d.text((x, 227), word, font=f, fill=WHITE, anchor="lm")
        x -= 14
        d.ellipse((x, 222, x + 10, 232), fill=color)
        x -= 10
    return im


def signature(s):
    """(important, values): what has to change before the screen is worth re-uploading.
    Important changes (connection, health, codes) refresh quickly; battery and temperature
    are rounded and refreshed slowly, because every upload takes ~5 s on the device."""
    v, t = s.get("volts"), s.get("coolant_c")
    important = (s.get("mode"), s.get("status"), s.get("score"), s.get("label"), tuple(s.get("codes") or ()),
                 s.get("capturing"), v is None, t is None)
    values = (None if v is None else int(v * 5 + 0.5) / 5, None if t is None else int(t / 2 + 0.5) * 2)
    return important, values


def file_name(image):
    """8.3 file name from the image's pixels: the same screen always gets the same name."""
    import hashlib
    return "o" + hashlib.sha1(image.tobytes()).hexdigest()[:7] + ".fwi"


def text_fallback(s):
    """One line for the device's text command if images can't be shown."""
    parts = ["Otto", s.get("status", "")]
    if s.get("score") is not None:
        parts.append(f"Health {s['score']}")
    if s.get("volts") is not None:
        parts.append(f"{s['volts']:.1f}V")
    return " | ".join(p for p in parts if p)
