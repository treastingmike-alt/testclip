"""Platform logos as PNG assets, generated once from vector paths.

A handle overlay used to be text in a brand colour, which reads as "someone
typed their name" rather than "follow me here". The logo is the part a viewer
actually recognises while scrolling, so it has to be in the burned-in frame, not
only in the editor's chrome.

PNGs are rendered at 512px on first use and cached on disk. ffmpeg composites
them as real inputs -- drawtext cannot draw an image, so the logo is an overlay
filter and the handle text is a drawtext clause pinned beside it.
"""

import os

LOGO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logos"))

RENDER_PX = 512

# Marks are drawn in FULL BRAND COLOUR, not tinted to the text colour.
#
# A flat white Instagram glyph on a solid magenta block does not read as
# Instagram -- the gradient IS the logo. Recognition happens in the fraction of a
# second someone spends scrolling, so the mark has to look like the real one.
# `svg` is a complete document (gradients and all); `accent` is the colour used
# for the pill's edge.
_BRAND_SVG = {
    "instagram": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<defs><radialGradient id="g" cx="0.3" cy="1.05" r="1.3">
<stop offset="0" stop-color="#FFD776"/><stop offset="0.25" stop-color="#F3A145"/>
<stop offset="0.45" stop-color="#EF4E5E"/><stop offset="0.65" stop-color="#D53F94"/>
<stop offset="1" stop-color="#7638FA"/></radialGradient></defs>
<rect x="1.6" y="1.6" width="20.8" height="20.8" rx="6.2" fill="url(#g)"/>
<rect x="5.1" y="5.1" width="13.8" height="13.8" rx="4.3" fill="none" stroke="#fff" stroke-width="1.9"/>
<circle cx="12" cy="12" r="3.7" fill="none" stroke="#fff" stroke-width="1.9"/>
<circle cx="17.1" cy="6.9" r="1.25" fill="#fff"/></svg>""",

    "x": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect x="1.6" y="1.6" width="20.8" height="20.8" rx="6.2" fill="#000"/>
<path transform="translate(4.4 4.4) scale(0.635)" fill="#fff" d="M18.9 2H22l-7 8 8.2 12h-6.4l-5-7.3L5.9 22H2.8l7.5-8.6L2.4 2h6.6l4.5 6.6L18.9 2Zm-1.1 18.2h1.7L7.3 3.7H5.5l12.3 16.5Z"/></svg>""",

    "tiktok": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect x="1.6" y="1.6" width="20.8" height="20.8" rx="6.2" fill="#000"/>
<g transform="translate(4.4 4.4) scale(0.635)">
<path fill="#25F4EE" d="M15.1 5.1a5 5 0 0 1-1.2-3.3h-3.3v13.2a2.9 2.9 0 1 1-2.1-2.8V8.7a6.2 6.2 0 1 0 5.4 6.1V8.4a8.2 8.2 0 0 0 4.8 1.5V6.6a4.9 4.9 0 0 1-3.6-1.5Z"/>
<path fill="#FE2C55" d="M17.6 6.5a5 5 0 0 1-1.2-3.3h-3.3v13.2a2.9 2.9 0 1 1-2.1-2.8v-3.5a6.2 6.2 0 1 0 5.4 6.1V9.8a8.2 8.2 0 0 0 4.8 1.5V8a4.9 4.9 0 0 1-3.6-1.5Z"/>
<path fill="#fff" d="M16.4 5.8a5 5 0 0 1-1.2-3.3h-3.3v13.2a2.9 2.9 0 1 1-2.1-2.8V9.4a6.2 6.2 0 1 0 5.4 6.1V9.1a8.2 8.2 0 0 0 4.8 1.5V7.3a4.9 4.9 0 0 1-3.6-1.5Z"/></g></svg>""",

    "youtube": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect x="1.6" y="4.6" width="20.8" height="14.8" rx="4.2" fill="#FF0033"/>
<path fill="#fff" d="M10.1 8.6v6.8l5.9-3.4-5.9-3.4Z"/></svg>""",

    "twitch": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect x="1.6" y="1.6" width="20.8" height="20.8" rx="6.2" fill="#9146FF"/>
<g transform="translate(4.6 4.6) scale(0.62)" fill="#fff">
<path d="M4.3 2 2.5 6.5v14h5v3h3l3-3h4L23 17V2H4.3Zm16.4 14L18 18.8h-4.5l-3 3v-3H6.7V4h14v12Z"/>
<path d="M17 7.8v5.3h-2V7.8h2Zm-5.2 0v5.3h-2V7.8h2Z"/></g></svg>""",

    "kick": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect x="1.6" y="1.6" width="20.8" height="20.8" rx="6.2" fill="#53FC18"/>
<path transform="translate(4.6 4.6) scale(0.62)" fill="#000" d="M3 2h5v6h2V5h2V2h7v7h-2v3h-2v2h2v3h2v7h-7v-3h-2v-3H8v6H3V2Z"/></svg>""",
}

