# 0019 One command, with subcommands

Decided 2026-09-03.

## Decision

`harness` is one command: `run`, `serve`, `threads`, `init`, `init-agents`,
`install-servers`, `evals`. The CLI is a package with one module per subcommand, a
terminal module for rendering, a person module for the two prompts, and one resolver that
settles flags, environment, config file and defaults for both `run` and `serve`. The
server package knows nothing about flags. The bare prompt is gone: it is `harness run
PROMPT`.

## Context

Four subcommands were flags on one command, and the parser had to say "a prompt is
required unless one of those is given". `harness "serve"` would be ambiguous, and treating
an unknown first word as a prompt is the magic subcommands exist to remove. The CLI and the
server resolved the provider separately, and the CLI refused a key that was in the config
file because it checked the flag and the environment first.

## Consequences

`harness evals` works from a checkout and says so plainly when it cannot: the evals are
deliberately not in the wheel. A console script does not put the working directory on
`sys.path`, so the command finds the checkout from the package's own location.
