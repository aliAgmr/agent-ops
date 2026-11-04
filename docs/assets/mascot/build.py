"""Build the Agent Ops mascot: writes two SVGs and renders them to PNG.

Run from anywhere:
    uv run --with resvg-py python docs/assets/mascot/build.py
"""

from pathlib import Path

import resvg_py

HERE = Path(__file__).resolve().parent

# Palette: red + neutrals + one accent (status green).
RED = "#EF4056"
RED_DARK = "#C8102E"
RED_DEEP = "#9E0C24"
RED_LIGHT = "#F46B7D"
CREAM = "#FFF8F3"
PAPER = "#FFFFFF"
BG = "#F5EFE9"
GREY_1 = "#E9E1DA"
GREY_2 = "#D5C9BF"
GREY_3 = "#A99A8F"
GREY_4 = "#6F625B"
INK = "#2A2322"
GREEN = "#1FAE7A"

W, H = 3840, 2160

# The date on the journal page (ISO).
# Wrapped in RLE/PDF marks so resvg lays the words out right-to-left.
DATE_LABEL = "2026-11-04"


def defs() -> str:
    return f"""
<defs>
  <linearGradient id="gRed" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{RED_LIGHT}"/>
    <stop offset="0.45" stop-color="{RED}"/>
    <stop offset="1" stop-color="{RED_DARK}"/>
  </linearGradient>
  <linearGradient id="gBody" x1="0" y1="0" x2="0.9" y2="1">
    <stop offset="0" stop-color="{RED}"/>
    <stop offset="1" stop-color="{RED_DARK}"/>
  </linearGradient>
  <linearGradient id="gFace" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#FFFFFF"/>
    <stop offset="1" stop-color="#F7EEE8"/>
  </linearGradient>
  <linearGradient id="gInk" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#3A3230"/>
    <stop offset="1" stop-color="{INK}"/>
  </linearGradient>
  <radialGradient id="gGlow" cx="0.5" cy="0.5" r="0.5">
    <stop offset="0" stop-color="{GREEN}" stop-opacity="0.45"/>
    <stop offset="1" stop-color="{GREEN}" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="gHalo" cx="0.5" cy="0.5" r="0.5">
    <stop offset="0" stop-color="{RED}" stop-opacity="0.16"/>
    <stop offset="0.7" stop-color="{RED}" stop-opacity="0.07"/>
    <stop offset="1" stop-color="{RED}" stop-opacity="0"/>
  </radialGradient>
  <pattern id="dots" width="64" height="64" patternUnits="userSpaceOnUse">
    <circle cx="32" cy="32" r="3.2" fill="{GREY_2}" opacity="0.55"/>
  </pattern>
  <filter id="soft" x="-20%" y="-20%" width="140%" height="150%">
    <feDropShadow dx="0" dy="22" stdDeviation="26" flood-color="#5A2A22" flood-opacity="0.16"/>
  </filter>
  <filter id="softSmall" x="-20%" y="-20%" width="140%" height="160%">
    <feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#5A2A22" flood-opacity="0.14"/>
  </filter>
  <filter id="blur18" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="18"/>
  </filter>
  <filter id="blur40" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="40"/>
  </filter>
  <clipPath id="clipHead">
    <rect x="1000" y="330" width="1000" height="780" rx="310"/>
  </clipPath>
  <clipPath id="clipBody">
    <path d="{BODY_PATH}"/>
  </clipPath>
</defs>"""


BODY_PATH = (
    "M 1215 1150 C 1215 1100 1255 1075 1305 1075 L 1695 1075 "
    "C 1745 1075 1785 1100 1785 1150 L 1842 1715 C 1850 1792 1800 1842 1725 1842 "
    "L 1275 1842 C 1200 1842 1150 1792 1158 1715 Z"
)


def background() -> str:
    return f"""
<rect width="{W}" height="{H}" fill="{BG}"/>
<rect width="{W}" height="{H}" fill="url(#dots)"/>
<circle cx="1410" cy="1060" r="1020" fill="url(#gHalo)"/>
<circle cx="1410" cy="1060" r="860" fill="none" stroke="{RED}" stroke-opacity="0.10" stroke-width="6" stroke-dasharray="2 26" stroke-linecap="round"/>
"""


