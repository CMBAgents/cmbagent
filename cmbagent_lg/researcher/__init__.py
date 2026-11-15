"""Researcher sub-graph: prose steps inside deep_research plans.

A tiny parallel to `self_debug/` for `sub_task_agent == "researcher"` steps.
The researcher writes a markdown report; a `step_evaluator` gate decides whether
it addressed the sub-task, and a `claim_auditor` gate then checks that each
result-claim is backed by the run's evidence. Bounded retries via the same
`ctx.max_n_attempts` the engineer uses. No code execution, no
execution_evaluator, no escalation.

Grounding: by default (`researcher_mode="react"`) the report writer is a bounded
ReAct node with READ-ONLY workspace tools — the numbers a Results section reports
usually live in a saved artifact rather than in the prior step's stdout, and a
researcher that cannot open them invents them. Both gates judge the report
against that same evidence.
"""

from cmbagent_lg.researcher.graph import graph
from cmbagent_lg.researcher.state import ResearcherState

__all__ = ["graph", "ResearcherState"]
