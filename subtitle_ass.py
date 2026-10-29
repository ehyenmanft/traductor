"""
Generación de subtítulos .ass / .srt (sin red). Soporta el estilo "CapCut /
Captions": palabra activa resaltada, karaoke, 1-5 palabras por pantalla,
animaciones de entrada, palabras clave en color, neón y barra de progreso.
"""
from __future__ import annotations

import re

from subtitle_style import (COLORS, FONTS, SINGLE_WEIGHT, OUTLINES, BGS, SIZES, SPACINGS,
                            Segment, SubtitleStyle)

MIN_SECONDS = 0.8


def norm_lang(code: str) -> str:
    code = (code or "auto").lower()
    if code.startswith("zh"):
        return "zh-cn"
    return code.split("-")[0] if code != "auto" else code


# ---------------------------------------------------------------- utilidades

def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")") \
               .replace("\n", " ")


def _tc(color_key: str) -> str:
    """Color para etiquetas de override: \\1c&HBBGGRR&"""
    return f"&H{COLORS.get(color_key, COLORS['white'])[1][4:]}&"


def fix_timing(segs: list[Segment], duration: float) -> list[Segment]:
    """Duración mínima y sin solapamientos entre subtítulos (devuelve copias)."""
    out = sorted((Segment(s.start, s.end, s.text, s.lang, s.translation, s.words)
                  for s in segs), key=lambda s: s.start)
    for i, s in enumerate(out):
        s.end = max(s.end, s.start + MIN_SECONDS)
        if i + 1 < len(out):
            s.end = min(s.end, out[i + 1].start - 0.02)
        if duration:
            s.end = min(s.end, duration)
        s.end = max(s.end, s.start + 0.2)
    return out


# ------------------------------------------------------- censura y keywords

_PROFANITY = re.compile(
    r"\b(fuck\w*|shit\w*|bitch\w*|asshole\w*|cunt\w*|dick\w*|bastard\w*|"
    r"mierda\w*|put[ao]s?|joder\w*|co[ñn]o|cabr[oó]n\w*|carajo|pendej\w*|"
    r"verga\w*|gilipollas|hijo\s?de\s?put\w*|merde|putain|salope|scheiß\w*|"
    r"scheiss\w*|arschloch|cazzo|stronz\w*|merda|porra|caralho|filho\s?da\s?put\w*)\b",
    re.IGNORECASE)


