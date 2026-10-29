"""
Estilo de subtítulos y datos compartidos (sin ffmpeg ni red).

Aquí viven los catálogos que el bot muestra como botones y el dataclass
SubtitleStyle con todas las opciones que el usuario puede personalizar.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

LANGUAGES = {
    "es": "Español", "en": "English", "pt": "Português", "fr": "Français",
    "de": "Deutsch", "it": "Italiano", "ja": "日本語", "ko": "한국어",
    "zh-cn": "中文", "ru": "Русский",
    "orig": "📝 Solo transcribir",   # subtítulos en el idioma original
}

# Colores ASS: &HAABBGGRR
COLORS = {
    "white": ("Blanco", "&H00FFFFFF"), "yellow": ("Amarillo", "&H0000E0FF"),
    "cyan": ("Cian", "&H00FFFF00"), "green": ("Verde", "&H0000FF40"),
    "orange": ("Naranja", "&H000080FF"), "pink": ("Rosa", "&H00C080FF"),
    "red": ("Rojo", "&H000000FF"), "blue": ("Azul", "&H00FF3000"),
    "purple": ("Morado", "&H00A00080"), "black": ("Negro", "&H00000000"),
    "gold": ("Dorado", "&H0000C4FF"), "lime": ("Lima", "&H0000FFA6"),
    "sky": ("Celeste", "&H00F7C34F"), "magenta": ("Magenta", "&H00D62BFF"),
    "coral": ("Coral", "&H006B6BFF"), "cream": ("Crema", "&H00D6F3FF"),
    "gray": ("Gris", "&H00B0B0B0"),
}
# (etiqueta, nombre de fuente instalada). Si falta, fontconfig usa otra similar.
FONTS = {
    "sans": ("Sans", "Liberation Sans"), "serif": ("Serif", "Liberation Serif"),
    "mono": ("Mono", "DejaVu Sans Mono"), "roboto": ("Roboto", "Roboto"),
    "impact": ("Impacto", "Anton"),
    # --- instaladas por deploy/fonts.sh (lista en deploy/fonts.list) ---
    "bebas": ("Bebas Neue", "Bebas Neue"), "bangers": ("Bangers", "Bangers"),
    "poppins": ("Poppins Black", "Poppins ExtraBold"), "archivo": ("Archivo Black", "Archivo Black"),
    "marker": ("Marcador", "Permanent Marker"), "luckiest": ("Luckiest", "Luckiest Guy"),
    "russo": ("Russo", "Russo One"), "barlow": ("Barlow Cond.", "Barlow Condensed ExtraBold"),
    "staat": ("Staatliches", "Staatliches"), "passion": ("Passion", "Passion One"),
    "titan": ("Titan One", "Titan One"), "bungee": ("Bungee", "Bungee"),
    "alfa": ("Alfa Slab", "Alfa Slab One"), "abril": ("Abril Fatface", "Abril Fatface"),
    "shrikhand": ("Shrikhand", "Shrikhand"), "righteous": ("Righteous", "Righteous"),
    "audiowide": ("Audiowide", "Audiowide"), "blackops": ("Black Ops", "Black Ops One"),
    "rubikmono": ("Rubik Mono", "Rubik Mono One"), "knewave": ("Knewave", "Knewave"),
    "lobster": ("Lobster", "Lobster"), "pacifico": ("Pacifico", "Pacifico"),
    "kalam": ("Manuscrita", "Kalam"), "typewriter": ("Máq. escribir", "Special Elite"),
    "pixel": ("Pixel", "Press Start 2P"), "ubuntu": ("Ubuntu", "Ubuntu"),
}
# Fuentes de un solo peso: si se pidiera negrita, libass la fabricaría deformando las letras.
SINGLE_WEIGHT = {"impact", "bebas", "bangers", "poppins", "archivo", "marker", "luckiest", "russo", "barlow",
                 "staat", "titan", "bungee", "alfa", "abril", "shrikhand", "righteous", "audiowide", "blackops",
                 "rubikmono", "knewave", "lobster", "pacifico", "typewriter", "pixel"}
FONTS_PER_PAGE = 12
OUTLINES = {"none": ("Sin contorno", 0.0), "thin": ("Fino", 0.04),
            "med": ("Medio", 0.07), "thick": ("Grueso", 0.11),
            "xthick": ("Extra", 0.15)}
BGS = {"none": ("Sin fondo", None), "soft": ("Caja suave", "&H70"),
       "solid": ("Caja sólida", "&H00")}
SIZES = {"xs": ("XS", 0.032), "s": ("S", 0.042), "m": ("M", 0.055),
         "l": ("L", 0.072), "xl": ("XL", 0.095)}
VPOS = ("top", "upper", "middle", "lower", "bottom")        # 5 filas
HPOS = ("left", "leftmid", "center", "rightmid", "right")   # 5 columnas
ALIGN = {(v, h): None for v in VPOS for h in HPOS}          # posiciones válidas

# ---- estilo "CapCut / Captions" ----
ANIMS = {"none": "Ninguna", "fade": "Fundido", "pop": "Pop",
         "bounce": "Rebote", "typewriter": "Máquina de escribir"}
HIGHLIGHTS = {"none": "Sin resaltar", "color": "Palabra en color",
              "pop": "Palabra en color + pop", "fill": "Relleno karaoke"}
WORDS = {0: "Frase completa", 1: "1 palabra", 2: "2 palabras",
         3: "3 palabras", 5: "5 palabras"}
SPACINGS = {"tight": ("Junto", -0.02), "normal": ("Normal", 0.0),
            "wide": ("Amplio", 0.08)}
PROGRESS = {"off": "Sin barra", "top": "Barra arriba", "bottom": "Barra abajo"}

# ---- traducción ----
TONES = {
    "natural": ("Natural", "natural, neutral and faithful to the speaker"),
    "formal": ("Formal", "formal and polite, professional register"),
    "casual": ("Casual", "casual, colloquial, like friends talking; contractions and everyday slang"),
    "gamer": ("Gamer", "gaming/streaming slang; keep terms like gg, clutch, nerf, gank, ult untranslated when natural"),
    "technical": ("Técnico", "precise technical register; keep technical terms and units exact"),
    "funny": ("Humor", "witty and playful, keeping jokes and punchlines landing in the target language"),
}

# ---- doblaje ----
DUBS = {"off": "Sin doblaje", "female": "Voz mujer", "male": "Voz hombre"}
ORIG_VOLS = {"keep": ("Mantener", 1.0), "low": ("Bajo (30%)", 0.3),
             "mute": ("Silenciar", 0.0)}


@dataclass
class SubtitleStyle:
    """Todas las opciones que el usuario puede elegir desde Telegram."""
    # --- aspecto ---
    font: str = "sans"            # clave de FONTS
    color: str = "white"          # clave de COLORS
    outline: str = "med"          # none | thin | med | thick | xthick
    outline_color: str = "black"  # clave de COLORS (también es el color de la caja)
    bg: str = "none"              # none | soft | solid  (caja tras el texto)
    shadow: bool = True
    bold: bool = True
    italic: bool = False
    upper: bool = False
    align_h: str = "center"       # left | leftmid | center | rightmid | right
    align_v: str = "bottom"       # top | upper | middle | lower | bottom
    size: str = "m"               # xs | s | m | l | xl  (relativo al alto)
    spacing: str = "normal"       # tight | normal | wide
    neon: bool = False            # resplandor
    # --- animación / karaoke ---
    anim: str = "none"            # none | fade | pop | bounce | typewriter
    highlight: str = "none"       # none | color | pop | fill
    hl_color: str = "yellow"      # color de la palabra activa y de la barra
    words: int = 0                # palabras por pantalla (0 = frase completa)
    keywords: bool = False        # resalta palabras clave automáticamente
    kw_color: str = "orange"
    progress: str = "off"         # off | top | bottom
    # --- traducción ---
    target: str = "es"            # clave de LANGUAGES ("orig" = solo transcribir)
    bilingual: bool = False       # texto original, más chico, bajo la traducción
    tone: str = "natural"         # clave de TONES (requiere Groq)
    censor: bool = False          # tapa groserías con ***
    # --- doblaje ---
    dub: str = "off"              # off | female | male
    orig_vol: str = "low"         # volumen del audio original al doblar
    preset: str = "classic"       # solo informativo (último preset aplicado)


# Campos que forman parte del "look" (los pisa un preset / plantilla)
LOOK_FIELDS = ("font", "color", "outline", "outline_color", "bg", "shadow",
               "bold", "italic", "upper", "spacing", "neon", "anim",
               "highlight", "hl_color", "words", "keywords", "kw_color",
               "progress")


def _p(label, **kw):
    return dict(label=label, **kw)


# Estilos listos: cada uno es un paquete de campos "look" de SubtitleStyle.
PRESETS = {
    "classic": _p("Clásico", color="white", outline="med"),
    "yellow": _p("Cine amarillo", color="yellow", outline="med"),
    "box": _p("Caja oscura", font="roboto", bg="soft", shadow=False, bold=False),
    "gamer": _p("Gamer neón", font="impact", color="cyan", outline="thick",
                outline_color="purple", bold=False, upper=True),
    "comic": _p("Cómic", font="impact", color="yellow", outline="xthick",
                bold=False, upper=True),
    "elegant": _p("Elegante", font="serif", outline="thin", bold=False,
                  italic=True),
    "retro": _p("Retro terminal", font="mono", color="green", bg="solid",
                shadow=False),
    "minimal": _p("Minimal", outline="thin", shadow=False, bold=False),
    # --- estilo redes sociales (palabra por palabra) ---
    "hormozi": _p("🔥 Hormozi", font="impact", color="white", outline="xthick",
                  bold=False, upper=True, highlight="color", hl_color="green",
                  words=3, anim="pop"),
    "beast": _p("⚡ Beast", font="impact", color="white", outline="xthick",
                bold=False, upper=True, highlight="pop", hl_color="yellow",
                words=2, anim="bounce"),
    "karaoke": _p("🎤 Karaoke", font="roboto", color="white", outline="thick",
                  highlight="fill", hl_color="cyan", words=5),
    "typing": _p("⌨️ Tecleo", font="mono", color="green", bg="solid",
                 shadow=False, anim="typewriter", words=5),
    "glow": _p("💡 Neón", font="roboto", color="pink", outline="thin",
               outline_color="pink", neon=True, shadow=False, spacing="wide",
               anim="fade"),
    # --- más estilos (necesitan las fuentes de deploy/fonts.sh) ---
    "tiktok": _p("🎵 TikTok", font="poppins", color="white", outline="thick", bold=False,
                 highlight="color", hl_color="magenta", words=3, anim="pop"),
    "podcast": _p("🎙 Podcast", font="barlow", color="white", bg="soft", bold=False, upper=True,
                  shadow=False, highlight="color", hl_color="gold", words=5),
    "arcade": _p("🕹 Arcade", font="pixel", color="yellow", outline="thick", outline_color="red",
                 bold=False, shadow=False, words=3, anim="bounce"),
    "graffiti": _p("🖌 Grafiti", font="marker", color="lime", outline="xthick", bold=False,
                   words=3, anim="pop"),
    "headline": _p("📰 Titular", font="bebas", color="white", outline="med", bold=False, upper=True,
                   spacing="wide"),
    "manga": _p("💢 Manga", font="bangers", color="yellow", outline="xthick", bold=False, upper=True,
                highlight="pop", hl_color="coral", words=3, anim="pop"),
    "luxury": _p("💎 Lujo", font="abril", color="gold", outline="thin", bold=False, spacing="wide",
                 anim="fade"),
    "script": _p("✍️ Manuscrita", font="pacifico", color="white", outline="med", outline_color="purple",
                 bold=False, anim="fade"),
    "oldtype": _p("📜 Máquina antigua", font="typewriter", color="cream", bg="soft", bold=False,
                  shadow=False, anim="typewriter", words=5),
    "urban": _p("🏙 Urbano", font="bungee", color="cyan", outline="med", outline_color="blue",
                bold=False, shadow=True, words=3),
    "redslab": _p("💥 Slab rojo", font="alfa", color="white", bg="solid", outline_color="red", bold=False,
                  shadow=False, upper=True, words=3, anim="pop"),
    "tactical": _p("🪖 Táctico", font="blackops", color="lime", outline="thick", bold=False, upper=True,
                   words=3),
    "sport": _p("🏁 Deportivo", font="russo", color="white", outline="thick", bold=False, upper=True,
                highlight="fill", hl_color="orange", words=5, progress="bottom"),
    "kids": _p("🎈 Divertido", font="titan", color="yellow", outline="xthick", outline_color="blue",
               bold=False, anim="bounce", words=3),
    "fire": _p("🔥 Fuego", font="passion", color="orange", outline="thick", bold=False, upper=True,
               highlight="pop", hl_color="yellow", words=2, anim="pop"),
    "news": _p("📺 Noticiero", font="barlow", color="white", bg="solid", outline_color="blue", bold=False,
               upper=True, shadow=False, words=0),
    "cinema": _p("🎬 Cine", font="serif", color="cream", outline="thin", bold=False, shadow=True,
                 anim="fade"),
    "neonpop": _p("🌈 Neón pop", font="righteous", color="pink", outline="thin", outline_color="purple",
                  bold=False, neon=True, shadow=False, highlight="color", hl_color="sky", words=3, anim="pop"),
    "ink": _p("🖋 Tinta", font="kalam", color="white", outline="med", bold=True, anim="fade", words=5),
}
PRESETS_PER_PAGE = 12


def apply_preset(style: SubtitleStyle, name: str) -> SubtitleStyle:
    """Aplica un estilo listo conservando posición, tamaño, idioma,
    tono y doblaje. Lo que el preset no menciona vuelve al valor base."""
    base = SubtitleStyle()
    look = {f: getattr(base, f) for f in LOOK_FIELDS}
    look.update({k: v for k, v in PRESETS[name].items() if k != "label"})
    return replace(style, preset=name, **look)


def look_of(style: SubtitleStyle) -> dict:
    return {f: getattr(style, f) for f in LOOK_FIELDS}


def apply_look(style: SubtitleStyle, look: dict) -> SubtitleStyle:
    valid = {f.name for f in fields(SubtitleStyle)}
    return replace(style, preset="custom",
                   **{k: v for k, v in look.items() if k in LOOK_FIELDS and k in valid})


@dataclass
class Segment:
    start: float
    end: float
    text: str
    lang: str = "auto"
    translation: str = ""
    words: list = field(default_factory=list)   # [(palabra, inicio, fin)] originales