def lineage() -> str:
    """Warehouse table -> page -> metric, joined by threads."""

    def card(x, y, w, h, inner):
        return f"""
<g transform="translate({x} {y})" filter="url(#softSmall)">
  <rect width="{w}" height="{h}" rx="44" fill="{PAPER}"/>
  {inner}
</g>"""

    table_icon = f"""
  <g transform="translate(70 52)">
    <path d="M0 22 L0 108 C0 130 160 130 160 108 L160 22" fill="{GREY_1}"/>
    <path d="M0 65 C0 87 160 87 160 65" fill="none" stroke="{GREY_2}" stroke-width="8"/>
    <ellipse cx="80" cy="22" rx="80" ry="22" fill="{RED}"/>
  </g>"""
    page_icon = f"""
  <g transform="translate(52 44)">
    <rect width="196" height="150" rx="22" fill="{GREY_1}"/>
    <rect width="196" height="34" rx="17" fill="{INK}"/>
    <circle cx="22" cy="17" r="6" fill="{RED}"/>
    <circle cx="42" cy="17" r="6" fill="{GREY_3}"/>
    <rect x="22" y="56" width="70" height="72" rx="12" fill="{RED}" opacity="0.9"/>
    <rect x="108" y="58" width="68" height="14" rx="7" fill="{GREY_3}"/>
    <rect x="108" y="84" width="52" height="14" rx="7" fill="{GREY_2}"/>
    <rect x="108" y="110" width="62" height="14" rx="7" fill="{GREY_2}"/>
  </g>"""
    metric_icon = f"""
  <g transform="translate(58 46)">
    <rect x="0" y="80" width="34" height="56" rx="10" fill="{GREY_2}"/>
    <rect x="50" y="46" width="34" height="90" rx="10" fill="{GREY_3}"/>
    <rect x="100" y="12" width="34" height="124" rx="10" fill="{RED}"/>
    <path d="M0 52 L66 18 L118 -10" fill="none" stroke="{INK}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="118" cy="-10" r="11" fill="{INK}"/>
  </g>"""

    threads = f"""
<g fill="none" stroke-linecap="round">
  <path d="M 450 1400 C 460 1250 640 1230 640 1080" stroke="{GREY_2}" stroke-width="12"/>
  <path d="M 640 860 C 640 720 440 720 430 590" stroke="{GREY_2}" stroke-width="12"/>
  <path d="M 450 1400 C 460 1250 640 1230 640 1080" stroke="{RED}" stroke-width="12" stroke-dasharray="1 44"/>
  <path d="M 640 860 C 640 720 440 720 430 590" stroke="{RED}" stroke-width="12" stroke-dasharray="1 44"/>
</g>
<g fill="{PAPER}" stroke="{RED}" stroke-width="10">
  <circle cx="450" cy="1400" r="18"/>
  <circle cx="640" cy="1080" r="18"/>
  <circle cx="640" cy="860" r="18"/>
  <circle cx="430" cy="590" r="18"/>
</g>"""

    return (
        threads
        + card(300, 1410, 300, 200, table_icon)
        + card(490, 860, 300, 220, page_icon)
        + card(290, 390, 280, 200, metric_icon)
    )


def paper_plane() -> str:
    """Notification plane with a dotted flight trail."""
    return f"""
<path d="M 2130 930 C 2250 880 2160 720 2270 650 C 2340 605 2385 640 2425 600"
      fill="none" stroke="{GREY_3}" stroke-width="12" stroke-linecap="round" stroke-dasharray="1 34"/>
<g transform="translate(2440 560) rotate(-18)" filter="url(#softSmall)">
  <path d="M 0 40 L 300 -70 L 120 100 Z" fill="{PAPER}"/>
  <path d="M 120 100 L 300 -70 L 230 150 Z" fill="{GREY_1}"/>
  <path d="M 120 100 L 150 168 L 175 120 Z" fill="{GREY_3}"/>
  <path d="M 120 100 L 300 -70" stroke="{GREY_2}" stroke-width="6" stroke-linecap="round"/>
</g>"""


