"""The notes command. Each subcommand is a function taking the parsed arguments."""

from __future__ import annotations

import argparse

from notes import store


def line(note: dict) -> str:
    tags = " ".join(f"#{tag}" for tag in note["tags"])
    return f"{note['id']:>3}  {note['text']}" + (f"  {tags}" if tags else "")


def cmd_add(args: argparse.Namespace) -> int:
    note = store.add(args.store, args.text)
    print(line(note))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for note in store.load(args.store):
        print(line(note))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="notes")
    parser.add_argument("--store", default="notes.json", help="the notes file")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add a note")
    add.add_argument("text")
    add.set_defaults(run=cmd_add)

    listing = commands.add_parser("list", help="show every note")
    listing.set_defaults(run=cmd_list)

    args = parser.parse_args(argv)
    return args.run(args)


#: Every subcommand, by name. Read by the help text and by the tests.
COMMANDS = {
    "add": cmd_add,
    "list": cmd_list,
}
