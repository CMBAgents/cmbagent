"""Engineer → format_engineer → executor → execution_evaluator → step_evaluator.

Two gates sharing one retry budget (`max_n_attempts`): `execution_evaluator`
judges only whether the code RAN cleanly, `step_evaluator` whether the run
ACHIEVED the sub-task's goal, so a run can pass the first and fail the second.
`error_history` accumulates one summary per failed attempt for the retries.

Between `format_engineer` and the executor sit two deterministic pre-flights
(`static_preflight.py`: does it parse, does it print; `api_preflight.py`: do the
third-party names exist) — see the graph module for the wiring.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext, effective_code_execution_timeout
from cmbagent_lg.llms import answer_mode, answer_problem, chat_model, model_failure_types
from cmbagent_lg.prompt_utils import schema_field_brief
from cmbagent_lg.self_debug.api_digest import digest_for_step as api_digest_for_step
from cmbagent_lg.self_debug import attempt_manifest as _attempt_manifest
from cmbagent_lg.self_debug import api_history as _api_history
from cmbagent_lg.self_debug.api_introspect import retry_report as api_retry_report
from cmbagent_lg.self_debug.api_introspect import classify_api_error as _bypass_classify_api_error
from cmbagent_lg.self_debug.api_introspect import module_shape_report as _bypass_module_shape_report
from cmbagent_lg.self_debug.prompts import (
    engineer_instructions,
    evaluator_instructions,
    step_evaluator_instructions,
    render_retry_context,
)
from cmbagent_lg.self_debug.schemas import (
    EngineerResponse,
    ExecutionVerdict,
    StepVerdict,
    infeasible_substitutions,
    step_goal_met,
)
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


# Per-role chat models from the run context (None → llms._DEFAULT_MODEL).
# chat_model caches by (model, role), so these stay lazy.
def _proposer(ctx: PlanContext):
    return chat_model(ctx.engineer_model, "coder")


def _critic(ctx: PlanContext):
    return chat_model(ctx.evaluator_model, "critic")


def _formatter(ctx: PlanContext):
    return chat_model(ctx.formatter_model, "formatter")


# ── generators ──────────────────────────────────────────────────────────


@timed_node("engineer")
def engineer(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Propose code for the current sub-task. Bumps `attempts`."""
    ctx = runtime.context
    step = state["step"]
    attempts = state.get("attempts", 0) + 1
    error_history = state.get("error_history", [])

    last_code = None
    last_stderr = None
    last_stdout = None
    last_returncode = None
    last_timed_out = None
    last_elapsed_s = None
    last_fix_suggestion = None
    last_step_unmet = None
    last_step_feedback = None
    last_step_infeasible = None
    last_image_issues = None
    last_image_suggestions = None
    api_introspect_report = None
    # One-shot: set by `api_preflight` when it refused to execute the last emitted code,
    # consumed here and cleared in the return below. None on every ordinary path.
    api_preflight_report = state.get("api_preflight_report")
    # One-shot (mirrors `api_preflight_report`): set by `execution_evaluator` when this
    # attempt SUCCEEDED but dropped a library that faulted on an earlier attempt.
    bypass_after_fault_report = state.get("bypass_after_fault_report")
    if attempts > 1:
        prev = state.get("current_code")
        last_code = prev.python_code if prev else None
        last_stdout = state.get("execution_stdout")
        exec_verdict = state.get("current_execution_verdict")
        # Pick the retry mode from the last verdict: showing a stale step/image
        # verdict after a code crash would misexplain the retry.
        if exec_verdict is not None and exec_verdict.status == "failure":
            last_stderr = state.get("execution_stderr")
            last_returncode = state.get("execution_returncode")
            last_timed_out = state.get("execution_timed_out")
            last_elapsed_s = state.get("execution_elapsed_s")
            last_fix_suggestion = exec_verdict.fix_suggestion
        elif bypass_after_fault_report:
            # The router sent this back before image_reviewer/step_evaluator ran on the
            # bypassing code, so any step/image verdict in state is an EARLIER attempt's.
            pass
        else:
            step_verdict = state.get("current_step_verdict")
            if step_verdict is not None:
                last_step_unmet = step_verdict.unmet_requirements
                last_step_feedback = step_verdict.feedback
                # Only ever PARTIAL here: a verdict whose every unmet item is declared
                # infeasible ends the step at the goal gate and never reaches a retry.
                last_step_infeasible = (
                    getattr(step_verdict, "infeasible_requirements", None) or None
                )
            review = state.get("current_image_review")
            if review is not None and review.needs_revision:
                last_image_issues = review.issues
                last_image_suggestions = review.suggestions
        # Deliberately OUTSIDE the failure branch and given stdout as well as stderr: the
        # engineer often try/excepts and PRINTS the error, exiting 0 with empty stderr, so
        # a stderr-only check in the branch above would miss the API fault entirely.
        # Skipped when the pre-flight refused to run this code — the output in state is
        # then an EARLIER attempt's. Returns None unless `api_introspect_retry` is on.
        if api_preflight_report is None:
            api_introspect_report = api_retry_report(
                ctx, last_stderr, last_code, last_stdout, state.get("work_dir")
            )

    retry_block = render_retry_context(
        attempts=attempts,
        max_attempts=ctx.max_n_attempts,
        last_code=last_code,
        last_stderr=last_stderr,
        last_stdout=last_stdout,
        last_returncode=last_returncode,
        last_timed_out=last_timed_out,
        last_elapsed_s=last_elapsed_s,
        # The budget the run was killed against, so the timeout steer can quote both
        # numbers ("ran 121s, exceeded the 120s budget").
        timeout_s=effective_code_execution_timeout(ctx, step),
        last_fix_suggestion=last_fix_suggestion,
        error_history=error_history,
        last_step_unmet=last_step_unmet,
        last_step_feedback=last_step_feedback,
        last_step_infeasible=last_step_infeasible,
        last_image_issues=last_image_issues,
        last_image_suggestions=last_image_suggestions,
        # Loader guidance from the data_loader_fixer's file inspection; persists in
        # state, so it keeps informing later attempts too.
        data_fixer_guidance=state.get("data_fixer_guidance"),
        api_introspect_report=api_introspect_report,
        api_preflight_report=api_preflight_report,
        bypass_after_fault_report=bypass_after_fault_report,
    )

    system = engineer_instructions(
        ctx,
        step,
        retry_block,
        previous_steps_execution_summary=state.get("previous_steps_execution_summary") or "",
        # Factual FILE REPORT on the FIRST attempt only (later attempts have their own
        # error trail). No-op unless `enable_preflight` produced a report.
        preflight_report=(state.get("preflight_report") if attempts <= 1 else None),
        # The do-not-fabricate directive goes on EVERY attempt, unlike the FILE REPORT
        # above: "no input was ever provided" is attempt-invariant, and dropping it is
        # how a retry talks itself into generating a stand-in. Both None unless the
        # intent gate flagged the step.
        needs_provided_input=state.get("needs_provided_input"),
        input_dir_empty=state.get("input_dir_empty"),
        # `enable_api_digest`: the installed packages' real API, stated before any code is
        # written. A pure function of ctx + step, hence no node of its own. First attempt
        # only — from attempt 2 the traceback is more specific and cheaper.
        api_digest=_first_attempt_api_facts(ctx, step, state.get("work_dir"), attempts),
    )
    user = (
        "Produce the script now, in the format described above. Write in natural "
        "prose around a single Python code block — a downstream specialist will "
        "extract the structured fields. Make sure your response covers:\n\n"
        + schema_field_brief(EngineerResponse)
    )
    msg = _proposer(runtime.context).invoke(
        [SystemMessage(system), HumanMessage(user)],
        config={"tags": ["engineer"]},
    )
    # Clearing the two reports is what makes them one-shot: leaving either set would
    # re-render it on every later retry and keep routing state that no longer applies.
    return {
        "raw_engineer": msg.text,
        "attempts": attempts,
        "api_preflight_report": None,
        "bypass_after_fault_report": None,
    }


