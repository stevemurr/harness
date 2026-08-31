# Working in this project

Run the tests with `python3 -m pytest -q` from this folder. Do not answer until they
pass.

Steps live in `pipeline/steps.py`, names are bound to them in `pipeline/registry.py`,
and `pipeline/runner.py` walks a sequence. A change to how a step is named or built
usually touches all three.
