Read `SPEC.md` and implement the Twig rendering engine in `render.py`, at the top level of
this folder.

`python3 render.py PAGE.html` reads one HTML file and prints its box tree to stdout.

There are 45 conformance cases in `cases/`. Run them with `python3 run_cases.py`, which
prints how many pass and shows a diff for the ones that do not. Keep working until all 45
pass.

Do not edit anything in `cases/` and do not edit `run_cases.py`. They are the specification
made executable; changing them changes the question rather than answering it.

This is a long task and the pieces depend on each other -- parsing feeds the cascade, the
cascade feeds layout, and a wrong answer in an early piece shows up as a wrong number in a
late one. Work through it in pieces, run the cases often, and let the failures tell you what
to do next.
