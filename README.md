# harness

A coding-agent harness: one loop, real tools, and a person's approval before anything on
the machine changes. It runs any OpenAI-compatible model over a folder, from a terminal,
behind an HTTP server, or inside an editor that speaks the Agent Client Protocol, and it
keeps every run as an append-only transcript you can read, follow live, and resume.

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
| `harness acp` | the agent over the Agent Client Protocol on stdin and stdout, for an editor to run |
| `harness threads` | list recent threads, with delegated threads under their parent |
| `harness init` | write a starter `~/.harness/config.toml` |
| `harness init-agents` | write a starter `AGENTS.md` in the folder, read at the start of every run |
| `harness init-skill NAME` | write a starter skill under `.harness/skills/NAME`, offered to the model when it applies |
| `harness install-servers` | provision the language servers code search uses |
| `harness install-browser` | fetch the headless Chromium `open_url` falls back to, after `uv sync --extra browser` |
| `harness install-webkit` | build and install `wkrender`, the Safari engine `web_search` goes through (macOS) |
| `harness evals run` / `report` | the eval ladder, from a checkout of this repository |

`run`, `serve` and `acp` share the provider flags (`--model`, `--base-url`, `--api-key`,
`--context-window`, `--extra-body`, `--config`). A flag beats an environment variable
(`HARNESS_MODEL`, `HARNESS_BASE_URL`, `HARNESS_API_KEY`, `HARNESS_EXTRA_BODY`, ...) beats
the config file beats the built-in default, for every setting, in both commands.

## Configuration

`~/.harness/config.toml` has eight tables. Unknown tables and keys are errors, so a typo
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
max_turns = 0              # 0 means no limit; set one to cap a run
max_consecutive_refusals = 10

[approval]
policy = "ask"             # ask | edits | full-access; what a run gets unless the client says
always_allow = ["run:git", "run:uv"]   # never ask about these; a grant key, fnmatch allowed

[web]
user_agent = "Mozilla/5.0 (...) Chrome/151.0.0.0 Safari/537.36"   # a current browser's, or bot checks refuse
accept_language = "en-US,en;q=0.9"
block_private = true       # open_url and screenshot stay off this machine and its network
render = true              # fall back to a headless browser when a page needs one
webkit = ""                # wkrender, when it is not under ~/.harness/bin

[mcp.servers.files]        # a tool server, one table each; see "Tool servers" below
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

The file holds the API key on purpose: a server started at boot has nobody to prompt, and a
file only its owner can read beats an environment variable that leaks into every child
process. `init` writes it 0600, and a file readable by others is refused if it holds a key.

Everything the harness keeps lives under `~/.harness/`: `threads/` (one JSONL transcript per
thread), `boards/` (one work board per folder), `servers/` (language servers), `bin/`
(`wkrender`), `screenshots/`, and `processes/` (background command output).

## How it works

**The transcript is the state.** Resuming is replaying a transcript; persisting is storing
one; what the model sees is the transcript rendered for a provider. There is no second
representation to disagree with it.

**Tools.** Thirty, each an `Arguments` dataclass and a class with `spec` and `run`.
The dataclass is the schema the model sees; arguments are validated against it before
`run` is called, and `run` receives the class rather than a dict.

| group | tools |
|---|---|
| files | `read_file`, `write_file`, `edit_file`, `list_dir`, `glob`, `grep` |
| commands | `run`, `read_process`, `stop_process`, `monitor`, `read_monitor`, `stop_monitor` |
| web | `web_search`, `open_url`, `screenshot` |
| skills | `use_skill` |
| code search | `find_definition`, `find_references` (Python, Go and Swift, via language servers) |
| the person | `ask_user`, `update_plan`, `exit_plan_mode` |
| other agents | `delegate`, `wait_agents`, `tell_agent`, `read_agent`, `stop_agent`, `report` |
| the board | `post_task`, `list_tasks`, `claim_task`, `release_task`, `finish_task` |

**Approval, and no sandbox.** Reads are never asked about. What else asks is the approval
policy, which a person names -- `ask` about anything that changes the machine, `edits` to
let file writes in the folder through and still ask about commands, delegation and tool
servers, or `full-access` to ask about nothing. Any policy honours the standing rules in
`[approval] always_allow`, and answering "always" to a prompt adds one for the session:
for a command that is the program (`git`, not the command line), for a write tool every
write, and the prompt says which. `run` executes with your authority and is not sandboxed:
structured writes are contained to the folder and to unprotected paths, but nothing can see
inside `bash -c`. The boundary is the person reading the command.

