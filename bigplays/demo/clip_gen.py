from __future__ import annotations

"""Synthetic highlight clip renderer.

Renders each scripted DemoPlay into a short broadcast-style MP4 (animated players,
ball, scoreboard bug, impact flash, lower-third headline) using Pillow for frames
and the ffmpeg binary bundled with imageio-ffmpeg for encoding. No system ffmpeg
needed. Output: data/demo_clips/<play_id>.mp4 and <play_id>.jpg poster.
"""

import math
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bigplays.demo.plays import PLAYS, DemoPlay

W, H = 960, 540
FPS = 30
DURATION = 6.5
T_PLAY = 3.6  # impact moment (seconds)

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
_font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(name: str, size: int) -> ImageFont.ImageFont:
    key = (name, size)
    if key not in _font_cache:
        candidates = [FONT_DIR / f"{name}.ttf", FONT_DIR / "Arial Bold.ttf", FONT_DIR / "Arial.ttf"]
        f: ImageFont.ImageFont = ImageFont.load_default()
        for c in candidates:
            if c.exists():
                f = ImageFont.truetype(str(c), size)
                break
        _font_cache[key] = f  # type: ignore[assignment]
    return _font_cache[key]


def hex_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease(u: float) -> float:
    u = clamp01(u)
    return u * u * (3 - 2 * u)


def prog(t: float, t0: float, t1: float) -> float:
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    return clamp01((t - t0) / (t1 - t0))


def arc(p0: Tuple[float, float], p1: Tuple[float, float], height: float, u: float) -> Tuple[float, float]:
    x = lerp(p0[0], p1[0], u)
    y = lerp(p0[1], p1[1], u) - height * math.sin(math.pi * u)
    return x, y


def px(p: Tuple[float, float]) -> Tuple[int, int]:
    return int(p[0] * W), int(p[1] * H)


# ---------------------------------------------------------------- backgrounds

def nba_background() -> Image.Image:
    img = Image.new("RGB", (W, H), (20, 14, 12))
    d = ImageDraw.Draw(img)
    # crowd gradient
    for y in range(0, int(H * 0.62)):
        k = y / (H * 0.62)
        d.line([(0, y), (W, y)], fill=(int(28 + 20 * k), int(20 + 14 * k), int(30 + 10 * k)))
    # crowd noise dots
    import random

    rng = random.Random(7)
    for _ in range(2600):
        x, y = rng.randint(0, W), rng.randint(int(H * 0.18), int(H * 0.60))
        c = rng.choice([(90, 70, 80), (120, 100, 110), (60, 50, 70), (140, 120, 90)])
        d.rectangle([x, y, x + 3, y + 5], fill=c)
    # ad boards
    d.rectangle([0, int(H * 0.60), W, int(H * 0.66)], fill=(210, 40, 50))
    d.text((W * 0.5, H * 0.63), "BIGPLAYS  •  LIVE HIGHLIGHTS  •  BIGPLAYS", fill=(255, 235, 235), font=font("Arial Black", 18), anchor="mm")
    # hardwood floor
    for y in range(int(H * 0.66), H):
        k = (y - H * 0.66) / (H * 0.34)
        d.line([(0, y), (W, y)], fill=(int(196 - 30 * k), int(150 - 30 * k), int(96 - 20 * k)))
    for x in range(0, W, 46):
        d.line([(x, int(H * 0.66)), (x - 40, H)], fill=(170, 125, 78), width=1)
    d.line([(0, int(H * 0.66)), (W, int(H * 0.66))], fill=(240, 240, 240), width=3)
    # three-point arc suggestion
    d.arc([W * 0.30, H * 0.60, W * 1.35, H * 1.15], 150, 260, fill=(235, 235, 235), width=3)
    # backboard + hoop
    d.rectangle([W * 0.905, H * 0.22, W * 0.925, H * 0.50], fill=(225, 225, 235))
    d.rectangle([W * 0.925, H * 0.0, W * 0.94, H * 0.24], fill=(120, 120, 130))
    d.rectangle([W * 0.925, H * 0.24, W * 0.99, H * 0.27], fill=(120, 120, 130))
    # rim + net
    d.rectangle([W * 0.855, H * 0.395, W * 0.907, H * 0.407], fill=(235, 95, 30))
    for i in range(6):
        x0 = W * (0.857 + i * 0.010)
        d.line([(x0, H * 0.407), (W * (0.866 + i * 0.006), H * 0.47)], fill=(230, 230, 230), width=1)
    d.line([(W * 0.86, H * 0.47), (W * 0.90, H * 0.47)], fill=(230, 230, 230), width=1)
    return img


