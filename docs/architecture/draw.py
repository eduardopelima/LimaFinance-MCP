#!/usr/bin/env python3
"""Desenha os diagramas de arquitetura do limafinance (SVG, só biblioteca padrão).

    python3 docs/architecture/draw.py        → docs/architecture/overview.svg + flows/*.svg

Estilo: fundo creme, caixas sólidas com ícone e texto branco (recursos), caixas lilás (código nosso,
dentro de um Worker), setas tracejadas com rótulo em pílula sólida. Ícones de marca: Simple Icons (CC0,
brands.json); ícones de traço no estilo Lucide (ISC). Edite as coordenadas à mão e rode de novo.
"""

import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BRANDS = {k: v for k, v in json.load(open(os.path.join(HERE, "brands.json"), encoding="utf-8")).items() if not k.startswith("_")}

LINE_ICONS = {
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "workflow": '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/>',
    "terminal": '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "tools": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "chat": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    "repeat": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
}

# Paleta
BG, INK, MUTED = "#f8f6f1", "#1f2a44", "#5b6782"
SLATE, ORANGE, RED, GREEN, TEAL, INDIGO = "#4b5565", "#e8833a", "#d64a56", "#2f9461", "#2a9d8f", "#5b55d6"
SOFT_FILL, SOFT_INK, SOFT_SUB = "#e3e1fb", "#3f3aa8", "#6a66b8"
GROUP_CF, GROUP_CF_FILL, GROUP_W, GROUP_W_FILL, GROUP_N = "#efc49f", "#fdf8f2", "#a29ee8", "#f1f0fd", "#c9ced8"

W, H = 230, 58
esc = html.escape


def text_w(s: str, size: float, bold: bool = False) -> float:
    return len(s) * size * (0.6 if bold else 0.56)


def icon(name: str, x: float, y: float, size: float = 22, color: str = "#fff") -> str:
    k = size / 24
    if name in BRANDS:
        return f'<path transform="translate({x},{y}) scale({k})" d="{BRANDS[name]}" fill="{color}"/>'
    return (f'<g transform="translate({x},{y}) scale({k})" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{LINE_ICONS[name]}</g>')


def box(x, y, title, sub, color=SLATE, ic=None, w=W, h=H, caption=None, kind="solid") -> str:
    """kind: solid (recurso gerenciado), soft (nosso código num Worker), outline (opcional/externo)."""
    if kind == "soft":
        fill, stroke, ink, subc, dash = SOFT_FILL, SOFT_FILL, SOFT_INK, SOFT_SUB, ""
    elif kind == "outline":
        fill, stroke, ink, subc, dash = "#ffffff", color, color, MUTED, ' stroke-dasharray="5 4"'
    else:
        fill, stroke, ink, subc, dash = color, color, "#ffffff", "#ffffffcc", ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>']
    tx = x + w / 2 + (14 if ic else 0)
    if ic:
        out.append(icon(ic, x + 16, y + h / 2 - 11, 22, ink))
    out.append(f'<text x="{tx}" y="{y + h / 2 - 3}" text-anchor="middle" font-size="14" font-weight="700" fill="{ink}">{esc(title)}</text>')
    if sub:
        out.append(f'<text x="{tx}" y="{y + h / 2 + 14}" text-anchor="middle" font-size="11" fill="{subc}">{esc(sub)}</text>')
    if caption:
        for i, line in enumerate(caption.split("\n")):
            out.append(f'<text x="{x + w / 2}" y="{y + h + 16 + i * 14}" text-anchor="middle" font-size="11" fill="{MUTED}">{esc(line)}</text>')
    return "".join(out)


def group(x, y, w, h, label, stroke=GROUP_N, fill="none", ink=MUTED, ic=None) -> str:
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.5" stroke-dasharray="6 5"/>']
    lx = x + 16
    if ic:
        out.append(icon(ic, lx, y + 10, 16, ink))
        lx += 22
    out.append(f'<text x="{lx}" y="{y + 23}" font-size="11" font-weight="800" letter-spacing="1.2" fill="{ink}">{esc(label)}</text>')
    return "".join(out)


_MARKERS: set[str] = set()


def _marker(color: str) -> str:
    _MARKERS.add(color)
    return "m" + color.lstrip("#")


