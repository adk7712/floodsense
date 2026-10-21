"""
FloodSense architecture diagram for the deck (B6): one SVG per theme, plus PNGs.

    python docs/architecture/make_diagram.py            # SVGs
    python docs/architecture/make_diagram.py --png      # also PNGs (needs Playwright + Chromium)

Facts come from DEPLOYMENT.md; edit the boxes here when the system changes.
"""

import argparse
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
W, H = 1600, 900
FONT = "Inter, 'Helvetica Neue', Arial, sans-serif"

THEMES = {
    "dark": {
        "bg": "#0B1220",
        "region": "#0F1A2E",
        "region_border": "#1F2D47",
        "card": "#14203A",
        "border": "#2A3B5C",
        "text": "#E5E7EB",
        "muted": "#94A3B8",
        "arrow": "#64748B",
        "accent": "#F97316",
        "databricks": "#FF5F46",
        "bronze": "#D08A4A",
        "silver": "#AAB6C8",
        "gold": "#EAB308",
    },
    "light": {
        "bg": "#FFFFFF",
        "region": "#F6F8FB",
        "region_border": "#D5DDE8",
        "card": "#FFFFFF",
        "border": "#C3CDDB",
        "text": "#0F172A",
        "muted": "#475569",
        "arrow": "#7C8BA1",
        "accent": "#EA580C",
        "databricks": "#E8331C",
        "bronze": "#B26A2E",
        "silver": "#64748B",
        "gold": "#B7860B",
    },
}


def card(x, y, w, h, title, lines, t, stripe=None, dashed=False, title_color=None):
    """A rounded box: bold title, then muted lines. ``stripe`` colours a left edge."""
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{t["card"]}" '
        f'stroke="{t["border"]}" stroke-width="1.5"{dash}/>'
    ]
    if stripe:
        out.append(
            f'<rect x="{x}" y="{y + 8}" width="4" height="{h - 16}" rx="2" fill="{stripe}"/>'
        )
    ty = y + 27
    out.append(
        f'<text x="{x + 16}" y="{ty}" font-size="17" font-weight="700" '
        f'fill="{title_color or t["text"]}">{escape(title)}</text>'
    )
    for i, line in enumerate(lines):
        out.append(
            f'<text x="{x + 16}" y="{ty + 23 + 20 * i}" font-size="14" fill="{t["muted"]}">'
            f"{escape(line)}</text>"
        )
    return "\n".join(out)


def arrow(points, t, label=None, label_at=None, dashed=False):
    """A polyline arrow through ``points``; optional small label at ``label_at``."""
    d = " ".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    out = [
        f'<polyline points="{d}" fill="none" stroke="{t["arrow"]}" stroke-width="2"'
        f'{dash} marker-end="url(#head)"/>'
    ]
    if label:
        lx, ly = label_at
        out.append(
            f'<text x="{lx}" y="{ly}" font-size="12.5" fill="{t["muted"]}" '
            f'text-anchor="middle">{escape(label)}</text>'
        )
    return "\n".join(out)


def heading(x, y, text, t, color=None, size=13):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="700" letter-spacing="1.2" '
        f'fill="{color or t["muted"]}">{escape(text.upper())}</text>'
    )