def nfl_background(end_color: Tuple[int, int, int], end_side: str, end_label: str) -> Image.Image:
    img = Image.new("RGB", (W, H), (30, 90, 40))
    d = ImageDraw.Draw(img)
    stripes = 12
    for i in range(stripes):
        x0, x1 = int(W * i / stripes), int(W * (i + 1) / stripes)
        g = (38, 112, 48) if i % 2 == 0 else (32, 100, 42)
        d.rectangle([x0, 0, x1, H], fill=g)
    # end zone
    ez = [0, 0, int(W * 0.10), H] if end_side == "left" else [int(W * 0.90), 0, W, H]
    d.rectangle(ez, fill=end_color)
    ezc = ((ez[0] + ez[2]) // 2, H // 2)
    txt = Image.new("RGBA", (H, 100), (0, 0, 0, 0))
    ImageDraw.Draw(txt).text((H // 2, 50), end_label, fill=(255, 255, 255, 230), font=font("Arial Black", 54), anchor="mm")
    txt = txt.rotate(90 if end_side == "left" else -90, expand=True)
    img.paste(txt, (ezc[0] - txt.width // 2, ezc[1] - txt.height // 2), txt)
    # yard lines
    for i in range(1, 9):
        x = int(W * (0.10 + 0.10 * i))
        d.line([(x, 0), (x, H)], fill=(230, 230, 230), width=2)
        num = str(10 * i if i <= 5 else 100 - 10 * i)
        d.text((x + 6, H * 0.12), num, fill=(230, 230, 230), font=font("Arial Bold", 22))
        d.text((x + 6, H * 0.84), num, fill=(230, 230, 230), font=font("Arial Bold", 22))
    for x in range(int(W * 0.10), int(W * 0.90), 19):
        d.line([(x, H * 0.36), (x, H * 0.38)], fill=(220, 220, 220))
        d.line([(x, H * 0.62), (x, H * 0.64)], fill=(220, 220, 220))
    return img


# ---------------------------------------------------------------- sprites

def draw_player(d: ImageDraw.ImageDraw, pos: Tuple[float, float], color: Tuple[int, int, int], scale: float = 1.0,
                jump: float = 0.0, fallen: float = 0.0, number: str = "", top_down: bool = False) -> None:
    x, y = px(pos)
    floor_y = y
    y -= int(jump * 70)
    if top_down:
        r = int(16 * scale)
        d.ellipse([x - r - 3, y - r - 3, x + r + 3, y + r + 3], fill=(0, 0, 0, 90))
        d.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(255, 255, 255), width=2)
        d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(240, 200, 160))
        return
    bw, bh = int(28 * scale), int(64 * scale)
    if fallen > 0:
        # tilt into a fallen pose
        ang = -80 * ease(fallen)
        spr = Image.new("RGBA", (bw * 3, bh * 2), (0, 0, 0, 0))
        sd = ImageDraw.Draw(spr)
        cx, cy = spr.width // 2, spr.height - 4
        sd.rounded_rectangle([cx - bw // 2, cy - bh, cx + bw // 2, cy - int(bh * 0.25)], 8, fill=color)
        sd.rectangle([cx - bw // 2 + 3, cy - int(bh * 0.28), cx + bw // 2 - 3, cy], fill=tuple(int(c * 0.6) for c in color))
        sd.ellipse([cx - 11, cy - bh - 22, cx + 11, cy - bh], fill=(240, 200, 160))
        spr = spr.rotate(ang, resample=Image.BICUBIC, center=(cx, cy))
        d._image.paste(spr, (x - cx, y - cy), spr)  # type: ignore[attr-defined]
        return
    # shadow
    d.ellipse([x - bw, floor_y - 6, x + bw, floor_y + 6], fill=(0, 0, 0, 70))
    # legs
    d.rectangle([x - bw // 2 + 3, y - int(bh * 0.28), x + bw // 2 - 3, y], fill=tuple(int(c * 0.6) for c in color))
    # torso
    d.rounded_rectangle([x - bw // 2, y - bh, x + bw // 2, y - int(bh * 0.25)], 8, fill=color, outline=(255, 255, 255), width=2)
    if number:
        d.text((x, y - int(bh * 0.62)), number, fill=(255, 255, 255), font=font("Arial Black", int(16 * scale)), anchor="mm")
    # head
    d.ellipse([x - 11, y - bh - 24, x + 11, y - bh - 2], fill=(240, 200, 160))


def draw_ball(d: ImageDraw.ImageDraw, pos: Tuple[float, float], football: bool = False, r: int = 11) -> None:
    x, y = px(pos)
    if football:
        d.ellipse([x - 14, y - 8, x + 14, y + 8], fill=(120, 60, 30), outline=(255, 255, 255))
        d.line([(x - 6, y), (x + 6, y)], fill=(255, 255, 255), width=2)
    else:
        d.ellipse([x - r, y - r, x + r, y + r], fill=(228, 110, 30), outline=(60, 20, 0))
        d.arc([x - r, y - r, x + r, y + r], 30, 150, fill=(60, 20, 0))
        d.line([(x - r, y), (x + r, y)], fill=(60, 20, 0))


# ---------------------------------------------------------------- scenes

Scene = Callable[[ImageDraw.ImageDraw, float, DemoPlay], None]


def scene_nba(d: ImageDraw.ImageDraw, t: float, p: DemoPlay) -> None:
    floor = 0.86
    hoop = (0.88, 0.395)
    off = hex_rgb(p.away_color if p.team == p.away else p.home_color)
    de = hex_rgb(p.home_color if p.team == p.away else p.away_color)
    if de == off:
        de = (200, 200, 200)
    post = clamp01(t - T_PLAY)
    if p.kind in ("three", "buzzer"):
        sx = lerp(0.22, 0.36, ease(prog(t, 0.2, 1.8)))
        jump = math.sin(math.pi * prog(t, 2.1, 3.0)) if 2.1 < t < 3.0 else 0.0
        draw_player(d, (sx - 0.10, floor), de, jump=math.sin(math.pi * prog(t, 2.3, 3.2)) if 2.3 < t < 3.2 else 0.0, number="0")
        draw_player(d, (sx, floor), off, jump=jump, number="23")
        if t < 2.45:
            draw_ball(d, (sx + 0.02, floor - 0.17 - 0.10 * jump))
        elif t < T_PLAY:
            draw_ball(d, arc((sx + 0.03, floor - 0.30), hoop, 0.30, prog(t, 2.45, T_PLAY)))
        else:
            draw_ball(d, (hoop[0] - 0.01, hoop[1] + 0.10 * min(1.0, post * 3)))
        if post > 0:
            draw_player(d, (sx, floor), off, jump=0.4 * math.sin(6 * post), number="23")
    elif p.kind == "dunk":
        u = ease(prog(t, 0.6, 3.3))
        sx = lerp(0.12, 0.83, u)
        jump = math.sin(math.pi * prog(t, 3.0, 3.9)) if 3.0 < t < 3.9 else 0.0
        fallen = prog(t, T_PLAY - 0.1, T_PLAY + 0.5)
        draw_player(d, (0.78, floor), de, fallen=fallen, number="3", jump=(math.sin(math.pi * prog(t, 2.9, 3.5)) * 0.6 if 2.9 < t < 3.5 else 0.0))
        draw_player(d, (sx, floor), off, jump=jump, number="5")
        if t < T_PLAY:
            draw_ball(d, (sx + 0.03, floor - 0.16 - 0.05 * abs(math.sin(t * 9)) - 0.22 * jump))
        else:
            draw_ball(d, (hoop[0] - 0.01, hoop[1] + 0.14 * min(1.0, post * 3)))
    elif p.kind == "block":
        u = ease(prog(t, 0.6, 3.4))
        ax = lerp(0.15, 0.80, u)
        jump_a = math.sin(math.pi * prog(t, 3.1, 3.9)) if 3.1 < t < 3.9 else 0.0
        jump_d = math.sin(math.pi * prog(t, 3.2, 4.0)) if 3.2 < t < 4.0 else 0.0
        draw_player(d, (ax, floor), de, jump=jump_a, number="2")
        draw_player(d, (0.86, floor), off, scale=1.25, jump=jump_d, number="1")
        if t < T_PLAY:
            draw_ball(d, (ax + 0.03, floor - 0.16 - 0.25 * jump_a))
        else:
            draw_ball(d, arc((0.86, 0.50), (0.15, 0.05), 0.12, prog(t, T_PLAY, T_PLAY + 1.2)))


def scene_nfl(d: ImageDraw.ImageDraw, t: float, p: DemoPlay) -> None:
    off = hex_rgb(p.away_color if p.team == p.away else p.home_color)
    de = hex_rgb(p.home_color if p.team == p.away else p.away_color)
    if de == off:
        de = (220, 220, 220)
    if p.kind == "pick_six":
        # QB (defense's opponent) throws right-to-left, DB jumps route and returns it to the left end zone
        qb, wr = (0.62, 0.50), (0.42, 0.34)
        db = (0.40, 0.42)
        u_throw = prog(t, 0.8, 1.7)
        u_run = ease(prog(t, 1.8, T_PLAY))
        car = (lerp(db[0], 0.04, u_run), lerp(db[1], 0.55, u_run) + 0.06 * math.sin(u_run * 9))
        draw_player(d, qb, de, top_down=True)
        draw_player(d, (lerp(wr[0], 0.36, u_throw), wr[1]), de, top_down=True)
        for i, (x0, y0) in enumerate([(0.55, 0.62), (0.50, 0.30), (0.66, 0.44)]):
            cu = ease(prog(t, 1.9 + 0.1 * i, T_PLAY + 0.3))
            draw_player(d, (lerp(x0, car[0] + 0.10 + 0.04 * i, cu), lerp(y0, car[1] + 0.02 * i, cu)), de, top_down=True)
        for i, (x0, y0) in enumerate([(0.30, 0.60), (0.24, 0.25)]):
            cu = ease(prog(t, 1.8, T_PLAY))
            draw_player(d, (lerp(x0, car[0] + 0.05, cu), lerp(y0, car[1] + (0.08 if i else -0.08), cu)), off, top_down=True)
        if t < 0.8:
            draw_ball(d, qb, football=True)
        elif t < 1.75:
            draw_ball(d, arc(qb, db, 0.10, u_throw), football=True)
        else:
            draw_player(d, car, off, top_down=True)
            draw_ball(d, (car[0] + 0.012, car[1]), football=True)
    elif p.kind == "deep_pass":
        qb = (0.16, 0.50)
        wr0, catch = (0.28, 0.30), (0.66, 0.26)
        u_route = ease(prog(t, 0.3, 2.7))
        u_throw = prog(t, 1.2, 2.7)
        u_run = ease(prog(t, 2.7, T_PLAY))
        wr = (lerp(wr0[0], catch[0], u_route), lerp(wr0[1], catch[1], u_route))
        if t >= 2.7:
            wr = (lerp(catch[0], 0.96, u_run), lerp(catch[1], 0.38, u_run))
        draw_player(d, qb, off, top_down=True)
        for i, (x0, y0) in enumerate([(0.34, 0.24), (0.50, 0.40)]):
            cu = ease(prog(t, 0.4 + 0.2 * i, T_PLAY))
            draw_player(d, (lerp(x0, wr[0] - 0.05 - 0.03 * i, cu), lerp(y0, wr[1] + 0.04, cu)), de, top_down=True)
        for x0, y0 in [(0.24, 0.55), (0.26, 0.64), (0.22, 0.44)]:
            draw_player(d, (lerp(x0, x0 + 0.08, ease(prog(t, 0.3, 2.0))), y0), de, top_down=True)
        draw_player(d, wr, off, top_down=True)
        if t < 1.2:
            draw_ball(d, (qb[0] + 0.012, qb[1]), football=True)
        elif t < 2.7:
            draw_ball(d, arc(qb, catch, 0.16, u_throw), football=True)
        else:
            draw_ball(d, (wr[0] + 0.012, wr[1]), football=True)
    elif p.kind == "sack":
        qb0 = (0.58, 0.50)
        u_drop = ease(prog(t, 0.2, 1.4))
        qb = (lerp(qb0[0], 0.66, u_drop), qb0[1])
        u_rush = ease(prog(t, 1.4, T_PLAY))
        rusher = (lerp(0.42, qb[0] - 0.02, u_rush), lerp(0.22, qb[1], u_rush))
        for i, (x0, y0) in enumerate([(0.50, 0.40), (0.50, 0.48), (0.50, 0.56), (0.50, 0.64)]):
            draw_player(d, (lerp(x0, x0 + 0.06, u_drop), y0), de, top_down=True)
        for x0, y0 in [(0.47, 0.44), (0.47, 0.52), (0.47, 0.60)]:
            draw_player(d, (lerp(x0, x0 + 0.05, u_drop), y0), off, top_down=True)
        draw_player(d, qb, de, top_down=True)
        draw_player(d, rusher, off, top_down=True)
        if t < T_PLAY:
            draw_ball(d, (qb[0] + 0.012, qb[1]), football=True)
        else:
            draw_ball(d, arc(qb, (qb[0] - 0.08, qb[1] + 0.12), 0.06, prog(t, T_PLAY, T_PLAY + 0.8)), football=True)


# ---------------------------------------------------------------- overlays

def draw_scoreboard(d: ImageDraw.ImageDraw, t: float, p: DemoPlay) -> None:
    x, y, h = 24, 20, 54
    away_s = p.away_after if t >= T_PLAY else p.away_before
    home_s = p.home_after if t >= T_PLAY else p.home_before
    # clock: count down until T_PLAY, then freeze
    clock = p.clock
    try:
        if ":" in clock:
            mm, ss = clock.split(":")
            secs = int(mm) * 60 + float(ss)
            remaining = max(0.0, secs + (T_PLAY - t) if t < T_PLAY else secs)
            if p.kind == "buzzer" and t >= T_PLAY:
                remaining = 0.0
            m = int(remaining // 60)
            s = remaining - m * 60
            clock = f"{m}:{s:04.1f}" if remaining < 60 and p.league == "nba" else f"{m}:{int(s):02d}"
    except ValueError:
        pass
    d.rounded_rectangle([x, y, x + 470, y + h], 8, fill=(12, 12, 16, 235), outline=(60, 60, 70))
    ac, hc = hex_rgb(p.away_color), hex_rgb(p.home_color)
    d.rectangle([x, y, x + 8, y + h], fill=ac)
    d.rectangle([x + 176, y, x + 184, y + h], fill=hc)
    f_ab = font("Arial Black", 24)
    f_sc = font("Arial Black", 28)
    d.text((x + 22, y + h // 2), p.away, fill=(240, 240, 240), font=f_ab, anchor="lm")
    d.text((x + 150, y + h // 2), str(away_s), fill=(255, 255, 255), font=f_sc, anchor="rm")
    d.text((x + 198, y + h // 2), p.home, fill=(240, 240, 240), font=f_ab, anchor="lm")
    d.text((x + 326, y + h // 2), str(home_s), fill=(255, 255, 255), font=f_sc, anchor="rm")
    d.rectangle([x + 344, y + 8, x + 346, y + h - 8], fill=(80, 80, 90))
    d.text((x + 362, y + 18), p.period, fill=(255, 200, 60), font=font("Arial Bold", 16), anchor="lm")
    d.text((x + 362, y + 38), clock, fill=(255, 255, 255), font=font("Arial Bold", 18), anchor="lm")
    # LIVE pill
    d.rounded_rectangle([W - 118, 22, W - 24, 50], 14, fill=(200, 30, 40))
    d.ellipse([W - 104, 30, W - 92, 42], fill=(255, 255, 255))
    d.text((W - 60, 36), "LIVE", fill=(255, 255, 255), font=font("Arial Black", 16), anchor="mm")
    # watermark
    d.text((W - 24, H - 18), "BIGPLAYS", fill=(255, 255, 255, 140), font=font("Arial Black", 16), anchor="rm")


def draw_lower_third(d: ImageDraw.ImageDraw, t: float, p: DemoPlay) -> None:
    u = ease(prog(t, T_PLAY + 0.15, T_PLAY + 0.55))
    if u <= 0:
        return
    bar_y0, bar_y1 = int(H * 0.74), int(H * 0.92)
    x_off = int((1 - u) * -W)
    color = hex_rgb(p.away_color if p.team == p.away else p.home_color)
    d.rectangle([x_off, bar_y0, x_off + W, bar_y1], fill=(8, 8, 12, 225))
    d.rectangle([x_off, bar_y0, x_off + 14, bar_y1], fill=color)
    size = 34
    f = font("Arial Black", size)
    while d.textlength(p.title, font=f) > W - 60 and size > 18:
        size -= 2
        f = font("Arial Black", size)
    d.text((x_off + 34, bar_y0 + 16), p.title, fill=(255, 255, 255), font=f)
    sub = f"{p.player}  •  {p.description[:70]}"
    d.text((x_off + 34, bar_y1 - 30), sub, fill=(200, 200, 210), font=font("Arial Bold", 16))
    # hype chip
    chip = f"HYPE {p.hype_score:.2f}"
    fw = d.textlength(chip, font=font("Arial Black", 16))
    d.rounded_rectangle([x_off + W - 40 - fw - 24, bar_y0 - 34, x_off + W - 40, bar_y0 - 6], 12, fill=(255, 190, 40))
    d.text((x_off + W - 40 - fw / 2 - 12, bar_y0 - 20), chip, fill=(20, 20, 20), font=font("Arial Black", 16), anchor="mm")


def render_frame(t: float, p: DemoPlay, bg: Image.Image) -> Image.Image:
    frame = bg.copy().convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if p.league == "nba":
        scene_nba(d, t, p)
    else:
        scene_nfl(d, t, p)
    frame.alpha_composite(layer)
    # impact flash + shake
    dt = t - T_PLAY
    if 0 <= dt < 0.35:
        a = int(200 * (1 - dt / 0.35))
        flash = Image.new("RGBA", (W, H), (255, 255, 255, a))
        frame.alpha_composite(flash)
        sh = int(8 * (1 - dt / 0.35))
        frame = frame.transform(frame.size, Image.AFFINE, (1, 0, sh * (1 if int(dt * 60) % 2 else -1), 0, 1, sh * (1 if int(dt * 40) % 2 else -1)))
    hud = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hud)
    draw_scoreboard(hd, t, p)
    draw_lower_third(hd, t, p)
    frame.alpha_composite(hud)
    # vignette
    return frame.convert("RGB")


def _vignette() -> Image.Image:
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse([-W * 0.2, -H * 0.3, W * 1.2, H * 1.3], fill=255)
    return m.filter(ImageFilter.GaussianBlur(120))


def render_play(p: DemoPlay, out_dir: Path, fps: int = FPS, duration: float = DURATION) -> Tuple[Path, Path]:
    import imageio_ffmpeg

    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{p.play_id}.mp4"
    poster = out_dir / f"{p.play_id}.jpg"
    if p.league == "nba":
        bg = nba_background()
    else:
        ez_color = hex_rgb(p.away_color if p.team == p.away else p.home_color)
        side = "left" if p.kind == "pick_six" else "right"
        bg = nfl_background(ez_color, side, p.team)
    vig = _vignette()
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    n = int(fps * duration)
    writer = imageio_ffmpeg.write_frames(
        str(mp4), size=(W, H), fps=fps, codec="libx264", pix_fmt_out="yuv420p", quality=None,
        output_params=["-crf", "22", "-preset", "veryfast", "-movflags", "+faststart", "-profile:v", "high"],
        macro_block_size=1,
    )
    writer.send(None)
    poster_saved = False
    for i in range(n):
        t = i / fps
        fr = render_frame(t, p, bg)
        fr = Image.composite(fr, Image.blend(fr, dark, 0.45), vig)
        if not poster_saved and t >= T_PLAY + 0.7:
            fr.save(poster, quality=88)
            poster_saved = True
        writer.send(fr.tobytes())
    writer.close()
    return mp4, poster


def render_all(out_dir: Path, only: Optional[List[str]] = None, force: bool = False) -> List[Path]:
    outs: List[Path] = []
    for p in PLAYS:
        if only and p.play_id not in only:
            continue
        mp4 = out_dir / f"{p.play_id}.mp4"
        if mp4.exists() and not force:
            outs.append(mp4)
            continue
        m, _ = render_play(p, out_dir)
        outs.append(m)
        print(f"rendered {m}")
    return outs


if __name__ == "__main__":
    import sys

    render_all(Path("data/demo_clips"), only=sys.argv[1:] or None, force=True)
