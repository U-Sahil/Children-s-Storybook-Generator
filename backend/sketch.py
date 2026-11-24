"""Offline fallback illustration, drawn locally with Pillow (NOT AI).

Used only when the AI image call fails, so a page is never blank at a demo. It reads the scene text for
time of day and place, and the character profile for hair/clothes colour, and paints a simple picture.
The image carries a small "offline sketch" label so nobody mistakes it for AI output.
"""
from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFont

from .models import Character, Page

W, H = 1024, 768
SS = 2  # draw at 2x and shrink for smooth edges

COLORS = {
    "black": (35, 30, 35), "brown": (110, 70, 40), "blonde": (235, 200, 110), "golden": (235, 200, 110),
    "yellow": (245, 210, 60), "red": (215, 60, 55), "ginger": (200, 100, 40), "orange": (240, 140, 40),
    "green": (70, 160, 90), "blue": (70, 120, 210), "purple": (140, 90, 190), "pink": (240, 130, 170),
    "white": (245, 245, 245), "grey": (150, 150, 155), "gray": (150, 150, 155), "teal": (50, 160, 160),
}


def _pick(text: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    low = text.lower()
    best = None
    for word, rgb in COLORS.items():
        i = low.find(word)
        if i != -1 and (best is None or i < best[0]):
            best = (i, rgb)
    return best[1] if best else default


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _gradient(d: ImageDraw.ImageDraw, top, bottom, h: int, w: int) -> None:
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))