def _first_attempt_api_facts(ctx, step, work_dir, attempts: int):
    """The API facts block for the FIRST attempt: what previous runs in this environment
    proved absent (`api_history.prompt_tier`, on by default and empty until a run records
    something) followed by the opt-in inventory digest. None on later attempts, where the
    traceback or the pre-flight report is the more specific fact."""
    if attempts > 1:
        return None
    parts = [_api_history.prompt_tier(ctx, work_dir), api_digest_for_step(ctx, step, work_dir)]
    parts = [p for p in parts if p]
    return "\n\n".join(parts) if parts else None


def _record_api_fact(ctx, code: str, stderr: str, stdout: str, work_dir) -> None:
    """Hand a finished run's traceback to the observed-absence store. Never raises, never
    changes the run: `record_from_run` swallows everything and writes at most one line."""
    try:
        _api_history.record_from_run(ctx, code, stderr, stdout, work_dir)
    except Exception as e:  # noqa: BLE001
        print(f"[api_history] recorder skipped (non-fatal): {e}", file=sys.stderr, flush=True)


@timed_node("format_engineer")
def format_engineer(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Turn the engineer's answer into a typed `EngineerResponse`.

    The script is read deterministically (`code_extract.extract_engineer_response`): the fenced block after
    `**Python Code:**`, else the last python block, with the explanation and modifications from their sections. Only
    an answer with no usable fence (none, a blank block, or one cut off that does not parse) goes to the formatter
    model, and its script is normalised the same way. Copying a whole script into a JSON string was a second long call per attempt for an open formatter, could overflow or fail to
    validate, and once handed the executor a JSON-escaped one-line script (b13-1, rusty, 23 Nov 2026).

    An empty engineer answer (a model that returned no text, e.g. after spending its budget on
    thinking) is turned into a script that fails with that diagnosis, so the attempt is counted and
    retried like any failed run; sending the empty text to the formatter crashed the whole graph
    ("contents are required")."""
    from cmbagent_lg.self_debug.code_extract import extract_engineer_response, normalise_code

    raw = state.get("raw_engineer") or ""
    if not raw.strip():
        print("[format_engineer] the engineer returned an empty answer; this attempt fails", file=sys.stderr, flush=True)
        return {"current_code": EngineerResponse(
            python_code='if __name__ == "__main__":\n    raise SystemExit("the engineer returned an empty answer: no code was written")\n',
            code_explanation="The engineer returned an empty answer, so no script was written; this attempt fails and is retried.")}
    obj, how = extract_engineer_response(raw)
    if obj is not None:
        print(f"[format_engineer] script read from {how} ({obj.python_code.count(chr(10))} lines); "
              f"no formatter call", file=sys.stderr, flush=True)
        return {"current_code": obj}
    print("[format_engineer] no usable fenced code block in the engineer's answer (none, blank, or cut off and not "
          "parsing); asking the formatter",
          file=sys.stderr, flush=True)
    structured = _formatter(runtime.context).with_structured_output(EngineerResponse)
    sys_prompt = SystemMessage(
        "You are a formatter. Convert the user's text into an EngineerResponse "
        "object. Preserve the Python code VERBATIM — do not reformat, lint, or "
        "alter it. The code block is the substantive content; do not summarize it."
    )
    obj = structured.invoke(
        [sys_prompt, HumanMessage(raw)],
        config={"tags": ["format_engineer"]},
    )
    return {"current_code": obj.model_copy(update={"python_code": normalise_code(obj.python_code)})}


# ── on-disk layout ───────────────────────────────────────────────────────
#
#   {work_dir}/codebase/step_{N}.py              ← canonical, overwritten each attempt
#   {work_dir}/codebase/step_{N}.log             ← stdout+stderr of the latest run
#   {work_dir}/codebase/step_{N}_failure_{I}.py  ← attempt I's code, kept on failure
#   {work_dir}/codebase/step_{N}_failure_{I}.log ← attempt I's run log, kept on failure
#   {work_dir}/data/                                  ← the conventional output dir
#   {work_dir}/<anything else>                        ← outputs a step writes elsewhere
#                                                       (answer.json, work/spectra.npz)
#   {work_dir}/logs/step_{N}_execution_verdict.json   ← did the code RUN cleanly
#   {work_dir}/logs/step_{N}_data_manifest.json       ← which files the latest attempt
#                                                       wrote, and the stdout path checks
#
# On failure the evaluator demotes `step_{N}.{py,log}` → `step_{N}_failure_{I}.*`,
# freeing the canonical name for the next attempt's overwrite. The manifest records
# step→file provenance externally: output files and their names are never touched.
# It covers the whole run directory, recursively, minus the engine's own directories
# (codebase/, logs/, planning/, reports/, chats/) and caches — see `attempt_manifest`.


def _codebase_dir(state: DebugState) -> Optional[Path]:
    """`{work_dir}/codebase/` — generated scripts live here. None if no work_dir."""
    raw = state.get("work_dir")
    if not raw:
        return None
    out = Path(raw).expanduser() / "codebase"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _logs_dir(state: DebugState) -> Optional[Path]:
    """`{work_dir}/logs/` — verdicts + run metadata, flat, sibling of codebase/.

    Files are step-prefixed, not nested per step. None if no work_dir.
    """
    raw = state.get("work_dir")
    if not raw:
        return None
    out = Path(raw).expanduser() / "logs"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _run_log(returncode: int, timed_out: bool, elapsed: float, stdout: str, stderr: str) -> str:
    return (
        f"# returncode={returncode} timed_out={timed_out} elapsed={elapsed:.4f}s\n"
        f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n"
    )


def _first_sentence(text: Optional[str], limit: int = 200) -> str:
    """The first sentence (or line) of a free-text field, for quoting in a steer."""
    if not text:
        return ""
    head = text.strip().splitlines()[0] if text.strip() else ""
    m = re.match(r"(.+?)[.!?](\s|$)", head)
    out = (m.group(1) if m else head).strip()
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _signal_steer(returncode: Optional[int], stderr: Optional[str], ctx) -> str:
    """Text to append to stderr when the script died by SIGNAL and left no traceback.

    Popen reports a signal death as `-signum`. A kill from OUTSIDE the interpreter (the
    cgroup OOM killer sends SIGKILL; a native crash raises SIGSEGV/SIGABRT) raises no
    Python exception, so both streams can come back EMPTY — and an empty failure reads
    to the engineer as "nothing happened", which is retried blind. Measured on the
    qm9-joint fleet: 21 of 918 failed attempts were signal deaths with empty stdout AND
    stderr. Names the mechanism and the concrete escape routes; quotes the run's own
    `hardware_constraints` because that is where the memory cap is stated. "" when the
    death was not by signal or stderr already carries a traceback that explains it.
    """
    if returncode is None or returncode >= 0:
        return ""
    if "Traceback" in (stderr or ""):
        return ""
    signum = -returncode
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = "unknown signal"
    hw = _first_sentence(getattr(ctx, "hardware_constraints", None))
    hw_note = f' — see the hardware constraints: "{hw}"' if hw else ""
    if signum == getattr(signal, "SIGKILL", 9):
        cause = (
            "A SIGKILL comes from OUTSIDE the interpreter, so no exception was raised and "
            "nothing was printed; on this fleet it is the cgroup memory cap (out-of-memory "
            f"kill){hw_note}."
        )
    else:
        cause = (
            "The signal came from outside the interpreter (a native crash in a compiled "
            "extension, or an out-of-memory kill), so no exception was raised and nothing "
            f"was printed{hw_note}."
        )
    return (
        f"\n[killed by signal {signum} ({name}) with no Python traceback. {cause} "
        "Reduce the working set (fewer columns/rows held at once, float32 instead of "
        "float64, chunked/streamed processing) or the parallel fan-out (each "
        "joblib/multiprocessing worker holds its own copy of the data).]"
    )


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGTERM → SIGKILL the timed-out script's whole process GROUP.

    Relies on the subprocess being launched with `start_new_session=True`, which makes it
    its own group leader (group id == pid) so `killpg` reaches every descendant —
    otherwise `multiprocessing` workers survive the kill. Never raises: the group may
    already be gone, and a failed kill must not crash the graph.
    """
    killpg = getattr(os, "killpg", None)  # POSIX only
    for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, None)):
        try:
            if killpg is not None:
                killpg(proc.pid, sig)
            else:  # pragma: no cover — non-POSIX fallback, direct child only
                proc.kill()
                return
        except (ProcessLookupError, PermissionError, OSError):
            return  # already reaped, or not ours to signal
        if grace is None:
            return  # SIGKILL sent; communicate() below drains and reaps it
        try:
            proc.wait(timeout=grace)
            return  # died politely on SIGTERM
        except subprocess.TimeoutExpired:
            continue  # still alive → escalate to SIGKILL