def diagram(t: dict[str, str]) -> str:
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
        "<defs>"
        f'<marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
        f'fill="{t["arrow"]}"/></marker></defs>',
        f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>',
    ]

    # --- Open data and reference (outside Databricks) -------------------------------------------
    p.append(heading(30, 70, "Open data · data.gov.sg", t))
    p.append(
        card(30, 112, 245, 86, "NEA rain radar", ["Rain-area image", "every 5 min (70 km)"], t)
    )
    p.append(
        card(30, 212, 245, 86, "NEA rain gauges", ["5-min readings", "~90 gauges island-wide"], t)
    )
    p.append(
        card(
            30,
            312,
            245,
            86,
            "PUB flash-flood alerts",
            ["Live only: the API", "keeps no history"],
            t,
        )
    )
    p.append(heading(30, 445, "Reference data", t))
    p.append(
        card(
            30,
            460,
            245,
            108,
            "In the repo",
            ["URA 2019 planning areas (55)", "88 verified flood events", "LTA MRT station exits"],
            t,
        )
    )
    p.append(heading(30, 612, "Model training", t))
    p.append(
        card(
            30,
            627,
            245,
            128,
            "MLflow experiment",
            [
                "60-min rain rule, calibrated",
                "selected on 2020–23 floods",
                "test 2024–26 scored once",
                "tiers set by alarm budgets",
            ],
            t,
            stripe=t["accent"],
        )
    )

    # --- Databricks region --------------------------------------------------------------------
    p.append(
        f'<rect x="305" y="35" width="1280" height="830" rx="16" fill="{t["region"]}" '
        f'stroke="{t["region_border"]}" stroke-width="1.5"/>'
    )
    p.append(heading(330, 70, "Databricks · Free Edition · serverless", t, color=t["databricks"]))

    # Lakeflow Jobs
    p.append(heading(330, 100, "Lakeflow Jobs", t, size=12))
    p.append(card(330, 112, 250, 86, "Daily archiver", ["03:00 SGT", "Radar frames + gauges"], t))
    p.append(
        card(
            330,
            212,
            250,
            86,
            "Rainfall poller",
            ["Every 30 min near the demo", "Lands data, pipeline, Telegram"],
            t,
        )
    )
    p.append(card(330, 312, 250, 86, "Flood-alert saver", ["Every 15 min", "Keeps PUB alerts"], t))

    # Volumes
    p.append(heading(610, 100, "Volumes", t, size=12))
    p.append(card(610, 112, 200, 86, "radar", ["Every frame since", "16 Jul 2026"], t))
    p.append(
        card(
            610, 212, 200, 186, "landing", ["Raw API pages,", "unchanged:", "rainfall + alerts"], t
        )
    )

    # Lakeflow Declarative Pipeline
    p.append(
        f'<rect x="840" y="88" width="345" height="430" rx="12" fill="none" '
        f'stroke="{t["border"]}" stroke-width="1.5" stroke-dasharray="4 4"/>'
    )
    p.append(heading(856, 112, "Lakeflow Declarative Pipeline", t, size=12))
    p.append(
        card(
            860,
            125,
            305,
            76,
            "Bronze · Auto Loader",
            ["Every payload, one row per file"],
            t,
            stripe=t["bronze"],
        )
    )
    p.append(
        card(
            860,
            225,
            305,
            96,
            "Silver · parse + expectations",
            ["Readings 0–100 mm, de-duplicated", "Bad payloads → quarantine"],
            t,
            stripe=t["silver"],
        )
    )
    p.append(
        card(
            860,
            345,
            305,
            150,
            "Gold · flood risk",
            [
                "flood_risk_predictions_gold",
                "Gauges → 55 zones (IDW)",
                "→ rain features → model",
                "→ Low / Moderate / High",
            ],
            t,
            stripe=t["gold"],
        )
    )

    # Model registry
    p.append(
        card(
            860,
            565,
            305,
            100,
            "Unity Catalog model registry",
            ["flood_model @champion", "Gold loads it on every run"],
            t,
            stripe=t["accent"],
        )
    )

    # Serving
    p.append(heading(1230, 100, "Serving", t, size=12))
    p.append(
        card(
            1230,
            112,
            330,
            176,
            "Databricks App",
            [
                "Streamlit, signed in via Databricks",
                "Live risk map for 55 zones",
                "Replay any day since 2017",
                "Rain radar + 1-hour nowcast",
                "Resident view: what to do",
            ],
            t,
            title_color=t["accent"],
        )
    )
    p.append(card(1230, 310, 330, 86, "AI/BI dashboard", ["Live risk by zone, from gold"], t))
    p.append(
        card(
            1230,
            418,
            330,
            106,
            "Telegram alerts",
            [
                "Posts when a zone's risk rises",
                "Runs with the poller task",
                "Poller paused until demo",
            ],
            t,
        )
    )

    # Unity Catalog governance band
    p.append(
        f'<rect x="330" y="700" width="1230" height="140" rx="12" fill="{t["card"]}" '
        f'stroke="{t["border"]}" stroke-width="1.5"/>'
    )
    p.append(heading(350, 730, "Unity Catalog · workspace.floodsense", t, color=t["databricks"]))
    chips = [
        "4 volumes",
        "8 pipeline tables",
        "Registered model + alias",
        "Descriptions + column comments",
        "Tags on every object",
        "Serverless SQL warehouse",
    ]
    x = 350
    for chip in chips:
        w = 18 + 8.2 * len(chip)
        p.append(
            f'<rect x="{x}" y="752" width="{w:.0f}" height="34" rx="17" fill="{t["region"]}" '
            f'stroke="{t["border"]}"/>'
            f'<text x="{x + w / 2:.0f}" y="774" font-size="14" fill="{t["text"]}" '
            f'text-anchor="middle">{escape(chip)}</text>'
        )
        x += w + 12
    p.append(
        f'<text x="350" y="818" font-size="14" fill="{t["muted"]}">'
        "All tables, volumes and the model live in one governed schema: lineage, access and "
        "descriptions in one place.</text>"
    )

    # --- Arrows -------------------------------------------------------------------------------
    p.append(arrow([(275, 155), (330, 155)], t))  # radar → archiver
    p.append(arrow([(275, 255), (330, 255)], t))  # gauges → poller
    p.append(arrow([(275, 355), (330, 355)], t))  # alerts → alert saver
    p.append(arrow([(580, 155), (610, 155)], t))  # archiver → radar volume
    p.append(arrow([(580, 185), (595, 185), (595, 232), (610, 232)], t))  # archiver → landing
    p.append(arrow([(580, 270), (610, 270)], t))  # poller → landing
    p.append(arrow([(580, 355), (610, 355)], t))  # alert saver → landing
    p.append(arrow([(810, 245), (832, 245), (832, 163), (860, 163)], t))  # landing → bronze
    p.append(arrow([(1012, 201), (1012, 225)], t))  # bronze → silver
    p.append(arrow([(1012, 321), (1012, 345)], t))  # silver → gold
    p.append(arrow([(1012, 565), (1012, 495)], t, "model", (1040, 535)))  # registry → gold
    p.append(arrow([(275, 691), (300, 691), (300, 615), (860, 615)], t, "registered", (720, 607)))
    p.append(arrow([(1165, 400), (1200, 400), (1200, 200), (1230, 200)], t))  # gold → app
    p.append(arrow([(1165, 420), (1210, 420), (1210, 353), (1230, 353)], t))  # gold → dashboard
    p.append(arrow([(1165, 460), (1230, 460)], t))  # gold → telegram
    p.append(
        f'<text x="1197" y="532" font-size="12.5" fill="{t["muted"]}" text-anchor="middle">'
        "via SQL warehouse</text>"
    )
    p.append(
        arrow(
            [(710, 112), (710, 80), (1395, 80), (1395, 112)],
            t,
            "radar frames for Replay and the nowcast",
            (1010, 73),
        )
    )

    p.append("</svg>")
    return "\n".join(p)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", action="store_true", help="also render PNGs with Playwright")
    args = parser.parse_args()
    svgs = {}
    for name, theme in THEMES.items():
        path = OUT / f"floodsense_architecture_{name}.svg"
        path.write_text(diagram(theme))
        svgs[name] = path
        print("wrote", path.name)
    if args.png:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
            for path in svgs.values():
                page.goto(path.as_uri())
                png = path.with_suffix(".png")
                page.screenshot(path=str(png), full_page=False)
                print("wrote", png.name)
            browser.close()


if __name__ == "__main__":
    main()