_PATHS = {
    "instagram": "M12 2.2c3.2 0 3.6 0 4.9.07 1.2.05 1.8.25 2.2.42.6.22 1 .48 1.4.9.43.42.7.82.9 1.4.18.4.37 1 .43 2.2.06 1.3.07 1.7.07 4.9s0 3.6-.07 4.9c-.06 1.2-.25 1.8-.42 2.2a3.8 3.8 0 0 1-.9 1.4c-.43.43-.83.7-1.4.9-.4.18-1 .37-2.2.43-1.3.06-1.7.07-4.9.07s-3.6 0-4.9-.07c-1.2-.06-1.8-.25-2.2-.42a3.8 3.8 0 0 1-1.4-.9 3.8 3.8 0 0 1-.9-1.4c-.18-.4-.37-1-.43-2.2C2.2 15.6 2.2 15.2 2.2 12s0-3.6.07-4.9c.06-1.2.25-1.8.42-2.2.22-.6.48-1 .9-1.4.42-.43.82-.7 1.4-.9.4-.18 1-.37 2.2-.43C8.4 2.2 8.8 2.2 12 2.2Zm0 3.24a6.56 6.56 0 1 0 0 13.12 6.56 6.56 0 0 0 0-13.12Zm0 10.82a4.26 4.26 0 1 1 0-8.52 4.26 4.26 0 0 1 0 8.52Zm8.35-11.08a1.53 1.53 0 1 1-3.06 0 1.53 1.53 0 0 1 3.06 0Z",
    "x": "M18.9 2H22l-7 8 8.2 12h-6.4l-5-7.3L5.9 22H2.8l7.5-8.6L2.4 2h6.6l4.5 6.6L18.9 2Zm-1.1 18.2h1.7L7.3 3.7H5.5l12.3 16.5Z",
    "tiktok": "M16.6 5.8a5 5 0 0 1-1.2-3.3h-3.3v13.2a2.9 2.9 0 1 1-2.1-2.8V9.4a6.2 6.2 0 1 0 5.4 6.1V9.1a8.2 8.2 0 0 0 4.8 1.5V7.3a4.9 4.9 0 0 1-3.6-1.5Z",
    "youtube": "M23 7.5s-.2-1.6-.9-2.3c-.9-.9-1.8-.9-2.3-1C17 4 12 4 12 4s-5 0-7.8.2c-.4.1-1.4.1-2.3 1C1.2 5.9 1 7.5 1 7.5S.8 9.4.8 11.2v1.6c0 1.9.2 3.7.2 3.7s.2 1.6.9 2.3c.9.9 2 .9 2.5 1 1.8.2 7.6.2 7.6.2s5 0 7.8-.2c.5-.1 1.4-.1 2.3-1 .7-.7.9-2.3.9-2.3s.2-1.9.2-3.7v-1.6c0-1.9-.2-3.7-.2-3.7ZM9.8 15V9l6.4 3-6.4 3Z",
    "twitch": "M4.3 2 2.5 6.5v14h5v3h3l3-3h4L23 17V2H4.3Zm16.4 14L18 18.8h-4.5l-3 3v-3H6.7V4h14v12ZM17 7.8v5.3h-2V7.8h2Zm-5.2 0v5.3h-2V7.8h2Z",
    "kick": "M3 2h5v6h2V5h2V2h7v7h-2v3h-2v2h2v3h2v7h-7v-3h-2v-3H8v6H3V2Z",
}

# `accent` edges the pill; the pill itself stays a soft dark glass so the mark
# and the handle carry the colour instead of a slab of brand paint.
PLATFORMS = {
    "instagram": {"name": "Instagram", "prefix": "@", "accent": "#E1306C"},
    "x":         {"name": "X",         "prefix": "@", "accent": "#8A9099"},
    "tiktok":    {"name": "TikTok",    "prefix": "@", "accent": "#25F4EE"},
    "youtube":   {"name": "YouTube",   "prefix": "@", "accent": "#FF0033"},
    "twitch":    {"name": "Twitch",    "prefix": "",  "accent": "#9146FF"},
    "kick":      {"name": "Kick",      "prefix": "",  "accent": "#53FC18"},
}


def brand_logo(platform: str) -> str:
    """Cached PNG of the full-colour brand mark."""
    svg = _BRAND_SVG.get(platform)
    if not svg:
        return None
    out = os.path.join(LOGO_DIR, f"{platform}_brand.png")
    if os.path.exists(out):
        return out
    try:
        import cairosvg
        os.makedirs(LOGO_DIR, exist_ok=True)
        cairosvg.svg2png(bytestring=svg.encode(), write_to=out,
                         output_width=RENDER_PX, output_height=RENDER_PX)
    except Exception:
        return None
    return out if os.path.exists(out) else None