**Skills.** A folder under `.harness/skills/<name>/` or `~/.harness/skills/<name>/` with a
`SKILL.md`: a name, a one-line description, and instructions, with any scripts and
references beside it. The model is told the names and descriptions at the start of a run
and reads a skill with `use_skill` when it applies, so a long skill costs one line of
context until it is needed; `pinned: true` puts one in the prompt from the start. A
message beginning `/name` invokes one. A skill's scripts run through the ordinary tools
under the ordinary approval policy, and a delegated agent sees the same skills as its
parent. Four ship with the harness -- `debugging`, `testing`, `architecture`, `design` --
and the folder's skills win a name clash with the person's, which win over those.

**Workflows.** A skill with `steps:` is one. Using it seeds the run's checklist with the
steps, so where the work stands shows in the plan a client already renders, and nothing
new holds that state. A skill with `triggers:` -- words like `bug` or `refactor` -- is
pointed out by the harness when a request names one, as a note through the inbox saying
which skill to read first, because a model that only has the index will sometimes not
look. The four built-ins have both.

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
A client sees each child as its own thing: `agent.started`, `agent.said`, `agent.finished`,
`agent.failed` and `agent.stopped` events bound its life and carry its words, and its
activity rows carry its `agent_id`, so a person watching can tell the child's work from
the parent's.

**The board.** Units of work with a status and an owner, one board per folder, durable
across runs and restarts. The agent reads it before it plans: an open task that is what
was asked for is claimed, a task someone else holds is left alone, a done task's result is
built on rather than redone. When the work has several pieces, the agent posts one task
per piece, claims each as it starts and finishes it saying what it did. Told to stop, it
releases what it holds with a note of where things stand rather than finishing it, so the
next run picks up from the note and not from a task that claims to be done. A person can
leave work for a run that has not started through the server. The board is not the plan:
the plan is one agent's checklist for the piece it is on, and does not survive the run.

**Three outcomes, not two.** A tool result is *ok*, *failed* (it could not do its job), or
*refused* (the harness declined). A non-zero exit is *ok*: the command ran and the answer
was negative. Only refusals count towards a stall, so a model watching its tests fail is
working and a model the harness keeps saying no to is stuck. Two loops are named for what
they are: a call that was refused and is made again unchanged, and a call that keeps
succeeding with the same answer -- the fourth identical answer in a row is replaced by a
refusal that says so, after the call was made, so a change in the world is never hidden.

**Background commands.** `run` with `background=true` answers at once with an id, and the
agent is told when the command ends. `read_process` shows what it has printed; with `wait`
set it blocks until the process exits, prints more, or the seconds run out, so waiting for
a build is one call rather than one per look. Stopping a process kills everything it
started, including children that put themselves in a process group of their own, as a
test runner's workers do.

**Bounded output.** One result is cut to 30k characters and one turn to 120k across all
its calls, keeping both ends so a test run's verdict survives. Tool calls run one at a
time. Every call gets an answer, even when the tool raises.

**Widening.** A thread works in one folder and may be given more: an editor's other
project folders at the start, or a folder added mid-thread through the server. The first
folder stays the working folder and the rest are reachable by absolute path. The addition
is a row in the transcript, so a resumed thread reaches the folder again, and it is
carried across compaction the way the person's own words are.

**Streaming.** A front end may listen to the model's words as they arrive; the terminal
prints them, the server and the editor forward them. The transcript is unchanged by it:
the loop still appends one whole message per turn, and a provider only streams when
someone is listening.

**The web.** `web_search` returns ranked results with snippets. When `wkrender` is
installed it loads DuckDuckGo's results page in a headless WKWebView presenting as this
Mac's Safari and parses that, the way talkie searches, because that is the one engine
the endpoint does not challenge: measured on a machine it was blocking, the fetch got the
anomaly page every time, headless Chromium got an error page, and WebKit got the results.
`wkrender` is a small Swift command in its own repository beside this one; `harness
install-webkit` builds it and puts it under `~/.harness/bin`. Without it, or when a render
fails, the search is one POST and one parse, and a challenge says how to install the
engine. `open_url` fetches one page and returns its main content as text, reader-mode
style, with links kept so the model can open what it finds. A long page is cut at a paragraph and says which `start` to call
again with for the rest, so nothing on a page is out of reach. A GitHub blob URL is read
as the raw file. The fetch sends what a browser sends when a person opens a page -- a
current Chrome's user agent, the navigation and client-hint headers that must agree with
it -- because bot checks read the whole request and answer a script-shaped one with a
challenge page. A page whose fetch reads as empty, or a site that answers with a bot check
anyway, is rendered in a headless Chromium, if one is installed (`uv sync --extra browser`,
then `harness install-browser`), and read the same way; the result says it was rendered and
why. The browser is a fallback and nothing else: every request a page makes is checked
against the same private-address guard as the fetch, images and fonts are not loaded,
downloads are refused, and a page gets fifteen seconds to settle. Without the browser, the
tool says the page needs one and how to install it.

