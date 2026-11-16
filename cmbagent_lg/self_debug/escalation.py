"""Escalation node — the self_debug escape hatch, via the Claude Agent SDK.

For a failure the strict loop structurally cannot fix (missing package, renamed/removed
API), runs one bounded SDK `query()` that may web-search, `pip install`, and/or make a
minimal `Edit` to `codebase/step_{N}.py`; control then returns to the executor.

This is the ONE Anthropic dependency in cmbagent_lg (`claude-agent-sdk` needs
`ANTHROPIC_API_KEY`); the rest of the graph is model-agnostic. Opt-in
(`PlanContext.enable_escalation`), one-shot per step, bounded by
`escalation_max_budget_usd` / `escalation_max_turns`, and does NOT consume an attempt.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
import time

from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug.nodes import _codebase_dir, _logs_dir
from cmbagent_lg.self_debug.prompts import escalation_instructions
from cmbagent_lg.self_debug.schemas import EngineerResponse
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


def _run_async(coro):
    """Run a coroutine to completion whether or not an event loop is already
    running — langgraph's sync `invoke`/`stream` has no loop; a notebook does."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # A loop is already running (e.g. Jupyter) — run in a separate thread.
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _summarize_tool_input(name: str, input_dict: dict) -> str:
    """One-line summary of a tool call's input, for the live ticker."""
    if not isinstance(input_dict, dict):
        return ""
    if name == "WebFetch":
        return str(input_dict.get("url", ""))[:120]
    if name == "WebSearch":
        return str(input_dict.get("query", ""))[:80]
    if name in ("Read", "Edit", "Write"):
        return str(input_dict.get("file_path", ""))
    if name == "Bash":
        cmd = str(input_dict.get("command", ""))
        return (cmd[:80] + "…") if len(cmd) > 80 else cmd
    # Fallback: first key=value, truncated.
    if input_dict:
        k, v = next(iter(input_dict.items()))
        sv = str(v)
        return f"{k}={sv[:60]}{'…' if len(sv) > 60 else ''}"
    return ""


async def _run_query(prompt: str, options):
    """Drive the Claude Agent SDK query to completion, streaming a live ticker
    of the agent's reasoning + tool calls to stderr as they arrive.

    Returns `(ResultMessage | None, [tool names invoked])`.
    """
    from claude_agent_sdk import query, AssistantMessage, ResultMessage

    result = None
    tool_calls: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                # Text block — the model's reasoning. Print the first line.
                text = getattr(block, "text", None)
                if text and text.strip():
                    snippet = text.strip().split("\n", 1)[0][:150]
                    print(f"[escalation]   {snippet}", file=sys.stderr, flush=True)
                # Tool-use block — show what's about to run.
                name = getattr(block, "name", None)
                if name:
                    tool_calls.append(name)
                    summary = _summarize_tool_input(
                        name, getattr(block, "input", {}) or {}
                    )
                    print(
                        f"[escalation] → {name}({summary})",
                        file=sys.stderr,
                        flush=True,
                    )
        elif isinstance(message, ResultMessage):
            result = message
    return result, tool_calls


def _emit_langfuse(record: dict) -> None:
    """Attach the escalation summary to the escalation node's own langfuse span, so the
    Claude work is visible in the trace next to the Gemini nodes. Best-effort: the JSON
    log is the reliable record, and this is skipped silently on any error.
    """
    try:
        from langfuse import get_client

        get_client().update_current_span(
            metadata={
                "escalation_reason": record["reason"],
                "escalation_cost_usd": record["cost_usd"],
                "escalation_turns": record["turns"],
                "escalation_tool_calls": record["tool_calls"],
                "tags": ["escalation"],
            }
        )
    except Exception as e:  # noqa: BLE001 — best-effort, never block the loop
        print(f"[escalation] langfuse note skipped: {e}", file=sys.stderr, flush=True)


def _vm_api_report(work_dir, module: str, symbol: str = "") -> str:
    """The SANDBOX's report for `module[.symbol]`, or "" when it cannot answer.

    Goes through `api_introspect.env_report`, the same seam the pre-flight and the retry injection
    use, so the answer describes the environment the code RUNS in. The local fallback is
    deliberately empty: this venv is a different package set, and a report from here would be
    confidently wrong.
    """
    from cmbagent_lg.self_debug import api_introspect

    return api_introspect.env_report(
        api_introspect.SNIPPET, {"module": module, "symbol": symbol}, str(work_dir), lambda: "")