def logo_path(platform: str, colour: str = "#ffffff") -> str:
    """PNG for a platform mark in `colour`, rendering it if not already cached.

    Returns None when the logo cannot be produced (no cairosvg installed, an
    unknown platform) -- callers fall back to a text-only handle rather than
    failing the export, because a missing logo is not worth losing a render to.
    """
    path = _PATHS.get(platform)
    if not path:
        return None

    safe = (colour or "#ffffff").lstrip("#").lower()
    out = os.path.join(LOGO_DIR, f"{platform}_{safe}.png")
    if os.path.exists(out):
        return out

    try:
        import cairosvg
    except ImportError:
        return None

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path d="{path}" fill="#{safe}"/></svg>').encode()
    try:
        os.makedirs(LOGO_DIR, exist_ok=True)
        cairosvg.svg2png(bytestring=svg, write_to=out,
                         output_width=RENDER_PX, output_height=RENDER_PX)
    except Exception:
        return None
    return out if os.path.exists(out) else None


def _hex_rgba(colour: str, alpha: float = 1.0) -> tuple:
    c = (colour or "#ffffff").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16),
            int(max(0.0, min(1.0, alpha)) * 255))


def compose_handle(overlay: dict, frame_h: int, work_dir: str) -> str:
    """Draws logo + handle text into ONE transparent PNG, returning its path.

    Composing the pair here rather than as two ffmpeg overlays solves the two
    things that made the split version wrong: the text's plate is drawn from the
    MEASURED text box (no guessing an advance width, and no mixing a
    height-fraction with a width-fraction), and the mark is painted after the
    plate so it sits on top of it instead of behind.
    """
    from PIL import Image, ImageDraw, ImageFont

    platform = overlay.get("platform")
    text = (overlay.get("text") or "").strip()
    if not platform or platform not in PLATFORMS or not text:
        return None

    accent = PLATFORMS[platform]["accent"]
    ink = overlay.get("color") or "#ffffff"
    png = brand_logo(platform)
    if not png:
        return None

    # Work in real output pixels so the result is crisp at final resolution.
    size_px = max(12, int(round(frame_h * float(overlay.get("size") or 0.038))))

    from app.pipeline.subtitles import font_entry, FONTS_DIR
    entry = font_entry(overlay.get("font") or "poppins")
    try:
        font = ImageFont.truetype(
            os.path.join(FONTS_DIR, entry["file"]), size_px) if entry and entry.get("file") \
            else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), text, font=font)
    text_w, text_h = box[2] - box[0], box[3] - box[1]

    logo_px = int(size_px * 1.12)
    gap = int(size_px * 0.34)
    pad_x = int(size_px * 0.40)
    pad_y = int(size_px * 0.30)
    border = max(2, int(size_px * 0.055))

    # Supersample the whole pill, then downscale. PIL has no antialiasing on
    # rounded_rectangle, so drawing at final size leaves visibly stepped edges on
    # the very shape that is supposed to look soft.
    SS = 4
    w = pad_x * 2 + logo_px + gap + text_w
    h = pad_y * 2 + max(logo_px, text_h)

    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    bd = ImageDraw.Draw(big)
    radius = (h * SS) // 2                      # fully rounded: a pill, not a box

    pill = overlay.get("pill", True)
    if pill:
        # Soft dark glass rather than a slab of brand colour: the mark and the
        # accent edge carry the identity, and white text stays readable over any
        # footage underneath.
        bd.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=radius,
                             fill=(18, 18, 22, 214),
                             outline=_hex_rgba(accent, 1.0), width=border * SS)

    img = big.resize((w, h), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    mark = Image.open(png).convert("RGBA").resize((logo_px, logo_px), Image.LANCZOS)
    img.alpha_composite(mark, (pad_x, (h - logo_px) // 2))

    draw.text((pad_x + logo_px + gap - box[0], (h - text_h) // 2 - box[1]),
              text, font=font, fill=_hex_rgba(ink))

    key = abs(hash((text, size_px, ink, platform, pill)))
    out = os.path.join(work_dir, f"_handle_{platform}_{key}.png")
    img.save(out)
    return out


def expand_handle(overlay: dict, frame_w: int = 1080, frame_h: int = 1920,
                  work_dir: str = None) -> list:
    """Turns a platform handle overlay into a single composed image overlay.

    The editor still authors ONE draggable handle; only the render knows it is
    really a mark plus text. Falls back to the plain text overlay whenever the
    image cannot be built, because a missing logo is not worth losing an export.
    """
    if not overlay.get("platform") or not work_dir:
        return [overlay]
    try:
        path = compose_handle(overlay, frame_h, work_dir)
    except Exception:
        return [overlay]
    if not path:
        return [overlay]

    from PIL import Image
    w, _ = Image.open(path).size
    # {**overlay} carries t_start/t_end through, so the composed image inherits
    # the handle's timing rather than showing for the whole clip.
    return [{**overlay, "type": "image", "path": path,
             # Image overlays are sized by WIDTH as a fraction of frame height,
             # which is how overlays.build scales them.
             "size": w / float(frame_h),
             "plate": None}]
