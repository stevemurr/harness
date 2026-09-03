# harness

A coding-agent harness: one loop, real tools, and a person's approval before anything on
the machine changes. It runs any OpenAI-compatible model over a folder, from a terminal or
behind an HTTP server, and it keeps every run as an append-only transcript you can read,
follow live, and resume.

```
while True:
    reply = await model(transcript)
    if not reply.tool_calls:
        break
    for call in reply.tool_calls:
        transcript.append(await run(call))
```

That is the entire control flow (`agent/loop.py`). Everything else is a tool, a provider
client, persistence, or a front end.

## Quick start

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev
uv run harness init                  # writes ~/.harness/config.toml, mode 0600
```

Point the config at a model:

```toml
[provider]
base_url = "http://localhost:4000/v1"
model = "qwen3.6"
api_key = ""                          # not needed for a local endpoint
context_window = 262144
```

Then run the agent in a folder:

```sh
uv run harness run "add a test for the parser"          # asks before anything changes
uv run harness run --plan "how should the cache work?"  # read-only until you approve a plan
uv run harness run -y "..."                              # approve everything; there is no sandbox
uv run harness run --resume <thread> "now do X"          # continue where it left off
uv run harness threads                                   # what has been run
```

## Commands

| command | does |
|---|---|
| `harness run PROMPT` | one exchange with the agent, in `-C FOLDER` (default: here) |
| `harness serve` | the HTTP server and the browser watch pages |
| `harness threads` | list recent threads, with delegated threads under their parent |
| `harness init` | write a starter `~/.harness/config.toml` |
| `harness init-agents` | write a starter `AGENTS.md` in the folder, read at the start of every run |
| `harness install-servers` | provision the language servers code search uses |
| `harness evals run` / `report` | the eval ladder, from a checkout of this repository |

`run` and `serve` share the provider flags (`--model`, `--base-url`, `--api-key`,
`--context-window`, `--extra-body`, `--config`). A flag beats an environment variable
(`HARNESS_MODEL`, `HARNESS_BASE_URL`, `HARNESS_API_KEY`, `HARNESS_EXTRA_BODY`, ...) beats
the config file beats the built-in default, for every setting, in both commands.

## Configuration

`~/.harness/config.toml` has five tables. Unknown tables and keys are errors, so a typo
cannot become a setting that silently does nothing.

```toml
[provider]
base_url = "http://localhost:4000/v1"
model = "qwen3.6"
api_key = "sk-..."
context_window = 262144
temperature = 0.7          # sampling, per the model's own card
top_p = 0.8
presence_penalty = 1.5
[provider.extra_body.chat_template_kwargs]   # merged into every request, for deployment
enable_thinking = false                      # dialect the OpenAI schema does not cover

[server]
host = "127.0.0.1"
port = 8080
token = ""                 # require a bearer token; empty means none

[compaction]
enabled = true
at = 0.5                   # share of the context window at which the agent hands off
keep_turns = 2

[output]
per_result = 30000         # characters one tool result may return
per_turn = 120000          # shared across every call in one turn

[limits]
max_turns = 100            # 0 means no limit
max_consecutive_refusals = 10
```

The file holds the API key on purpose: a server started at boot has nobody to prompt, and a
file only its owner can read beats an environment variable that leaks into every child
process. `init` writes it 0600, and a file readable by others is refused if it holds a key.

Everything the harness keeps lives under `~/.harness/`: `threads/` (one JSONL transcript per
thread), `boards/` (one work board per folder), `servers/` (language servers), and
`processes/` (background command output).

## How it works

**The transcript is the state.** Resuming is replaying a transcript; persisting is storing
one; what the model sees is the transcript rendered for a provider. There is no second
representation to disagree with it.

**Tools.** Twenty-eight, each an `Arguments` dataclass and a class with `spec` and `run`.
The dataclass is the schema the model sees; arguments are validated against it before
`run` is called, and `run` receives the class rather than a dict.

| group | tools |
|---|---|
| files | `read_file`, `write_file`, `edit_file`, `list_dir`, `glob`, `grep` |
| commands | `run`, `read_process`, `stop_process`, `monitor`, `read_monitor`, `stop_monitor` |
| web | `web_search`, `open_url` |
| code search | `find_definition`, `find_references` (Python, Go and Swift, via language servers) |
| the person | `ask_user`, `update_plan`, `exit_plan_mode` |
| other agents | `delegate`, `wait_agents`, `tell_agent`, `read_agent`, `stop_agent`, `report` |
| the board | `post_task`, `list_tasks`, `claim_task`, `finish_task` |

**Approval, and no sandbox.** Reads are never asked about. Anything that can change the
machine asks once; you can answer "always" for that program for the session, or set
standing rules. `run` executes with your authority and is not sandboxed: structured writes
are contained to the folder and to unprotected paths, but nothing can see inside
`bash -c`. The boundary is the person reading the command.

**Plan mode.** `harness run --plan` starts read-only. The agent reads, proposes a plan with
`exit_plan_mode`, and nothing changes until you approve it. A person sets the mode, never
the model. The mode is checked both when tools are offered and when one is dispatched, so a
tool the model invents or a resumed transcript carries cannot slip past it.

**Compaction.** At half the context window the agent writes a structured handoff note and
carries on from it. Nothing is removed from the transcript; the note is one more row in it,
and the render the provider sees pins the user's own words across the boundary. Measured on
a 681-turn run: two handoffs, each under 5k characters replacing over 500k, and the run
finished 45 of 45 cases.

**Delegation.** A parent may hand a self-contained task to a child agent with `delegate`,
wait for it or be told when it finishes, and speak to it mid-run. A child inherits the
folder, the approvals and the mode, owns its own plan, tools, inbox and thread, and cannot
delegate in turn. Whether a model reaches for this is measured in the evals, not assumed.

**The board.** Units of work with a status and an owner, one board per folder, durable
across runs. Agents post, claim and finish tasks by their own identity; a person can leave
work for a run that has not started through the server.

**Three outcomes, not two.** A tool result is *ok*, *failed* (it could not do its job), or
*refused* (the harness declined). A non-zero exit is *ok*: the command ran and the answer
was negative. Only refusals count towards a stall, so a model watching its tests fail is
working and a model the harness keeps saying no to is stuck.

**Bounded output.** One result is cut to 30k characters and one turn to 120k across all
its calls, keeping both ends so a test run's verdict survives. Tool calls run one at a
time. Every call gets an answer, even when the tool raises.

## The server

```sh
uv run harness serve --port 8080         # --token, or [server] token, to require a bearer token
```

The same agent behind HTTP. A run is a background task with an append-only event log; a
client follows it over server-sent events and reconnects from any cursor, and the same
cursor always yields the same suffix. Closing a client is not cancelling: a run parked on
an approval waits for the person, and approvals, questions and steering arrive as commands.

| method | path | |
|---|---|---|
| `GET` | `/api/v1/health`, `/api/v1/capabilities` | liveness and protocol version |
| `GET` `POST` | `/api/v1/workspaces` | registered folders |
| `GET` `POST` | `/api/v1/workspaces/{id}/tasks` | the folder's board |
| `GET` `POST` | `/api/v1/folders` | browse and create directories, for a picker |
| `GET` `POST` | `/api/v1/threads`, `/api/v1/threads/{id}` | conversations |
| `POST` | `/api/v1/threads/{id}/runs` | start a run; answers with a run id at once |
| `GET` | `/api/v1/runs`, `/api/v1/runs/{id}/events` | runs, and one run's event stream |
| `POST` | `/api/v1/runs/{id}/commands` | `pause`, `resume`, `cancel`, `resolve_approval`, `answer`, `steer` |
| `GET` | `/watch`, `/watch/{thread}`, `/console` | browser pages, no build step |

`/watch` tails the stored transcript rather than the in-memory event log, so a run started by
another process, an eval for instance, is watchable too.

## Extending it

**A tool** is an arguments class, a tool class, and one line in `tools/kit.py`:

```python
@dataclass(frozen=True, slots=True)
class Counted(Arguments):
    path: Annotated[str, "A file in the workspace."]

