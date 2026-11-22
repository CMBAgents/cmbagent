"""Run-scoped variables (LangGraph `context_schema`).

The values the cmbagent YAML templates reference as `{main_task}`,
`{hardware_constraints}`, etc. Passed at `graph.invoke` time rather than as graph
state, so a single compiled graph generalizes across tasks.
"""

import os
from dataclasses import dataclass, field
from typing import List, Tuple


# Exposed to the planner/reviewer prompts AND used to constrain
# `Step.sub_task_agent` at format time (a runtime `Literal[*names]`).
# Mirrors cmbagent's `planner_response_evaluator.Subtasks` set, so plans are
# drop-in compatible with cmbagent's control phase.
DEFAULT_AVAILABLE_AGENTS: List[Tuple[str, str]] = [
    ("engineer", "Writes and runs Python code: numeric work, data I/O, computations, plotting."),
    ("researcher", "Reads, reasons, writes prose; no code execution."),
]

# The api_digest character budget. Defined here so the field default and
# api_digest's own fallback cannot drift apart.
DEFAULT_API_DIGEST_MAX_CHARS = 6000


# Ceiling applied when NEITHER the step NOR the run context sets a
# code-execution timeout; execution must never be unbounded.
DEFAULT_CODE_EXECUTION_TIMEOUT = 300


def effective_code_execution_timeout(ctx, step=None) -> int:
    """The timeout the executor will actually enforce, in seconds.

    Resolution order: the step's `code_execution_timeout` → the run context's →
    `DEFAULT_CODE_EXECUTION_TIMEOUT`. Never returns None. `step` is duck-typed
    (any object with the attribute, or None).
    """
    for value in (getattr(step, "code_execution_timeout", None),
                  getattr(ctx, "code_execution_timeout", None)):
        if value:
            return int(value)
    return DEFAULT_CODE_EXECUTION_TIMEOUT


def _env_knob(name: str, default):
    """A field default that a deployed worker can flip through the environment:
    `CMBAGENT_LG_<NAME>` overrides `default`, coerced to the default's type
    (`"0"`/`"false"`/`"off"` disable a boolean). Resolved when the context is
    BUILT, so a value passed explicitly to `PlanContext(...)` still wins and the
    grader nodes read one plain attribute. Only the researcher-grounding knobs
    use this: they are the ones a fleet tunes without a redeploy."""
    raw = os.environ.get(f"CMBAGENT_LG_{name.upper()}")
    if raw is None or raw == "":
        return default
    try:
        if isinstance(default, bool):
            return raw.strip().lower() not in ("0", "false", "no", "off")
        if isinstance(default, int):
            return int(float(raw))
        if isinstance(default, float):
            return float(raw)
    except ValueError:
        return default
    return raw


