"""The brief, made executable. `python3 check_site.py` from this folder.

Opens `index.html` in a headless Safari engine (`wkrender`, which the harness installs
under `~/.harness/bin`) and checks what the brief asks for: structure, reachability, both
colour schemes, both widths, no network, no errors. It does not judge how the page looks
-- nothing here can -- and instead writes four screenshots under `.site-shots/` for a
person to. Prints one line per check, then `SCORE passed total`, and exits non-zero
unless every check passed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


#: Everything the checks need, read in the page in one go.
FACTS = """() => {
  const all = (s) => Array.from(document.querySelectorAll(s));
  const text = (n) => (n.textContent || '').trim();
  const has = (s, needle) => all(s).filter((n) => text(n).includes(needle));
  const start = document.querySelector('main p') || document.body;
  let node = start, background = null;
  while (node) {
    const bg = getComputedStyle(node).backgroundColor;
    const alpha = bg.startsWith('rgba') ? parseFloat(bg.split(',')[3]) : 1;
    if (bg !== 'transparent' && alpha > 0) { background = bg; break; }
    node = node.parentElement;
  }
  const levels = all('h1,h2,h3,h4,h5,h6').map((h) => parseInt(h.tagName[1]));
  let seen = 0, ordered = levels.length > 0 && levels[0] === 1;
  for (const level of levels) { if (level > seen + 1) ordered = false; seen = Math.max(seen, level); }
  const portrait = document.querySelector('img[src$="assets/portrait.svg"]');
  return {
    h1: all('h1').map(text),
    title: document.title,
    lang: !!document.querySelector('html[lang]'),
    viewport_meta: !!document.querySelector('meta[name="viewport"]'),
    nav: all('nav').length,
    nav_links: Object.fromEntries(['work', 'about', 'contact'].map((s) => [s, all('nav a[href="#' + s + '"]').length])),
    sections: Object.fromEntries(['work', 'about', 'contact'].map((s) => [s, all('#' + s).length])),
    main: all('main').length,
    cards: all('#work article').length,
    cards_named: Object.fromEntries(['Tidewatch', 'Ledgerline', 'Field Notes'].map((p) => {
      const found = has('#work article', p);
      return [p, {
        count: found.length,
        linked: found.some((c) => c.querySelector('a[href^="https://"]')),
        headed: found.some((c) => c.querySelector('h2, h3, h4')),
      }];
    })),
    portrait: portrait ? (portrait.getAttribute('alt') || '').trim() : null,
    images_without_alt: all('img:not([alt])').length,
    mailto: all('#contact a[href="mailto:ada@okafor.design"]').length,
    headings_ordered: ordered,
    color: getComputedStyle(start).color,
    background: background || 'rgb(255, 255, 255)',
    scroll_width: document.scrollingElement.scrollWidth,
    resources: performance.getEntriesByType('resource').map((e) => e.name),
  };
}"""


def wkrender() -> Path | None:
    home = Path("~/.harness/bin/wkrender").expanduser()
    if home.is_file():
        return home
    found = shutil.which("wkrender")
    return Path(found) if found else None


def render(binary: Path, width: int, height: int, dark: bool, shot: str) -> dict:
    argv = [
        str(binary), "--json", "--timeout", "20", "--viewport", f"{width}x{height}",
        "--eval", FACTS, "--png", str(SHOTS / f"{shot}.png"), "--full-page",
        "--files-under", str(HERE),
    ]
    if dark:
        argv.append("--dark")
    argv.append(INDEX.as_uri())
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"wkrender exited {done.returncode}")
    return json.loads(done.stdout)


def main() -> int:
    if not INDEX.exists():
        check("index.html exists", False, "no index.html at the top level")
        return finish()
    binary = wkrender()
    if binary is None:
        print("wkrender is needed: uv run harness install-webkit")
        check("a browser to check with", False)
        return finish()

    SHOTS.mkdir(exist_ok=True)
    try:
        laptop = render(binary, 1280, 900, False, "laptop-light")
        phone = render(binary, 390, 800, False, "phone-light")
        dark = render(binary, 1280, 900, True, "laptop-dark")
        _ = render(binary, 390, 800, True, "phone-dark")
    except RuntimeError as exc:
        check("the page loads in a browser", False, str(exc))
        return finish()

    facts = laptop["eval"]
    check("one h1, and it is her name", len(facts["h1"]) == 1 and NAME in facts["h1"][0])
    check("the title names her", NAME in facts["title"])
    check("lang on html and a viewport meta", facts["lang"] and facts["viewport_meta"])
    check(
        "nav links reach #work, #about and #contact, and each exists",
        facts["nav"] >= 1
        and all(facts["nav_links"][s] >= 1 and facts["sections"][s] == 1 for s in SECTIONS),
    )
    check("exactly one main", facts["main"] == 1)
    named = facts["cards_named"]
    check(
        "three project cards under #work, each named, each linked",
        facts["cards"] == 3
        and all(named[p]["count"] == 1 and named[p]["linked"] and named[p]["headed"] for p in PROJECTS),
        f"{facts['cards']} cards",
    )
    check("the portrait, with alt text", bool(facts["portrait"]))
    check("every image has alt", facts["images_without_alt"] == 0)
    check("a mailto link in #contact", facts["mailto"] >= 1)
    check("headings in order: h1 before any h2, h2 before any h3", facts["headings_ordered"])
    external = [r for r in facts["resources"] if not r.startswith(("file:", "data:", "blob:", "about:"))]
    check("nothing fetched from the network", not external, "; ".join(external[:3]))
    errors = list(laptop["errors"]) + list(laptop["failed"])
    check("no console errors and nothing failed to load", not errors, "; ".join(errors[:3]))
    text, background = parse_rgb(facts["color"]), parse_rgb(facts["background"])
    check(
        "light scheme: text reads at 4.5:1 or better",
        text and background and contrast(text, background) >= 4.5,
        f"{facts['color']} on {facts['background']}",
    )
    check("light scheme is light", background is not None and luminance(background) > 0.5, facts["background"])

    width = phone["eval"]["scroll_width"]
    check("no sideways scroll at 390px", width <= 390, f"document is {width}px wide")

    dark_facts = dark["eval"]
    text, background = parse_rgb(dark_facts["color"]), parse_rgb(dark_facts["background"])
    check("dark scheme is dark", background is not None and luminance(background) < 0.25, dark_facts["background"])
    check(
        "dark scheme: text reads at 4.5:1 or better",
        text and background and contrast(text, background) >= 4.5,
        f"{dark_facts['color']} on {dark_facts['background']}",
    )
    check("no console errors in dark", not (dark["errors"] or dark["failed"]), "; ".join(list(dark["errors"])[:3]))

    print(f"screenshots in {SHOTS}")
    return finish()


if __name__ == "__main__":
    sys.exit(main())