def censor_text(text: str) -> str:
    return _PROFANITY.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def find_keywords(tokens: list[str]) -> set[int]:
    """Heurística: números, SIGLAS, exclamaciones y las palabras más largas."""
    idx: set[int] = set()
    for i, t in enumerate(tokens):
        core = re.sub(r"\W", "", t)
        if any(c.isdigit() for c in core) or (len(core) >= 3 and core.isupper()) \
                or t.endswith("!"):
            idx.add(i)
    if tokens:
        for i in sorted(range(len(tokens)), key=lambda i: -len(tokens[i]))[:max(1, len(tokens) // 5)]:
            if len(re.sub(r"\W", "", tokens[i])) >= 7:
                idx.add(i)
    return idx


# ---------------------------------------------------------------- tokens

def tokenize(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if " " in text:
        return text.split()
    # idiomas sin espacios (ja/zh): trozos de 3 caracteres
    return [text[i:i + 3] for i in range(0, len(text), 3)]


def seg_tokens(seg: Segment, text: str) -> list[tuple]:
    """[(token, inicio, fin)]. Usa los tiempos reales de Deepgram si el texto
    es el original; si es traducción, reparte el tiempo según la longitud."""
    toks = tokenize(text)
    if not toks:
        return []
    t0, t1 = seg.start, seg.end
    if seg.words and text.strip().lower() == seg.text.strip().lower() and len(seg.words) == len(toks):
        out = []
        for tok, (_, s, e) in zip(toks, seg.words):
            s = min(max(s, t0), t1)
            out.append((tok, s, min(max(e, s + 0.05), t1)))
        return out
    weights = [len(t) + 1 for t in toks]
    total, acc, out = sum(weights), 0, []
    for tok, w in zip(toks, weights):
        a = t0 + (t1 - t0) * acc / total
        acc += w
        out.append((tok, a, t0 + (t1 - t0) * acc / total))
    return out



# ------------------------------------------------- estilo «stack» (línea pequeña + clave grande)

_STOP = {
    "es": "el la los las un una unos unas de del al a en y o u e que qué se su sus por para con sin sobre entre como cómo "
          "más mas muy ya no sí si pero porque pues le les lo me te nos mi tu tus mis es son era fue ser está están esta "
          "este esto estos estas ese eso esa eso aquí ahí así también cuando donde dónde hay he ha han hemos del lo",
    "en": "the a an of to in on at for from by with and or but if so as is are was were be been it its this that these "
          "those i you he she we they me my your our their his her them us do does did not no just very more most than then "
          "there here what which who whom how when where why also can could will would should have has had",
    "pt": "o a os as um uma de do da dos das em no na nos nas por para com sem sobre entre como mais muito já não sim se mas "
          "porque pois me te se nos meu seu é são era foi ser está estão este isto esse isso essa aqui assim também quando",
    "fr": "le la les un une des de du au aux en dans sur par pour avec sans et ou mais si donc comme plus très ne pas je tu il "
          "elle nous vous ils elles me te se mon ton son est sont était être ce cet cette ces qui que quoi où",
    "de": "der die das ein eine einen einem von zu in im auf an für mit ohne und oder aber wenn so als ist sind war waren sein "
          "ich du er sie es wir ihr nicht kein sehr mehr auch noch schon dass",
    "it": "il lo la i gli le un una di del della dei delle a al alla in su per con senza e o ma se come più molto non sì io tu "
          "lui lei noi voi loro è sono era essere questo questa che chi",
}
STOPWORDS = {w for ws in _STOP.values() for w in ws.split()}
NEGATIVE = set(
    "no nunca jamás error errores quiebra quiebras pierde pierdes perder pérdida pérdidas riesgo riesgos peligro peligroso "
    "malo mala falso falsa fracaso fraude estafa cuidado never error errors lose loses lost loss losses risk risks danger "
    "bad fail fails failure fake scam fraud broke não nunca erro perda risco perigo".split())


def _core(tok: str) -> str:
    return re.sub(r"[^\w]", "", tok, flags=re.UNICODE).lower()


_CONNECT = set("de del of da do dos das di du des von zu y e and et und".split())


def pick_emphasis(tokens: list[str], max_chars: int = 24) -> tuple[int, int] | None:
    """(inicio, fin) del grupo a destacar: la última palabra con contenido y, si lo que queda detrás es poco
    (≤ 2 palabras), hasta el final; hacia la izquierda suma palabras con contenido (o un conector como «de»)
    mientras quepa. None si solo hay relleno o es una palabra corta suelta."""
    cores = [_core(t) for t in tokens]
    content = [i for i, c in enumerate(cores) if (len(c) >= 3 and c not in STOPWORDS) or c in NEGATIVE]
    if not content:
        return None
    if len(tokens) == 1 and len(cores[0]) < 5:
        return None
    last = content[-1]
    end = len(tokens) if len(tokens) - (last + 1) <= 2 else last + 1
    start = last
    while start > 0 and end - start < 4:
        prev = start - 1
        is_content = prev in content
        is_link = cores[prev] in _CONNECT and prev > 0 and (prev - 1) in content
        if not (is_content or is_link) or re.search(r"[:;,.!?]$", tokens[prev]):
            break
        if len(" ".join(tokens[prev:end])) > max_chars:
            break
        start = prev
    while start < end - 1 and cores[start] in _CONNECT:       # una clave no empieza con «de», «of»…
        start += 1
    return start, end


def chunk_tokens(toks: list, maxw: int = 7) -> list[list]:
    """Trozos de frase para mostrar: corta en la puntuación (si ya hay ≥3 palabras) o al llegar a maxw."""
    chunks, cur = [], []
    for t in toks:
        cur.append(t)
        if len(cur) >= maxw or (len(cur) >= 3 and re.search(r"[,.:;?!…]$", t[0])):
            chunks.append(cur); cur = []
    if cur:
        if chunks and len(cur) < 2 and len(chunks[-1]) + len(cur) <= maxw + 2:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    return chunks


def _est_width(text: str, fs: float) -> float:
    """Ancho aproximado (px) de MAYÚSCULAS extra-negras cursivas: ~0.70 em por letra."""
    return sum(0.34 if ch == " " else 0.72 for ch in text) * fs


def _stack_events(seg: Segment, body: str, st: SubtitleStyle, width: int, fs_small: int, fs_key: int,
                  kw_font: str, state: dict, hook_c: str, main_c: str) -> list[tuple]:
    """[(capa, inicio, fin, tags, texto)] para una frase en el estilo stack."""
    toks = seg_tokens(seg, _esc(body))
    if not toks:
        return []
    out: list[tuple] = []
    units = chunk_tokens(toks)
    for ui, unit in enumerate(units):
        words = [t[0] for t in unit]
        u0, u_end = unit[0][1], unit[-1][2]
        u_end = max(u_end, seg.end) if ui + 1 == len(units) else units[ui + 1][0][1]
        emp = pick_emphasis(words)
        small_words = [w for i, w in enumerate(words) if not emp or not (emp[0] <= i < emp[1])]
        small = " ".join(small_words)
        fade = "\\fad(120,0)" if st.anim == "fade" else ""
        if not emp:
            out.append((1, u0, u_end, fade, small))
            continue
        key = re.sub(r"[,;:.]+$", "", " ".join(words[emp[0]:emp[1]]).upper())
        k_start = unit[emp[0]][1]
        k_start = max(u0, min(k_start, u_end - 0.1))
        # tamaño: lo mayor posible sin pasarse del 90 % del ancho
        fsk = int(min(fs_key, 0.90 * width / max(1.0, _est_width(key, 1.0))))
        fsk = max(fsk, int(fs_small * 1.15))
        negative = any(_core(w) in NEGATIVE for w in words[emp[0]:emp[1]])
        if negative:
            col, glow, halo = "&H" + COLORS["coral"][1][4:] + "&", True, "&H" + COLORS["red"][1][4:] + "&"
        elif not state["hook_done"]:
            col, glow, halo = hook_c, st.kw_glow, "&H" + COLORS["azure"][1][4:] + "&"
            state["hook_done"] = True
        else:
            col, glow, halo = main_c, False, ""
        kw_tags = f"\\fn{kw_font}\\fs{fsk}\\i1\\b0\\1c{col}"
        reset = f"\\fn{FONTS.get(st.font, FONTS['sans'])[1]}\\fs{fs_small}\\i0\\1c{main_c}"
        hidden = f"{{\\alpha&HFF&}}{key}"
        pop = "\\fscx82\\fscy82\\t(0,140,\\fscx100\\fscy100)" if st.anim in ("fade", "pop", "bounce") else ""
        if k_start > u0 + 0.05:                         # la línea pequeña aparece antes que la clave
            out.append((1, u0, k_start, fade, f"{small}\\N{hidden}"))
        shown = f"{small}\\N{{{kw_tags}{pop}}}{key}{{{reset}}}"
        out.append((1, k_start, u_end, "" if k_start > u0 + 0.05 else fade, shown))
        if glow and halo:
            blur = max(4, round(fsk * 0.16))
            g_text = (f"{{\\alpha&HFF&}}{small}\\N{{\\alpha&H00&\\1a&HFF&\\3c{halo}\\bord{max(3, round(fsk * 0.14))}"
                      f"\\blur{blur}\\fn{kw_font}\\fs{fsk}\\i1\\b0{pop}}}{key}")
            out.append((0, k_start, u_end, "", g_text))
    return out


# ------------------------------------------------------------ layout / ASS

def layout(st: SubtitleStyle, width: int, height: int) -> tuple[int, int, int, int]:
    """(alineación ASS, margen izq, margen der, margen vertical)."""
    mh, mv = round(width * 0.05), round(height * 0.05)
    row = {"top": 7, "upper": 7, "middle": 4, "lower": 1, "bottom": 1}[st.align_v]
    col = {"left": 0, "leftmid": 1, "center": 1, "rightmid": 1, "right": 2}[st.align_h]
    ml, mr = mh, mh
    if st.align_h == "leftmid":
        mr = round(width * 0.40)      # región 5–60 % → texto centrado a ~32 %
    elif st.align_h == "rightmid":
        ml = round(width * 0.40)      # región 40–95 % → texto centrado a ~68 %
    if st.align_v in ("upper", "lower"):
        mv = round(height * 0.25)     # a medio camino entre el borde y el centro
    return row + col, ml, mr, mv


def _anim_tags(anim: str, first: bool, last: bool) -> str:
    if anim == "fade":
        fi, fo = (150 if first else 0), (120 if last else 0)
        return f"\\fad({fi},{fo})" if fi or fo else ""
    if anim == "pop" and first:
        return "\\fscx70\\fscy70\\t(0,140,\\fscx100\\fscy100)"
    if anim == "bounce" and first:
        return "\\fscx40\\fscy40\\t(0,110,\\fscx118\\fscy118)\\t(110,210,\\fscx100\\fscy100)"
    return ""


def build_ass(segs: list[Segment], st: SubtitleStyle, width: int, height: int,
              duration: float = 0.0) -> str:
    fs = max(12, round(height * SIZES.get(st.size, SIZES["m"])[1]))
    font = FONTS.get(st.font, FONTS["sans"])[1]
    main = COLORS.get(st.color, COLORS["white"])[1]
    hl = COLORS.get(st.hl_color, COLORS["yellow"])[1]
    ocol = COLORS.get(st.outline_color, COLORS["black"])[1]
    box_alpha = BGS.get(st.bg, BGS["none"])[1]
    fill_mode = st.highlight == "fill"
    primary, secondary = (hl, main) if fill_mode else (main, "&H000000FF")

    blur = ""
    if box_alpha:
        # BorderStyle 3: la "caja" usa OutlineColour; Outline es el relleno
        border, outline, shadow = 3, round(fs * 0.22, 1), 0
        ocol = box_alpha + ocol[3:]
    elif st.neon:
        border, outline, shadow = 1, round(fs * 0.09, 1), 0
        ocol = main                                   # el contorno brilla del color del texto
        blur = f"\\blur{fs * 0.10:.1f}"
    else:
        border = 1
        outline = round(fs * OUTLINES.get(st.outline, OUTLINES["med"])[1], 1)
        shadow = round(fs * 0.05, 1) if st.shadow else 0
    back = "&H80000000" if st.shadow else "&HFF000000"
    spacing = round(fs * SPACINGS.get(st.spacing, SPACINGS["normal"])[1], 1)
    align, ml, mr, mv = layout(st, width, height)
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\n"
        f"PlayResX: {width}\nPlayResY: {height}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
        "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,"
        "MarginR,MarginV,Encoding\n"
        f"Style: Default,{font},{fs},{primary},{secondary},{ocol},{back},"
        f"{-1 if st.bold and st.font not in SINGLE_WEIGHT else 0},{-1 if st.italic else 0},0,0,100,100,{spacing},0,"
        f"{border},{outline},{shadow},{align},{ml},{mr},{mv},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,"
        "MarginV,Effect,Text\n"
    )

    main_c, hl_c, kw_c = _tc(st.color), _tc(st.hl_color), _tc(st.kw_color)
    per_token = st.anim == "typewriter" or st.highlight in ("color", "pop")
    lines: list[str] = []

    if st.layout == "stack":
        state = {"hook_done": False}
        fs_small = max(12, round(fs * 0.58))
        fs_key = round(fs * 1.55)
        kfont = FONTS.get(st.kw_font, FONTS["impact"])[1]
        for seg in fix_timing(segs, duration):
            body = seg.translation or seg.text
            if st.censor:
                body = censor_text(body)
            for layer, a, b, tags, text in _stack_events(seg, body, st, width, fs_small, fs_key, kfont, state,
                                                         kw_c, main_c):
                lines.append(f"Dialogue: {layer},{_ts_ass(a)},{_ts_ass(max(b, a + 0.05))},Default,,0,0,0,,"
                             f"{{{tags}}}{text}")
        head = head.replace(f"Style: Default,{font},{fs},", f"Style: Default,{font},{fs_small},", 1)
        if st.align_v == "top":                        # los titulares van un poco más abajo del borde (≈ 8 %)
            head = re.sub(r"(,\d+,\d+,)(\d+)(,1\n\n\[Events)", lambda m: m.group(1) + str(round(height * 0.075)) + m.group(3), head, count=1)
        return head + "\n".join(lines) + "\n"

    def emit(a: float, b: float, tags: str, text: str):
        lines.append(f"Dialogue: 0,{_ts_ass(a)},{_ts_ass(max(b, a + 0.05))},"
                     f"Default,,0,0,0,,{{{tags}{blur}}}{text}")

    def render_line(unit, visible=None, active=None) -> str:
        parts, hidden = [], []
        for i, (tok, _, _, kw) in enumerate(unit):
            if visible is not None and i > visible:
                hidden.append(tok)
                continue
            if i == active and st.highlight in ("color", "pop"):
                tg = f"\\1c{hl_c}" + ("\\fscx115\\fscy115" if st.highlight == "pop" else "")
                parts.append(f"{{{tg}}}{tok}{{\\1c{main_c}\\fscx100\\fscy100}}")
            elif kw and st.keywords and not fill_mode:
                parts.append(f"{{\\1c{kw_c}}}{tok}{{\\1c{main_c}}}")
            else:
                parts.append(tok)
        text = " ".join(parts)
        if hidden:      # texto invisible: mantiene el diseño estable mientras aparece
            text += " {\\alpha&HFF&}" + " ".join(hidden)
        return text

    for seg in fix_timing(segs, duration):
        body = seg.translation or seg.text
        if st.censor:
            body = censor_text(body)
        if st.upper:
            body = body.upper()
        toks = seg_tokens(seg, _esc(body))
        if not toks:
            continue
        kws = find_keywords([t[0] for t in toks]) if st.keywords else set()
        toks = [(t, a, b, i in kws) for i, (t, a, b) in enumerate(toks)]
        n = st.words if st.words and st.words < len(toks) else len(toks)
        units = [toks[i:i + n] for i in range(0, len(toks), n)]

        same = norm_lang(seg.lang) == st.target or body.strip().lower() == seg.text.strip().lower()
        extra = ""
        if st.bilingual and not same and len(units) == 1:
            orig = censor_text(seg.text) if st.censor else seg.text
            orig = _esc(orig).upper() if st.upper else _esc(orig)
            extra = f"\\N{{\\fs{max(9, round(fs * 0.62))}\\b0\\alpha&H30&}}{orig}"

        for ui, unit in enumerate(units):
            u0 = unit[0][1]
            u_end = unit[-1][2]
            if ui + 1 < len(units):
                nxt = units[ui + 1][0][1]
                u_end = nxt if nxt - u_end < 0.4 else u_end + 0.15
            else:
                u_end = max(u_end, seg.end)
            if per_token:
                for k in range(len(unit)):
                    a = u0 if k == 0 else unit[k][1]
                    b = unit[k + 1][1] if k + 1 < len(unit) else u_end
                    text = render_line(
                        unit, visible=k if st.anim == "typewriter" else None,
                        active=k if st.highlight in ("color", "pop") else None)
                    emit(a, b, _anim_tags(st.anim, k == 0, k == len(unit) - 1),
                         text + extra)
            elif fill_mode:
                text = " ".join(f"{{\\kf{max(1, round((t1 - t0) * 100))}}}{tok}"
                                for tok, t0, t1, _ in unit)
                emit(u0, u_end, _anim_tags(st.anim, True, True), text + extra)
            else:
                emit(u0, u_end, _anim_tags(st.anim, True, True),
                     render_line(unit) + extra)

    if st.progress != "off" and duration > 0:
        bar_h = max(4, round(height * 0.012))
        y = 0 if st.progress == "top" else height - bar_h
        ms = int(duration * 1000)
        lines.append(
            f"Dialogue: 5,{_ts_ass(0)},{_ts_ass(duration)},Default,,0,0,0,,"
            f"{{\\an7\\pos(0,{y})\\1c{hl_c}\\bord0\\shad0\\blur0\\p1"
            f"\\clip(0,0,0,{height})\\t(0,{ms},\\clip(0,0,{width},{height}))}}"
            f"m 0 0 l {width} 0 {width} {bar_h} 0 {bar_h}")
    return head + "\n".join(lines) + "\n"


# ---------------------------------------------------------------- SRT

def build_srt(segs: list[Segment]) -> str:
    out = []
    for i, s in enumerate(fix_timing(segs, 0), 1):
        out.append(f"{i}\n{_ts_srt(s.start)} --> {_ts_srt(s.end)}\n"
                   f"{s.translation or s.text}\n")
    return "\n".join(out)


def parse_srt_texts(srt: str) -> list[str]:
    """Textos de cada bloque de un .srt, en orden (ignora índices y tiempos)."""
    srt = srt.replace("\r\n", "\n").lstrip("﻿")
    texts = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = block.strip().split("\n")
        i = 0
        if lines and lines[0].strip().isdigit():
            i = 1
        if i < len(lines) and "-->" in lines[i]:
            i += 1
        else:
            continue
        texts.append(" ".join(l.strip() for l in lines[i:]).strip())
    return texts