def antenna() -> str:
    return f"""
<circle cx="1500" cy="178" r="150" fill="url(#gGlow)"/>
<rect x="1486" y="196" width="28" height="150" rx="14" fill="{INK}"/>
<rect x="1430" y="310" width="140" height="44" rx="22" fill="{INK}"/>
<circle cx="1500" cy="178" r="52" fill="{GREEN}"/>
<circle cx="1482" cy="160" r="16" fill="#FFFFFF" opacity="0.75"/>
"""


def head() -> str:
    return f"""
<!-- ear pods -->
<rect x="938" y="620" width="110" height="290" rx="55" fill="url(#gInk)"/>
<rect x="1952" y="620" width="110" height="290" rx="55" fill="url(#gInk)"/>
<rect x="958" y="700" width="30" height="130" rx="15" fill="{RED}"/>
<rect x="2012" y="700" width="30" height="130" rx="15" fill="{RED}"/>

<!-- head shell -->
<g filter="url(#soft)">
  <rect x="1000" y="330" width="1000" height="780" rx="310" fill="url(#gRed)"/>
</g>
<g clip-path="url(#clipHead)">
  <ellipse cx="1820" cy="1120" rx="520" ry="300" fill="{RED_DARK}" opacity="0.55" filter="url(#blur40)"/>
  <ellipse cx="1280" cy="400" rx="320" ry="100" fill="#FFFFFF" opacity="0.2" transform="rotate(-12 1280 400)" filter="url(#blur18)"/>
</g>

<!-- face plate -->
<rect x="1105" y="480" width="790" height="540" rx="250" fill="{RED_DEEP}" opacity="0.18" transform="translate(0 14)"/>
<rect x="1105" y="480" width="790" height="540" rx="250" fill="url(#gFace)"/>

<!-- brows: calm, attentive -->
<g fill="none" stroke="{INK}" stroke-width="26" stroke-linecap="round">
  <path d="M 1288 598 Q 1352 566 1414 590"/>
  <path d="M 1586 590 Q 1648 566 1712 598"/>
</g>
<!-- eyes -->
<g>
  <ellipse cx="1352" cy="730" rx="64" ry="86" fill="{INK}"/>
  <ellipse cx="1648" cy="730" rx="64" ry="86" fill="{INK}"/>
  <circle cx="1374" cy="700" r="24" fill="#FFFFFF"/>
  <circle cx="1670" cy="700" r="24" fill="#FFFFFF"/>
  <circle cx="1336" cy="770" r="10" fill="#FFFFFF" opacity="0.8"/>
  <circle cx="1632" cy="770" r="10" fill="#FFFFFF" opacity="0.8"/>
</g>
<!-- cheeks -->
<ellipse cx="1238" cy="860" rx="64" ry="36" fill="{RED}" opacity="0.28"/>
<ellipse cx="1762" cy="860" rx="64" ry="36" fill="{RED}" opacity="0.28"/>
<!-- smile -->
<path d="M 1420 868 Q 1500 940 1580 868" fill="none" stroke="{INK}" stroke-width="26" stroke-linecap="round"/>
"""