`screenshot` uses the same browser to look at a page the agent is building: a URL, or an
HTML file in the folder, at a viewport it chooses, light or dark. The PNG goes under
`~/.harness/screenshots/` for a person to open. What the model gets back is a reading of
the page rather than the picture -- title, document width against the viewport, headings,
landmarks, images without alt text, the body's font and colours, console errors, requests
that failed -- because the transcript is text and a text-only model can act on every one of
those. A file page may load files from the working folder and nothing else on the disk.

**Tool servers.** Any MCP server named in `[mcp.servers.<name>]`, or handed over by an
editor, joins the registry: each of its tools is offered as `<name>__<tool>`, validated
against the schema the server sent, asked about before it runs unless the server marks it
read-only, and its result is fenced as someone else's text. An image a server returns is
written under `~/.harness/screenshots/` and the result says where. A server that is down is
logged and left out rather than keeping the agent from starting. stdio transport only, so
far.

The one worth naming is Chrome's own: `chrome-devtools-mcp` gives the agent a headless
Chrome it can drive, inspect and screenshot while it works on a page -- console messages,
network requests, an accessibility snapshot, a Lighthouse audit.

```toml
[mcp.servers.chrome]
command = "npx"
args = ["-y", "chrome-devtools-mcp@1.8.0", "--headless", "--isolated", "--viewport", "1280x900"]
```

Its tools take a `pageId` from `chrome__list_pages`; `--isolated` gives each run its own
profile, and `npx` fetches the package on first use. Only its listing tools say they are
read-only, so under `ask` a navigation or a screenshot prompts; a standing rule such as
`always_allow = ["mcp:chrome:*"]` lets them through.

## The editor

