"""The ledger command: `list`, and `export` to a file."""

from __future__ import annotations

import argparse

from ledger.export_json import write_json
from ledger.records import read_records


def cmd_list(args: argparse.Namespace) -> int:
    for record in read_records(args.file):
        print(f"{record.date}  {record.account:<10} {record.amount:>9.2f}  {record.memo}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    records = read_records(args.file)
    if args.json:
        write_json(records, args.json)
        return 0
    print("say where to export: --json FILE")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    parser.add_argument("--file", default="ledger.txt", help="the ledger file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print every record").set_defaults(run=cmd_list)
    export = commands.add_parser("export", help="write the records to a file")
    export.add_argument("--json", metavar="FILE", help="write JSON to FILE")
    export.set_defaults(run=cmd_export)
    args = parser.parse_args(argv)
    return args.run(args)