def body() -> str:
    return f"""
<!-- ground shadow -->
<ellipse cx="1480" cy="1905" rx="620" ry="46" fill="{INK}" opacity="0.12"/>

<!-- feet -->
<rect x="1255" y="1790" width="200" height="104" rx="52" fill="url(#gInk)"/>
<rect x="1545" y="1790" width="200" height="104" rx="52" fill="url(#gInk)"/>

<!-- torso -->
<g filter="url(#soft)">
  <path d="{BODY_PATH}" fill="url(#gBody)"/>
</g>
<g clip-path="url(#clipBody)">
  <ellipse cx="1500" cy="1095" rx="420" ry="70" fill="{RED_DEEP}" opacity="0.45" filter="url(#blur40)"/>
  <ellipse cx="1840" cy="1700" rx="300" ry="420" fill="{RED_DEEP}" opacity="0.25" filter="url(#blur40)"/>
  <path d="M 1150 1640 Q 1500 1690 1850 1640" fill="none" stroke="{RED_DEEP}" stroke-opacity="0.35" stroke-width="10"/>
</g>

<!-- chest status light (release gate: all green) -->
<g filter="url(#softSmall)">
  <rect x="1360" y="1200" width="280" height="104" rx="52" fill="url(#gInk)"/>
</g>
<circle cx="1420" cy="1252" r="26" fill="#4A403D"/>
<circle cx="1500" cy="1252" r="26" fill="#4A403D"/>
<circle cx="1580" cy="1252" r="44" fill="url(#gGlow)"/>
<circle cx="1580" cy="1252" r="26" fill="{GREEN}"/>
<circle cx="1572" cy="1243" r="8" fill="#FFFFFF" opacity="0.7"/>

<!-- chest pocket with pen: the careful note-taker -->
<rect x="1376" y="1390" width="248" height="190" rx="44" fill="{RED_DARK}" opacity="0.55"/>
<rect x="1376" y="1390" width="248" height="40" rx="20" fill="{RED_DEEP}" opacity="0.35"/>
<g transform="rotate(8 1560 1380)">
  <rect x="1542" y="1316" width="36" height="120" rx="18" fill="{INK}"/>
  <rect x="1542" y="1316" width="36" height="40" rx="18" fill="{CREAM}"/>
  <rect x="1574" y="1340" width="10" height="70" rx="5" fill="#5A4E4A"/>
</g>
<rect x="1376" y="1420" width="248" height="160" rx="44" fill="{RED_DARK}"/>
"""


def shield_arm() -> str:
    """Viewer's left: the guard."""
    return f"""
<path d="M 1235 1210 Q 1090 1300 1130 1470" fill="none" stroke="{RED_DARK}" stroke-width="128" stroke-linecap="round"/>
<g transform="rotate(-7 1040 1540)" filter="url(#soft)">
  <path d="M 1040 1250 C 1125 1295 1195 1305 1262 1305 L 1262 1515 C 1262 1665 1170 1760 1040 1815
           C 910 1760 818 1665 818 1515 L 818 1305 C 885 1305 955 1295 1040 1250 Z" fill="url(#gInk)"/>
  <path d="M 1040 1300 C 1110 1336 1168 1346 1222 1346 L 1222 1515 C 1222 1638 1146 1718 1040 1766
           C 934 1718 858 1638 858 1515 L 858 1346 C 912 1346 970 1336 1040 1300 Z" fill="{CREAM}"/>
  <path d="M 1040 1300 L 1040 1766 C 934 1718 858 1638 858 1515 L 858 1346 C 912 1346 970 1336 1040 1300 Z"
        fill="{GREY_1}" opacity="0.55"/>
  <!-- lock emblem -->
  <path d="M 985 1505 L 985 1462 C 985 1428 1010 1406 1040 1406 C 1070 1406 1095 1428 1095 1462 L 1095 1505"
        fill="none" stroke="{RED_DARK}" stroke-width="26" stroke-linecap="round"/>
  <rect x="948" y="1495" width="184" height="150" rx="36" fill="{RED}"/>
  <circle cx="1040" cy="1555" r="20" fill="{CREAM}"/>
  <rect x="1031" y="1560" width="18" height="44" rx="9" fill="{CREAM}"/>
</g>
<!-- hand gripping the shield edge -->
<circle cx="1212" cy="1478" r="70" fill="url(#gInk)" filter="url(#softSmall)"/>
<path d="M 1178 1442 Q 1160 1478 1178 1514" fill="none" stroke="#5A4E4A" stroke-width="10" stroke-linecap="round"/>
"""


