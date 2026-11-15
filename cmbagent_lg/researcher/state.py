"""Graph state for the researcher loop (see `graph.py` for the wiring).

`current_report` is the raw markdown the LLM wrote — no structured output, the
report content IS the deliverable. `step_feedback_history` accumulates one entry
per goal-miss so the step_evaluator sees its own prior reasoning across retries.
"""

import operator
from typing import TypedDict, List, Optional, Annotated

from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.schemas import ClaimAudit, StepVerdict
from cmbagent_lg.timing import NodeTiming


class ResearcherState(TypedDict, total=False):
    # Input
    step: Step
    step_number: int  # drives the on-disk filename reports/step_{N}.md
    work_dir: Optional[str]
    # Set by deep_research when this step runs as part of a multi-step plan
    # (prior steps' code+output / reports + a workspace file manifest). Empty
    # for standalone researcher_graph invocations.
    previous_steps_execution_summary: Optional[str]

    # Retry budget
    attempts: int  # bumped by the researcher node at the start of each attempt

    # Researcher output
    current_report: str          # raw markdown string the LLM emitted
    report_path: str             # absolute path written to disk

    # step_evaluator — did the report ACHIEVE the sub-task's goal?
    current_step_verdict: StepVerdict
    # One entry per goal-miss; surfaced to the next step_evaluator pass.
    step_feedback_history: List[str]

    # claim_auditor — runs AFTER a fulfilled goal verdict; grades every
    # result-claim against the run's clear signal. Plain-replace channels.
    current_claim_audit: ClaimAudit
    claim_audit_attempts: int      # charged only on a revision request (VLM-style)
    claim_audits_logged: int       # monotonic index for per-audit log files
    claim_audit_failed: bool       # exhausted with a surviving HARD claim → fail step

    # Deterministic grounding (researcher/grounding.py). `claim_accusations` is
    # the livelock breaker: fingerprints of every claim already charged HARD
    # this step, so a re-charge of the same numbers is settled by the index
    # instead of another writer round-trip. `claim_hints` carries the step
    # evaluator's `suspected_fabrications` to the auditor. `source_index_key`
    # identifies the shared per-step index (work_dir + step + source digest).
    claim_audit_revise: bool       # the auditor asks for a writer round-trip (fresh hard claim + budget)
    claim_accusations: List[str]
    claim_hints: List[str]
    source_index_key: str

    # Bookkeeping
    node_elapsed_s: Annotated[List[NodeTiming], operator.add]
