#!/bin/sh
set -eu
# The brief, the checker and the portrait are the question; a run that edits them has
# changed the question rather than answered it. Digests are committed literals.
if [ "$(shasum check_site.py | cut -d' ' -f1)" != "004b115d1407cf388d71b77d62d7a1750721f630" ]; then
    echo "FAILED: check_site.py has been changed" >&2
    exit 1
fi
if [ "$(shasum BRIEF.md | cut -d' ' -f1)" != "b4622ef1f5a28f70726711071281d7679db73110" ]; then
    echo "FAILED: BRIEF.md has been changed" >&2
    exit 1
fi
if [ "$(shasum assets/portrait.svg | cut -d' ' -f1)" != "790ae86edffb4bd607b54b5b21afcfc50ef2241a" ]; then
    echo "FAILED: assets/portrait.svg has been changed" >&2
    exit 1
fi
test -f index.html
test -f styles.css
# The stylesheet opens with the note the task asked for: a comment, before any rule.
test "$(head -c 2 styles.css)" = "/*"
# Prints `SCORE passed total` for partial credit, and fails unless every check passes.
python3 check_site.py
