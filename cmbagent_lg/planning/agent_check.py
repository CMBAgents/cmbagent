"""Researcher steps that need code: a plan-time check and a run-time signal.

The researcher writes reports; it cannot execute code. Plans from some open models give it
computation steps anyway ("compute the statistics of the catalogue", "load the snapshot
and fit ..."), and the researcher then spends every attempt saying it cannot execute code.

Two deterministic, model-free checks, both conservative and both only when an `engineer`
agent is available and `PlanContext.reassign_researcher_code_steps` holds:

- `reassign_code_steps(plan, ctx)` — after `format_plan` (and on a replanned tail): a
  researcher step that is NOT the plan's last step and whose sub_task / bullets ask, in an
  imperative clause, to compute, calculate, load data, read binary data files, run or execute
  code, or persist results to a data file becomes an engineer step (`Step.reassigned_from = "researcher"`). The last step stays
  the researcher's: it is the final report.
- `needs_code_signal(verdict, report, step)` — at run time, a researcher verdict judged NOT
  fulfilled whose report says it cannot execute code, or, on a step whose text asks for code
  work, whose feedback says the step needs code or whose report says it cannot compute or
  write files. `deep_research.run_step` then runs the step once with the engineer instead of
  spending the researcher's remaining attempts; never on the last step of a plan of several
  steps (the final report).
"""

from __future__ import annotations

import re
import sys
from typing import List, Optional, Tuple

# ── plan-time: what a step ASKS for ─────────────────────────────────────

_DATA_NOUN = (r"(?:files?|data(?:sets?)?|catalog(?:ue)?s?|tables?|snapshots?|arrays?|spectra|"
              r"[\w./-]+\.(?:csv|tsv|txt|dat|hdf5|h5|fits|npy|npz|json|parquet|pkl|nc))")
# Formats the researcher's `read_file` cannot parse (it reads text: CSV, JSON, logs, markdown);
# "read the output files of step 2" or "read the tables in Schaye et al." is researcher work.
_BINARY = (r"(?:\.(?:hdf5|h5|fits|npy|npz|parquet|pkl|pickle)\b|"
           r"\b(?:hdf5|fits|numpy|parquet|pickle|binary)\b)")
# Where an imperative clause starts: the start of the text, after a sentence or clause mark, after
# a list marker, or after and/then/first/next/also/finally. "summarize the methods used to
# calculate them", "discuss how to compute ...", "explain the computational load" do not ask.
_LEAD = (r"(?:^|[.;:,!?\n(]|\b(?:and|then|first|next|also|finally)\b)\s*(?:[-*•]\s*|\d+[.)]\s*)?"
         r"(?:(?:and|then|first|next|also|finally|please)\s*,?\s+)*")
_REJECT_BEFORE = re.compile(r"\b(?:how to|used to|methods? (?:to|for)|ways? to|approach(?:es)? to)\s*$", re.I)
# A persist ask whose target is the report itself ("save it to literature.md", "write the findings
# into the report file") is researcher work: the engine saves the report.
_REPORT_TARGET = re.compile(r"\.md\b|\bmarkdown\b|\breports?\b|\bsummar(?:y|ies)\b|\bnotes?\b", re.I)


def _ask(verb: str, rest: str = "") -> re.Pattern:
    return re.compile(_LEAD + r"(?P<verb>" + verb + r")\b" + rest, re.I)


# Base (imperative) verb forms only: "computed in step 2" or "the loaded table" describe
# earlier work, not work this step must do.
_ASK_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("compute", _ask(r"(?:re-?)?(?:compute|calculate)",
                     r"(?![^.;\n]{0,25}?\b(?:analytically|by hand|qualitatively|conceptually)\b)")),
    ("load", _ask(r"(?:re-?)?load", r"[^.;\n]{0,40}?\b" + _DATA_NOUN + r"\b")),
    ("read files", _ask(r"read|open|parse|ingest|inspect", r"[^.;\n]{0,40}?" + _BINARY)),
    ("run code", _ask(r"(?:re-?)?(?:run|execute)",
                      r"[^.;\n]{0,30}?\b(?:code|scripts?|python|programs?|simulations?|pipelines?|mcmc|"
                      r"chains?|fits?|regressions?|tests?)\b")),
    ("persist", _ask(r"save|persist|store|write|export|dump",
                     r"[^.;\n]{0,50}?\b(?:to|into|as|in)\b[^.;\n]{0,30}?(?:\bfiles?\b|\bdisk\b|\bdata/|"
                     r"\.(?:csv|tsv|txt|json|npy|npz|png|pdf|hdf5|h5|fits|parquet|pkl)\b)")),
]
_NEGATION = re.compile(r"\b(?:do not|don't|does not|never|without|no need to|not)\s+(?:\w+\s+){0,2}$", re.I)


