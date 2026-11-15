"""Recognise a code-gate failure that is a fact about the DATA, not a bug in the code.

`StepVerdict.infeasible_requirements` gives the GOAL gate a way to say "this cannot be
done here". The CODE gate has no such concept and cannot have one: a crashed attempt
never produces a `StepVerdict`, so no field on that schema can reach it. Its only
hatches are `missing_module` / `renamed_api` → `escalation` and an absent input →
`missing_input`, which leaves a model that cannot be fitted on this cohort as an
ordinary crash, retried until the budget is gone. This module names that failure; the
plan-level backstop for it is `deep_research._step_exhausted`, which makes an exhausted
step replannable regardless of which gate rejected it. Measured on metabric Project4 step 5: four of eight attempts died at the
code gate, three of them with the same lifelines
`ConvergenceError('delta contains nan value(s)')` raised from
`_newton_raphson_for_efron_model`, because the clusters x INTCLUST interaction design is
rank-deficient on 1,392 samples. No amount of retrying makes 1,392 samples fill those
cells; the PLAN had to change, and nothing in the engine said so.

Two deterministic jobs, no model call:

  1. COUNT it, quietly. Each code-gate failure is matched against the markers and
     tallied per marker in `numeric_fault_seen`. Seeing one numerical fault is not a
     conclusion — the next attempt often fixes it with a penalizer, a simpler design
     matrix or a dropped interaction term — so nothing is claimed yet and nothing is
     written to the log.
  2. CONCLUDE, once the SAME marker has come back `max_data_infeasibility_repeats`
     times. Only then does the engine state it: `execution_infeasible_signal` on state
     and `logs/step_{N}_infeasible_execution.json` on disk, and the step stops.
     `deep_research` then routes it to the adaptive replanner (NOT to the terminal
     missing-input path): a requirement the data cannot support is exactly what
     rewriting the plan is for.

The split matters for more than tidiness. A record in a feasibility-named file has to
mean the engine DREW that conclusion. Three DIFFERENT failures in a row — a
ConvergenceError, then a pandas `KeyError`, then an `UnboundLocalError` — is an engineer
making real progress through three problems, and writing "the data cannot support this"
on the first of them would be both wrong and the thing a later reader believes. The
counter is per MARKER for the same reason: two different numerical faults are two
problems, each of which deserves its own retries.

CONSERVATIVE: the markers are numerical-failure phrases that no ordinary logic bug
produces, matched case-insensitively in stderr/stdout; a `KeyError`, a shape mismatch, a
typo or a timeout is none of them and keeps the full retry budget. A miss costs what the
engine spends today; a false positive would end a step that another attempt could have
fixed, which is why recurrence is required rather than a single hit.
"""

from __future__ import annotations

import json
import sys
from typing import Dict, Optional, Tuple

from cmbagent_lg.self_debug.state import DebugState


# Phrases that mean "the numerics could not be carried out on these numbers". Each is
# raised by a fitting/decomposition routine about its INPUT, never by a syntax or
# control-flow mistake. Mapped to the short label that goes on the record.
_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("delta contains nan", "non_convergence"),
    ("convergenceerror", "non_convergence"),
    ("failed to converge", "non_convergence"),
    ("did not converge", "non_convergence"),
    ("convergence halted", "non_convergence"),
    ("matrix is singular", "singular_design"),
    ("singular matrix", "singular_design"),
    ("rank-deficient", "singular_design"),
    ("rank deficient", "singular_design"),
    ("not positive definite", "singular_design"),
    ("perfect separation", "perfect_separation"),
    ("perfectly separated", "perfect_separation"),
    ("too sparse", "insufficient_data"),
    ("too few events", "insufficient_data"),
    ("not enough observations", "insufficient_data"),
)


def classify(stderr: Optional[str], stdout: Optional[str] = None) -> Optional[str]:
    """`"<label>: <phrase>"` for the first data-infeasibility marker in the run's
    output, or None. stdout is scanned too: the engineer often catches the exception and
    prints it, exiting 0 with empty stderr."""
    text = ((stderr or "") + "\n" + (stdout or "")).lower()
    if not text.strip():
        return None
    for phrase, label in _MARKERS:
        if phrase in text:
            return f"{label}: {phrase}"
    return None