@timed_node("escalation")
def escalation(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Run one bounded Claude Agent SDK pass to resolve an escalatable failure."""
    ctx = runtime.context
    step = state["step"]
    n = state.get("step_number", 1)
    code = state["current_code"].python_code
    stderr = state.get("execution_stderr", "")

    # Reason comes from execution_evaluator's classification (it reads
    # stdout+stderr and isn't fooled by a caught-and-printed exception).
    verdict = state.get("current_execution_verdict")
    kind = getattr(verdict, "failure_kind", None) or "unknown"
    summary = getattr(verdict, "error_summary", None) or ""
    reason = f"{kind}: {summary}".strip().rstrip(":")

    codebase = _codebase_dir(state)
    if codebase is None:
        # The SDK agent needs a real codebase dir to edit. Degrade gracefully: mark
        # escalated so the loop falls through to the strict engineer retry.
        print("[escalation] no work_dir — skipping", file=sys.stderr, flush=True)
        return {"escalated": True, "escalation_reason": reason}

    # `execution_evaluator` already renamed the failed step_{N}.py to a failure-variant,
    # so re-materialize the canonical name from state for the agent to read and edit.
    code_path = (codebase / f"step_{n}.py").resolve()
    code_path.write_text(code)
    work_dir = codebase.parent

    prompt = escalation_instructions(ctx, step, str(code_path), stderr, reason)

    from claude_agent_sdk import ClaudeAgentOptions

    # Under `CMBAGENT_LG_REMOTE_EXECUTOR` the code runs on a sandbox VM, so local `Bash`
    # would test the fix against the WRONG environment (different packages, none of the
    # VM's data). Drop Bash and give it VM-scoped tools instead. The LLM + creds stay on
    # the engine; only code execution reaches into the VM.
    import os

    remote_spec = os.environ.get("CMBAGENT_LG_REMOTE_EXECUTOR")
    options_kwargs = dict(
        permission_mode="acceptEdits",
        cwd=str(work_dir),
        max_turns=ctx.escalation_max_turns,
        max_budget_usd=ctx.escalation_max_budget_usd,
    )
    if remote_spec:
        from claude_agent_sdk import create_sdk_mcp_server, tool

        from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

        executor = RemoteExecutor.from_env(remote_spec)
        session_name = work_dir.name
        _timeout = step.code_execution_timeout or ctx.code_execution_timeout or 120

        # Scoped VM tools instead of a shell: Read code engine-side, read the VM's DATA,
        # install, and run in the sandbox. Nothing runs unsandboxed on the VM.
        @tool("run_step",
              f"Execute the current codebase/step_{n}.py ON THE SANDBOX VM (the real runtime "
              f"environment and data — this machine is NOT where the code runs) and return returncode, "
              f"stdout, stderr. A missing package is auto-installed on the VM and the step re-runs. Use "
              f"this to verify your fix before finishing.",
              {"type": "object", "properties": {}})
        async def _run_step(args):  # noqa: ANN001
            files = {p.name: p.read_text() for p in sorted(codebase.glob("step_*.py"))}
            res = await asyncio.to_thread(executor.run_code, session_name, n, files, _timeout)
            txt = (f"returncode={res.get('returncode')}\ninstalled={res.get('installed')}\n"
                   f"--- stdout ---\n{res.get('stdout', '')}\n--- stderr ---\n{res.get('stderr', '')}")
            return {"content": [{"type": "text", "text": txt[:8000]}]}

        @tool("list_vm_files",
              "List the files in the sandbox data/ directory on the VM (the real inputs and outputs).",
              {"type": "object", "properties": {}})
        async def _list_vm_files(args):  # noqa: ANN001
            res = await asyncio.to_thread(executor.list_files, session_name)
            lines = "\n".join(f"{f['name']} ({f['bytes']} B)" for f in res.get("files", []))
            return {"content": [{"type": "text", "text": lines or "(no data files)"}]}

        @tool("read_vm_file",
              "Read a file from the sandbox data/ directory on the VM (e.g. inspect a CSV's header/"
              "columns to see the real schema). Argument: name — a basename under data/.",
              {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
        async def _read_vm_file(args):  # noqa: ANN001
            res = await asyncio.to_thread(executor.read_file, session_name, args.get("name", ""))
            if res.get("type") == "error":
                return {"content": [{"type": "text", "text": "error: " + res.get("message", "")}]}
            if res.get("binary"):
                return {"content": [{"type": "text", "text": f"{res['name']}: binary, {res['bytes']} bytes"}]}
            return {"content": [{"type": "text", "text": res.get("text", "")}]}

        @tool("install_package",
              "Install a Python package into the VM sandbox (wheels only). Argument: package — the pip "
              "name. Note: a missing package is usually auto-installed when you run_step, so use this "
              "only when you need a package no import has triggered yet.",
              {"type": "object", "properties": {"package": {"type": "string"}}, "required": ["package"]})
        async def _install_package(args):  # noqa: ANN001
            res = await asyncio.to_thread(executor.install, args.get("package", ""))
            status = "installed" if res.get("ok") else "FAILED"
            return {"content": [{"type": "text", "text": f"{status} {res.get('package')}\n{res.get('log', '')[:500]}"}]}

        @tool("inspect_vm_api",
              "Ask the SANDBOX VM what a module's API actually is: its version, whether a symbol "
              "exists, its signature, and the closest existing names when it does not. This machine "
              "has a DIFFERENT package set, so never answer that question from here. Arguments: "
              "module — the dotted import path; symbol — an optional attribute name.",
              {"type": "object",
               "properties": {"module": {"type": "string"}, "symbol": {"type": "string"}},
               "required": ["module"]})
        async def _inspect_vm_api(args):  # noqa: ANN001
            report = await asyncio.to_thread(
                _vm_api_report, work_dir, args.get("module", ""), args.get("symbol") or "")
            return {"content": [{"type": "text", "text": report or
                                 "the sandbox could not answer (guest unreachable, or the module "
                                 "does not import there)"}]}

        vm_tools = [_run_step, _list_vm_files, _read_vm_file, _install_package, _inspect_vm_api]
        options_kwargs["mcp_servers"] = {"vmexec": create_sdk_mcp_server("vmexec", tools=vm_tools)}
        options_kwargs["allowed_tools"] = [
            "WebSearch", "WebFetch", "Read", "Edit",
            "mcp__vmexec__run_step", "mcp__vmexec__list_vm_files",
            "mcp__vmexec__read_vm_file", "mcp__vmexec__install_package",
            "mcp__vmexec__inspect_vm_api"]
        prompt += (
            f"\n\nIMPORTANT: the code executes on a REMOTE sandbox VM, not this machine. Read/Edit act "
            f"on the code here, but to see the real DATA use `list_vm_files`/`read_vm_file`, and to TEST "
            f"your fix call `run_step` (runs codebase/step_{n}.py on the VM, auto-installing missing "
            f"packages). Use `install_package` only for a package no import has triggered. To find out "
            f"what a library's API really is there — versions, whether a symbol exists, its signature "
            f"— call `inspect_vm_api`, not this machine's Python. Do NOT run the "
            f"code with a local shell — this machine's environment and data differ from the VM.")
    else:
        options_kwargs["allowed_tools"] = ["WebSearch", "WebFetch", "Read", "Edit", "Bash"]

    if ctx.escalation_model:
        options_kwargs["model"] = ctx.escalation_model
    options = ClaudeAgentOptions(**options_kwargs)

    print(
        f"[escalation] reason={reason!r}  model={ctx.escalation_model or '(SDK default)'}  "
        f"budget=${ctx.escalation_max_budget_usd}  max_turns={ctx.escalation_max_turns}",
        file=sys.stderr,
        flush=True,
    )
    t0 = time.perf_counter()
    try:
        result, tool_calls = _run_async(_run_query(prompt, options))
    except Exception as e:  # noqa: BLE001 — a failed escalation must not crash the graph
        print(f"[escalation] query failed: {e}", file=sys.stderr, flush=True)
        result, tool_calls = None, []
    elapsed = time.perf_counter() - t0

    cost = float(getattr(result, "total_cost_usd", 0.0) or 0.0)
    turns = getattr(result, "num_turns", None)

    # Read whatever the agent left on disk — edited, or unchanged (a pure
    # `pip install` fix leaves the code byte-identical).
    fixed_code = code_path.read_text()
    code_changed = fixed_code != code
    print(
        f"[escalation] done in {elapsed:.1f}s  cost=${cost:.4f}  turns={turns}  "
        f"code_changed={code_changed}  tool_calls={tool_calls}",
        file=sys.stderr,
        flush=True,
    )

    record = {
        "step_number": n,
        "reason": reason,
        "elapsed_s": round(elapsed, 2),
        "cost_usd": cost,
        "turns": turns,
        "tool_calls": tool_calls,
        "code_changed": code_changed,
    }
    logs = _logs_dir(state)
    if logs is not None:
        (logs / f"step_{n}_escalation.json").write_text(json.dumps(record, indent=2))
    _emit_langfuse(record)

    new_code = EngineerResponse(
        python_code=fixed_code,
        code_explanation=state["current_code"].code_explanation,
        modification_summary=f"escalation resolved: {reason}",
    )
    return {
        "current_code": new_code,
        "escalated": True,
        "escalation_reason": reason,
    }


__all__ = ["escalation"]
