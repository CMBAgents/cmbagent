"""Graph state — the mutable bag that flows between nodes (see `graph.py`).

`raw_*` are the generators' free-form prose; `current_*` are the typed
formatter outputs. `history` accumulates one `(plan, review)` pair per
completed review cycle, so each planner pass sees every prior round.
"""

import operator
from typing import TypedDict, List, Tuple, Optional, Annotated
from cmbagent_lg.planning.schemas import Plan, Review
from cmbagent_lg.timing import NodeTiming


class PlanState(TypedDict, total=False):
    raw_plan: str
    current_plan: Plan
    raw_review: str
    current_review: Optional[Review]
    history: List[Tuple[Plan, Review]]
    round: int
    # appended per node pass via @timed_node; concatenated by operator.add
    node_elapsed_s: Annotated[List[NodeTiming], operator.add]
