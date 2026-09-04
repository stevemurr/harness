"""The brief, made executable. `python3 check_site.py` from this folder.

Opens `index.html` in a headless browser and checks what the brief asks for: structure,
reachability, both colour schemes, both widths, no network, no errors. It does not judge
how the page looks -- nothing here can -- and instead writes four screenshots under
`.site-shots/` for a person to. Prints one line per check, then `SCORE passed total`, and
exits non-zero unless every check passed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
SHOTS = HERE / ".site-shots"

NAME = "Ada Okafor"
EMAIL = "ada@okafor.design"
PROJECTS = ("Tidewatch", "Ledgerline", "Field Notes")
SECTIONS = ("work", "about", "contact")

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: object, detail: str = "") -> None:
    checks.append((name, bool(ok), detail))


def finish() -> int:
    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {name}" + (f"  ({detail})" if detail and not ok else ""))
    print(f"SCORE {passed} {len(checks)}")
    return 0 if passed == len(checks) else 1


def channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[float, float, float]) -> float:
    red, green, blue = (channel(c) for c in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def parse_rgb(text: str) -> tuple[float, float, float] | None:
    found = re.findall(r"[\d.]+", text or "")
    if len(found) < 3:
        return None
    return (float(found[0]), float(found[1]), float(found[2]))


#: The body text's colour and the first opaque background behind it, walking up from the
#: first paragraph in `main` (or the body, if there is none).
COLOURS = """() => {
  const start = document.querySelector('main p') || document.body;
  const color = getComputedStyle(start).color;
  let node = start;
  let background = null;
  while (node) {
    const bg = getComputedStyle(node).backgroundColor;
    const alpha = bg.startsWith('rgba') ? parseFloat(bg.split(',')[3]) : 1;
    if (bg !== 'transparent' && alpha > 0) { background = bg; break; }
    node = node.parentElement;
  }
  return { color, background: background || 'rgb(255, 255, 255)' };
}"""


def main() -> int:
    if not INDEX.exists():
        check("index.html exists", False, "no index.html at the top level")
        return finish()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is needed: uv sync --extra browser && uv run harness install-browser")
        check("a browser to check with", False)
        return finish()

    SHOTS.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        def opened(width: int, height: int, dark: bool):
            context = browser.new_context(
                viewport={"width": width, "height": height},
                color_scheme="dark" if dark else "light",
            )
            page = context.new_page()
            external: list[str] = []
            errors: list[str] = []
            page.on(
                "request",
                lambda r: external.append(r.url)
                if not r.url.startswith(("file:", "data:", "blob:", "about:"))
                else None,
            )
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(INDEX.as_uri(), wait_until="load")
            page.wait_for_timeout(300)
            return context, page, external, errors

        # Laptop, light: structure and the light scheme.
        context, page, external, errors = opened(1280, 900, False)
        h1 = page.locator("h1")
        check("one h1, and it is her name", h1.count() == 1 and NAME in h1.first.inner_text())
        check("the title names her", NAME in page.title())
        check(
            "lang on html and a viewport meta",
            page.locator("html[lang]").count() == 1
            and page.locator('meta[name="viewport"]').count() == 1,
        )
        check(
            "nav links reach #work, #about and #contact, and each exists",
            page.locator("nav").count() >= 1
            and all(
                page.locator(f'nav a[href="#{s}"]').count() >= 1
                and page.locator(f"#{s}").count() == 1
                for s in SECTIONS
            ),
        )
        check("exactly one main", page.locator("main").count() == 1)
        cards = page.locator("#work article")
        check(
            "three project cards under #work, each named, each linked",
            cards.count() == 3
            and all(page.locator("#work article", has_text=name).count() == 1 for name in PROJECTS)
            and all(
                page.locator("#work article", has_text=name).locator("a[href^='https://']").count() >= 1
                for name in PROJECTS
            )
            and all(
                page.locator("#work article", has_text=name).locator("h2, h3, h4").count() >= 1
                for name in PROJECTS
            ),
            f"{cards.count()} cards",
        )
        portrait = page.locator('img[src$="assets/portrait.svg"]')
        check(
            "the portrait, with alt text",
            portrait.count() == 1 and (portrait.first.get_attribute("alt") or "").strip() != "",
        )
        check("every image has alt", page.locator("img:not([alt])").count() == 0)
        check(
            "a mailto link in #contact",
            page.locator(f'#contact a[href="mailto:{EMAIL}"]').count() >= 1,
        )
        check("headings in order: h1 before any h2, h2 before any h3", page.evaluate(
            """() => {
              const levels = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
                .map(h => parseInt(h.tagName[1]));
              let seen = 0;
              for (const level of levels) { if (level > seen + 1) return false; seen = Math.max(seen, level); }
              return levels.length > 0 && levels[0] === 1;
            }"""
        ))
        check("nothing fetched from the network", not external, "; ".join(external[:3]))
        check("no console errors", not errors, "; ".join(errors[:3]))
        light = page.evaluate(COLOURS)
        text, background = parse_rgb(light["color"]), parse_rgb(light["background"])
        check(
            "light scheme: text reads at 4.5:1 or better",
            text and background and contrast(text, background) >= 4.5,
            f"{light['color']} on {light['background']}",
        )
        check(
            "light scheme is light",
            background is not None and luminance(background) > 0.5,
            light["background"],
        )
        page.screenshot(path=str(SHOTS / "laptop-light.png"), full_page=True)
        context.close()

        # Phone, light: nothing spills sideways.
        context, page, _, _ = opened(390, 800, False)
        width = page.evaluate("document.scrollingElement.scrollWidth")
        check("no sideways scroll at 390px", width <= 390, f"document is {width}px wide")
        page.screenshot(path=str(SHOTS / "phone-light.png"), full_page=True)
        context.close()

        # Laptop and phone, dark.
        context, page, _, errors = opened(1280, 900, True)
        dark = page.evaluate(COLOURS)
        text, background = parse_rgb(dark["color"]), parse_rgb(dark["background"])
        check(
            "dark scheme is dark",
            background is not None and luminance(background) < 0.25,
            dark["background"],
        )
        check(
            "dark scheme: text reads at 4.5:1 or better",
            text and background and contrast(text, background) >= 4.5,
            f"{dark['color']} on {dark['background']}",
        )
        check("no console errors in dark", not errors, "; ".join(errors[:3]))
        page.screenshot(path=str(SHOTS / "laptop-dark.png"), full_page=True)
        context.close()
        context, page, _, _ = opened(390, 800, True)
        page.screenshot(path=str(SHOTS / "phone-dark.png"), full_page=True)
        context.close()
        browser.close()

    print(f"screenshots in {SHOTS}")
    return finish()


if __name__ == "__main__":
    sys.exit(main())