def _snapshot_data_dir(data_dir: Path) -> dict:
    """Map filename → mtime for every file at the top level of `data/`.

    Only the `input_dir_empty` fact reads this ("was data/ empty before any code ran").
    What an attempt WROTE is the recursive run-directory diff in `attempt_manifest`."""
    if not data_dir.is_dir():
        return {}
    return {p.name: p.stat().st_mtime for p in data_dir.iterdir() if p.is_file()}


def _write_manifest_log(state: DebugState, n: int, step, manifest: list, info: dict) -> None:
    """`logs/step_{N}_data_manifest.json`: provenance, external to the files themselves."""
    logs = _logs_dir(state)
    if logs is None:
        return
    (logs / f"step_{n}_data_manifest.json").write_text(
        json.dumps(
            {
                "step_number": n,
                "sub_task": step.sub_task,
                "attempt": state.get("attempts", 0),
                "files": manifest,
                "scope": info.get("scope"),
                "complete": info.get("complete"),
                "incomplete_reason": info.get("incomplete_reason"),
                "omitted": info.get("omitted", 0),
                "symlinked_dirs": info.get("symlinked_dirs", []),
                "partial_dirs": info.get("partial_dirs", []),
                "stdout_paths": info.get("stdout_paths", []),
                "carried_over": info.get("carried_over"),
            },
            indent=2,
        )
    )


# ── executor ────────────────────────────────────────────────────────────


def _run_remote(spec: str, state: DebugState, codebase: Path, n: int, step, code: str,
                timeout: int, ctx=None) -> DebugState:
    """Remote execution backend (`CMBAGENT_LG_REMOTE_EXECUTOR=host:port`).

    Writes step_N.py to the engine-side codebase/ (provenance stays here) but ships EVERY
    step to the guest agent, so cross-step imports resolve on a disposable sandbox. The
    data lives guest-side; the evaluators read stdout from state, so they are unaffected.
    """
    from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

    (codebase / f"step_{n}.py").write_text(code)
    code_files = {p.name: p.read_text() for p in sorted(codebase.glob("step_*.py"))}
    session = Path(state["work_dir"]).name

    t0 = time.perf_counter()
    res = RemoteExecutor.from_env(spec).run_code(session, n, code_files, timeout)
    execution_elapsed_s = time.perf_counter() - t0

    stdout = res.get("stdout", "")
    stderr = res.get("stderr", "")
    returncode = res.get("returncode", 1)
    timed_out = bool(res.get("timed_out"))
    manifest = res.get("manifest", [])
    # Same steer as the local path, applied to the guest's own result dict — the wire
    # protocol is untouched, only what the evaluators and the engineer read from state.
    if not timed_out:
        stderr = (stderr or "") + _signal_steer(returncode, stderr, ctx)
    print(
        f"[time] executor.remote: {execution_elapsed_s:.2f}s "
        f"(guest={res.get('elapsed_s')}s, rc={returncode}, timed_out={timed_out})",
        file=sys.stderr, flush=True,
    )

    (codebase / f"step_{n}.log").write_text(
        _run_log(returncode, timed_out, res.get("elapsed_s", execution_elapsed_s), stdout, stderr)
    )
    if returncode not in (0, None):
        _record_api_fact(ctx, code, stderr, stdout, state.get("work_dir"))
    # The guest's manifest is the top level of ITS data/, as before; the info says so, so
    # the evaluator is not told the run directory was checked recursively.
    info = _attempt_manifest.remote_info()
    _write_manifest_log(state, n, step, manifest, info)
    return {
        "execution_stdout": stdout,
        "execution_stderr": stderr,
        "execution_returncode": returncode,
        "execution_timed_out": timed_out,
        "execution_elapsed_s": execution_elapsed_s,
        "data_baseline": {},  # baseline is snapshotted guest-side in remote mode
        "data_manifest": manifest,
        "data_manifest_info": info,
    }


