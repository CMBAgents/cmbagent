"""Check the infeasibility claim instead of believing it.

`StepVerdict.infeasible_requirements` lets the step evaluator end a step by asserting
that a requirement cannot be met here. That assertion is a FACT ABOUT THE ENVIRONMENT OR
THE DATA, and the bug this whole channel exists to fix was an evaluator asserting exactly
such a fact and being wrong: it demanded
`sksurv.linear_model.FineGrayPHSurvivalAnalysis`, which does not exist in any installed
release, and refused the engineer's correct substitution eight times. Taking the same
agent's word that something does NOT exist would reproduce the bug with the sign flipped
— and worse, it would hand any model a universal bypass: echo every unmet requirement
into `infeasible_requirements` and every hard step "completes".

So the engine checks the claim against what it already observed. Three verdicts:

  `verified_absent`  the justification names a symbol, and that symbol genuinely does not
                     resolve in the installed environment (`api_preflight`'s resolver —
                     the same machinery that proves absence for the API pre-flight). The
                     claim is CONFIRMED and the step may pass by substitution.
  `refuted`          the engine holds evidence AGAINST the claim, so the substitution is
                     refused and the step fails as before, with the contradiction handed
                     to the engineer. Three independent ways to be refuted:
                       * every symbol the justification names RESOLVES — the thing the
                         evaluator says is missing is right there, and the engineer is
                         told its real import path;
                       * a bullet declared infeasible DECLARES an output artifact that
                         this attempt actually WROTE (`output_postcondition`'s parser, in
                         reverse, against the executor's per-attempt manifest) — a bullet
                         whose deliverable is on disk cannot be unmet, let alone
                         impossible;
                       * the declaration covers EVERY bullet of a multi-bullet step from
                         a run that produced results. Probed adversarially in the replay
                         harness (`case5_partial_infeasible`, phase `over_declared`): all
                         six bullets called impossible on the words "The data is too
                         sparse", while the run's own stdout held the cross-tabulation,
                         both cause-specific Cox fits and the Aalen-Johansen CIF, and its
                         manifest held the six files it wrote.
  `unverified`        the claim names nothing the engine can resolve — "the cohort has no
                     samples with both methylation and treatment recorded". That cannot
                     be checked automatically and may well be true, so it is ALLOWED, but
                     marked: it reaches the step summary, the run outcome and
                     `analysis_status.json`, and it counts against a per-plan budget
                     (`max_unverified_infeasible_steps`) so a run cannot quietly produce
                     a paper whose every hard step was declared impossible on one agent's
                     word.

A missing PACKAGE is deliberately NOT a confirmation. `sksurv.X` on a machine without
scikit-survival installed does not resolve, but the package can be installed — the
engineer's escalation path exists for exactly that — so the root being absent lands in
`unverified`, never in `verified_absent`.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from typing import List, Optional, Tuple

from cmbagent_lg.self_debug.state import DebugState


# A dotted identifier: `sksurv.linear_model.FineGrayPHSurvivalAnalysis`, `lifelines.X`.
# At least two parts, because a bare word is not a resolvable claim. Backticks, quotes
# and surrounding prose are stripped by the regex boundaries.
_DOTTED_RE = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(?![\w])")

# Roots that are never a library claim: the stdlib names a justification uses in passing,
# and the file-ish shapes `_DOTTED_RE` would otherwise pick up.
_IGNORED_ROOTS = frozenset({
    "self", "cls", "np", "pd", "plt", "df", "data", "os", "sys", "json", "csv",
    "e", "g", "i", "x", "y", "z",
})
# Last components that mean "this was a filename, not a symbol".
_FILE_SUFFIXES = frozenset({
    "py", "csv", "parquet", "json", "md", "txt", "png", "pdf", "npy", "npz", "h5",
    "hdf5", "xlsx", "log", "yaml", "yml", "pkl", "tsv", "feather", "jpg", "jpeg",
})


def candidate_symbols(text: Optional[str]) -> List[str]:
    """Dotted names in a justification that are worth resolving, in order, de-duplicated.

    Conservative: a name is a candidate only if its ROOT is an importable top-level
    module. That is what keeps `data_description.md` and `step_5.py` out — and it is why
    a claim about an uninstalled package yields no candidates, which lands it in
    `unverified` rather than in a confirmation.
    """
    out: List[str] = []
    for raw in _DOTTED_RE.findall(text or ""):
        parts = raw.split(".")
        if parts[0] in _IGNORED_ROOTS or parts[-1].lower() in _FILE_SUFFIXES:
            continue
        if raw in out:
            continue
        try:
            if importlib.util.find_spec(parts[0]) is None:
                continue
        except BaseException:  # noqa: BLE001 — find_spec raises for a broken package
            continue
        out.append(raw)
    return out


def resolve_claimed_symbols(text: Optional[str], work_dir: Optional[str] = None
                            ) -> Tuple[List[str], List[str]]:
    """`(absent, present)` for the resolvable symbols a justification names.

    `absent` is the evidence FOR the claim, `present` the evidence against it. Resolution
    is `api_preflight.resolve_dotted_object`, the same importlib+inspect path the API
    pre-flight uses to prove a name absent — so a confirmation here means the same thing
    it means there. Never raises: a resolver failure leaves the name out of both lists,
    which degrades to `unverified`.
    """
    absent: List[str] = []
    present: List[str] = []
    try:
        from cmbagent_lg.self_debug.api_preflight import resolve_dotted_object
    except Exception as e:  # noqa: BLE001
        print(f"[infeasibility_check] resolver unavailable (non-fatal): {e}",
              file=sys.stderr, flush=True)
        return absent, present
    for dotted in candidate_symbols(text):
        try:
            obj = resolve_dotted_object(dotted, work_dir)
        except BaseException:  # noqa: BLE001 — resolving touches third-party import bodies
            continue
        (present if obj is not None else absent).append(dotted)
    return absent, present


def _written_artifacts(state: DebugState) -> set:
    """Paths and basenames this ATTEMPT wrote anywhere in the run directory (the
    executor's recursive manifest diff, so an earlier attempt's leftovers do not count),
    plus any file outside it that stdout named and this attempt wrote."""
    from cmbagent_lg.self_debug.attempt_manifest import written_names

    return written_names(state.get("data_manifest"))


def _contradicting_artifact(verdict, step, state: DebugState) -> Optional[Tuple[str, str]]:
    """`(bullet, artifact)` when a bullet declared infeasible names an output artifact
    that this attempt WROTE — the deliverable is on disk, so that bullet was not unmet.

    Reuses `output_postcondition.declared_output_paths`, which already distinguishes a
    save-shaped declaration from a path the bullet merely READS; imported lazily to keep
    the module graph acyclic.
    """
    try:
        from cmbagent_lg.self_debug.output_postcondition import declared_output_paths
        from cmbagent_lg.self_debug.schemas import _same_requirement
    except Exception:  # noqa: BLE001
        return None
    written = _written_artifacts(state)
    if not written:
        return None
    declared = [d for d in (getattr(verdict, "infeasible_requirements", None) or [])
                if (d or "").strip()]
    for bullet in (getattr(step, "bullet_points", None) or []):
        if not any(_same_requirement(bullet, d) for d in declared):
            continue
        for path in declared_output_paths(_OneBullet(bullet)):
            if path in written or path.rsplit("/", 1)[-1] in written:
                return bullet, path
    return None


class _OneBullet:
    """Minimal step-shaped object so one bullet can be run through the step parser."""

    sub_task = ""

    def __init__(self, bullet: str):
        self.bullet_points = [bullet]


def _covers_every_bullet(verdict, step) -> bool:
    """Does the declaration name every bullet of a MULTI-bullet step? You cannot
    over-claim on a one-item list — a single-bullet step whose only requirement is
    unachievable is the ordinary case this channel exists for."""
    from cmbagent_lg.self_debug.schemas import _same_requirement

    bullets = [b for b in (getattr(step, "bullet_points", None) or []) if (b or "").strip()]
    if len(bullets) < 2:
        return False
    declared = [d for d in (getattr(verdict, "infeasible_requirements", None) or [])
                if (d or "").strip()]
    return all(any(_same_requirement(b, d) for d in declared) for b in bullets)


def _produced_results(state: DebugState) -> bool:
    return bool(state.get("data_manifest")
                or (state.get("execution_stdout") or "").strip())


def audit_declaration(verdict, step, state: DebugState) -> Optional[dict]:
    """The engine's own verdict on the evaluator's infeasibility claim, or None when the
    verdict makes no such claim (nothing to audit, and no record to write).

    `{"status": ..., "absent": [...], "present": [...], "reason": ...}`. Never raises:
    any failure answers `unverified`, which is allowed-but-counted rather than trusted.
    """
    declared = [d for d in (getattr(verdict, "infeasible_requirements", None) or [])
                if (d or "").strip()]
    if not declared:
        return None
    try:
        hit = _contradicting_artifact(verdict, step, state)
        if hit is not None:
            bullet, path = hit
            return {
                "status": "refuted",
                "reason": "artifact_written",
                "artifact": path,
                "bullet": bullet,
                "detail": (f"this attempt wrote `{path}`, the deliverable the bullet "
                           f"declared, so that requirement was not unmet"),
            }
        if _covers_every_bullet(verdict, step) and _produced_results(state):
            return {
                "status": "refuted",
                "reason": "over_broad",
                "detail": ("every bullet of the sub-task was declared impossible, yet "
                           "the run produced output files and printed results"),
            }
        absent, present = resolve_claimed_symbols(
            getattr(verdict, "infeasible_justification", None), state.get("work_dir"))
        if absent:
            return {"status": "verified_absent", "absent": absent, "present": present,
                    "detail": "not present in the installed environment: "
                              + ", ".join(absent)}
        if present:
            return {"status": "refuted", "reason": "symbol_resolves", "present": present,
                    "detail": ("the installed environment DOES provide "
                               + ", ".join(present))}
        return {"status": "unverified",
                "detail": ("the justification names no symbol this engine can resolve, "
                           "so the claim is recorded as asserted, not checked")}
    except Exception as e:  # noqa: BLE001 — an audit must never break the graph
        print(f"[infeasibility_check] audit failed, treating as unverified: {e}",
              file=sys.stderr, flush=True)
        return {"status": "unverified", "detail": f"audit error: {e}"}


def refutation_feedback(audit: dict) -> str:
    """What the engineer is told when the substitution was refused. Names the
    contradiction, because "try again" on a step the evaluator just called impossible is
    the loop this whole branch exists to break."""
    reason = (audit or {}).get("reason")
    if reason == "symbol_resolves":
        names = ", ".join(audit.get("present") or [])
        return (f"The step was NOT accepted as infeasible: the API the evaluator called "
                f"missing is INSTALLED and importable here — {names}. Use it by that "
                f"exact dotted path (import the module and reference the name as "
                f"written) rather than substituting a different method.")
    if reason == "artifact_written":
        return (f"The step was NOT accepted as infeasible: this run wrote "
                f"`{audit.get('artifact')}`, which is the deliverable the requirement "
                f"called impossible. That requirement was met; address whatever else "
                f"the evaluator listed.")
    if reason == "over_broad":
        return ("The step was NOT accepted as infeasible: every requirement was declared "
                "impossible, but this run produced output files and printed results, so "
                "most of them plainly were not. Treat the listed requirements as "
                "ordinary work.")
    return ""


def unverified_budget_spent(prior_outcomes, ctx) -> bool:
    """Has this PLAN already passed as many steps on unchecked infeasibility claims as
    it is allowed? (`max_unverified_infeasible_steps`, default 2.)

    The budget exists because an unverified claim is the one branch the engine takes on
    the evaluator's word alone. Two is deliberately small: plans are capped at
    `maximum_number_of_steps_in_plan` (default 5), so a third unchecked "this was
    impossible" means the majority of the analysis was abandoned by assertion, and a
    halt — visible, and cheap to diagnose — is better than a paper whose every hard step
    was recorded as impossible. Checked absences do NOT count: those are facts.
    """
    limit = getattr(ctx, "max_unverified_infeasible_steps", 2) if ctx is not None else 2
    spent = sum(1 for o in (prior_outcomes or [])
                if isinstance(o, dict) and o.get("infeasibility_status") == "unverified")
    return spent >= limit


__all__ = [
    "audit_declaration",
    "candidate_symbols",
    "resolve_claimed_symbols",
    "refutation_feedback",
    "unverified_budget_spent",
]
