"""Graph state for the deep_research orchestrator (see `graph.py` for the wiring).

`step_summaries` accumulates one block (header + executed code + stdout, or the
report) per *completed* step. Combined with a freshly-scanned workspace file
manifest, this is what the agent at step N+1 sees in its prompt so it can
`from step_1 import …` or load `data/foo.csv`. `step_outcomes` is the structured
per-step result reported at the end and consulted by the router.

The adaptive-planning fields are only set when `PlanContext.adaptive_planning` is
on; `adaptive_review` early-returns otherwise:

    adaptive_review   the latest reviewer verdict (routes the conditional edge)
    raw_tail          the replanner's PROSE tail, before the formatter structures it
    new_tail          the structured replanned tail, before it is merged
    adaptive_history  one record per applied revision {after_step, recommendations, …}
"""

import operator
from typing import TypedDict, List, Optional, Annotated

from cmbagent_lg.planning.schemas import Plan
from cmbagent_lg.deep_research.schemas import AdaptiveReview
from cmbagent_lg.timing import NodeTiming


class DeepResearchState(TypedDict, total=False):
    # Input / mutable plan (the tail may be rewritten mid-run when adaptive).
    plan: Plan
    work_dir: Optional[str]

    # 1-based plan-step pointer.
    step_index: int

    # Accumulated across the loop.
    step_summaries: List[str]   # one block per completed step
    step_outcomes: List[dict]   # {step_number, fulfilled, attempts, escalated, reason?}

    # Adaptive-review branch (only set when adaptive planning is enabled).
    adaptive_review: Optional[AdaptiveReview]
    raw_tail: Optional[str]
    new_tail: Optional[Plan]
    adaptive_history: List[dict]

    # Per-node wall-clock, concatenated by operator.add.
    node_elapsed_s: Annotated[List[NodeTiming], operator.add]
