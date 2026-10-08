"""
Estilo del overlay (sin Qt: se puede probar en cualquier sitio).

Se guarda en config.json bajo la clave "style". Los presets son paquetes de
campos; el usuario puede ajustar cada uno desde el panel de ajustes (🎨).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class OverlayStyle:
    preset: str = "gamer"
    font: str = "Segoe UI"
    orig_color: str = "#e2e8f0"     # texto original
    trans_color: str = "#ffe066"    # traducción
    tag_color: str = "#7ea8f8"      # etiqueta de idioma [EN]
    outline: int = 2                # grosor del contorno en px (0 = sin contorno)
    outline_color: str = "#000000"
    shadow: bool = True
    bold: bool = True               # negrita en la traducción
    italic: bool = False
    align: str = "left"             # left | center | right
    max_lines: int = 4              # líneas visibles en modo subtítulo
    orig_scale: float = 0.85        # tamaño del original respecto a la traducción
    line_spacing: float = 1.12
    show_tag: bool = True           # mostrar [EN]
    anim: bool = True               # entrada con fundido


# Cada preset sobreescribe solo lo que menciona.
PRESETS: dict[str, dict] = {
    "gamer": dict(label="🎮 Gamer", trans_color="#ffe066", orig_color="#e2e8f0",
                  outline=2, bold=True, align="left", max_lines=4),
    "classic": dict(label="💬 Clásico", trans_color="#7dffb2", orig_color="#c8d0dc",
                    outline=1, bold=True, align="left", max_lines=6),
    "cine": dict(label="🎬 Cine", trans_color="#ffffff", orig_color="#d0d0d0",
                 outline=3, bold=False, align="center", max_lines=2, orig_scale=0.7,
                 show_tag=False),
    "minimal": dict(label="▫️ Minimal", trans_color="#f2f2f2", orig_color="#9aa4b2",
                    outline=1, bold=False, align="center", max_lines=2,
                    orig_scale=0.75, show_tag=False, shadow=False),
    "neon": dict(label="💡 Neón", trans_color="#ff7ae6", orig_color="#7df9ff",
                 outline=3, outline_color="#3b0a45", bold=True, align="center",
                 max_lines=3, tag_color="#7df9ff"),
    "terminal": dict(label="⌨️ Terminal", font="Consolas", trans_color="#39ff6a",
                     orig_color="#2fbf55", outline=1, outline_color="#021a08",
                     bold=False, align="left", max_lines=5, show_tag=True),
}
LIMITS = {"outline": (0, 8), "max_lines": (1, 10), "orig_scale": (0.5, 1.2),
          "line_spacing": (1.0, 1.6)}
ALIGNS = ("left", "center", "right")
COLOR_FIELDS = ("orig_color", "trans_color", "tag_color", "outline_color")


def apply_preset(name: str) -> OverlayStyle:
    base = OverlayStyle()
    p = {k: v for k, v in PRESETS.get(name, {}).items() if k != "label"}
    return replace(base, preset=name if name in PRESETS else "gamer", **p)


def from_dict(d: dict | None, default_preset: str = "gamer") -> OverlayStyle:
    """Lee el estilo de config.json validando todo (un config roto no tumba la app)."""
    st = apply_preset(default_preset)
    if not isinstance(d, dict):
        return st
    valid = {f.name: f.type for f in fields(OverlayStyle)}
    for k, v in d.items():
        if k not in valid:
            continue
        try:
            if k in COLOR_FIELDS:
                if isinstance(v, str) and _HEX.match(v):
                    setattr(st, k, v)
            elif k == "align":
                if v in ALIGNS:
                    setattr(st, k, v)
            elif k in ("font", "preset"):
                if isinstance(v, str) and v.strip():
                    setattr(st, k, v.strip()[:60])
            elif k in ("shadow", "bold", "italic", "show_tag", "anim"):
                setattr(st, k, bool(v))
            elif k in LIMITS:
                lo, hi = LIMITS[k]
                num = int(v) if k in ("outline", "max_lines") else float(v)
                setattr(st, k, max(lo, min(hi, num)))
        except (TypeError, ValueError):
            continue
    return st


def to_dict(st: OverlayStyle) -> dict:
    return asdict(st)


def set_field(st: OverlayStyle, key: str, value) -> OverlayStyle:
    """Cambia un campo validándolo; el preset pasa a 'custom'."""
    return from_dict({**to_dict(st), key: value, "preset": "custom"}, default_preset="gamer") \
        if key in to_dict(st) else st
