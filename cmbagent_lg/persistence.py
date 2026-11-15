"""Local persistence for the planning and deep_research modules.

Only the final plan is written to disk — telemetry lives in langfuse. The plan is
the input to a downstream control phase, so its shape matches
`cmbagent/agents/planning/planner_response_evaluator.save_final_plan()`:

    {
      "sub_tasks": [
        {
          "sub_task": "...",
          "sub_task_agent": "...",
          "bullet_points": [...],
          "code_execution_timeout": null
        },
        ...
      ]
    }

ANALYSIS STATUS CONTRACT — `{work_dir}/logs/analysis_status.json`
------------------------------------------------------------------
Written by `save_analysis_status` at EVERY deep_research checkpoint (before step 1
runs, then after each step, next to `deep_research_run.json`), so a caller can tell
a finished analysis from a halted or still-running one WITHOUT parsing the run
summary or guessing from the shape of downstream files. Denario's results stage
previously had no such signal: a halted run's step-summary dump was written as
`results.md` and consumed as if it were a report (298/465 qm9-joint iterations).

    {
      "status":           "in_progress" | "halted" | "complete",
      "n_steps":          <int>   number of steps in the plan being executed,
      "n_outcomes":       <int>   number of steps that have finished (any verdict),
      "n_fulfilled":      <int>   number of finished steps judged fulfilled,
      "all_fulfilled":    <bool>  n_outcomes > 0 and every outcome fulfilled,
      "halted_at_step":   <int> | null   step_number of the FIRST unfulfilled step,
      "attempts":         <int> | null   engineer attempts on that step,
      "reason":           <str> | null   that step's outcome reason,
      "final_step_agent": <str> | null   agent of the plan's LAST step
                                         (Denario's report is written by a
                                         trailing "researcher" step),
      "flagged_steps":    [<int>, ...]   fulfilled steps whose report carries an
                                         advisory claim (numbers present in the
                                         run's outputs, attribution unconfirmed);
                                         additive — never changes `status`,
      "infeasible_steps": [<int>, ...]   steps completed with a requirement the step
                                         evaluator judged impossible in this
                                         environment and recorded as a substitution
                                         (`StepVerdict.infeasible_requirements`); the
                                         step's own outcome carries the list and the
                                         justification, and the step summary states
                                         what was not delivered. Additive — never
                                         changes `status`,
      "unverified_infeasible_steps":     the subset of those whose claim the engine
                        [<int>, ...]     could NOT check against the installed
                                         environment (no resolvable symbol named), so
                                         it rests on the evaluator's assertion alone.
                                         A verified absence is a fact and is NOT
                                         listed here; `max_unverified_infeasible_steps`
                                         caps how many of these one plan may pass on,
      "input_sourced_demotion_steps":    steps where a claim the deterministic grounding
                        [<int>, ...]     pass had charged as unsupported was demoted to
                                         advisory because a forced-choice classification,
                                         inside the envelope in `researcher/grounding.py`,
                                         called it a description of PROVIDED data rather
                                         than an asserted result. The step outcome and
                                         `logs/step_N_input_sourced_demotions.json` carry
                                         the quoted span and the justification. This is
                                         the only model judgement in the engine that can
                                         weaken a deterministic finding, so it is listed
                                         for audit. Additive — never changes `status`,
      "updated_at":       <ISO-8601 UTC timestamp>
    }

`status` is:
  * "complete"    ONLY when every step in the plan has an outcome AND every outcome
                  is fulfilled — the only state in which downstream may treat the
                  run's artifacts as a finished analysis;
  * "halted"      as soon as any step's outcome is unfulfilled. `halted_at_step` is
                  the FIRST unfulfilled step; with adaptive planning on, a step that
                  merely exhausted its attempt budget may be answered by rewriting the
                  remaining plan, so later steps can still have run after it (see
                  `deep_research.nodes._replannable_exhaustion`). The status stays
                  "halted" either way — the plan did not deliver every step, which is
                  the conservative signal a consumer needs;
  * "in_progress" otherwise — including a run that crashed or was killed mid-way,
                  since the file is only ever advanced by a checkpoint. A consumer
                  MUST treat anything other than "complete" as not done.

Adaptive planning can rewrite the not-yet-run tail, so `n_steps` reflects the plan
as of the latest checkpoint. The file is best-effort like the run summary: a
failed write is logged and never breaks the run.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from cmbagent_lg.planning.schemas import Plan

ANALYSIS_STATUS_FILENAME = "analysis_status.json"


def save_final_plan(plan: Plan, work_dir: str | Path) -> Path:
    """Write the final plan to `{work_dir}/planning/final_plan.json`. Returns the path."""
    out = Path(work_dir).expanduser() / "planning" / "final_plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(plan.model_dump_json(indent=2))
    return out


def load_final_plan(work_dir: str | Path) -> Plan:
    """Read the final plan back from `{work_dir}/planning/final_plan.json`.

    Inverse of `save_final_plan` — lets an out-of-process caller (e.g. the MCP
    server) execute or restart a previous plan without re-planning.
    """
    src = Path(work_dir).expanduser() / "planning" / "final_plan.json"
    if not src.is_file():
        raise FileNotFoundError(f"No saved plan at {src}")
    return Plan.model_validate_json(src.read_text())


def save_trace_id(trace_id: str, work_dir: str | Path) -> Path:
    """Write the langfuse trace ID to `{work_dir}/langfuse_trace_id.txt`.

    Consumed by `cmbagent-lg-cost <work_dir>`; the trace is also viewable at
    `{LANGFUSE_HOST}/project/<pid>/traces/<id>`.
    """
    out = Path(work_dir).expanduser() / "langfuse_trace_id.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(trace_id + "\n")
    return out


def default_work_dir() -> Path:
    """`./work_dir/{YYYY-mm-dd_HH-MM-SS}/` — cmbagent-style timestamped run dir."""
    return Path("work_dir") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def save_deep_research_summary(
    work_dir: str | Path,
    plan: Plan,
    step_outcomes: list,
    step_summaries: list | None = None,
) -> Path:
    """Write a deep_research run summary to `{work_dir}/logs/deep_research_run.json`.

    The plan that was executed plus each step's outcome, in one file, so a run
    can be inspected without grepping the per-step verdict files.
    """
    import json as _json
    out_dir = Path(work_dir).expanduser() / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "deep_research_run.json"
    out.write_text(
        _json.dumps(
            {
                "plan": plan.model_dump(),
                "n_steps": len(plan.sub_tasks),
                "outcomes": step_outcomes,
                "all_fulfilled": bool(
                    step_outcomes
                    and all(o.get("fulfilled") for o in step_outcomes)
                ),
                "step_summaries": step_summaries or [],
            },
            indent=2,
        )
    )
    return out


def analysis_status(plan: Plan, step_outcomes: list) -> dict:
    """The status record described in the module docstring, computed from the plan and
    the per-step outcomes (`{"step_number", "fulfilled", "attempts", "reason", ...}`).
    Pure; never raises on malformed outcomes (missing keys read as unfulfilled/None)."""
    outcomes = list(step_outcomes or [])
    sub_tasks = list(getattr(plan, "sub_tasks", None) or [])
    n_steps = len(sub_tasks)
    n_outcomes = len(outcomes)
    n_fulfilled = sum(1 for o in outcomes if isinstance(o, dict) and o.get("fulfilled"))
    halted: Optional[dict] = next(
        (o for o in outcomes if not (isinstance(o, dict) and o.get("fulfilled"))), None
    )
    all_fulfilled = n_outcomes > 0 and halted is None
    if halted is not None:
        status = "halted"
    elif all_fulfilled and n_steps > 0 and n_outcomes >= n_steps:
        status = "complete"
    else:
        status = "in_progress"

    def _get(key):
        return halted.get(key) if isinstance(halted, dict) else None

    halted_step = _get("step_number")
    if halted is not None and halted_step is None:
        halted_step = outcomes.index(halted) + 1
    reason = _get("reason")
    return {
        "status": status,
        "n_steps": n_steps,
        "n_outcomes": n_outcomes,
        "n_fulfilled": n_fulfilled,
        "all_fulfilled": all_fulfilled,
        "halted_at_step": halted_step,
        "attempts": _get("attempts"),
        "reason": str(reason) if reason is not None else None,
        "final_step_agent": (
            getattr(sub_tasks[-1], "sub_task_agent", None) if sub_tasks else None
        ),
        "flagged_steps": [
            o.get("step_number", i + 1) for i, o in enumerate(outcomes)
            if isinstance(o, dict) and o.get("fulfilled") and o.get("advisory_claims")
        ],
        "infeasible_steps": [
            o.get("step_number", i + 1) for i, o in enumerate(outcomes)
            if isinstance(o, dict) and o.get("infeasible_requirements")
        ],
        "unverified_infeasible_steps": [
            o.get("step_number", i + 1) for i, o in enumerate(outcomes)
            if isinstance(o, dict) and o.get("infeasible_requirements")
            and o.get("infeasibility_status") != "verified_absent"
        ],
        "input_sourced_demotion_steps": [
            o.get("step_number", i + 1) for i, o in enumerate(outcomes)
            if isinstance(o, dict) and o.get("input_sourced_demotions")
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def save_analysis_status(work_dir: str | Path, plan: Plan, step_outcomes: list) -> Path:
    """Write `{work_dir}/logs/analysis_status.json` (see the module docstring for the
    contract). Written atomically (tmp + rename) so a reader never sees a torn file."""
    out_dir = Path(work_dir).expanduser() / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ANALYSIS_STATUS_FILENAME
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(analysis_status(plan, step_outcomes), indent=2))
    tmp.replace(out)
    return out


def load_analysis_status(work_dir: str | Path) -> Optional[dict]:
    """Read the status record back, or None when absent/unreadable (an older run)."""
    src = Path(work_dir).expanduser() / "logs" / ANALYSIS_STATUS_FILENAME
    try:
        return json.loads(src.read_text())
    except Exception:  # noqa: BLE001 — absent or torn file reads as "unknown"
        return None


def prepare_work_dir(work_dir: str | Path, clear: bool = True) -> Path:
    """Resolve, optionally wipe, and (re)create a work_dir. Returns the Path.

    `clear=True` (default) empties it first: rerunning into an existing work_dir
    otherwise leaves stale artifacts (e.g. `codebase/step_2.py` from a longer
    previous run) that make the directory misleading.
    """
    wd = Path(work_dir).expanduser()
    if clear and wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True, exist_ok=True)
    return wd