def observe(state: DebugState, verdict, ctx=None) -> DebugState:
    """State update for one code-gate FAILURE: the marker tally, and — only once a
    marker has recurred to the threshold — the engine's conclusion.

    `{}` on every other path, so a run that never hits a numerical fault is
    byte-for-byte unchanged. Never raises.
    """
    try:
        if getattr(verdict, "status", None) != "failure":
            return {}
        marker = classify(state.get("execution_stderr"), state.get("execution_stdout"))
        if marker is None:
            return {}
        attempt = state.get("attempts", 0)
        seen: Dict[str, list] = {k: list(v) for k, v in
                                 (state.get("numeric_fault_seen") or {}).items()}
        seen.setdefault(marker, []).append(attempt)
        out: DebugState = {"numeric_fault_seen": seen}

        n = state.get("step_number", 1) or 1
        count = len(seen[marker])
        limit = getattr(ctx, "max_data_infeasibility_repeats", 3) if ctx is not None else 3
        if count < limit:
            # Observed, not concluded: the next attempt may well get past it.
            print(f"[data_infeasibility] step {n} attempt {attempt}: {marker} "
                  f"(seen {count}x of {limit}) - retrying, the engineer may fix it",
                  file=sys.stderr, flush=True)
            return out
        print(f"[data_infeasibility] step {n} attempt {attempt}: {marker} came back "
              f"{count}x - concluding this is a property of the DATA, not a code bug",
              file=sys.stderr, flush=True)
        _record(state, marker, seen[marker])
        out["execution_infeasible_signal"] = marker
        return out
    except Exception as e:  # noqa: BLE001 - a classifier must never break the graph
        print(f"[data_infeasibility] classification skipped (non-fatal): {e}",
              file=sys.stderr, flush=True)
        return {}


def _record(state: DebugState, marker: str, attempts: list) -> None:
    """Write `logs/step_{N}_infeasible_execution.json` - the engine's CONCLUSION, so
    written once it is drawn and never for a single observation. The filename says what
    was concluded, so a later reader (or the paper stage) does not have to infer "the
    data could not support this" from a repeated traceback. Never raises."""
    try:
        from cmbagent_lg.self_debug.nodes import _logs_dir

        logs = _logs_dir(state)
        if logs is None:
            return
        path = logs / f"step_{state.get('step_number', 1) or 1}_infeasible_execution.json"
        path.write_text(json.dumps({
            "step_number": state.get("step_number", 1),
            "sub_task": getattr(state.get("step"), "sub_task", None),
            "marker": marker,
            "seen_on_attempts": list(attempts),
            "conclusion": "the run failed on a property of the DATA (not a code fault), "
                          "and the identical fault returned after the engineer's "
                          "attempt(s) to fix it; retrying the same design cannot help",
            "stderr_tail": (state.get("execution_stderr") or "")[-800:],
        }, indent=2))
    except Exception as e:  # noqa: BLE001
        print(f"[data_infeasibility] could not write the record (non-fatal): {e}",
              file=sys.stderr, flush=True)


def retries_spent(state: DebugState, ctx) -> bool:
    """Has the SAME numerical fault now come back often enough to stop retrying?

    `max_data_infeasibility_repeats` (default 3) counts observations of ONE marker: the
    first reveals the fault, the next two are the engineer's chances to get past it (a
    penalizer, a simpler design, pooled categories), and a third identical traceback is
    the engine buying nothing - the shape metabric Project4 step 5 produced. False
    whenever the feature is off, no marker was seen, or the budget remains.
    """
    if not getattr(ctx, "stop_on_repeated_data_infeasibility", True):
        return False
    seen = state.get("numeric_fault_seen") or {}
    if not seen:
        return False
    limit = getattr(ctx, "max_data_infeasibility_repeats", 3)
    return any(len(v) >= limit for v in seen.values())


__all__ = ["classify", "observe", "retries_spent"]
