# Working in this project

Run the tests with `python3 -m pytest -q` from the folder you are working in. The store is
a JSON file; every command takes `--store PATH` and the tests pass a temporary one.
Commands are registered in `COMMANDS` at the bottom of `notes/cli.py`.
