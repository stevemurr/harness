Build a personal website for the person described in `BRIEF.md`, in this folder: a single
page at `index.html`, with its CSS in `styles.css` beside it and no build step. It has to
work opened straight from disk, with nothing loaded from the network -- no CDN fonts, no
scripts from elsewhere, no analytics. The portrait is at `assets/portrait.svg`; use it.

The brief says what must be on the page and what a good version of it does. `check_site.py`
is the brief made executable: `python3 check_site.py` opens the page in a headless browser
at a laptop and a phone width, in light and dark, prints what passed and what did not, and
saves screenshots under `.site-shots/`. Do not edit it, `BRIEF.md`, or the portrait.

Make it look like a site a designer would put her name on, not a template: choose type,
colour and spacing on purpose, and say why in a short note at the top of `styles.css`.
Use the `screenshot` tool to check the page at 390 and 1280 pixels wide, light and dark,
and act on what it tells you. Before you answer, run `check_site.py` until it passes.