def journal_arm() -> str:
    """Viewer's right: the action journal, every row has an author and an undo."""
    rows = ""
    avatars = [GREY_4, RED, GREY_3, INK]
    lengths = [190, 150, 210, 120]
    for i, (col, ln) in enumerate(zip(avatars, lengths)):
        y = 262 + i * 88
        rows += f"""
    <circle cx="70" cy="{y}" r="24" fill="{col}"/>
    <rect x="112" y="{y - 10}" width="{ln}" height="20" rx="10" fill="{GREY_1}"/>
    <g transform="translate(372 {y})" fill="none" stroke="{RED}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round">
      <path d="M 18 16 C 18 -4 6 -12 -12 -12"/>
      <path d="M -2 -24 L -16 -12 L -2 0"/>
    </g>"""

    return f"""
<path d="M 1765 1210 Q 1876 1350 1832 1665" fill="none" stroke="{RED_DARK}" stroke-width="128" stroke-linecap="round"/>
<g transform="translate(1905 1060) rotate(7)">
  <g filter="url(#soft)">
    <rect x="-14" y="-6" width="458" height="670" rx="48" fill="{RED_DARK}"/>
  </g>
  <rect x="0" y="0" width="430" height="640" rx="40" fill="{PAPER}"/>
  <!-- spiral -->
  <g fill="{INK}">
    <rect x="60" y="-30" width="22" height="70" rx="11"/>
    <rect x="130" y="-30" width="22" height="70" rx="11"/>
    <rect x="200" y="-30" width="22" height="70" rx="11"/>
    <rect x="270" y="-30" width="22" height="70" rx="11"/>
    <rect x="340" y="-30" width="22" height="70" rx="11"/>
  </g>
  <text x="44" y="118" font-family="Avenir Next" font-weight="700" font-size="42" letter-spacing="4" fill="{INK}">JOURNAL</text>
  <text x="386" y="172" text-anchor="end" font-family="Avenir Next, Helvetica, Arial" font-weight="600" font-size="32" fill="{GREY_3}" >{DATE_LABEL}</text>
  <rect x="44" y="196" width="342" height="6" rx="3" fill="{GREY_1}"/>
  {rows}
  <!-- big undo badge -->
  <g transform="translate(430 646)">
    <circle r="86" fill="{INK}" filter="url(#softSmall)"/>
    <g fill="none" stroke="#FFFFFF" stroke-width="18" stroke-linecap="round" stroke-linejoin="round">
      <path d="M 34 30 C 34 -8 12 -24 -24 -24"/>
      <path d="M -2 -48 L -28 -24 L -2 0"/>
    </g>
  </g>
</g>
<!-- hand holding the journal -->
<circle cx="1829" cy="1680" r="70" fill="url(#gInk)" filter="url(#softSmall)"/>
<path d="M 1861 1642 Q 1881 1680 1861 1718" fill="none" stroke="#5A4E4A" stroke-width="10" stroke-linecap="round"/>
"""


def title() -> str:
    return f"""
<g transform="translate(2470 0)">
  <rect x="0" y="826" width="150" height="22" rx="11" fill="{RED}"/>
  <text x="-8" y="1080" font-family="Avenir Next" font-weight="700" font-size="236" letter-spacing="-4" fill="{INK}">Agent <tspan fill="{RED}">Ops</tspan></text>
  <text x="0" y="1210" font-family="Avenir Next" font-weight="500" font-size="78" fill="{GREY_4}">Careful. Helpful. Accountable.</text>
</g>"""


def character() -> str:
    # Order matters: back to front.
    return antenna() + body() + head() + shield_arm() + journal_arm()


def scene_svg() -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        + defs()
        + background()
        + '<g transform="translate(-90 0)">'
        + lineage()
        + paper_plane()
        + character()
        + "</g>"
        + title()
        + "</svg>"
    )


def avatar_svg() -> str:
    # Square crop around head and shoulders, no props.
    x, y, s = 860, 40, 1280
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="{x} {y} {s} {s}">'
        + defs()
        + f'<rect x="{x}" y="{y}" width="{s}" height="{s}" fill="{BG}"/>'
        + f'<circle cx="1500" cy="{y + s / 2}" r="{s * 0.47}" fill="{RED}" opacity="0.10"/>'
        + antenna()
        + body()
        + head()
        + "</svg>"
    )


def render(svg: str, name: str, width: int, height: int) -> None:
    (HERE / f"{name}.svg").write_text(svg, encoding="utf-8")
    png = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height)
    (HERE / f"{name}.png").write_bytes(bytes(png))


if __name__ == "__main__":
    render(scene_svg(), "agent-ops-character-4k", W, H)
    render(avatar_svg(), "agent-ops-avatar-1024", 1024, 1024)
    print("done")
