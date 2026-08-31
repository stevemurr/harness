Create `wc.py` in this folder, at the top level. It counts a text file:

- `python3 wc.py -l FILE` prints the line count, `-w` words, `-c` characters.
- Flags may be combined: `-lw` prints lines then words, space separated, in the fixed order
  lines, words, characters regardless of the order the flags were given.
- With no flags it prints all three in that order.
- With no FILE, or with `-` as the FILE, it reads standard input instead.
- A final line with no trailing newline still counts as a line.
- Characters means bytes of the file as read, newlines included.

Print nothing else.
