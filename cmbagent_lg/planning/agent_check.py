"""Researcher steps that need code: a plan-time check and a run-time signal.

The researcher writes reports; it cannot execute code. Plans from some open models give it
computation steps anyway ("compute the statistics of the catalogue", "load the snapshot
and fit ..."), and the researcher then spends every attempt saying it cannot execute code.

Two deterministic, model-free checks, both conservative and both only when an `engineer`
agent is available and `PlanContext.reassign_researcher_code_steps` holds:

- `reassign_code_steps(plan, ctx)` — after `format_plan` (and on a replanned tail): a
  researcher step that is NOT the plan's last step and whose sub_task / bullets ask to
  compute, calculate, load, read data files, run or execute code, or persist results to a
  file becomes an engineer step (`Step.reassigned_from = "researcher"`). The last step stays
  the researcher's: it is the final report.
- `needs_code_signal(verdict, report)` — at run time, a researcher verdict judged NOT
  fulfilled whose feedback, or whose report itself, says the work needs executing code,
  reading data files or writing files. `deep_research.run_step` then runs the step once with
  the engineer instead of spending the researcher's remaining attempts.
"""

from __future__ import annotations

import re
import sys
from typing import List, Optional, Tuple

# ── plan-time: what a step ASKS for ─────────────────────────────────────

_DATA_NOUN = (r"(?:files?|data(?:sets?)?|catalog(?:ue)?s?|tables?|snapshots?|arrays?|spectra|"
              r"[\w./-]+\.(?:csv|tsv|txt|dat|hdf5|h5|fits|npy|npz|json|parquet|pkl|nc))")
# Base (imperative) verb forms only: "computed in step 2" or "the loaded table" describe
# earlier work, not work this step must do.
_ASK_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("compute", re.compile(r"\b(?:compute|re-?compute|calculate|re-?calculate)\b", re.I)),
    ("load", re.compile(r"\bload\b", re.I)),
    ("read files", re.compile(r"\b(?:read|open|parse|ingest)\b[^.;\n]{0,40}?\b" + _DATA_NOUN + r"\b", re.I)),
    ("run code", re.compile(r"\b(?:run|execute)\b[^.;\n]{0,30}?\b(?:code|scripts?|python|programs?|"
                            r"simulations?|pipelines?|mcmc|chains?|fits?|regressions?|tests?)\b", re.I)),
    ("persist", re.compile(r"\b(?:save|persist|store|write|export|dump)\b[^.;\n]{0,50}?\b(?:to|into|as|in)\b"
                           r"[^.;\n]{0,30}?(?:\bfiles?\b|\bdisk\b|\bdata/|\.(?:csv|tsv|txt|json|npy|npz|png|pdf|"
                           r"hdf5|h5|fits|parquet|pkl)\b)", re.I)),
]
_NEGATION = re.compile(r"\b(?:do not|don't|does not|never|without|no need to|not)\s+(?:\w+\s+){0,2}$", re.I)


def step_code_asks(step) -> List[str]:
    """The kinds of code work a step's text asks for (empty: none). A match preceded by a
    negation ("do not recompute", "without loading") does not count."""
    texts = [getattr(step, "sub_task", "") or ""] + list(getattr(step, "bullet_points", None) or [])
    found: List[str] = []
    for text in texts:
        for label, pat in _ASK_PATTERNS:
            if label in found:
                continue
            for m in pat.finditer(text):
                if not _NEGATION.search(text[max(0, m.start() - 40):m.start()]):
                    found.append(label)
                    break
    return found


def reassignment_enabled(ctx) -> bool:
    if not getattr(ctx, "reassign_researcher_code_steps", True):
        return False
    agents = getattr(ctx, "available_agents", None) or []
    return any(name == "engineer" for name, _ in agents)


def reassign_code_steps(plan, ctx, label: str = "plan_check", last_is_final: bool = True):
    """(plan, records): researcher steps that ask for code work, except the last step, moved to
    the engineer. The plan is returned unchanged (the same object) when nothing moves. Never
    raises."""
    try:
        if plan is None or not reassignment_enabled(ctx):
            return plan, []
        steps = list(plan.sub_tasks)
        records = []
        for i, s in enumerate(steps):
            if s.sub_task_agent != "researcher" or (last_is_final and i == len(steps) - 1):
                continue
            asks = step_code_asks(s)
            if not asks:
                continue
            steps[i] = s.model_copy(update={"sub_task_agent": "engineer", "reassigned_from": "researcher"})
            rec = {"step_number": i + 1, "from": "researcher", "to": "engineer", "at": label,
                   "asks": asks, "sub_task": s.sub_task[:200]}
            records.append(rec)
            print(f"[{label}] step {i + 1} is a researcher step that asks to {', '.join(asks)} "
                  f"→ reassigned to the engineer ({s.sub_task[:120]!r})", file=sys.stderr, flush=True)
        if not records:
            return plan, []
        return plan.model_copy(update={"sub_tasks": steps}), records
    except Exception as e:  # noqa: BLE001 — a check must never break planning
        print(f"[{label}] researcher-step check skipped (non-fatal): {e}", file=sys.stderr, flush=True)
        return plan, []