@dataclass(frozen=True, slots=True)
class WordCount:
    spec = spec_for(Counted, name="word_count", description="Count words in a workspace file.")

    async def run(self, args: Counted, ctx: ToolContext, /) -> ToolResult:
        return ToolResult(str(len(ctx.paths.read(args.path).split())))
```

Paths are resolved by `ctx.paths`, never by the tool, which is what keeps a tool inside the
folder.

**A provider** implements `Provider` in one file under `providers/`: a transcript and the
tools in, a `Completion` out. Wire shapes live there and nowhere else.

**A store** implements `Store`: `create`, `append`, `load`, `threads`. The conformance suite
in `tests/test_store.py` runs against every implementation.

**A front end** supplies five things, all plain callables or protocols: an approver, a
questioner, an observer, a store, and a spawner for child agents. The terminal and the HTTP
server are the two that exist, and they share an unmodified core.

## Evals

The ladder under `evals/` grades behaviour: each rung is a task, a seed folder, and a
`verify.sh` that runs the artifact and exits zero only if the work was done. The fast suite
is twelve rungs from a greeting script to a cross-file rename over a 5,000-line codebase; the
long suite is four rungs of thirty minutes to several hours, including a layout engine built
to a spec and a fleet of sixteen packages with one planted bug each.

```sh
uv run harness evals run --both --repeat 3 --label name          # both arms: with and without the extra tools
uv run harness evals run --suite long --only 14-engine --max-turns 0 --label engine
uv run harness evals report evals/results/<sweep>/sweep.json [other-sweep.json]
```

A sweep writes `evals/results/<date>-<label>/` with a header saying what produced it: commit,
prompt hash, model, sampling, turn limit. `report` compares two sweeps only where the
pairing is honest and names every header field that differs first. Before any sweep, every
chosen rung's checks are run against its own unsolved seed and must fail. What the ladder
has shown, retractions included, is in `evals/FINDINGS.md`.

## Layout

```
src/harness/
  types.py  workspace.py  config.py  settings.py    the vocabulary
  state/      approval, mode, plan, inbox, board    what a run carries
  agent/      the loop, the runner, compaction, and new_agent
  tools/      the tool contract, the kit, and every tool
  exec/       processes, monitors, and child agents
  symbols/    code search: the contract and the LSP backends
  providers/  the model contract and the OpenAI-compatible client
  store/      the transcript contract, the JSONL store, the board file
  server/     routes, runs, events, streams, and the pages
  cli/        the harness command and its subcommands
  prompts/    the system prompt, with attribution
evals/        rungs, the runner, results, and findings
tests/
```

Every module opens with why it is shaped the way it is, including the incident that shaped
it. `docs/adr/` holds the decision records, one per decision with what forced it;
`evals/DESIGN.md` records what was reasoned through and not built; `evals/FINDINGS.md` is
what the ladder has shown, retractions included.

## Development

```sh
uv run pytest -q
uv run ruff check src tests evals
uv run basedpyright
```

The package is type-checked under basedpyright's recommended ruleset with no suppressions,
and formatted to ruff's rules with explicit `+` for long strings. Both are meant to stay at
zero.

## Status

Early, single-user, and used daily against a local model. There is no sandbox; the design
assumes a person reading approvals. Multi-agent delegation and the board are built and
tested, and their value is still being measured on the ladder.