def step_code_asks(step) -> List[str]:
    """The kinds of code work a step's text asks for (empty: none). Only an imperative clause
    counts (`_LEAD`); a match preceded by a negation ("do not recompute", "without loading") or
    by "how to" / "used to" / "methods for", and a persist ask aimed at the report, do not."""
    texts = [getattr(step, "sub_task", "") or ""] + list(getattr(step, "bullet_points", None) or [])
    found: List[str] = []
    for text in texts:
        for label, pat in _ASK_PATTERNS:
            if label in found:
                continue
            for m in pat.finditer(text):
                before = text[max(0, m.start("verb") - 40):m.start("verb")]
                if _NEGATION.search(before) or _REJECT_BEFORE.search(before):
                    continue
                if label == "persist" and _REPORT_TARGET.search(m.group(0)):
                    continue
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

# Code-ish or file objects only: "we do not have access to the raw data" is a normal report
# sentence (quote the literature instead).
_OBJ = r"(?:code|scripts?|python|programs?|files?|computations?|calculations?)"
_CANNOT = (r"\b(?:i|we)\s+(?:cannot|can ?not|can't|am unable to|are unable to|am not able to|are not able to|"
           r"do not have|don't have|lack|have no)\b[^.\n]{0,60}?\b")
# Executing code: the researcher saying it cannot do that is a signal on any step.
_RUN = r"(?:execut\w*|run(?:ning)?)"
_RUN_OBJ = r"(?:code|scripts?|python|programs?)"
# Other work (compute, write, save ...): a signal only when the step's own text asks for code work
# (`step_code_asks`), since "we cannot perform new calculations; we rely on published values" is a
# normal sentence in a literature step. Reading, opening and accessing are not in it: the ReAct
# researcher has `read_file`, so "I cannot read data/answer.json" means the file is missing.
_OTHER = (r"(?:comput\w*|calculat\w*|load(?:ing)?|writ(?:e|ing)|sav(?:e|ing)|creat(?:e|ing)|"
          r"generat(?:e|ing)|perform(?:ing)?)")

_REPORT_STRONG = [
    re.compile(_CANNOT + _RUN + r"\b[^.\n]{0,60}?\b" + _RUN_OBJ + r"\b", re.I),
    re.compile(r"\b(?:no|without)\s+(?:the\s+)?(?:ability|capability|access|means|tools?)\s+(?:to|for)\s+"
               r"(?:execut\w*|run\w*)\s+(?:the\s+)?(?:code|scripts?|python|programs?)", re.I),
    re.compile(r"\bcode execution\s+(?:is\s+)?(?:not available|unavailable|not possible)\b", re.I),
    re.compile(r"\bas an? (?:ai|language model|text-based)\b[^.\n]{0,80}?\b(?:execute|run|compute)\b", re.I),
]
_REPORT_WEAK = [
    re.compile(_CANNOT + _OTHER + r"\b[^.\n]{0,60}?\b" + _OBJ + r"\b", re.I),
]
# The verdict: the evaluator's statement that the step needs code. A signal only when the step's
# own text asks for code work: an evaluator asking a final report for "the values obtained from
# running the scripts in step 2" wants the report to quote them, not new code.
_VERDICT_PATTERNS = [
    re.compile(r"\b(?:requires?|required|needs?|need to|must)\b[^.\n]{0,40}?\b(?:execut\w*|running|run)\b"
               r"[^.\n]{0,30}?\b(?:code|scripts?|python|programs?)\b", re.I),
    re.compile(r"\bno\s+(?:code|scripts?|python)\s+(?:was|were|has been|have been)\s+(?:executed|run)\b", re.I),
    re.compile(r"\bresearcher\b[^.\n]{0,40}?\b(?:cannot|can't|can not|is unable to|is not able to|does not have)\b"
               r"[^.\n]{0,40}?\b(?:" + _RUN + "|" + _OTHER + r")\b[^.\n]{0,40}?\b" + _OBJ + r"\b", re.I),
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


def needs_code_signal(verdict, report: str, step=None) -> Optional[dict]:
    """{"source", "matched"} when an UNFULFILLED researcher verdict, or the report it judged,
    says the step needs code; else None. The report saying it cannot execute code counts on any
    step; the verdict saying the step needs code, or the report saying it cannot compute / write
    / save, counts only when `step`'s text asks for code work (`step_code_asks`)."""
    if verdict is None or getattr(verdict, "fulfilled", False):
        return None
    asks = bool(step_code_asks(step)) if step is not None else False
    hit = _first(_REPORT_STRONG, report or "")
    if hit:
        return {"source": "report", "matched": hit}
    if not asks:
        return None
    vtext = "\n".join([getattr(verdict, "feedback", None) or ""]
                      + list(getattr(verdict, "unmet_requirements", None) or []))
    hit = _first(_VERDICT_PATTERNS, vtext)
    if hit:
        return {"source": "verdict", "matched": hit}
    hit = _first(_REPORT_WEAK, report or "")
    if hit:
        return {"source": "report", "matched": hit}
    return None
