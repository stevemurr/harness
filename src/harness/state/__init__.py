"""What a run carries, and a person controls or reads.

Five things, all state that outlives a turn and that both the agent and its tools reach --
which is why they sit below both packages and not inside either. Two are a person's to
set: `Approvals`, what may proceed without asking, and `Mode`, what the run may do at all.
Two are how things reach an agent: the `Inbox`, messages consumed as they arrive, and the
`Board`, units of work observed until someone finishes them. `Plan` is the agent's own
checklist, written by one tool and rendered for a person.
"""

from harness.state.approval import Approvals, Approver, Decision, Policy, Request
from harness.state.board import Board, MemoryBoard, Status, Task, board_id_for
from harness.state.inbox import Inbox, render
from harness.state.mode import NORMAL, PLAN, Mode, ModeState
from harness.state.plan import Plan, Step

__all__ = [
    "NORMAL",
    "PLAN",
    "Approvals",
    "Approver",
    "Board",
    "Decision",
    "Inbox",
    "MemoryBoard",
    "Mode",
    "ModeState",
    "Plan",
    "Policy",
    "Request",
    "Status",
    "Step",
    "Task",
    "board_id_for",
    "render",
]
