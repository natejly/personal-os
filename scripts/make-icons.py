"""Generate the Grain app icons from the rice-grain logo shape.

The master artwork is build/grain.svg (same geometry as src/renderer/src/components/GrainLogo.tsx).
This script redraws that shape with Pillow because nothing in the toolchain rasterizes SVG:

  build/icon.png       1024x1024 app icon (electron-builder finds it in buildResources)
  stdout               base64 template PNGs (16px + 32px) to paste into src/main/tray.ts

Run: backend/.venv/bin/python scripts/make-icons.py
"""
import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

FILL = (233, 196, 106, 255)      # #e9c46a golden grain
OUTLINE = (122, 82, 48, 255)     # #7a5230 husk outline
CREASE = (138, 90, 43, 170)      # lengthwise groove
HILITE = (255, 246, 224, 235)    # #fff6e0 sheen
BG = (247, 239, 220, 255)        # warm cream icon plate


def _bezier(p0, p1, p2, p3, n=80):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        pts.append((mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
                    mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]))
    return pts


def grain(size: int, color=None, detail=True) -> Image.Image:
    """The grain alone, tilted 35°, centered on a transparent square canvas."""
    ss = size * 4  # supersample, downscaled at the end for smooth edges
    rx, ry = int(ss * 0.23), int(ss * 0.41)
    pad = int(ss * 0.09)
    layer = Image.new("RGBA", (2 * (ry + pad), 2 * (ry + pad)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = cy = ry + pad
    ow = max(2, ss // 28) if detail else 0
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color or FILL,
              outline=OUTLINE if detail else None, width=ow)
    if detail:
        # Crease: the lengthwise groove, tip to tip, bowing slightly left of the long axis.
        # Same cubic as GrainLogo.tsx (M12 4.6 C10.3 7.4 10.3 16.6 12 19.4) scaled to rx/ry.
        cw = max(2, ss // 40)
        d.line(_bezier((cx, cy - 0.86 * ry), (cx - 0.354 * rx, cy - 0.535 * ry),
                       (cx - 0.354 * rx, cy + 0.535 * ry), (cx, cy + 0.86 * ry)),
               fill=CREASE, width=cw, joint="curve")
        # Sheen: a short arc hugging the upper-right shoulder.
        inset = int(rx * 0.30)
        d.arc([cx - rx + inset, cy - ry + inset, cx + rx - inset, cy + ry - inset],
              start=-80, end=-15, fill=HILITE, width=max(2, ss // 32))
    layer = layer.rotate(-35, resample=Image.BICUBIC, expand=True)
    layer.thumbnail((size, size), Image.LANCZOS)
    return layer


def app_icon() -> None:
    s = 1024
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = int(s * 0.06)  # macOS icons leave a transparent margin around the plate
    d.rounded_rectangle([m, m, s - m, s - m], radius=int(s * 0.20), fill=BG,
                        outline=(122, 82, 48, 60), width=6)
    g = grain(int(s * 0.94))
    img.alpha_composite(g, ((s - g.width) // 2, (s - g.height) // 2))
    out = ROOT / "build" / "icon.png"
    out.parent.mkdir(exist_ok=True)
    img.save(out)
    print(f"wrote {out}")


def tray_b64(size: int) -> str:
    """Alpha-only black silhouette; macOS tints template images itself."""
    g = grain(size, color=(0, 0, 0, 255), detail=False)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.alpha_composite(g, ((size - g.width) // 2, (size - g.height) // 2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


if __name__ == "__main__":
    app_icon()
    print("\nICON_1X (16px):\ndata:image/png;base64," + tray_b64(16))
    print("\nICON_2X (32px):\ndata:image/png;base64," + tray_b64(32))