# ── run-time: the researcher or its evaluator SAYS code is needed ───────

_ACT = (r"(?:execut\w*|run(?:ning)?|comput\w*|calculat\w*|access(?:ing)?|read(?:ing)?|load(?:ing)?|"
        r"open(?:ing)?|writ(?:e|ing)|sav(?:e|ing)|creat(?:e|ing)|generat(?:e|ing)|perform(?:ing)?)")
# Code-ish or file objects only: "we do not have access to the raw data" is a normal report
# sentence (quote the literature instead), "I cannot read the data files" is not.
_OBJ = r"(?:code|scripts?|python|programs?|files?|computations?|calculations?)"

# The report: the researcher's own statement that it cannot do the work.
_REPORT_PATTERNS = [
    re.compile(r"\b(?:i|we)\s+(?:cannot|can ?not|can't|am unable to|are unable to|am not able to|are not able to|"
               r"do not have|don't have|lack|have no)\b[^.\n]{0,60}?\b" + _ACT + r"\b[^.\n]{0,60}?\b" + _OBJ + r"\b",
               re.I),
    re.compile(r"\b(?:no|without)\s+(?:the\s+)?(?:ability|capability|access|means|tools?)\s+(?:to|for)\s+"
               r"(?:execut\w*|run\w*)\s+(?:the\s+)?(?:code|scripts?|python|programs?)", re.I),
    re.compile(r"\bcode execution\s+(?:is\s+)?(?:not available|unavailable|not possible)\b", re.I),
    re.compile(r"\bas an? (?:ai|language model|text-based)\b[^.\n]{0,80}?\b(?:execute|run|access|read|compute)\b",
               re.I),
]
# The verdict: the evaluator's statement that the step needs code.
_VERDICT_PATTERNS = [
    re.compile(r"\b(?:requires?|required|needs?|need to|must)\b[^.\n]{0,40}?\b(?:execut\w*|running|run)\b"
               r"[^.\n]{0,30}?\b(?:code|scripts?|python|programs?)\b", re.I),
    re.compile(r"\bno\s+(?:code|scripts?|python)\s+(?:was|were|has been|have been)\s+(?:executed|run)\b", re.I),
    re.compile(r"\bresearcher\b[^.\n]{0,40}?\b(?:cannot|can't|can not|is unable to|is not able to|does not have)\b"
               r"[^.\n]{0,40}?\b" + _ACT + r"\b[^.\n]{0,40}?\b" + _OBJ + r"\b", re.I),
    re.compile(r"\b(?:should|must|needs? to)\s+be\s+(?:assigned|delegated|given|handed|re-?assigned)\s+to\s+"
               r"(?:the\s+|an\s+)?engineer\b", re.I),
    re.compile(r"\brequires?\s+(?:the\s+|an\s+)?engineer\b", re.I),
    re.compile(r"\b(?:requires?|needs?)\s+(?:actual\s+|real\s+)?(?:code execution|executing code|running code|"
               r"reading (?:the\s+)?(?:raw\s+)?data files?|writing (?:the\s+)?(?:output\s+)?files?)\b", re.I),
]


def _first(patterns, text: str) -> Optional[str]:
    for p in patterns:
        m = p.search(text or "")
        if m:
            return " ".join(m.group(0).split())[:200]
    return None


def needs_code_signal(verdict, report: str) -> Optional[dict]:
    """{"source", "matched"} when an UNFULFILLED researcher verdict, or the report it judged,
    says the step needs code execution / data-file reading / file writing; else None."""
    if verdict is None or getattr(verdict, "fulfilled", False):
        return None
    vtext = "\n".join([getattr(verdict, "feedback", None) or ""]
                      + list(getattr(verdict, "unmet_requirements", None) or []))
    hit = _first(_VERDICT_PATTERNS, vtext)
    if hit:
        return {"source": "verdict", "matched": hit}
    hit = _first(_REPORT_PATTERNS, report or "")
    if hit:
        return {"source": "report", "matched": hit}
    return None
