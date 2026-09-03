# 0020 Method: what to believe, and when

Learned 2026-09-01 and 2026-09-02, the hard way. Recorded 2026-09-03.

## Decisions

- A rung seeded from live source asserts properties, never tallies, and now seeds from a
  frozen copy. A count went stale mid-session and a rung failed ten of ten for a reason
  unrelated to the model; a package split did the same to three rungs' file paths.
- Check every rung in three directions: fails unsolved, passes solved, fails the plausible
  wrong solution. The runner enforces the first before any sweep.
- Read transcripts before believing anything derived from counts. A refused-then-retried
  call read as diligence; a best-versus-worst pairing produced a claim repetition cut by
  two thirds; a 45-minute stall that looked like a provider drop was a `curl` holding a pipe.
- A comment saying what the code should do is not the code doing it. `_terminate` carried a
  correct comment about killing the process group for a month while killing one process.
- When a fix can kill the test runner, it can kill anything: a signal that can reach the
  harness's own group gets a guard and a test.
- `! cmd` is exempt from `set -e`. Every inverted shell check is `if ... then exit 1`.
- Prove a test can fail before trusting it.
- One variable per run. Broken repeatedly out of impatience, and every time it made the
  result harder to read.
- A number at n=1 is a direction. Say so wherever it is quoted.

## Still open, carried from the last working note

- A tool call emitted as prose -- `<tool_call>` text with an empty parsed call list -- is
  scored as a capability failure. Detection is trivial and the fix is feedback at the moment
  of the mistake (0009). Not done.
- Plan shape via schema rather than prose: a required per-step field naming its check would
  make the catch-all step unsubmittable. The one idea 0004 leaves untried.
- Three provider stalls from before 2026-09-01 remain unexplained; the evidence that pointed
  at them turned out to be 0010. Do not start from `keepalive_expiry`, which was already in
  force.
