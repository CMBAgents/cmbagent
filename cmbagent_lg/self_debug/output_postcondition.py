"""Declared-output post-condition — the deterministic half of the goal gate.

Runs between `executor` and `execution_evaluator`, with `ast`-free text parsing and no
model call, and asks one question of the finished run: the sub-task named an output
artifact by PATH — "save the cleaned cohort to `data/cohort.parquet`" — is that file
actually there?

A clean exit with the file missing is otherwise discovered only by `step_evaluator`,
i.e. after the execution verdict AND the step verdict have both been paid for, and the
engineer then gets "the required file was not produced" from a model instead of from the
filesystem. The same bounce costs nothing here, and names the exact path.

Shaped like `static_preflight`'s refusal — the executor's result is rewritten into a
failure (`execution_returncode=1`, the diagnosis appended to stderr) — but it does NOT
hand the verdict to `execution_evaluator`. Whether a declared file is on disk is a
FACT, so the node writes the `ExecutionVerdict` itself, appends the `error_history`
entry and demotes `step_{N}.py`/`.log` exactly as the code gate would, and
`route_after_output_postcondition` sends the attempt straight back to the engineer
(or to END at the attempt cap, so the bounce stays bounded by `max_n_attempts`).
Asking a model to confirm a `stat()` call would cost one round-trip per bounce for
nothing, and would make a deterministic bounce depend on the model agreeing.

The router is deliberately its own one-liner rather than a reuse of
`route_after_execution_evaluator`: the data-safety layers on that path adjudicate
MISSING INPUT (`safe_loading`, the intent gate, `data_fixer`), and "the script did not
save its own output" is none of those. Keeping them apart means a missing output can
never be mistaken for a missing input and terminate the step.

stdout is left untouched: the script really did print what it printed, and the engineer
needs to see it.

PRESENCE IS JUDGED FROM THE PER-ATTEMPT MANIFEST, NOT FROM `Path.exists()`. The
executor re-snapshots the run directory (recursively, see `attempt_manifest`) at the
start of every attempt and diffs it afterwards, so `data_manifest` is exactly "written
or modified by THIS attempt", at any depth; a bare existence check
would be waved through by a file an EARLIER step or an earlier attempt left on disk.
That is not hypothetical — it is the measured case: metabric Project3/Iteration1 step 5
declared `data/loocv_results.parquet`, a stale copy from a previous run was sitting in
`data/`, and the eight attempts that never wrote it would all have passed an
existence check. It is also consistent with how the engine treats attempts: each one is
a complete standalone script (see `executor`), so re-writing its own declared outputs is
the expected behaviour, not an optimisation to skip.

A DECLARED OUTPUT ONLY AN EARLIER ATTEMPT OF THIS STEP WROTE IS STILL REJECTED. The
manifest now comes with the step's history (`data_manifest_info["carried_over"]`: files
an earlier attempt of the same step wrote and this one left unchanged), and the step
evaluator is shown it, so a resumable script that skips 61 already-saved chunk files is
not mistaken for one that never processed them. This check deliberately does not read
it. A carried-over file proves that SOME attempt wrote it, not that it holds this
attempt's result: the earlier attempt may have failed after writing it, or used an
approach this one replaced, and the declared path is the one file a later step will read
as this step's answer. Accepting it would reopen the stale-file hole above for every
file written within the step. The cost is bounded and explicit: a resumable script must
rewrite (or touch) its DECLARED outputs on every attempt, which the bounce message says;
intermediate files it skips are unaffected, because only declared paths are checked.

CONSERVATIVE in five independent ways, because a false positive burns an attempt on
correct code:
  1. only SAVE-shaped declarations count. For each candidate path the nearest preceding
     verb decides: `load the cohort from data/raw.csv` is an INPUT (an absent input is
     the `safe_loading` layer's business — bouncing it here would pressure the engineer
     into inventing one), `write it to data/clean.csv` is an output;
  2. only unambiguous `data/...` paths with a real extension — a placeholder
     (`data/{run}.csv`, `data/*.png`) never parses;
  3. only on a run that otherwise SUCCEEDED (exit 0, no timeout). After a crash the
     missing file is already explained by the traceback;
  4. never under `CMBAGENT_LG_REMOTE_EXECUTOR`: the data lives guest-side, so engine-side
     absence proves nothing; and never without a manifest to read;
  5. a declared path the manifest could not see — the walk hit a cap
     (`data_manifest_info["complete"]` is False) or the path is under a symlinked
     directory the walk does not enter or one too large to list file by file
     (`partial_dirs`) — counts as written when it changed (mtime or ctime) after the
     attempt started. Never a bare existence check: a stale copy is older than the
     attempt, so the metabric case above is still caught. The manifest is never cut for
     display (only the evaluator's rendering is), so a step that also writes hundreds of
     other files is still judged on the full list. A declared path in a
     subdirectory of `data/` (`data/plots/km.png`) is judged from the manifest like any
     other, since the snapshot is recursive.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from langgraph.runtime import Runtime

from langgraph.graph import END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug import attempt_manifest as _am
from cmbagent_lg.self_debug.nodes import _codebase_dir
from cmbagent_lg.self_debug.schemas import ExecutionVerdict
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


# Prefix the rejection carries, mirroring `static_preflight.STATIC_MARKER`.
POSTCONDITION_MARKER = "[post-run check"

# A path under `data/` with a plain extension. The character class admits no brace,
# angle bracket, star or question mark, so a templated or globbed path simply does not
# match. `(?<![\w/.-])` stops a longer path (`raw_data/x.csv`) matching at `data/`.
_PATH_RE = re.compile(r"(?<![\w/.\-])(?:\./)?(data/[\w][\w./\-]*\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/\-])")

# The verb immediately before a path decides whether it is an output or an input.
_SAVE_RE = re.compile(
    r"\b(sav(?:e|es|ed|ing)|writ(?:e|es|ing)|written|stor(?:e|es|ed|ing)"
    r"|export(?:s|ed|ing)?|output(?:s|ted|ting)?|dump(?:s|ed|ing)?"
    r"|persist(?:s|ed|ing)?|produc(?:e|es|ed|ing)|generat(?:e|es|ed|ing)"
    r"|creat(?:e|es|ed|ing)|to_csv|to_parquet|to_pickle|savefig|np\.save)\b",
    re.IGNORECASE,
)
_LOAD_RE = re.compile(
    r"\b(load(?:s|ed|ing)?|read(?:s|ing)?|open(?:s|ed|ing)?|import(?:s|ed|ing)?"
    r"|pars(?:e|es|ed|ing)|ingest(?:s|ed|ing)?|input(?:s)?|provided|given"
    r"|available|existing|supplied|fetch(?:es|ed|ing)?)\b",
    re.IGNORECASE,
)


def declared_output_paths(step) -> List[str]:
    """The output artifacts this step DECLARES, as relative `data/...` paths.

    The sub-task line and each bullet point are scanned independently (a verb in one
    bullet says nothing about a path in another). Order-preserving, de-duplicated,
    `[]` for a step that declares none — which is the common case, and makes the whole
    check a no-op for it.
    """
    texts = [getattr(step, "sub_task", "") or ""]
    texts += [b or "" for b in (getattr(step, "bullet_points", None) or [])]
    out: List[str] = []
    for text in texts:
        if not text:
            continue
        # Nearest-preceding-verb adjudication: one scan of each verb family, then the
        # last match that ENDS before the path wins.
        saves = [m.end() for m in _SAVE_RE.finditer(text)]
        loads = [m.end() for m in _LOAD_RE.finditer(text)]
        for m in _PATH_RE.finditer(text):
            at = m.start()
            last_save = max([e for e in saves if e <= at], default=None)
            last_load = max([e for e in loads if e <= at], default=None)
            if last_save is None:
                continue  # no save verb at all → not a declared output
            if last_load is not None and last_load > last_save:
                continue  # the nearer verb reads it → an input, not an output
            path = m.group(1)
            if path not in out:
                out.append(path)
    return out


def missing_declared_outputs(step, work_dir: Optional[str], manifest=None,
                             info: Optional[dict] = None) -> List[str]:
    """Declared output paths THIS ATTEMPT did not write (see `declared_output_paths`
    for what counts as declared, and the module docstring for why the manifest rather
    than `Path.exists()` decides).

    A path counts as WRITTEN when the attempt's `data_manifest` reports it, by path or
    by basename (never because an earlier attempt of this step wrote it: carried-over
    files are information for the evaluator, see the module docstring) — a script that wrote the right content under a sibling name is the step
    evaluator's judgement call, not this one's. Where the manifest could not see the
    path (`info` says the listing is incomplete, or the path is under a symlinked or
    partial directory), a file changed since the attempt started counts (item 5).
    `[]` when there is no declaration, no workspace, or no manifest to read: never
    bounce blind.
    """
    declared = declared_output_paths(step)
    if not declared or not work_dir or not isinstance(manifest, list):
        return []
    names = _am.written_names(manifest)
    root = Path(work_dir).expanduser()
    whole = _am.listing_is_whole(info)
    missing = []
    for path in declared:
        if path in names or Path(path).name in names:
            continue
        unseen = not whole or _am.under_unscanned_dir(path, info)
        if unseen and _am.written_since_start(root / path, info):
            continue
        missing.append(path)
    return missing


def missing_output_report(missing: List[str]) -> str:
    """stderr text for a run that exited 0 without the files its step declared."""
    paths = ", ".join(f"`{p}`" for p in missing)
    return (
        f"The script exited 0 but the output file(s) this sub-task declared were not "
        f"written by this run: {paths}. Checked against the files this attempt actually "
        f"created or modified anywhere in the run directory, so this is a FACT, "
        f"not a review comment — and a stale copy left in `data/` by an earlier run "
        f"does not count, because a later step would read the wrong numbers from it; "
        f"nor does a copy an earlier attempt of this step saved, so a script that "
        f"reuses earlier results must still write its declared outputs on every run. "
        f"The step cannot be fulfilled without it. Write each declared path "
        f"exactly as the sub-task spells it (relative to the working directory, so "
        f"`data/<name>`, creating `data/` first if needed), print the path and the byte "
        f"size of every file you save, and keep the analysis you already have — only "
        f"the saving step is missing.\n"
        f"{POSTCONDITION_MARKER} — no model was involved]"
    )


def _demote(state: DebugState) -> None:
    """Free the canonical `step_{N}.{py,log}` names for the next attempt while keeping
    this one's artifacts, exactly as `execution_evaluator` does on a failure verdict
    (which does not run on this path). Never raises."""
    try:
        codebase = _codebase_dir(state)
        if codebase is None:
            return
        n = state.get("step_number", 1) or 1
        attempt = state.get("attempts", 0) or 0
        for ext in ("py", "log"):
            canonical = codebase / f"step_{n}.{ext}"
            if canonical.exists():
                canonical.rename(codebase / f"step_{n}_failure_{attempt}.{ext}")
    except Exception as e:  # noqa: BLE001 — bookkeeping must never suppress the bounce
        print(f"[output_postcondition] could not demote the attempt's artifacts "
              f"(non-fatal): {e}", file=sys.stderr, flush=True)


@timed_node("output_postcondition")
def output_postcondition(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Check the step's declared outputs were written by this attempt; turn a clean run
    that lacks one into a failed attempt, with no model call.

    Writes `output_postcondition_rejected` on EVERY pass (None when it passes), so a
    bounce never leaks into a later attempt. Never raises: a check that cannot answer
    lets the run through exactly as before.
    """
    ctx = runtime.context
    out: DebugState = {"output_postcondition_rejected": None}
    if not getattr(ctx, "check_declared_outputs", True):
        return out
    # Guest-side data: engine-side absence proves nothing (see the module docstring).
    if os.environ.get("CMBAGENT_LG_REMOTE_EXECUTOR"):
        return out
    # Only a run that otherwise succeeded — a traceback already explains a missing file.
    if state.get("execution_returncode") != 0 or state.get("execution_timed_out"):
        return out
    try:
        missing = missing_declared_outputs(
            state.get("step"), state.get("work_dir"), state.get("data_manifest"),
            state.get("data_manifest_info"),
        )
    except Exception as e:  # noqa: BLE001 — a post-condition must never break the graph
        print(f"[output_postcondition] check skipped (non-fatal): {e}",
              file=sys.stderr, flush=True)
        return out
    if not missing:
        return out

    report = missing_output_report(missing)
    print(f"[output_postcondition] clean run, declared output(s) not written by this "
          f"attempt: {', '.join(missing)} → bounced with no model call",
          file=sys.stderr, flush=True)
    stderr = (state.get("execution_stderr") or "")
    summary = ("declared output(s) not written by this run: " + ", ".join(missing))
    _demote(state)
    out.update({
        "output_postcondition_rejected": "missing_output",
        "execution_stderr": (stderr + "\n" if stderr.strip() else "") + report,
        "execution_returncode": 1,
        # The verdict the code gate would have produced, produced here instead: the
        # premise is a `stat()` call, not a judgement. `failure_kind="other"` keeps it
        # away from `escalation`, which only handles missing packages / renamed APIs.
        "current_execution_verdict": ExecutionVerdict(
            status="failure", failure_kind="other",
            error_summary=summary, fix_suggestion=report),
        "error_history": list(state.get("error_history", [])) + [summary],
    })
    return out


def route_after_output_postcondition(state: DebugState, runtime: Runtime[PlanContext]) -> str:
    """A bounced attempt goes straight back to the engineer (END at the attempt cap);
    anything else continues to the code gate as before.

    No model call and no data-safety branch on this edge — see the module docstring for
    why a missing OUTPUT must not be routed through the missing-INPUT layers."""
    if state.get("output_postcondition_rejected"):
        ctx = runtime.context
        if state.get("attempts", 0) >= getattr(ctx, "max_n_attempts", 3):
            return END
        return "engineer"
    return "execution_evaluator"


__all__ = [
    "output_postcondition",
    "route_after_output_postcondition",
    "declared_output_paths",
    "missing_declared_outputs",
    "missing_output_report",
    "POSTCONDITION_MARKER",
]
