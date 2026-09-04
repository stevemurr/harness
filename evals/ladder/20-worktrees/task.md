This folder is a git repository on `main` holding `notes`, a small note-taking CLI. Three
features are specified by tests that currently fail:

- `tests/test_search.py`: `notes search TERM` prints the notes whose text contains TERM,
  case-insensitively.
- `tests/test_tags.py`: `notes tag ID NAME` adds a tag to a note, and `notes list --tag
  NAME` shows only the notes with that tag.
- `tests/test_export.py`: `notes export FILE` writes the notes to FILE as Markdown.

Read the tests for what each must do exactly. Each feature is registered in `notes/cli.py`
and gets a line under `## Unreleased` in `CHANGELOG.md`, so all three touch the same two
files.

Build them in parallel, each by a delegated agent in its own git worktree, so nobody edits
the files someone else is editing:

1. For each feature, create a worktree under `worktrees/<feature>` on a new branch
   `feature/<feature>` from `main` (`git worktree add worktrees/search -b feature/search`).
2. Delegate one agent per feature with `wait=false`. Tell each which folder is its own,
   that it must edit and run tests only inside that folder (`cd worktrees/<feature> &&
   python3 -m pytest -q tests/test_<feature>.py`), and to commit its work on its branch
   there when its tests pass. Wait for all three with `wait_agents`.
3. Merge the three branches into `main` in this folder, resolving the conflicts in
   `notes/cli.py` and `CHANGELOG.md` so that all three features remain, and commit the
   merges.
4. Run the whole test suite on `main` (`python3 -m pytest -q`) and make it pass.
5. Remove the three worktrees (`git worktree remove worktrees/<feature>`); keep the
   branches.

Do not edit anything under `tests/`. Before you answer, check `git status` is clean on
`main` and `git worktree list` shows only this folder.