def draw_sketch(character: Character, page: Page) -> bytes:
    scene = page.scene_description.lower()
    rnd = random.Random(page.page_number * 7919)
    w, h = W * SS, H * SS
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)

    space = _has(scene, ("space", "rocket", "planet", "galaxy", "astronaut", "moon surface"))
    night = space or _has(scene, ("night", "moon", "stars", "bedtime", "midnight", "dark"))
    dusk = _has(scene, ("sunset", "sunrise", "evening", "dusk", "golden hour"))
    sea = _has(scene, ("sea", "ocean", "beach", "shore", "boat", "island", "wave", "pirate", "lake", "river"))
    snow = _has(scene, ("snow", "winter", "ice", "frozen"))
    trees = _has(scene, ("forest", "woods", "jungle", "tree", "garden", "meadow", "park", "hill", "farm"))

    # sky
    if night:
        _gradient(d, (20, 24, 70), (90, 70, 140), h, w)
        for _ in range(70):
            x, y, r = rnd.randrange(w), rnd.randrange(int(h * 0.55)), rnd.randrange(2, 6) * SS
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 250, 210))
        d.ellipse([w * 0.78, h * 0.08, w * 0.78 + 110 * SS, h * 0.08 + 110 * SS], fill=(250, 245, 215))
    elif dusk:
        _gradient(d, (250, 170, 100), (250, 220, 170), h, w)
        d.ellipse([w * 0.7, h * 0.30, w * 0.7 + 150 * SS, h * 0.30 + 150 * SS], fill=(255, 235, 170))
    else:
        _gradient(d, (120, 190, 245), (225, 245, 255), h, w)
        d.ellipse([w * 0.8, h * 0.07, w * 0.8 + 110 * SS, h * 0.07 + 110 * SS], fill=(255, 225, 90))
        for _ in range(3):
            cx, cy = rnd.randrange(w), rnd.randrange(int(h * 0.05), int(h * 0.3))
            for dx in (-60, 0, 60):
                d.ellipse([cx + dx * SS - 70 * SS, cy - 30 * SS, cx + dx * SS + 70 * SS, cy + 40 * SS], fill=(255, 255, 255))

    # ground
    ground_top = int(h * 0.62)
    if space:
        ground, far = (150, 150, 165), (110, 110, 130)
    elif sea:
        ground, far = (60, 130, 200), (230, 205, 150)
    elif snow:
        ground, far = (240, 245, 250), (215, 225, 240)
    else:
        ground, far = (110, 190, 100), (85, 165, 90)
    d.ellipse([-w * 0.2, ground_top - 90 * SS, w * 0.7, h * 1.4], fill=far)
    d.ellipse([w * 0.3, ground_top - 50 * SS, w * 1.3, h * 1.5], fill=ground)
    d.rectangle([0, ground_top + 60 * SS, w, h], fill=ground)

    if sea:
        for i in range(8):
            y = ground_top + 90 * SS + i * 34 * SS
            for x in range(0, w, 120 * SS):
                d.arc([x + (i % 2) * 60 * SS, y, x + 100 * SS + (i % 2) * 60 * SS, y + 30 * SS], 200, 340,
                      fill=(220, 240, 255), width=4 * SS)
    elif trees and not space:
        for tx in (0.08, 0.2, 0.9):
            x, base = int(w * tx), ground_top + 40 * SS
            d.rectangle([x - 14 * SS, base - 90 * SS, x + 14 * SS, base + 30 * SS], fill=(120, 80, 50))
            d.ellipse([x - 75 * SS, base - 220 * SS, x + 75 * SS, base - 40 * SS], fill=(45, 140, 75))

    # the child: position varies a little per page
    cx = int(w * (0.32 + 0.12 * (page.page_number % 4)))
    base_y = int(h * 0.88)
    skin = (240, 200, 165)
    hair = _pick(character.hair, (90, 60, 40))
    shirt = _pick(character.clothing, (239, 111, 108))
    legs = _pick(character.colors, (70, 100, 170))
    u = SS * 5
    d.rectangle([cx - 11 * u, base_y - 26 * u, cx - 2 * u, base_y], fill=legs)
    d.rectangle([cx + 2 * u, base_y - 26 * u, cx + 11 * u, base_y], fill=legs)
    d.rounded_rectangle([cx - 16 * u, base_y - 62 * u, cx + 16 * u, base_y - 22 * u], radius=6 * u, fill=shirt)
    arm_up = page.page_number % 2 == 1
    for sign in (-1, 1):
        x0 = cx + sign * 16 * u
        y_hand = base_y - (84 if arm_up else 36) * u
        d.line([(x0, base_y - 55 * u), (x0 + sign * 12 * u, y_hand)], fill=skin, width=7 * u)
        d.ellipse([x0 + sign * 12 * u - 4 * u, y_hand - 4 * u, x0 + sign * 12 * u + 4 * u, y_hand + 4 * u], fill=skin)
    hy = base_y - 82 * u
    d.ellipse([cx - 20 * u, hy - 20 * u, cx + 20 * u, hy + 20 * u], fill=hair)
    d.ellipse([cx - 17 * u, hy - 14 * u, cx + 17 * u, hy + 20 * u], fill=skin)
    for sign in (-1, 1):
        d.ellipse([cx + sign * 7 * u - 2 * u, hy - 2 * u, cx + sign * 7 * u + 2 * u, hy + 2 * u], fill=(40, 40, 50))
    d.arc([cx - 8 * u, hy + 2 * u, cx + 8 * u, hy + 13 * u], 20, 160, fill=(170, 60, 70), width=2 * u)

    # a companion, if the story has one
    if character.companions:
        pet = _pick(character.companions[0].description, (200, 150, 90))
        px, py = cx + 38 * u, base_y - 10 * u
        d.ellipse([px - 16 * u, py - 12 * u, px + 16 * u, py + 12 * u], fill=pet)
        d.ellipse([px + 8 * u, py - 22 * u, px + 26 * u, py - 4 * u], fill=pet)
        d.polygon([(px + 10 * u, py - 20 * u), (px + 13 * u, py - 30 * u), (px + 17 * u, py - 21 * u)], fill=pet)
        d.ellipse([px + 17 * u, py - 15 * u, px + 20 * u, py - 12 * u], fill=(30, 30, 30))

    img = img.resize((W, H), Image.LANCZOS)
    tag = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=20)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    tag.text((W - 250, H - 34), "offline sketch (not AI)", fill=(60, 60, 70), font=font)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
