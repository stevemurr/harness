# The Pebble virtual machine

A stack machine. A program is a text file, one instruction per line, `#` starts a comment,
blank lines are ignored. Labels are a name followed by `:` on a line of their own.

## Rules that apply everywhere

These hold for every instruction below. They are stated once, here.

1. **All arithmetic is 32-bit two's complement and wraps.** `2147483647 ADD 1` is
   `-2147483648`. Every arithmetic result is reduced into that range before it is pushed.
2. **Every error prints exactly `E<nn>: <message>` on its own line and stops the program.**
   Two digits, zero padded. Nothing else is printed after it. The process exits 1. The
   message is not yours to choose -- these are the only errors and these are their exact
   texts:

   | printed exactly | when |
   |---|---|
   | `E01: stack is empty` | popping a value when the stack is empty |
   | `E02: division by zero` | `DIV` or `MOD` with a divisor of zero |
   | `E03: variable is not set` | `LOAD` of a variable never `STORE`d |
   | `E04: unknown label` | a jump or call to a label that is not defined |
   | `E05: no call frame` | `RET` with nothing to return to |
   | `E06: unknown instruction` | an instruction this table does not list |
3. **`PRINT` writes the value followed by a newline.** Nothing else writes to output.
4. **The stack is empty at the start.** Popping an empty stack is error `E01`.

## Instructions

| instruction | effect |
|---|---|
| `PUSH n`   | push the integer `n` |
| `POP`      | discard the top |
| `DUP`      | push a copy of the top |
| `SWAP`     | exchange the top two |
| `ADD`      | pop b, pop a, push a+b |
| `SUB`      | pop b, pop a, push a-b |
| `MUL`      | pop b, pop a, push a*b |
| `DIV`      | pop b, pop a, push a/b truncated toward zero. b of 0 is error `E02` |
| `MOD`      | pop b, pop a, push the remainder, sign following a. b of 0 is error `E02` |
| `NEG`      | negate the top |
| `EQ`       | pop b, pop a, push 1 if a==b else 0 |
| `LT`       | pop b, pop a, push 1 if a<b else 0 |
| `GT`       | pop b, pop a, push 1 if a>b else 0 |
| `NOT`      | pop a, push 1 if a==0 else 0 |
| `LOAD k`   | push the value of variable `k`. Never assigned is error `E03` |
| `STORE k`  | pop and store into variable `k` |
| `JMP L`    | jump to label `L`. Unknown label is error `E04` |
| `JZ L`     | pop, jump to `L` if it is zero |
| `JNZ L`    | pop, jump to `L` if it is not zero |
| `CALL L`   | push the return address, jump to `L` |
| `RET`      | pop the return address and jump there. No frame is error `E05` |
| `PRINT`    | pop and print |
| `HALT`     | stop, exit 0 |

An unknown instruction is error `E06`. Running past the last line stops the program as if
`HALT`.

## Running the cases

`python3 run_cases.py` runs every case in `cases/` and prints how many passed. Each case is
`NN-name.pbl` with its expected output in `NN-name.out`. Do not edit anything in `cases/`
and do not edit `run_cases.py`.