The same agent as an [Agent Client Protocol](https://agentclientprotocol.com) server:
JSON-RPC on stdin and stdout, one message per line, protocol version 1. Zed speaks it;
this is how to set it up there.

**1. Have a model configured.** The editor runs `harness acp` from the project folder with
your login shell's environment, so the same `~/.harness/config.toml` the terminal uses is
what it reads. `uv run harness init`, point `[provider]` at a model, and check it works
once from the terminal with `uv run harness run "say hello"`.

**2. Register the agent in Zed.** Either through the agent panel -- the `+` menu, *Add
Custom Agent* -- or by adding this to Zed's `settings.json` (`zed: open settings`), with
the path of this checkout:

```json
{
  "agent_servers": {
    "harness": {
      "type": "custom",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/harness", "harness", "acp"]
    }
  }
}
```

The provider flags work in `args` too -- `"--model", "gpt-4o"` -- and an API key can go in
`"env": {"HARNESS_API_KEY": "..."}` instead of the file. Zed reloads settings on save; no
restart. If `uv` is not on the PATH Zed inherits, give its absolute path.

**3. Open a thread.** In the agent panel, start a new thread and choose *harness*. The
first prompt opens a session, which is a thread under `~/.harness/threads/`, so the
editor can come back to it later and `harness threads` lists it beside the others.

**What you get.** The editor's mode picker is the person choosing between `normal` and
`plan`. Every approval is the editor's permission prompt, with the diff shown before a
write and *Allow*, *Always allow* and *Reject* mapped onto the same session grants as the
terminal's; the approval policy and standing rules from `[approval]` apply here too. The
plan is the editor's plan view. Tool calls appear as they run, with their output, and the
model's words stream as they are written. When the editor offers its buffers, `read_file`,
`write_file` and `edit_file` go through it, so the agent reads what you have not saved yet
and its edits land in the editor's review. A project of several folders gives the agent
the first as its working folder and the rest by absolute path. MCP servers configured in
Zed are handed to the session and join the tools beside the ones in `[mcp]`.

**Debugging.** Nothing but protocol goes to stdout; everything else -- logging, and any
stray print -- is on stderr, which Zed records. `dev: open acp logs` in Zed shows both
sides of the wire and that log. A session that never answers is usually a model that
cannot be reached: the same `harness run` from a terminal will say so in a sentence.

**Not there yet.** `ask_user` has nobody to ask in the editor, so the model is told to
decide for itself; images are not offered, so the editor does not send them; protocol
version 2 is a draft Zed does not speak.

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
| `GET` | `/api/v1/health`, `/api/v1/capabilities` | liveness; the protocol version, and the modes and approval policies a client may offer |
| `GET` `POST` | `/api/v1/workspaces` | registered folders |
| `GET` `POST` | `/api/v1/workspaces/{id}/tasks` | the folder's board |
| `GET` `POST` | `/api/v1/folders` | browse and create directories, for a picker |
| `GET` `POST` | `/api/v1/threads`, `/api/v1/threads/{id}` | conversations |
| `POST` | `/api/v1/threads/{id}/runs` | start a run; answers with a run id at once |
| `POST` | `/api/v1/threads/{id}/folders` | widen the thread to another folder, now and for every later run |
| `GET` | `/api/v1/runs`, `/api/v1/runs/{id}/events` | runs, and one run's event stream |
| `POST` | `/api/v1/runs/{id}/commands` | `pause`, `resume`, `cancel`, `stop`, `resolve_approval`, `answer`, `steer` |
| `GET` | `/watch`, `/watch/{thread}`, `/console` | browser pages, no build step |

`stop` is the command between `steer` and `cancel`: its `content` reaches the model as a
steer -- "write your work to the board" -- and the run then has two turns before the loop
ends it as cancelled, whatever it is doing. Asking the model to stop is not the same as
stopping it; the harness does the second.

Stopping the server ends every open stream within a moment with `stream.end` reason
`server_stopping`, which a client reads as "reconnect from the cursor", and any
connection that does not drain is closed after five seconds; runs in flight are cancelled
with their terminal event. A restart loses nothing a client needs. A thread's runs and their events are rebuilt from
its transcript when it is next opened -- the same run ids, the same rows, the same cursors
-- so a client that comes back after the server restarted finds the history it saw live.
Approvals, questions and pauses are live states and do not replay; a run that ended
without an answer replays as failed. A run's terminal `summary` is everything the model
said in that run, joined as it was streamed, so a client that replaces its streamed answer
with the summary shows the same text.

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
questioner, an observer, a store, and a spawner for child agents -- and a listener, if it
wants the words as they come. The terminal, the HTTP server and the editor are the three
that exist, and they share an unmodified core.

## Evals

The ladder under `evals/` grades behaviour: each rung is a task, a seed folder, and a
`verify.sh` that runs the artifact and exits zero only if the work was done. The fast suite
is seventeen rungs: from a greeting script to a cross-file rename over a 5,000-line
codebase, then five that flex one part of the harness each -- a board that already holds
work (`18-board`), a board shared by delegated agents (`19-board-agents`), three agents on
one repository through git worktrees (`20-worktrees`), a personal website from a brief
checked in a real browser (`21-site`), and a page that is nothing until its JavaScript
runs, served by the agent and read through `open_url`'s browser (`22-rendered`). The long
suite is five rungs of thirty minutes to several hours, including a layout engine built
to a spec, a fleet of sixteen packages with one planted bug each, and a native macOS media
player built against a mock server, whose checks compile a Swift package and so declare a
longer `verify_timeout` in their `rung.json`.

A rung's `rung.json` says what the task needs: `agents` for delegation, `board` for a
board the seed may pre-fill at `.harness/board.jsonl`, `setup` for a command the staged
folder runs first (a git history, which a checkout cannot hold), `local_web` for a page
the agent serves on this machine, and `verify_timeout`.

```sh
uv run harness evals run --repeat 3 --label name                              # every tool
uv run harness evals run --without find_definition,find_references --label ctl  # a control
uv run harness evals run --suite long --only 14-engine --max-turns 0 --label engine
uv run harness evals report evals/results/<sweep>/sweep.json [other-sweep.json]
```

A sweep writes `evals/results/<date>-<label>/` with a header saying what produced it: commit,
prompt hash, model, sampling, turn limit, and any tools withheld. Every sweep runs with every
tool; a tool's worth is measured by a control sweep that withholds it by name. `report`
compares two sweeps by rung, only where the pairing is honest, and names every header field
that differs first. Before any sweep, every
chosen rung's checks are run against its own unsolved seed and must fail. What the ladder
has shown, retractions included, is in `evals/FINDINGS.md`.

## Layout

```
src/harness/
  types.py  workspace.py  config.py  settings.py    the vocabulary
  jsonrpc.py  JSON-RPC over streams, one line each: the editor's wire and the tool servers'
  state/      approval, mode, plan, inbox, board    what a run carries
  agent/      the loop, the runner, compaction, and new_agent
  tools/      the tool contract, the kit, and every tool
  exec/       processes, monitors, and child agents
  symbols/    code search: the contract and the LSP backends
  providers/  the model contract and the OpenAI-compatible client
  store/      the transcript contract, the JSONL store, the board file
  server/     routes, runs, events, streams, and the pages
  acp/        the editor front end: sessions, the protocol's words, files through the editor
  mcp/        tool servers: what one is, and connecting to one
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