@timed_node("executor")
def executor(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Write `codebase/step_{N}.py` and run it in a subprocess (host venv).

    The subprocess runs with `cwd = work_dir`, so the script's relative output
    paths (`data/<file>`) land in `{work_dir}/data/` — cmbagent's convention.
    The script's own directory (`codebase/`) stays on `sys.path[0]` regardless
    of cwd, so cross-step `from step_1 import …` still resolves.

    The run directory is snapshotted recursively right before the script starts and
    again after it ends; `data_manifest` lists what changed, wherever it was written,
    and `data_manifest_info` says how complete that listing is and how each file path
    stdout names checked out (see `attempt_manifest`).
    """
    ctx = runtime.context
    code = state["current_code"].python_code
    step = state["step"]
    n = state.get("step_number", 1)
    # Always a real number — an unset timeout does not mean "unbounded".
    timeout = effective_code_execution_timeout(ctx, step)
    codebase = _codebase_dir(state)

    # Remote backend (opt-in): run on a guest agent so the engine/source stays off the
    # execution machine.
    _remote = os.environ.get("CMBAGENT_LG_REMOTE_EXECUTOR")
    if _remote and codebase is not None:
        return _run_remote(_remote, state, codebase, n, step, code, timeout, ctx)

    # Canonical name when work_dir is set; ephemeral tempfile otherwise (deleted in the
    # finally below — the evaluator reads output from state, not disk).
    data_dir = None
    data_baseline = state.get("data_baseline")
    if codebase is not None:
        code_path = codebase / f"step_{n}.py"
        # {work_dir}/data/ — where the script saves plots & generated data.
        run_cwd = codebase.parent
        data_dir = run_cwd / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        # Top level of data/ only, for the `input_dir_empty` fact below.
        data_baseline = _snapshot_data_dir(data_dir)
    else:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            code_path = Path(f.name)
        run_cwd = None
    if data_baseline is None:
        data_baseline = {}
    code_path.write_text(code)

    # Must be absolute, or it would resolve relative to the cwd handed to the subprocess.
    code_path = code_path.resolve()
    if run_cwd is None:
        run_cwd = code_path.parent

    # Re-snapshot at the START of *every* attempt, so the manifest holds the LATEST
    # attempt's outputs only: files written by an earlier step or failed attempt land in
    # this baseline and are excluded. Each attempt is a complete standalone script.
    tree_before = _attempt_manifest.snapshot_tree(run_cwd) if codebase is not None else None
    # "Changed since the attempt started" is judged on the filesystem's own clock where
    # there is one (a sentinel touched in logs/), so a skewed NFS/ceph server clock does
    # not misclassify the attempt's files.
    started_ns = _attempt_manifest.attempt_start_ns(
        _logs_dir(state) if codebase is not None else None)
    # The step's history, so the evaluator can tell a file an EARLIER ATTEMPT OF THIS STEP
    # wrote (a resumable script skips it) from one that was on disk before the step began.
    # The baseline is the snapshot taken before the step's first executed attempt: state
    # is fresh for each step (deep_research invokes the graph once per step) and persists
    # across its attempts, so the first executor pass with no `step_baseline` takes it.
    step_baseline = state.get("step_baseline")
    new_baseline = None
    if not isinstance(step_baseline, dict):
        step_baseline = new_baseline = _attempt_manifest.freeze_baseline(tree_before, started_ns)
    history = _attempt_manifest.StepHistory(
        step_baseline, state.get("step_writes"), state.get("attempts", 0))
    timed_out = False
    t0 = time.perf_counter()
    try:
        # Popen + communicate rather than subprocess.run, so a timeout can kill the whole
        # process GROUP: `start_new_session=True` makes the script its own group leader,
        # so its multiprocessing workers die with it instead of being orphaned.
        proc = subprocess.Popen(
            [sys.executable, str(code_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(run_cwd),
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc)
            # Drain whatever the script wrote before the kill; the group is dead, so this
            # returns immediately.
            stdout, stderr = proc.communicate()
            stderr = (stderr or "") + (
                f"\n[killed: TimeoutExpired after {timeout}s — the whole "
                f"process group was terminated]"
            )
            # 124 is the GNU `timeout` convention; the `timed_out` flag carries
            # the real signal — see DebugState.execution_timed_out.
            returncode = 124
    finally:
        if codebase is None:
            code_path.unlink(missing_ok=True)
    execution_elapsed_s = time.perf_counter() - t0
    # A signal death with nothing on either stream: say what it was, or the engineer
    # retries blind (the timeout branch above has its own marker and rc 124, so the two
    # steers never both fire).
    if not timed_out:
        stderr = (stderr or "") + _signal_steer(returncode, stderr, ctx)
    print(
        f"[time] executor.subprocess: {execution_elapsed_s:.2f}s (timeout={timeout}s, timed_out={timed_out})",
        file=sys.stderr,
        flush=True,
    )

    try:
        if tree_before is not None:
            manifest, manifest_info = _attempt_manifest.build(
                tree_before, _attempt_manifest.snapshot_tree(run_cwd), started_ns,
                stdout or "", run_cwd, run_cwd, history=history)
        else:
            manifest, manifest_info = _attempt_manifest.no_work_dir_info(
                stdout or "", run_cwd, started_ns, history=history)
    except Exception as e:  # noqa: BLE001 — bookkeeping must never fail the attempt
        print(f"[executor] output manifest not built (non-fatal): {e}", file=sys.stderr, flush=True)
        manifest, manifest_info = [], {
            "scope": "work_dir" if tree_before is not None else "none", "complete": False,
            "incomplete_reason": f"the scan failed ({type(e).__name__})", "omitted": 0,
            "symlinked_dirs": [], "partial_dirs": [], "stdout_paths": [],
            "started_ns": started_ns}

    if returncode != 0 and not timed_out:
        _record_api_fact(ctx, code, stderr, stdout, state.get("work_dir"))

    if codebase is not None:
        (codebase / f"step_{n}.log").write_text(
            _run_log(returncode, timed_out, execution_elapsed_s, stdout, stderr)
        )
        # Provenance, external to the files themselves (names + bytes untouched).
        _write_manifest_log(state, n, step, manifest, manifest_info)

    out: DebugState = {
        "execution_stdout": stdout,
        "execution_stderr": stderr,
        "execution_returncode": returncode,
        "execution_timed_out": timed_out,
        "execution_elapsed_s": execution_elapsed_s,
        "data_baseline": data_baseline,
        "data_manifest": manifest,
        "data_manifest_info": manifest_info,
        "step_writes": _attempt_manifest.update_step_writes(
            state.get("step_writes"), manifest, state.get("attempts", 0)),
    }
    if new_baseline is not None:
        out["step_baseline"] = new_baseline
    # Not set in remote mode: engine-side data/ is not the exec dir.
    if data_dir is not None:
        fact = _initial_input_dir_empty(state, ctx, data_dir, data_baseline)
        if fact is not None:
            out["input_dir_empty"] = fact
    return out


def _initial_input_dir_empty(state: DebugState, ctx: PlanContext, data_dir: Path,
                             data_baseline: dict) -> Optional[bool]:
    """Data-safety fact (`safe_loading`): was data/ empty before ANY code ran?

    Captured ONCE on attempt 1 and never overwritten — a later attempt's baseline can
    contain a file an EARLIER attempt fabricated. Fallback for when `preflight` is off (it
    sets this first when on). Shared with `static_preflight`, which takes the reading when
    it refuses attempt 1 (nothing ran, so `data/` is still pristine) — otherwise the fact
    would never be set for that step. None when it is not this call's to set.
    """
    if state.get("input_dir_empty") is not None or state.get("attempts", 0) > 1:
        return None
    empty = not data_baseline
    if empty:
        # Same reading as pre-flight: an input the task names at an existing path
        # OUTSIDE data/ is provided input, and the step_evaluator's "everything the
        # script loaded was self-created" fact would be false.
        try:
            from cmbagent_lg.self_debug.preflight import named_input_for_step

            if named_input_for_step(state.get("step"), data_dir.parent, ctx) is not None:
                empty = False
        except Exception:  # noqa: BLE001 — never let the check break execution
            pass
    return empty


# ── bypass-after-fault guard (opt-in `enable_bypass_after_fault_guard`) ─────────────────
#
# Watches for one transition `api_preflight`/`api_introspect_retry` cannot see: a real
# module-shaped API fault fires on an EARLIER attempt this step, then a LATER attempt
# SUCCEEDS with code that no longer references the faulted module at all — i.e. it
# hand-rolled the computation and may be silently wrong.
#
# Deliberately narrow:
#   * only `classify_api_error`'s "module" shape; the "instance"/"signature"/
#     "artist_kwarg" shapes are the different mechanism the other guards cover.
#   * fires at most ONCE per step (`bypass_guard_nudges` capped at 1); no fault recorded
#     means no `bypass_guard_fault`, so the guard is a no-op by construction.
#   * a NUDGE, not a block — the bypassing code already ran and is never discarded.
#   * `bypass_after_fault_detected_live` is the permanent live signal; nothing here ever
#     mutates `status` or `science`.

_BYPASS_GUARD_KIND = "module"  # the only classify_api_error() shape this guard reads


def _bypass_guard_enabled(ctx: PlanContext) -> bool:
    return bool(getattr(ctx, "enable_bypass_after_fault_guard", False)) or (
        os.environ.get("CMBAGENT_LG_BYPASS_AFTER_FAULT_GUARD") == "1"
    )


def _bypass_guard_classify(stderr: Optional[str]) -> Optional[Tuple[str, str]]:
    """`(module, symbol)` for a real removed-API fault on `stderr`, or None.

    `api_introspect.classify_api_error` restricted to its "module" shape. stderr ONLY:
    the caught-and-printed/stdout form `retry_report` also reads is deliberately not
    accepted, keeping this guard's evidence anchored to an actual crash.
    """
    hit = _bypass_classify_api_error(stderr)
    if hit is None or hit[0] != _BYPASS_GUARD_KIND:
        return None
    return hit[1], hit[2]


def _bypass_guard_still_references(code: str, module: str, symbol: str) -> bool:
    """True if `code` still imports the faulted `module` (or `module.symbol`) anywhere —
    i.e. the fault was addressed by adapting the SAME call, not dropped.

    One-directional: the module itself, the dotted symbol, or a DEEPER submodule counts;
    a mere ANCESTOR package does not (importing it says nothing about the faulted symbol).
    Unparseable code counts as "still references it", so the guard stays silent rather
    than nudging on a guess.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return True
    dotted = f"{module}.{symbol}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = a.name
                if name == module or name == dotted or name.startswith(dotted + "."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == module or mod == dotted or mod.startswith(dotted + "."):
                return True
    return False


def _bypass_guard_report(module: str, symbol: str) -> str:
    """Nudge text for the next engineer attempt: a steer, never a claim that the
    hand-rolled code is wrong.

    Appends `api_introspect.module_shape_report`, the deterministic introspection of the
    faulted `(module, symbol)`, so the nudge also names the real replacement. That report
    is omitted (warning only) when it would be unprovable or environment-unsafe.
    """
    warning = (
        f"**BYPASS-AFTER-FAULT NOTE (deterministic, no model call) — an earlier attempt on "
        f"this step hit a real API fault: `{module}.{symbol}` is not in the installed "
        f"release (see the error trail above). The code that JUST RAN SUCCESSFULLY does "
        f"not import or call `{module}` anywhere at all.**\n\n"
        f"That is consistent with two different things: (a) a genuinely different, equally "
        f"valid approach, or (b) quietly working around the missing API by hand-rolling the "
        f"computation instead of finding what `{module}` was renamed or moved to in the "
        f"installed release. (b) is cheaper to write but risks a plausible, CONFIDENTLY "
        f"WRONG answer if any definitional detail the library handles (an edge case, an "
        f"excluded special case, an exact normalization) differs from the hand-rolled "
        f"version — exactly the failure mode this note exists to flag.\n\n"
        f"Before finalizing this approach: check whether `{module}` (or a nearby module in "
        f"the same package) offers the real replacement for what you are computing by hand, "
        f"and prefer that over a hand-rolled substitute. This is a NUDGE, not a proof that "
        f"the current code is wrong — if you have already verified the hand-rolled "
        f"computation is equivalent to the library's own definition, you may keep it."
    )
    real_report = _bypass_module_shape_report(module, symbol)
    if not real_report:
        return warning
    return warning + "\n\n" + real_report


def _bypass_guard_update(state: DebugState, ctx: PlanContext, verdict: "ExecutionVerdict") -> dict:
    """Partial state update from the bypass-after-fault guard, or `{}` when it has nothing
    to say (flag off, no prior fault, already nudged this step, or the faulted library is
    still referenced). Called from `execution_evaluator`.
    """
    if not _bypass_guard_enabled(ctx):
        return {}
    if verdict.status == "failure":
        hit = _bypass_guard_classify(state.get("execution_stderr"))
        return {"bypass_guard_fault": hit} if hit else {}
    # success
    fault = state.get("bypass_guard_fault")
    if not fault:
        return {}
    if (state.get("bypass_guard_nudges") or 0) >= 1:
        return {}
    code_obj = state.get("current_code")
    code = getattr(code_obj, "python_code", None) if code_obj is not None else None
    if not code:
        return {}
    module, symbol = fault
    if _bypass_guard_still_references(code, module, symbol):
        # Adapted rather than dropped: the fault is resolved, so forget it.
        return {"bypass_guard_fault": None}
    return {
        "bypass_after_fault_report": _bypass_guard_report(module, symbol),
        "bypass_after_fault_detected_live": True,
        "bypass_guard_nudges": (state.get("bypass_guard_nudges") or 0) + 1,
        "bypass_guard_fault": None,
    }


# ── execution_evaluator ─────────────────────────────────────────────────


@timed_node("execution_evaluator")
def execution_evaluator(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Did the code RUN cleanly? One structured-output call → ExecutionVerdict.

    This is *not* a step-goal judgment — see `step_evaluator`.
    """
    ctx = runtime.context
    step = state["step"]
    system = evaluator_instructions(
        ctx,
        step,
        executed_code=state["current_code"].python_code,
        stdout=state.get("execution_stdout", ""),
        stderr=state.get("execution_stderr", ""),
        returncode=state.get("execution_returncode", -1),
        timed_out=state.get("execution_timed_out", False),
    )
    user = (
        "Judge the execution above and emit a verdict. Cover these fields:\n\n"
        + schema_field_brief(ExecutionVerdict)
    )
    n = state.get("step_number", 1)
    attempt = state.get("attempts", 0)
    structured = _critic(runtime.context).with_structured_output(ExecutionVerdict)
    fallbacks = {}
    # Only an unusable answer, a timeout or a 5xx falls back (`llms.model_failure_types`); a wrong key or model name,
    # a server that is gone or a quota error still ends the run rather than failing every later step quietly.
    try:
        verdict: ExecutionVerdict = structured.invoke(
            [SystemMessage(system), HumanMessage(user)],
            config={"tags": ["execution_evaluator"]},
        )
        mode = answer_mode(verdict)
    except model_failure_types() as err:  # the run itself is judged below
        verdict = deterministic_execution_verdict(state)
        mode = "deterministic_fallback"
        fallbacks = {"execution_verdict_fallbacks": state.get("execution_verdict_fallbacks", 0) + 1}
        print(f"[execution_evaluator] step {n} attempt {attempt}: EVALUATOR ERROR "
              f"({type(err).__name__}: {' '.join(str(err).split())[:300]}); verdict decided from the run instead "
              f"(returncode, timeout, Traceback in stderr): {verdict.status}", file=sys.stderr, flush=True)
    fallbacks["execution_verdict_modes"] = list(state.get("execution_verdict_modes") or []) + [mode]

    history = list(state.get("error_history", []))
    if verdict.status == "failure":
        # Fall back to the last stderr line when the evaluator left `error_summary` empty.
        summary = verdict.error_summary or (
            state.get("execution_stderr", "").strip().splitlines() or ["(no stderr)"]
        )[-1]
        history.append(summary)

    logs = _logs_dir(state)
    if logs is not None:
        (logs / f"step_{n}_execution_verdict.json").write_text(_verdict_json(verdict, mode))

    # `{}` on every ordinary path — see the `_bypass_guard_*` helpers above.
    bypass_update = _bypass_guard_update(state, ctx, verdict)

    # Deterministic, no model call: was this failure a fact about the DATA (a fit that
    # cannot converge, a rank-deficient design) rather than a code fault? Names it on
    # the record and counts it, so `route_after_execution_evaluator` can stop retrying
    # a design the data cannot support. `{}` for every other failure.
    from cmbagent_lg.self_debug.data_infeasibility import observe as _observe_infeasible

    infeasible_update = _observe_infeasible(state, verdict, ctx)

    # Demote the canonical script/log so the next attempt can overwrite `step_{N}.py`
    # cleanly while the audit trail survives. A SUCCESS the bypass guard just flagged is
    # demoted too (own suffix): it is about to be retried, and its artifact would
    # otherwise be overwritten with no record of what was nudged away from.
    codebase = _codebase_dir(state)
    if codebase is not None and (verdict.status == "failure" or bypass_update.get(
            "bypass_after_fault_report")):
        suffix = "failure" if verdict.status == "failure" else "bypassed"
        for ext in ("py", "log"):
            canonical = codebase / f"step_{n}.{ext}"
            if canonical.exists():
                canonical.rename(codebase / f"step_{n}_{suffix}_{attempt}.{ext}")

    return {"current_execution_verdict": verdict, "error_history": history,
            **bypass_update, **infeasible_update, **fallbacks}


def _verdict_json(verdict, mode: str, problem: Optional[str] = None) -> str:
    """A verdict log file: the verdict's fields plus `answer_mode`, the attempt or fallback that produced it
    (`llms.answer_mode`; "deterministic_fallback", "failed_closed"), and `answer_problem` when the lenient retry check
    let a flawed answer through. Readers that validate the file as the schema ignore the extra keys."""
    d = verdict.model_dump(mode="json")
    d["answer_mode"] = mode
    if problem:
        d["answer_problem"] = problem
    return json.dumps(d, indent=2)


def deterministic_execution_verdict(state: DebugState) -> ExecutionVerdict:
    """The code gate's verdict without a model, for when the evaluator call fails: success iff the script exited 0,
    did not time out and printed no Traceback to stderr. A caught-and-printed error that exits 0 passes here; the
    step evaluator still judges the output."""
    rc = state.get("execution_returncode", -1)
    timed_out = bool(state.get("execution_timed_out", False))
    stderr = state.get("execution_stderr", "") or ""
    if rc == 0 and not timed_out and "Traceback" not in stderr:
        return ExecutionVerdict(status="success")
    last = (stderr.strip().splitlines() or [""])[-1].strip()
    if timed_out:
        what = f"the script timed out after {state.get('execution_elapsed_s', 0) or 0:.0f} s"
    elif "Traceback" in stderr:
        what = "the script raised an exception"
    else:
        what = f"the script exited with return code {rc}"
    return ExecutionVerdict(
        status="failure", failure_kind="other",
        error_summary=f"{what}" + (f": {last}" if last else "")
        + " (judged from the return code and stderr: the evaluator model gave no verdict)")


# ── step_evaluator ──────────────────────────────────────────────────────


@timed_node("step_evaluator")
def step_evaluator(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Did the run ACHIEVE the sub-task's goal? Runs only after the code is
    known to have executed cleanly. One structured-output call → StepVerdict.
    Judges from stdout + the data manifest (file paths/sizes, not contents).
    """
    ctx = runtime.context
    step = state["step"]
    prior_feedback = state.get("step_feedback_history", [])
    _code_obj = state.get("current_code")
    system = step_evaluator_instructions(
        ctx,
        step,
        stdout=state.get("execution_stdout", ""),
        data_manifest=state.get("data_manifest", []),
        data_manifest_info=state.get("data_manifest_info"),
        step_feedback_history=prior_feedback,
        # Safety fact: data/ held no provided input before the run, so anything "loaded"
        # was self-created. None when undetermined → the rule is not rendered.
        input_dir_empty=state.get("input_dir_empty"),
        # The executed code, so a requirement about a parameter/method is verifiable
        # without a re-run and fabrication is directly visible.
        code=getattr(_code_obj, "python_code", None) if _code_obj else None,
    )
    user = (
        "Judge whether the step goal was achieved and emit a verdict. "
        "Cover these fields:\n\n" + schema_field_brief(StepVerdict)
    )
    # An image_reviewer request that outlived its revision budget would otherwise vanish
    # here, letting a known-defective figure pass as fulfilled. Surfaced softly: ImageReview
    # carries no severity, so a cosmetic nit must not sink an otherwise-correct step.
    review = state.get("current_image_review")
    if review is not None and getattr(review, "needs_revision", False):
        issues = getattr(review, "issues", None) or []
        issue_lines = "\n".join(f"- {i}" for i in issues) or "- (see the reviewer's notes)"
        user += (
            "\n\nUNRESOLVED VISUAL REVIEW — the figure reviewer requested a revision "
            "and the revise-the-plot budget ran out before it could be fixed, so "
            "these figure defects REMAIN in the delivered figure(s):\n"
            f"{issue_lines}\n"
            "If any of these mean the figure does not actually show what the "
            "sub-task asked for — it is blank, plots the wrong quantity, or states "
            "a wrong result — then the step goal is NOT achieved: mark it "
            "unfulfilled and list the defect in the unmet requirements. Purely "
            "cosmetic issues (slightly dense ticks, minor styling) need not fail an "
            "otherwise-correct step."
        )
    n = state.get("step_number", 1)
    attempt = state.get("attempts", 0)
    structured = _critic(runtime.context).with_structured_output(StepVerdict)
    # A model failure (the call raised even after its structured retry) is not a verdict on the engineer's work:
    # the same run is judged again without using an attempt and without feedback to the engineer, and only
    # `_MAX_STEP_EVALUATOR_ERRORS` consecutive errors fail the attempt closed. Before, it ended the whole run.
    errors = state.get("step_evaluator_errors", 0)
    errors_total = state.get("step_evaluator_errors_total", 0)
    failed_closed = False
    # Only an unusable answer, a timeout or a 5xx (`llms.model_failure_types`) is absorbed; a wrong configuration or
    # a server that is gone ends the run as before.
    try:
        verdict: StepVerdict = structured.invoke(
            [SystemMessage(system), HumanMessage(user)],
            config={"tags": ["step_evaluator"]},
        )
        errors = 0
    except model_failure_types() as err:
        errors, errors_total = errors + 1, errors_total + 1
        failed_closed = errors >= _MAX_STEP_EVALUATOR_ERRORS
        detail = f"{type(err).__name__}: {' '.join(str(err).split())[:300]}"
        print(f"[step_evaluator] step {n} attempt {attempt}: EVALUATOR ERROR {errors} in a row ({detail}); "
              + ("failing closed: the attempt is not accepted" if failed_closed
                 else "judging the same run again (no attempt used, nothing sent to the engineer)"),
              file=sys.stderr, flush=True)
        _log_evaluator_error(state, n, attempt, errors, detail, failed_closed)
        if not failed_closed:
            return {"step_evaluator_outcome": "evaluator_error", "step_evaluator_errors": errors,
                    "step_evaluator_errors_total": errors_total}
        verdict = StepVerdict(fulfilled=False, feedback=_EVALUATOR_FAILED_FEEDBACK)
    mode = "failed_closed" if failed_closed else answer_mode(verdict)
    problem = None if failed_closed else answer_problem(verdict)

    # A requirement the evaluator declared IMPOSSIBLE in this environment is not a
    # goal-miss: the step is ACCEPTED with the substitution recorded, so the engineer is
    # not sent back to argue with a demand it cannot satisfy. Adjudicated
    # deterministically (`schemas.infeasible_substitutions`) and all-or-nothing — one
    # fixable unmet requirement still fails the step. `[]`/False on every ordinary path
    # and on a verdict from an older transcript, which has no such fields.
    # CHECK the infeasibility claim before acting on it. An "X does not exist here"
    # assertion is a fact about the environment or the data, and the bug this channel
    # fixes was an evaluator getting exactly such a fact wrong — so the engine resolves
    # the symbols the justification names, looks for an artifact the run actually wrote,
    # and refuses an over-broad declaration, rather than taking the verdict's word. The
    # audit is computed HERE, where the step, the manifest and the installed environment
    # are all in hand, and goes onto state so the goal gate, `deep_research.run_step`
    # and the step summary all read the same answer.
    from cmbagent_lg.self_debug.infeasibility_check import (
        audit_declaration,
        refutation_feedback,
    )

    audit = None if failed_closed else audit_declaration(verdict, step, state)
    state_with_audit = {**state, "infeasibility_check": audit}
    substituted = infeasible_substitutions(verdict, step, state_with_audit)
    accepted = verdict.fulfilled or bool(substituted)

    # A refuted claim is a goal-miss with a specific, actionable reason: the engineer is
    # told WHAT contradicted it (the real import path, or the file the run wrote), so the
    # retry is not another round of "it is impossible" / "try again".
    if audit is not None and audit.get("status") == "refuted" and not verdict.fulfilled:
        note = refutation_feedback(audit)
        print(f"[infeasibility_check] step {state.get('step_number', 1)}: REFUTED "
              f"({audit.get('reason')}) — {audit.get('detail')}",
              file=sys.stderr, flush=True)
        if note:
            verdict = verdict.model_copy(update={
                "feedback": note + ("\n\n" + (verdict.feedback or "")
                                    if verdict.feedback else "")})

    # On a goal-miss, record this attempt's feedback so the *next*
    # step_evaluator pass sees its own trail (mirrors `error_history`).
    feedback_history = list(prior_feedback)
    if not accepted and not failed_closed:     # a failed-closed attempt carries no feedback of the evaluator's own
        bits = []
        if verdict.unmet_requirements:
            bits.append("unmet — " + "; ".join(verdict.unmet_requirements))
        if verdict.feedback:
            bits.append("feedback — " + verdict.feedback)
        feedback_history.append(
            f"attempt {attempt}: " + (" | ".join(bits) if bits else "goal not met")
        )

    logs = _logs_dir(state)
    if logs is not None:
        (logs / f"step_{n}_verdict.json").write_text(_verdict_json(verdict, mode, problem))
        # Preserve rejections too: the canonical file is overwritten each attempt, so the
        # accepted verdict would otherwise clobber the "why did it retry" trail.
        if not accepted:
            (logs / f"step_{n}_verdict_failure_{attempt}.json").write_text(
                _verdict_json(verdict, mode, problem))

    # A goal-miss is a failure too: demote this attempt's script/log so the audit trail
    # covers BOTH gates (the code ran cleanly, so execution_evaluator left the canonical
    # name in place). An attempt fails exactly one gate, so `failure_{attempt}` is unique.
    # An accepted-with-substitution attempt is a PASS and keeps the canonical names.
    codebase = _codebase_dir(state)
    if codebase is not None and not accepted:
        for ext in ("py", "log"):
            canonical = codebase / f"step_{n}.{ext}"
            if canonical.exists():
                canonical.rename(codebase / f"step_{n}_failure_{attempt}.{ext}")

    if substituted:
        print(f"[step_evaluator] step {n}: {len(substituted)} requirement(s) declared "
              f"INFEASIBLE in this environment — accepting the step with the "
              f"substitution recorded instead of retrying: "
              f"{'; '.join(substituted)[:300]}", file=sys.stderr, flush=True)

    return {
        "current_step_verdict": verdict,
        "step_feedback_history": feedback_history,
        # None when the verdict made no infeasibility claim, so an ordinary step's state
        # is unchanged; `_evidence_refuses` fails closed on a missing key, not a None one.
        "infeasibility_check": audit,
        "step_evaluator_outcome": "failed_closed" if failed_closed else "verdict",
        "step_evaluator_errors": errors,
        "step_evaluator_errors_total": errors_total,
        # One entry per attempt judged: which model attempt or fallback produced its verdict.
        "step_verdict_modes": list(state.get("step_verdict_modes") or []) + [mode],
    }


# Consecutive step_evaluator model failures on one step before an attempt fails closed.
_MAX_STEP_EVALUATOR_ERRORS = 2

_EVALUATOR_FAILED_FEEDBACK = (
    "No verdict: the step evaluator's model failed on this attempt "
    f"{_MAX_STEP_EVALUATOR_ERRORS} times in a row, so the attempt is not accepted. Nothing specific was found "
    "wrong; keep what works, and make sure the script prints every result the step asks for.")


def _log_evaluator_error(state: DebugState, n: int, attempt: int, consecutive: int, detail: str,
                         failed_closed: bool) -> None:
    """Append one record to `logs/step_{N}_evaluator_errors.json`, so evaluator failures are counted apart from the
    model's verdicts."""
    logs = _logs_dir(state)
    if logs is None:
        return
    path = logs / f"step_{n}_evaluator_errors.json"
    try:
        records = json.loads(path.read_text()) if path.exists() else []
    except ValueError:
        records = []
    records.append({"attempt": attempt, "consecutive": consecutive, "error": detail,
                    "outcome": "failed_closed" if failed_closed else "judged_again",
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    path.write_text(json.dumps(records, indent=2))


# Failure kinds the strict loop structurally cannot fix. Classified by
# `execution_evaluator` (an LLM reading stdout+stderr), NOT a stderr regex — the engineer
# often catches the exception and prints it, leaving no traceback to match. These kinds
# are recoverable, so the safety layer below never treats them as terminal.
_ESCALATABLE_KINDS = ("missing_module", "renamed_api")


# ── missing-input safety layer (gated by `safe_loading`, default on) ──────────
#
# The dangerous outcome of a data-loading failure is the engineer "recovering" by
# inventing the data. Adjudicated here — no model, no node of its own on the happy path —
# because the shipping config runs the pre-flight without the reactive `data_fixer` node.


# Markers meaning a missing PACKAGE / import fault, NOT a missing data FILE. A missing
# package is recoverable, so it must never be short-circuited as terminal; this is a hard
# exclusion on top of the `failure_kind` check below.
_PACKAGE_FAULT_MARKERS = (
    "modulenotfounderror",
    "no module named",
    "importerror",
    "cannot import name",
    "dll load failed",
)


def _missing_provided_input(state: DebugState, ctx: PlanContext) -> Optional[str]:
    """Name of a genuinely-absent provided input FILE, or None.

    Conservative: every uncertain case returns None and falls through to the normal
    retry/escalation path. Requires all of — `safe_loading` on and a failure verdict; not
    a missing-package/import fault; and `_missing_required_input` (shared with
    `data_fixer`, so both paths adjudicate identically) finding a `FileNotFoundError` on a
    path not on disk that the script does not itself write. Never raises.

    "Absent" is judged against everything the task names, not only `data/`: a script
    that tried `data/qm9.csv` when the task says the file is at `/abs/dir/qm9.csv` hit a
    wrong PATH, not a missing INPUT — the traceback fixes that on retry, whereas a
    terminal verdict here ends the plan (qm9-joint Project35/Iteration1).
    """
    if not getattr(ctx, "safe_loading", True):
        return None
    verdict = state.get("current_execution_verdict")
    if verdict is None or getattr(verdict, "status", None) != "failure":
        return None
    # A missing package / renamed API is fixable — never a terminal "input does not exist".
    if getattr(verdict, "failure_kind", None) in _ESCALATABLE_KINDS:
        return None
    try:
        from cmbagent_lg.self_debug.data_fixer import (
            _data_dir,
            _error_text,
            _missing_required_input,
        )

        if any(m in _error_text(state) for m in _PACKAGE_FAULT_MARKERS):
            return None
        cc = state.get("current_code")
        code = getattr(cc, "python_code", "") or "" if cc is not None else ""
        missing = _missing_required_input(state, _data_dir(state), code)
        if missing is None:
            return None
        from cmbagent_lg.self_debug.preflight import named_input_for_step

        wd = state.get("work_dir")
        named = named_input_for_step(state.get("step"), Path(wd) if wd else None, ctx)
        # The parsed name is best-effort: a traceback's own code line —
        # `raise FileNotFoundError(f'{path} not found')` — yields "{path} not found", and
        # the placeholder "(required input file)" means nothing parsed at all. Neither
        # names a file, so with a named input on disk they read as "wrong path".
        name = Path(missing).name
        plausible = "." in name and re.fullmatch(r"[\w.\-]+", name) is not None
        if named is not None and (not plausible or named.name == name):
            print(f"[safe_loading] '{missing}' was not found at the path the script used, "
                  f"but the task names it at {named} → retry, not terminal",
                  file=sys.stderr, flush=True)
            return None
        return missing
    except Exception as e:  # noqa: BLE001 — safety check must never break the graph
        print(f"[safe_loading] missing-input check skipped (non-fatal): {e}",
              file=sys.stderr, flush=True)
        return None


# ── intent-gate terminal guard ──────────────────────────────────────────
#
# Backstop for an engineer that ignored the do-not-fabricate directive (see
# prompts.render_intent_gate_directive): on a step whose provided input never existed, a
# CLEAN SUCCESS is itself the signature of fabricated data, so the step terminates instead
# of going to the evaluators. Disjoint from the `safe_loading` layer above by construction
# (that fires only on FAILURE, this only on SUCCESS), so they can never both fire.


def _intent_gate_flagged(state: DebugState, ctx: PlanContext) -> bool:
    """The gate is armed: enabled, the sub-task needs PROVIDED input, and `data/` was
    verified empty before any code ran. False on every ordinary path — the last two are
    None/False whenever the gate did not run or failed open."""
    return bool(
        getattr(ctx, "enable_intent_gate", True)
        and state.get("needs_provided_input")
        and state.get("input_dir_empty")
    )


def _intent_gate_fabrication(state: DebugState, ctx: PlanContext) -> bool:
    """True when the run must have fabricated its data: the gate is armed and
    the script nonetheless completed cleanly and produced output."""
    if not _intent_gate_flagged(state, ctx):
        return False
    verdict = state.get("current_execution_verdict")
    if verdict is None or getattr(verdict, "status", None) != "success":
        return False
    # It wrote result files and/or reported numbers; a clean run that did neither is not
    # evidence of anything.
    return bool(state.get("data_manifest") or (state.get("execution_stdout") or "").strip())


def _intent_gate_unfulfillable(state: DebugState, ctx: PlanContext) -> bool:
    """True when the gate is armed and the attempt FAILED with a GENUINE file-not-found on
    a provided input the script does not itself write — the engineer diagnosed the absent
    input, which is the FINAL answer: the file cannot appear between attempts, so a retry
    is pure pressure to invent a stand-in.

    Only that failure is terminal. Every OTHER failure — a `ValueError` from a wrong
    reader argument, a `KeyError` on a column, a bug in the engineer's own code, a
    timeout — is an ordinary retry with the do-not-fabricate directive still in force;
    the fabrication guard (`_intent_gate_fabrication`) remains the backstop if a retry
    then "succeeds" by inventing data. Previously ANY non-package failure at attempt 1
    ended the step, so a first honest `ValueError` on a real input was final (measured:
    47/465 qm9-joint iterations). A missing PACKAGE is excluded exactly as in
    `_missing_provided_input` (recoverable, never terminal). Adjudication of the
    file-not-found shares `looks_like_file_not_found` / `_missing_required_input` with
    the `safe_loading` path, so both paths judge identically.
    """
    if not _intent_gate_flagged(state, ctx):
        return False
    verdict = state.get("current_execution_verdict")
    if verdict is None or getattr(verdict, "status", None) != "failure":
        return False
    if getattr(verdict, "failure_kind", None) in _ESCALATABLE_KINDS:
        return False
    try:
        from cmbagent_lg.self_debug.data_fixer import (
            _data_dir,
            _error_text,
            _missing_required_input,
            looks_like_file_not_found,
        )

        if any(m in _error_text(state) for m in _PACKAGE_FAULT_MARKERS):
            return False
        if not looks_like_file_not_found(state)[0]:
            return False
        cc = state.get("current_code")
        code = getattr(cc, "python_code", "") or "" if cc is not None else ""
        return _missing_required_input(state, _data_dir(state), code) is not None
    except Exception:  # noqa: BLE001 — inspection failure must not break the graph
        return False


@timed_node("missing_input")
def missing_input(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Terminal node: a required provided INPUT FILE is absent (no model call). Ends the
    step with an explicit not-fulfilled `StepVerdict` saying why.

    Two paths lead here and report different things: `safe_loading` arrives from a FAILED
    run whose `FileNotFoundError` names the absent file; the intent gate arrives from a
    run that SUCCEEDED with no input to read, and names no file.
    """
    ctx = runtime.context
    if _intent_gate_flagged(state, ctx):
        reason = state.get("intent_gate_reason") or ""
        missing = "(the input this sub-task was supposed to be given)"
        preamble = (
            "This sub-task requires input data that was supposed to be provided, "
            "but data/ was verified EMPTY before any code ran"
            + (f" ({reason})" if reason else "")
            + ". "
        )
        if _intent_gate_fabrication(state, ctx):
            # The gate cannot tell fabrication from a load off a path the task
            # never named (seen: the script guessed its way to a real qm9.csv
            # next to the nonexistent path it was given). Say exactly that
            # rather than asserting the data was invented.
            notes = preamble + (
                "The script nevertheless ran to completion. Since no provided "
                "input was reachable, whatever it analysed was either generated "
                "by the script itself or loaded from a path the task never named; "
                "neither is a valid input for this step, so the step is "
                "unfulfillable until the real input is provided."
            )
            unmet = [
                "the required input data was never provided; the analysed data "
                "was generated by the script or loaded from an un-named path"
            ]
            print("[intent_gate] clean run on a step with NO provided input → "
                  "terminal not-fulfilled (data not from a provided input)",
                  file=sys.stderr, flush=True)
        else:
            notes = preamble + (
                "The script failed with a file-not-found on the provided input "
                "it was supposed to load. That is the right answer and it is "
                "FINAL: the file cannot appear between attempts, so the step "
                "ends here rather than retrying the engineer into inventing a "
                "stand-in."
            )
            unmet = [
                "the required input data was never provided, so the sub-task "
                "cannot be carried out"
            ]
            print("[intent_gate] honest failure on a step with NO provided input → "
                  "terminal not-fulfilled (no retry into fabrication)",
                  file=sys.stderr, flush=True)
    else:
        missing = _missing_provided_input(state, ctx) or "(required input file)"
        notes = (
            f"Required input '{missing}' is absent from data/ and the script does "
            f"not create it. It cannot be loaded; the step is unfulfillable. Data "
            f"must NOT be fabricated to stand in for it."
        )
        print(f"[safe_loading] MISSING input {missing!r} → terminal not-fulfilled "
              f"(refusing to fabricate)", file=sys.stderr, flush=True)
        unmet = [f"required input '{missing}' is missing and cannot be loaded"]

    n = state.get("step_number", 1)
    logs = _logs_dir(state)
    if logs is not None:
        try:
            (logs / f"step_{n}_missing_input.json").write_text(
                json.dumps({"missing": missing, "notes": notes}, indent=2)
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        # `unfulfillable` is what keeps the exhaustion replanner away from this step:
        # the input does not exist, so a rewritten plan could only invent a stand-in
        # (see `deep_research.nodes._replannable_exhaustion`).
        "unfulfillable": True,
        "current_step_verdict": StepVerdict(
            fulfilled=False,
            unmet_requirements=unmet,
            feedback=notes,
        ),
    }


# ── routers ─────────────────────────────────────────────────────────────


def route_after_execution_evaluator(
    state: DebugState, runtime: Runtime[PlanContext]
) -> str:
    """Code gate. Success → image_reviewer / step_evaluator, unless the intent gate's
    terminal guard or the bypass-after-fault guard diverts it. Failure → `escalation`
    (missing package / renamed API, once per step), the terminal `missing_input` node
    (absent provided input), END (a repeated data-infeasibility, or the attempt cap)
    or engineer (retry)."""
    ctx = runtime.context
    verdict = state["current_execution_verdict"]
    if verdict.status == "success":
        # A clean run on a step whose provided input never existed means the data was
        # invented; terminate rather than review results computed from it.
        if _intent_gate_fabrication(state, ctx):
            return "missing_input"
        # One nudged retry for a success that dropped a faulted library — but only while
        # attempt budget remains, degrading to "surface, don't force" otherwise
        # (`bypass_after_fault_detected_live` is set in state either way).
        if state.get("bypass_after_fault_report") and state.get("attempts", 0) < ctx.max_n_attempts:
            return "engineer"
        # Visual review first (if enabled) — it may bounce the plot back to the engineer
        # before the goal is judged.
        return "image_reviewer" if ctx.vlm_enabled else "step_evaluator"
    if (
        ctx.enable_escalation
        and not state.get("escalated", False)
        and verdict.failure_kind in _ESCALATABLE_KINDS
    ):
        return "escalation"
    # Counterpart of the guard above: the engineer honestly diagnosed the absent input and
    # exited non-zero. `safe_loading` structurally cannot catch this — an honest
    # `sys.exit(1)` raises no FileNotFoundError to match on.
    if _intent_gate_unfulfillable(state, ctx):
        return "missing_input"
    # Opt-in: a DATA-LOAD failure goes once to the data_loader_fixer, which inspects the
    # real file and returns loader guidance — or, for an absent required input, a terminal
    # not-fulfilled. Inert when the flag is off.
    if ctx.enable_data_fixer:
        # Belt-and-suspenders; the data_fixer edge normally ENDs first.
        if state.get("data_fixer_format") == "missing":
            return END
        if not state.get("data_fixed", False):
            from cmbagent_lg.self_debug.data_fixer import should_data_fix

            if should_data_fix(state):
                return "data_fixer"
    # Safety layer (`safe_loading`, default ON, independent of data_fixer and preflight):
    # a genuinely absent provided input FILE the script does not itself write makes the
    # step unfulfillable — terminate rather than retry the engineer into fabricating one.
    if _missing_provided_input(state, ctx) is not None:
        return "missing_input"
    # A failure that is a property of the DATA, seen often enough that another attempt
    # at the same design buys nothing (`max_data_infeasibility_repeats`). The step ends
    # here NAMED — `logs/step_N_infeasible_execution.json` — and `deep_research` routes
    # it to the adaptive replanner, because a requirement the data cannot support is a
    # plan problem, not an engineering one. Deliberately placed after the recoverable
    # hatches above and before the attempt cap.
    from cmbagent_lg.self_debug.data_infeasibility import retries_spent

    if retries_spent(state, ctx):
        print(f"[data_infeasibility] step {state.get('step_number', 1)}: "
              f"{state.get('execution_infeasible_signal')} did not clear on retry → "
              f"ending the step for the replanner instead of spending "
              f"{ctx.max_n_attempts - state.get('attempts', 0)} more attempt(s)",
              file=sys.stderr, flush=True)
        return END
    if state.get("attempts", 0) >= ctx.max_n_attempts:
        return END
    return "engineer"


def route_after_data_fixer(state: DebugState, runtime: Runtime[PlanContext]) -> str:
    """After the data_loader_fixer: an absent required input (`format="missing"`) routes
    to END — never toward fabricating data. A recoverable diagnosis routes back to the
    engineer, whose next attempt gets the loader guidance in its retry context."""
    if state.get("data_fixer_format") == "missing":
        return END
    return "engineer"


def route_after_step_evaluator(
    state: DebugState, runtime: Runtime[PlanContext]
) -> str:
    """Goal gate: fulfilled → END; not fulfilled & attempts left → engineer;
    not fulfilled & exhausted → END; an evaluator error (no verdict) → step_evaluator again.

    `step_goal_met` also ends the step when EVERY unmet requirement was declared
    infeasible in this environment (`StepVerdict.infeasible_requirements`); a verdict
    without those fields answers exactly as `fulfilled` did. Exhaustion still ends the
    SUB-graph — `deep_research.route_after_review` is what then sends an exhausted step
    to the adaptive replanner instead of halting the run."""
    ctx = runtime.context
    if state.get("step_evaluator_outcome") == "evaluator_error":
        return "step_evaluator"          # the model failed, not the run: judge the same run again
    if step_goal_met(state["current_step_verdict"], state.get("step"), state):
        return END
    if state.get("attempts", 0) >= ctx.max_n_attempts:
        return END
    return "engineer"


__all__ = [
    "engineer",
    "format_engineer",
    "executor",
    "execution_evaluator",
    "step_evaluator",
    "missing_input",
    "route_after_execution_evaluator",
    "route_after_step_evaluator",
    "route_after_data_fixer",
]