def pill(cx, cy, label, color) -> str:
    w = text_w(label, 11, True) + 20
    return (f'<rect x="{cx - w / 2}" y="{cy - 10}" width="{w}" height="20" rx="10" fill="{color}"/>'
            f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">{esc(label)}</text>')


def arrow(points, label=None, color=SLATE, seg=0, t=0.5, both=False, dashed=True) -> str:
    """Polilinha ortogonal; a pílula fica no segmento `seg`, na fração `t`."""
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    m = _marker(color)
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    start = f' marker-start="url(#{m}s)"' if both else ""
    out = [f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.7"{dash} marker-end="url(#{m})"{start}/>']
    if label:
        (x1, y1), (x2, y2) = points[seg], points[seg + 1]
        out.append(pill(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, label, color))
    return "".join(out)


def note(x, y, s, color=MUTED, size=11, anchor="start", weight="400") -> str:
    return "".join(
        f'<text x="{x}" y="{y + i * (size + 3)}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" fill="{color}">{esc(line)}</text>'
        for i, line in enumerate(s.split("\n"))
    )


def svg(width, height, title, subtitle, body) -> str:
    defs = "".join(
        f'<marker id="m{c.lstrip("#")}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
        f'<marker id="m{c.lstrip("#")}s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
        for c in sorted(_MARKERS)
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'font-family="Inter, \'Segoe UI\', system-ui, -apple-system, sans-serif"><defs>{defs}</defs>'
            f'<rect width="{width}" height="{height}" rx="16" fill="{BG}"/>'
            f'<text x="32" y="44" font-size="22" font-weight="800" fill="{INK}">{esc(title)}</text>'
            f'<text x="32" y="66" font-size="13" fill="{MUTED}">{esc(subtitle)}</text>{body}</svg>\n')


def write(name: str, content: str) -> None:
    path = os.path.join(HERE, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    _MARKERS.clear()
    print("ok", os.path.relpath(path, HERE))


# ───────────────────────────── mapa geral ─────────────────────────────
o = [
    group(32, 96, 262, 120, "LOGIN (IDENTIDADE)"),
    box(48, 128, "Google", "OIDC · só identidade", SLATE, "google", caption="e-mail verificado + sub"),

    group(32, 236, 262, 400, "CLIENTES MCP"),
    box(48, 268, "Claude", "OAuth · CIMD", SLATE, "claude"),
    box(48, 342, "ChatGPT", "OAuth · DCR", SLATE, "openai"),
    box(48, 416, "Gemini CLI", "OAuth · DCR", SLATE, "googlegemini"),
    box(48, 490, "Claude Code", "PAT lf_pat_… no header", SLATE, "terminal"),
    note(163, 576, "toda chamada: POST /mcp\ncom Authorization: Bearer", anchor="middle"),

    group(330, 96, 1210, 560, "CLOUDFLARE", GROUP_CF, GROUP_CF_FILL, ORANGE, "cloudflare"),

    group(360, 136, 540, 168, "WORKER DE AUTH · TYPESCRIPT", GROUP_W, GROUP_W_FILL, SOFT_INK, "typescript"),
    box(384, 190, "Servidor OAuth 2.1", "/authorize · /token · /register", ic="lock", w=250, kind="soft",
        caption="consentimento + allowlist"),
    box(650, 190, "validateToken", "RPC · Service Binding", ic="shield", w=226, kind="soft", caption="token preso ao resource"),

    box(990, 190, "KV", "OAUTH_KV · grants", SLATE, "key", caption="tokens só como hash"),

    group(360, 336, 540, 290, "WORKER MCP · PYTHON (PYODIDE)", GROUP_W, GROUP_W_FILL, SOFT_INK, "python"),
    box(384, 390, "Endpoint MCP", "POST /mcp · 14 tools · Pydantic", ic="tools", w=492, kind="soft",
        caption="guarded() · escopo mcp:write · user_id vem só do token"),
    box(384, 520, "Cron", "todo dia 00:00 (SP)", ic="clock", w=230, kind="soft"),
    box(646, 520, "Consumer da fila", "gera até 12 meses", ic="workflow", w=230, kind="soft"),

    box(990, 390, "D1", "livro-razão · SQLite", GREEN, "database",
        caption="transactions · recurrences\nbudgets · audit_log"),
    box(990, 520, "Queue", "limafinance-jobs · 3 tentativas", ORANGE, "list"),
    box(1290, 520, "DLQ", "limafinance-jobs-dlq", RED, "alert", w=210),

    # clientes → barramento → auth / mcp
    "".join(f'<path d="M278,{y} L306,{y}" stroke="{SLATE}" stroke-width="1.7" stroke-dasharray="6 5"/>' for y in (297, 371, 445, 519)),
    f'<path d="M306,297 L306,519" stroke="{SLATE}" stroke-width="1.7" stroke-dasharray="6 5"/>',
    arrow([(306, 297), (306, 236), (382, 236)], "OAuth 2.1 + PKCE", INDIGO, seg=0, t=0.70),
    arrow([(306, 419), (382, 419)], "MCP + Bearer", SLATE, t=0.5),
    # auth ↔ Google
    arrow([(384, 206), (318, 206), (318, 157), (280, 157)], "login", ORANGE, seg=1, t=0.5, both=True),
    # grant no KV
    arrow([(509, 190), (509, 168), (1105, 168), (1105, 188)], "grava grant", SLATE, seg=1, t=0.62),
    arrow([(876, 219), (988, 219)], "lê grant", SLATE),
    # mcp → validateToken (RPC)
    arrow([(858, 390), (858, 251)], "validateToken (RPC)", INDIGO, t=0.45),
    # mcp → D1
    arrow([(876, 419), (988, 419)], "SQL · user_id", GREEN),
    # recorrências
    arrow([(499, 578), (499, 604), (1105, 604), (1105, 580)], "1 job por usuário", ORANGE, seg=1, t=0.72),
    arrow([(988, 549), (878, 549)], "entrega", ORANGE),
    arrow([(761, 520), (761, 494), (1008, 494), (1008, 450)], "rec:<id>:<mês>", GREEN, seg=1, t=0.55),
    arrow([(1220, 549), (1288, 549)], "3 falhas", RED),
]
write("overview.svg", svg(1572, 690, "limafinance · arquitetura",
                          "Servidor MCP remoto em Cloudflare: o LLM conversa, as tools gravam no D1. Dois Workers, OAuth 2.1 com login Google.",
                          "".join(o)))

# ───────────────────────────── fluxo 1 · conectar ─────────────────────────────
f1 = [
    box(40, 130, "Cliente MCP", "Claude · ChatGPT · Gemini", SLATE, "chat", w=220, h=90),
    group(500, 96, 300, 300, "CLOUDFLARE", GROUP_CF, GROUP_CF_FILL, ORANGE, "cloudflare"),
    box(530, 130, "Servidor OAuth", "Worker de auth", ic="lock", w=240, h=90, kind="soft"),
    box(530, 300, "Endpoint MCP", "Worker Python", ic="tools", w=240, kind="soft"),
    box(940, 110, "Google", "só identidade", SLATE, "google", w=220, caption="e-mail na allowlist?\nsenão 403"),
    box(940, 250, "KV", "OAUTH_KV", SLATE, "key", w=220, caption="grant + tokens (hash)"),

    arrow([(260, 148), (528, 148)], "1. register (DCR / CIMD)", SLATE, t=0.5),
    arrow([(260, 175), (528, 175)], "2. authorize + PKCE", INDIGO, t=0.5),
    arrow([(528, 202), (262, 202)], "6. access 1 h + refresh 90 d", GREEN, t=0.5),
    arrow([(770, 150), (860, 150), (860, 139), (938, 139)], "3. login", ORANGE, seg=0, t=0.5),
    arrow([(938, 158), (880, 158), (880, 188), (772, 188)], "4. callback", ORANGE, seg=2, t=0.5),
    arrow([(720, 220), (720, 250), (900, 250), (900, 279), (938, 279)], "5. grava grant", SLATE, seg=1, t=0.5),
    arrow([(150, 220), (150, 329), (528, 329)], "7. POST /mcp + Bearer", INDIGO, seg=1, t=0.55),
    arrow([(650, 300), (650, 222)], "8. validateToken", INDIGO, t=0.5),
    note(530, 426, "Token preso ao MCP_RESOURCE; o Python resolve o usuário pelo sub do Google (upsert_user)."),
]
write("flows/1-conectar.svg", svg(1200, 450, "Fluxo 1 · Conectar um cliente",
                                  "OAuth 2.1 com o Google como provedor de identidade; só e-mails da allowlist recebem um grant.", "".join(f1)))

# ───────────────────────────── fluxo 2 · registrar gasto ─────────────────────────────
f2 = [
    box(40, 130, "Cliente MCP", "\"gastei 45,90 no mercado\"", SLATE, "claude", w=230),
    group(420, 96, 320, 260, "CLOUDFLARE", GROUP_CF, GROUP_CF_FILL, ORANGE, "cloudflare"),
    box(450, 130, "Endpoint MCP", "add_transactions", ic="tools", w=260, kind="soft"),
    box(450, 270, "Worker de auth", "validateToken (RPC)", ic="shield", w=260, kind="soft"),
    box(840, 130, "D1", "livro-razão", GREEN, "database", w=220,
        caption="INSERT transaction + audit_log\nna mesma transação"),

    arrow([(270, 150), (448, 150)], "1. add_transactions", SLATE),
    arrow([(448, 172), (272, 172)], "4. ok · R$ 45,90", GREEN),
    arrow([(580, 190), (580, 268)], "2. quem é? → user_id", INDIGO, both=True),
    arrow([(710, 159), (838, 159)], "3. batch atômico", GREEN),
    note(950, 240, "idempotency_key repetida\n= devolve o existente", anchor="middle"),
    note(40, 222, "\"45,90\" → 4590 centavos\ncategoria inexistente = erro com sugestões\nvalida o lote inteiro antes de gravar"),
]
write("flows/2-registrar.svg", svg(1100, 380, "Fluxo 2 · Registrar um gasto",
                                   "Uma chamada de tool vira um lote atômico no D1, sempre com o user_id do token e trilha de auditoria.", "".join(f2)))

# ───────────────────────────── fluxo 3 · recorrências ─────────────────────────────
f3 = [
    group(32, 96, 1250, 190, "TODO DIA · JANELA ROLANTE DE 12 MESES", GROUP_N, "none", MUTED, "repeat"),
    box(60, 140, "Cron", "0 3 * * * = 00:00 SP", ic="clock", w=220, kind="soft"),
    box(380, 140, "Queue", "1 job por usuário", ORANGE, "list", w=220),
    box(700, 140, "Consumer", "materializa a janela", ic="workflow", w=220, kind="soft"),
    box(1020, 140, "D1", "transactions", GREEN, "database", w=240,
        caption="chave rec:<id>:<YYYY-MM>\nON CONFLICT DO NOTHING"),
    box(380, 232, "DLQ", "após 3 falhas", RED, "alert", w=220, h=40),
    arrow([(280, 169), (378, 169)], "1. enfileira", ORANGE),
    arrow([(600, 169), (698, 169)], "2. entrega", ORANGE),
    arrow([(920, 169), (1018, 169)], "3. +12 meses", GREEN),
    arrow([(490, 198), (490, 230)], None, RED),

    group(32, 310, 1250, 250, "PEDIDOS DO USUÁRIO", GROUP_N, "none", MUTED, "tools"),
    box(60, 410, "Cliente MCP", "fala em linguagem natural", SLATE, "claude", w=220),
    box(700, 410, "Endpoint MCP", "domain/recurrences", ic="tools", w=220, kind="soft"),
    arrow([(280, 424), (320, 424), (320, 360), (760, 360), (760, 408)], "create_recurrence · gera 12 meses na hora", INDIGO, seg=2, t=0.47),
    arrow([(280, 439), (698, 439)], "update_recurrence · reajuste a partir de um mês", SLATE),
    arrow([(280, 454), (320, 454), (320, 520), (810, 520), (810, 470)], "update_transaction · rateio = override", ORANGE, seg=2, t=0.45),
    arrow([(920, 439), (1040, 439), (1040, 200)], "regra + meses", GREEN, seg=1, t=0.42),
    note(1060, 470, "reajuste não toca meses\neditados à mão\n(recurrence_override = 1);\nend_recurrence exclui os\nfuturos (soft delete)"),
]
write("flows/3-recorrencias.svg", svg(1310, 590, "Fluxo 3 · Recorrências",
                                      "A regra guarda o valor padrão; exceções vivem nos lançamentos. Tudo idempotente pela chave rec:<id>:<mês>.", "".join(f3)))