@dataclass
class PlanContext:
    main_task: str
    hardware_constraints: str = "Standard laptop. Single CPU. No GPU."
    code_execution_timeout: int | None = 120
    maximum_number_of_steps_in_plan: int = 5
    planner_append_instructions: str = ""
    plan_reviewer_append_instructions: str = ""
    # Number of review cycles. Total planner passes = num_rounds + 1
    # (the extra pass is the final, un-reviewed plan).
    num_rounds: int = 2
    # After each executed step a reviewer may rewrite the not-yet-run tail of the
    # plan (completed steps stay frozen). False makes `adaptive_review`
    # early-return with no model call: `run_step → route → run_step | END`.
    adaptive_planning: bool = True
    # Max tail re-plans per run. With `maximum_number_of_steps_in_plan` (which
    # bounds the merged plan length) this makes the adaptive loop terminate.
    max_adaptations: int = 3
    # Per-step summaries are injected into every later step's prompt, so an
    # unbounded carryover costs O(steps²) tokens. Only the most recent N steps
    # are carried verbatim; older ones collapse to their one-line header (their
    # detail stays reachable on disk via the WORKSPACE FILE MANIFEST paths).
    carryover_recent_full: int = 3
    # `(name, one-line description)` pairs. Override per run to expose a
    # different team to the planner (e.g. add `("plotter", "Renders plots.")`).
    available_agents: List[Tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_AVAILABLE_AGENTS)
    )

    # ── per-role model overrides ─────────────────────────────────────────
    # Each None falls back to llms._DEFAULT_MODEL. The provider is inferred from
    # the model name (gemini-* / gpt-* / o[1-4]* / claude-*), so a caller's config
    # can be passed straight through. Node modules read the field for their role;
    # the llms role used is: planner/researcher → generator, engineer → coder,
    # plan_reviewer/evaluators → critic, formatters → formatter.
    planner_model: str | None = None
    plan_reviewer_model: str | None = None
    engineer_model: str | None = None
    researcher_model: str | None = None
    evaluator_model: str | None = None
    formatter_model: str | None = None
    # Models of the adaptive reviewer and replanner between steps. None → the env overrides
    # ADAPTIVE_REVIEWER_MODEL / ADAPTIVE_PLANNER_MODEL, else non-lite Gemini defaults.
    adaptive_reviewer_model: str | None = None
    adaptive_planner_model: str | None = None

    # ── multimodal grounding (cmbagent_lg.vlm) ───────────────────────────
    # Give the researcher (and the engineer step evaluator) the generated plots
    # under `data/` as image content blocks; the role's model must be
    # vision-capable (gemini-*, gpt-4o/gpt-5*, claude-*). The images ride along on
    # a call that was happening anyway, so grounding the report costs no extra
    # call — but this same switch routes self_debug through `image_reviewer`,
    # which DOES cost one vision call per step plus a bounded revision loop.
    vlm_enabled: bool = True
    vlm_max_images: int = 8
    # Model for the image_reviewer node (vision-capable). None → default model.
    vlm_model: str | None = None
    # Image-review/revision cycles a step may go through before continuing
    # regardless. Mirrors old cmbagent's `max_vlm_review_attempts`.
    max_vlm_review_attempts: int = 2

    # ── self_debug module ────────────────────────────────────────────────
    # Max engineer attempts per sub-task before giving up. Mirrors
    # cmbagent's `max_n_attempts` (default 3 there too).
    max_n_attempts: int = 3
    # ── operator text, and WHICH AGENTS SEE IT ───────────────────────────
    # Three slots, because "who is this addressed to" is a real distinction and
    # getting it wrong is expensive. Measured on the metabric fleet: the operator
    # put both "no Fine-Gray implementation exists here, substitute and carry on"
    # and "a negative outcome completes a step" into `engineer_append_instructions`.
    # The engineer obeyed both; the step evaluator was never shown either and
    # refused the engineer's correct substitution eight times, until the step
    # exhausted its budget and halted the project. An environment fact stated once
    # must not reach only half the loop.
    #
    #   shared_append_instructions    → the engineer AND both evaluators. This is
    #       where facts about the ENVIRONMENT and about WHAT COUNTS AS A FINISHED
    #       STEP belong: which libraries exist, which methods do not, that a
    #       correctly-measured negative result completes a step, that a documented
    #       substitution is acceptable. Both the agent acting on a fact and the
    #       agent judging against it need it.
    #   engineer_append_instructions  → the engineer, plus the step evaluator under
    #       an explicit "instructions the engineer was given" header (NOT merged
    #       into the evaluator's own directions). The evaluator cannot fairly judge
    #       a script written under instructions it has not seen, and this keeps a
    #       deployment that already states its facts here from silently losing
    #       half of them. Use it for how-to-code guidance.
    #   evaluator_append_instructions → the evaluators only. Judging guidance that
    #       would be noise or, worse, a hint to game, in the engineer's prompt.
    shared_append_instructions: str = ""
    engineer_append_instructions: str = ""
    evaluator_append_instructions: str = ""
    # Static pre-flight (`self_debug/static_preflight.py`, always on, no model
    # call): a script that does not parse is never executed — the SyntaxError
    # is handed to the evaluator/retry path directly. This flag adds the second
    # check: a script with no `print(` / `sys.stdout` write anywhere is rejected
    # the same way, because the step evaluator sees ONLY stdout and rejects a
    # silent run as unverifiable regardless. Measured on the qm9-joint fleet
    # (918 failed attempts): 47 silent scripts, 9 SyntaxErrors — each one a
    # subprocess run plus one or two evaluator calls spent on a foregone verdict.
    # Set False for a run whose scripts legitimately produce no stdout.
    require_stdout: bool = True
    # Third static check (same node, same no-model cost): refuse a script that loads a
    # BUNDLED EXAMPLE dataset — `lifelines.datasets.load_rossi()`,
    # `sklearn.datasets.load_*`/`fetch_*`, `seaborn.load_dataset`, `plotly.data`,
    # `bokeh.sampledata`, `keras`/`torchvision.datasets`, … — instead of the project's
    # own files. Measured on the breast-cancer fleet: one script fitted the Rossi
    # recidivism demo and reported it as the cohort result, with rc 0 and nothing in
    # stdout to give it away, which no evaluator reading stdout can catch.
    # CONSERVATIVE: `sklearn.datasets.make_*` (synthetic generators) and the generic
    # directory readers (`torchvision.datasets.ImageFolder`) are exempt, and the check
    # stands down when the SUB-TASK itself names the dataset, so a plan that
    # legitimately says "cluster sklearn's iris data" still runs.
    reject_demo_datasets: bool = True
    # Deterministic POST-condition on a finished run (`self_debug/output_postcondition.py`,
    # no model call): when the sub-task declares an output artifact by path — a bullet
    # saying "save the cohort to `data/cohort.parquet`" — and the script exits 0 without
    # that file on disk, the attempt is bounced with the missing path named, instead of
    # spending the two evaluator calls it takes to discover the same thing. Only
    # SAVE-shaped declarations count, and only clear `data/...` paths; an absent INPUT is
    # the `safe_loading` layer's business, never this one. Set False to disable.
    check_declared_outputs: bool = True
    # The CODE gate's counterpart to `StepVerdict.infeasible_requirements` (see
    # `self_debug/data_infeasibility.py`, no model call): a failure whose traceback is a
    # fact about the DATA — a fit that will not converge, a rank-deficient design, a
    # perfectly separated logit — is NAMED on the record instead of being retried as an
    # anonymous crash, and the step stops once the SAME marker has come back
    # `max_data_infeasibility_repeats` times. metabric Project4 step 5 spent four of
    # eight attempts on one lifelines `ConvergenceError('delta contains nan value(s))'`
    # from an interaction design that is rank-deficient on 1,392 samples; no retry can
    # fix that, only a different plan, and `deep_research` routes the stopped step to
    # the adaptive replanner rather than halting.
    stop_on_repeated_data_infeasibility: bool = True
    # Observations of ONE marker before the step gives up on it: the first reveals the
    # fault, the next two are the engineer's chances to get past it (a penalizer, a
    # simpler design, pooled categories), and a third identical traceback buys nothing.
    # Three is also the shape the fleet produced. Raise it for a run whose numerics are
    # expected to need more attempts to stabilise.
    max_data_infeasibility_repeats: int = 3
    # How many steps ONE PLAN may pass on an UNVERIFIED infeasibility claim — one the
    # engine could not check because the justification named no resolvable symbol (see
    # `self_debug/infeasibility_check.py`). A checked absence is a fact and never counts.
    # Two is deliberately small: plans are capped at `maximum_number_of_steps_in_plan`
    # (default 5), so a third unchecked "this was impossible" means the majority of the
    # analysis was abandoned by assertion, and a visible halt beats a paper whose every
    # hard step was recorded as impossible on one agent's word.
    max_unverified_infeasible_steps: int = 2

    # ── researcher module (prose steps inside deep_research) ─────────────
    researcher_append_instructions: str = ""

    # ── researcher strategy ──────────────────────────────────────────────
    # Selects the report-writing node at START:
    #   "react" — bounded ReAct researcher (react_researcher.py) with read-only
    #             `list_workspace` / `read_file` / `view_image`, so it can open
    #             the artifact holding the numbers instead of inventing them.
    #   "plain" — single-`invoke` researcher.
    # Non-fatal: any error inside the ReAct loop falls back to the plain node.
    researcher_mode: str = "react"
    # Upper bound on researcher tool-call round-trips. Higher than the
    # engineer's: a Results section legitimately spans several artifacts.
    researcher_max_tool_calls: int = 6

    # ── researcher claim-audit guardrail ─────────────────────────────────
    # After step_evaluator PASSES a report, `claim_auditor` grades each
    # result-claim against the run's clear signal; an unsupported-HARD claim
    # loops back to the writer, exhaustion FAILS the step. It never sets
    # `fulfilled` and never feeds the goal router, so it cannot reintroduce the
    # evaluator's fabrication-false-positive livelock.
    claim_audit_enabled: bool = True
    claim_audit_model: str | None = None      # None → evaluator/critic model
    max_claim_audit_attempts: int = 2         # own ceiling (shares max_n_attempts too)
    # The input-sourced envelope (`researcher/grounding.py`): a claim whose numbers were
    # located ONLY in a file the run was GIVEN stays hard unless it carries no
    # result-assertion marker AND a forced-choice classification, quoting a verified
    # span, calls it a description of the provided data. THE ONLY PLACE IN THE ENGINE
    # WHERE A MODEL'S JUDGEMENT CAN WEAKEN A DETERMINISTIC FINDING — read that module's
    # envelope section before touching it. False restores the old reading, where an
    # input-only claim always stood and could halt a finished run.
    input_sourced_description_demotion: bool = True
    # Per-source excerpt the auditor sees — larger than the evaluator's coarse
    # 2500 so truncation degrades to WEAK rather than a false HARD.
    claim_audit_source_chars: int = 8_000

    # ── deterministic claim grounding (researcher/grounding.py) ─────────
    # ON by default: the fix for the fleet's false-fabrication livelock IS the
    # grounding. Every grader window used to be an unmarked prefix of a prefix,
    # and an LLM cannot prove a number ABSENT from an excerpt — so presence and
    # absence are now whole-token lookups over the run's FULL sources, and the
    # LLM keeps authority only over meaning. Each knob falls back to
    # `CMBAGENT_LG_<UPPER>` in the environment (see `_env_knob`).
    grounding_enabled: bool = field(
        default_factory=lambda: _env_knob("grounding_enabled", True))
    # Wall-clock budget for building one step's source index; exceeding it marks
    # the index incomplete, and an incomplete index never proves absence.
    grounding_budget_s: float = field(
        default_factory=lambda: _env_knob("grounding_budget_s", 1.5))
    # Head/tail bytes indexed per file (a 50 MB log is read at both ends).
    grounding_file_head_bytes: int = field(
        default_factory=lambda: _env_knob("grounding_file_head_bytes", 16 * 1024 * 1024))
    grounding_file_tail_bytes: int = field(
        default_factory=lambda: _env_knob("grounding_file_tail_bytes", 4 * 1024 * 1024))
    # An unlabelled hit counts as STRONG only when fewer than this many chance
    # matches are expected at the claim's precision in this run's own number
    # density (E = tokens-in-decade × 10^-sig).
    grounding_coincidence_max: float = field(
        default_factory=lambda: _env_knob("grounding_coincidence_max", 0.05))
    # Cap on the QUANTITY LEDGER block handed to the graders.
    evidence_ledger_chars: int = field(
        default_factory=lambda: _env_knob("evidence_ledger_chars", 24_000))
    # `read_file` page size: first call head+tail, later calls one page at
    # `offset`, always with a footer saying what was omitted. Always on.
    researcher_read_page_chars: int = field(
        default_factory=lambda: _env_knob("researcher_read_page_chars", 8_000))
    # True keeps today's halt on a deterministically-absent hard claim; False
    # delivers the annotated report with the step flagged instead (opt-in).
    claim_audit_halts: bool = field(
        default_factory=lambda: _env_knob("claim_audit_halts", True))

    # ── escalation (self_debug escape hatch) ─────────────────────────────
    # On a failure the strict loop structurally can't fix (missing package,
    # renamed/removed API), escalate once to a free-form Claude Agent SDK agent
    # that can web-search. Needs ANTHROPIC_API_KEY; bounded by the caps below.
    enable_escalation: bool = False
    escalation_max_budget_usd: float = 0.50
    escalation_max_turns: int = 12
    escalation_append_instructions: str = ""
    # Claude model the escalation agent uses. None = SDK default.
    escalation_model: str | None = None

    # ── data-integrity safety layer ──────────────────────────────────────
    # Mandatory, not opt-in: fabricating data to stand in for a required input is
    # never an acceptable outcome. Independent of `enable_data_fixer` and
    # `enable_preflight`, and costs zero extra model calls. When True:
    #   (a) the step evaluator gets the data-integrity rules (fabricated /
    #       synthesized / np.random-substituted input, wrong-named column,
    #       non-converged fit → NOT fulfilled);
    #   (b) if a run fails because a provided input FILE the script does not
    #       itself write is genuinely absent, the step ends as a terminal
    #       not-fulfilled instead of being retried into fabricating the data
    #       (`self_debug.nodes.missing_input`).
    # (b) is conservative: anything that might be a create-then-read, or a
    # missing PACKAGE rather than a missing FILE, falls through to the normal
    # retry/escalation path.
    safe_loading: bool = True

    # Directories that hold the run's PROVIDED input data, read-only, OUTSIDE the work dir (a shared
    # data mirror, a mounted dataset). When any of them exists, an empty `data/` is not "no input":
    # the empty-input guards (`input_dir_empty`, the intent gate's do-not-fabricate directive) stand
    # down, and the engineer is told where the inputs are and that `data/` is for its outputs only.
    input_roots: List[str] = field(default_factory=list)

    # ── data-loader fixer ────────────────────────────────────────────────
    # When True (nothing below fires when it is False):
    #   (a) an additive "Loading a provided INPUT data file" block in the
    #       engineer prompt (magic-byte loader choice, no fabrication, …);
    #   (b) the `data_loader_fixer` node — on a data-LOAD failure it inspects
    #       the real file (magic bytes / csv.Sniffer / HDF5 tree) and, via one
    #       Gemini call, synthesises the correct loader for the next attempt;
    #       an absent required input yields format="missing" and refuses to
    #       fabricate, short-circuiting to a terminal not-fulfilled.
    # Non-fatal: any exception inside the fixer is a no-op that falls back to
    # the normal retry loop. The data-INTEGRITY rules and the missing-input
    # short-circuit are NOT here — they are gated by `safe_loading` above.
    enable_data_fixer: bool = False
    # Gemini model for the fixer's loader-synthesis call. None → falls back to
    # `engineer_model` → a non-lite default. The heavy lifting is deterministic
    # file inspection; the model only turns that report into a loader snippet.
    data_fixer_model: str | None = None

    # ── pre-flight file inspection ───────────────────────────────────────
    # Independent of `enable_data_fixer`. When True, a deterministic (no-LLM)
    # `preflight` node runs once BEFORE the first engineer attempt: it inspects
    # every file in `work_dir/data/` (magic bytes, csv delimiter, HDF5 tree,
    # npy/npz dtype, json shape) and injects a factual FILE REPORT into the
    # engineer's FIRST-attempt context, so the loader is right on attempt 1 —
    # from facts, not fault-specific rules. Adds zero model calls. When False the
    # graph is wired START → engineer and the node never executes.
    enable_preflight: bool = True

    # ── API introspection on retry ───────────────────────────────────────
    # The reactive one of the three API-shape defences (digest → preflight →
    # introspect). On an API-shaped error (`AttributeError: module 'X' has no
    # attribute 'y'`, `ImportError: cannot import name 'y' from 'X'`), run
    # deterministic introspection (importlib + inspect.signature + difflib, no
    # LLM call) and inject the report into the plain engineer's retry context, so
    # the next attempt sees what the installed release actually provides. Without
    # it, a step that died on a REMOVED library function looks like a logic bug.
    # Also settable with `CMBAGENT_LG_API_INTROSPECT_RETRY=1`.
    # Under `CMBAGENT_LG_REMOTE_EXECUTOR` the report comes from the guest sandbox
    # (`api_introspect.env_report`); silence if the guest cannot answer.
    api_introspect_retry: bool = True

    # ── proactive API pre-flight ─────────────────────────────────────────
    # The middle defence, independent of the other two. A deterministic (no-LLM)
    # `api_preflight` node runs between `format_engineer` and `executor`: it
    # extracts, with `ast`, every third-party symbol the freshly-written code
    # references and verifies each against the INSTALLED packages. A provably
    # absent symbol means the code is not executed at all — the engineer gets the
    # real API facts (version, ABSENT, closest names with signatures) and
    # re-emits, costing no execution round-trip and no model call.
    # CONSERVATIVE by construction: a symbol is reported only when the module
    # imports cleanly and the attribute genuinely is not on it. Star imports,
    # rebound names, accesses inside `try:`, dynamic `getattr`, monkeypatching
    # and unimportable modules all pass silently — a false positive would block
    # correct code and burn an attempt, which is worse than a miss.
    # Also settable with `CMBAGENT_LG_API_PREFLIGHT=1`.
    # Under `CMBAGENT_LG_REMOTE_EXECUTOR` every verdict comes from the guest sandbox
    # (`api_introspect.env_report`); an unreachable guest lets the code run.
    enable_api_preflight: bool = True
    # How many times one step may be bounced back by the check before it is
    # allowed to execute regardless; the loop must be provably finite. A bounce
    # also costs one engineer attempt, so `max_n_attempts` bounds it too, and a
    # bounce is only ever issued for a finding the step has NOT seen before
    # (`api_preflight_bounced`), which is what makes 4 safe: the same fault
    # coming back twice is executed and explained by a real traceback instead.
    max_api_preflight_bounces: int = 4
    # Wall-clock budget, in seconds, for imports the check makes that are NOT
    # already loaded in this process (a cold heavy import is paid once per
    # process, then free). Over budget every further question answers
    # "unknown", never "absent". Env twin: CMBAGENT_LG_API_IMPORT_BUDGET_S.
    api_preflight_import_budget_s: float = 6.0
    # Shared directory for the observed-absence store (`api_absent.jsonl`, see
    # `self_debug/api_history.py`). None → `{work_dir}/logs/api_cache/`, which is
    # PER RUN: a fleet that wants facts to carry across projects and iterations
    # MUST point every worker at one directory, here or through the env twin
    # CMBAGENT_LG_API_CACHE_DIR. Precedence, as for every CMBAGENT_LG_API_* twin:
    # the environment variable, when set, overrides this field.
    api_preflight_cache_dir: str | None = None
    # A top-level package the script imports at module level that is NOT
    # installed: "pass" lets it run (an installer will handle it), "report"
    # bounces it as a finding, "auto" reports only when this run has no way to
    # install anything (`api_preflight_can_install` below) AND a previous run in
    # this environment already died on that import (the store, re-verified).
    # Env twin: CMBAGENT_LG_API_MISSING_PACKAGE (overrides).
    api_preflight_missing_package: str = "auto"
    # Can this run add a package? The "auto" policy above needs to know. None →
    # only `enable_escalation` says yes; an integrator with its own installer
    # sets True explicitly (no agent-naming convention is consulted). Env twin:
    # CMBAGENT_LG_API_CAN_INSTALL=1/0 (overrides).
    api_preflight_can_install: bool | None = None
    # Path to a JSON file of DECLARATIVE typing entries for libraries the
    # package itself does not know (which class a factory produces, what
    # iterating a call yields, which forwarder hands its keywords on), in the
    # shape `self_debug/api_types.py` documents under "extensions". Each entry is
    # witness-gated against the installed package and silently disabled when the
    # witness fails. None → nothing loaded, behaviour identical. The package's
    # own table names only universal libraries; a deployment's domain entries
    # live in its file. Env twin: CMBAGENT_LG_API_EXTENSIONS (overrides).
    api_preflight_extensions: str | None = None
    # Render the store's facts for this environment ("KNOWN ABSENT IN THIS
    # ENVIRONMENT") in the engineer's first-attempt prompt. A no-op while the
    # store is empty, so an unset store leaves the prompt byte-identical. Env
    # twin: CMBAGENT_LG_API_HISTORY_PROMPT=0 to disable (overrides).
    # Related, informational only: CMBAGENT_LG_WORKER stamps each store record
    # with a stable worker label (default host:pid); nothing reads it back.
    api_history_in_prompt: bool = True

    # ── bypass-after-fault guard ─────────────────────────────────────────
    # Covers the gap the three defences above cannot see: they assume the
    # engineer TRIES to call the library. This fires when a real removed-API
    # fault happened (the `classify_api_error` "module" shape) and the NEXT
    # attempt succeeds by dropping the faulted module entirely and hand-rolling
    # the computation — exit 0 that looks like a win but can be silently wrong.
    # The step goes back to the engineer ONCE (bounded, mirrors
    # `max_api_preflight_bounces`) with a steer note naming the library. A NUDGE,
    # not a block: the code already ran and is never prevented from executing,
    # and the note allows keeping the hand-rolled version if verified equivalent.
    # Sets `bypass_after_fault_detected_live=True` on state — a separate live
    # signal, not a mutation of `status`/`science`; scoring is unchanged.
    # Also settable with `CMBAGENT_LG_BYPASS_AFTER_FAULT_GUARD=1`.
    enable_bypass_after_fault_guard: bool = False

    # ── proactive API digest ─────────────────────────────────────────────
    # The earliest of the three interception points, independent of
    # `enable_api_preflight` so the two can be enabled separately:
    #   digest     — facts in the prompt      → the stale call is never written
    #   preflight  — check the emitted code   → the stale call is never RUN
    #   introspect — explain the traceback    → the stale call runs and fails
    # A deterministic (no-LLM) inventory of the packages the step is likely to
    # use — exact installed versions, submodule lists, and the public names of
    # any module the task names explicitly — is injected into the engineer's
    # FIRST-attempt prompt. Candidates come from the task/bullets and from
    # `hardware_constraints`; no library is named in the prompt text itself.
    # Also settable with `CMBAGENT_LG_API_DIGEST=1`. LOCAL EXECUTION ONLY —
    # under `CMBAGENT_LG_REMOTE_EXECUTOR` it would state the engine's versions as
    # if they were the sandbox's, so it no-ops.
    enable_api_digest: bool = False
    # Hard ceiling on the injected block, in CHARACTERS (~3.9 chars per token for
    # this content). The digest is paid on every clean step too, so an exhaustive
    # inventory can cost more than the failures it prevents. Content is assembled
    # in value-per-token order and whole tiers are dropped at the cap,
    # cheapest-to-lose first; the version manifest is never dropped.
    api_digest_max_chars: int = DEFAULT_API_DIGEST_MAX_CHARS

    # ── proactive intent gate ────────────────────────────────────────────
    # The proactive half of the anti-fabrication defence (`safe_loading` is the
    # post-hoc backstop). Fires ONLY when `preflight` finds `work_dir/data/`
    # EMPTY — a populated input directory costs zero extra calls. In that case
    # one cheap classifier call decides, from the sub-task text alone, whether
    # the task NEEDS input that was supposed to be PROVIDED or whether CREATING
    # the data is itself the point of the step. When it says "needs provided
    # input":
    #   (a) a hard do-NOT-fabricate directive is injected into the engineer's
    #       FIRST-attempt prompt;
    #   (b) if the engineer disobeys and the run nevertheless completes cleanly,
    #       the router short-circuits to the terminal `missing_input` node — a
    #       clean success with no input to load IS the fabrication.
    # FAIL-OPEN: any error, timeout or unparseable answer is treated as "does
    # not need provided input", i.e. the ordinary flow.
    enable_intent_gate: bool = True
    # Model for that one classifier call. None → `formatter_model` (the cheap
    # structured-output role); it is a two-field yes/no judgement.
    intent_gate_model: str | None = None
