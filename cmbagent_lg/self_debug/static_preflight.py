"""Static pre-flight — refuse to execute a script whose verdict is already known.

Runs on EVERY attempt between `format_engineer` and the API pre-flight, and asks two
questions of the source text alone, with `ast` and no model call:

  1. does it PARSE? A SyntaxError would fail the subprocess on its own terms, and the
     executor's traceback says nothing the parser did not already say;
  2. does it load a BUNDLED EXAMPLE DATASET instead of the project's own data?
     (`PlanContext.reject_demo_datasets`, default on.) A script that fits
     `lifelines.datasets.load_rossi()` — the Rossi recidivism demo — runs cleanly,
     prints plausible numbers and reports results for the wrong cohort entirely; the
     step evaluator sees only stdout and cannot tell. Seen once on the breast-cancer
     fleet, and it is the one failure mode whose output is indistinguishable from
     success. The rule is generic: a few general-purpose libraries' example-data
     modules by name, and any package's `datasets` / `sampledata` submodule called
     without a file argument;
  3. does it PRINT anything? (`PlanContext.require_stdout`, default on.) The step
     evaluator judges from stdout and the data manifest only, and the engineer template
     already insists on printing every result — a script with no `print(` and no
     `sys.stdout` write anywhere runs to completion, writes its files, and is then
     rejected as unverifiable.

Measured on the qm9-joint fleet (918 failed execution attempts): 47 silent scripts and
9 SyntaxErrors, each costing a subprocess run plus the execution_evaluator and (for the
silent ones) the step_evaluator call before the engineer saw the same feedback this
node produces in microseconds.

A rejection is shaped EXACTLY like a failed execution — `execution_returncode=1`,
empty stdout, the diagnosis on stderr, the failure log on disk — and is routed to
`execution_evaluator`, so the retry machinery (error_history, attempt budget, the
demotion of the canonical script, the safety layers in the router) proceeds unchanged.
That is also why this is its own node rather than a branch inside `api_preflight`: that
node's contract is "bounce to the ENGINEER with API facts, bounded by its own cap and
never past the attempt budget", whereas a script that cannot run is an ordinary failed
attempt and must consume one. Keeping the two apart keeps both routers one-line and the
API pre-flight's tests untouched; the syntax check also runs first so an unparseable
script never spends an environment probe.

Conservative: the print check counts ANY plausible stdout write (a `print` not redirected
to stderr/a file, `pprint`, any `.print(...)` method, any `sys.stdout` reference, a
subprocess/`os.system` call whose child inherits stdout). The demo-dataset check names
only accessors that can ONLY return bundled/downloaded example data, exempts the
synthetic generators (`sklearn.datasets.make_*`) and the generic directory readers
(`torchvision.datasets.ImageFolder`) that do read real data, and stands down entirely
when the sub-task itself names the dataset — a plan that legitimately says "cluster
sklearn's iris data" must not be blocked. A false negative merely runs the script as
before; a false positive would burn an attempt on correct code. Never raises.
"""

from __future__ import annotations

import ast
import re
import sys
from typing import Optional

from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug.api_preflight import route_after_format_engineer
from cmbagent_lg.self_debug.nodes import (
    _codebase_dir,
    _initial_input_dir_empty,
    _run_log,
    _snapshot_data_dir,
)
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


# Prefix every rejection carries; the evaluator template keys on it.
STATIC_MARKER = "[static check"

NO_STDOUT_MESSAGE = (
    "[static check — not executed] The script never prints anything. The evaluator sees "
    "ONLY stdout; every final numerical result must be printed (see PRINT YOUR RESULTS in "
    "your instructions). Add prints and resubmit, KEEPING every file the script "
    "already saves and the exact paths it saves them to -- printing is IN ADDITION "
    "to writing the step's declared output files, never instead of it."
)

# Bare-name or method-name calls that write to stdout. Loose on purpose (see the module
# docstring): a name that is NOT really a print only makes the check quieter.
_PRINT_NAMES = frozenset({"print", "pprint", "pp", "echo"})
# Module roots whose calls hand stdout to a CHILD process (`subprocess.run(["Rscript", …])`).
_CHILD_PROCESS_ROOTS = frozenset({"subprocess", "os"})


# ── the two questions ────────────────────────────────────────────────────────


def syntax_error_report(code: str) -> Optional[str]:
    """stderr text for a script that does not parse, or None when it does.

    Parsed with THIS interpreter's grammar. Locally that is the interpreter the
    subprocess uses (`sys.executable`); under a remote executor a newer guest could
    accept what this parser rejects only if the engine ran an older Python than the
    sandbox, which the deployment does not do. Anything other than a parse failure
    (e.g. a RecursionError on pathological nesting) answers None — the script runs.
    """
    try:
        ast.parse(code)
    except SyntaxError as e:  # IndentationError / TabError are subclasses
        lineno = e.lineno if e.lineno is not None else "?"
        text = (e.text or "").rstrip("\n")
        if not text and isinstance(e.lineno, int):
            lines = code.splitlines()
            if 1 <= e.lineno <= len(lines):
                text = lines[e.lineno - 1]
        lines_out = [f"{type(e).__name__} at line {lineno}: {e.msg}"]
        if text:
            lines_out.append(f"    {text.strip()}")
        lines_out.append(f"{STATIC_MARKER} — the script was not executed]")
        return "\n".join(lines_out)
    except ValueError as e:
        # "source code string cannot contain null bytes" — the subprocess would refuse it too.
        return f"ValueError: {e}\n{STATIC_MARKER} — the script was not executed]"
    except Exception:  # noqa: BLE001 — anything else is not a verdict; let it run
        return None
    return None


def _print_is_redirected(call: ast.Call) -> bool:
    """`print(..., file=X)` where X is provably not stdout (stderr, an open file)."""
    for kw in call.keywords:
        if kw.arg != "file":
            continue
        v = kw.value
        if isinstance(v, ast.Constant) and v.value is None:
            return False
        if isinstance(v, ast.Attribute) and v.attr == "stdout":
            return False
        return True
    return False


def writes_stdout(code: str) -> bool:
    """True when the script contains a call that plausibly writes to stdout.

    Also True for anything unparseable: that is the syntax check's verdict to give, and
    this one must never add a second reason on top of it.
    """
    try:
        tree = ast.parse(code)
    except Exception:  # noqa: BLE001
        return True
    for node in ast.walk(tree):
        # `sys.stdout.write(...)`, `logging.StreamHandler(sys.stdout)`, `redirect_stdout`,
        # `print(..., file=sys.stdout)`: any reference to the stream is an intent to use it.
        if (isinstance(node, ast.Attribute) and node.attr == "stdout"
                and isinstance(node.value, ast.Name) and node.value.id == "sys"):
            return True
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr
            root = fn.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in _CHILD_PROCESS_ROOTS and (
                    root.id == "subprocess" or name == "system"):
                return True
        else:
            continue
        if name in _PRINT_NAMES:
            if name == "print" and _print_is_redirected(node):
                continue
            return True
    return False


# ── demo-dataset gate ────────────────────────────────────────────────────────
#
# One breast-cancer-fleet script silently fitted lifelines' Rossi recidivism demo
# instead of the real cohort: rc 0, sensible-looking survival curves, and nothing in
# stdout to give it away. No model call can catch that reliably, but the import/call
# that loads a bundled dataset is plain in the source text.
#
# Module paths of general-purpose libraries whose members can ONLY be bundled or
# auto-downloaded example data. Any other package's `datasets` / `sampledata` submodule is
# caught by the generic rule below (`_DEMO_SEGMENTS`).
_DEMO_MODULES = frozenset({
    "sklearn.datasets",
    "statsmodels.datasets",
    "statsmodels.api.datasets",
    "plotly.data",
    "plotly.express.data",
    "bokeh.sampledata",
    "keras.datasets",
    "tensorflow.keras.datasets",
    "torchvision.datasets",
})
# Fully-qualified callables that are demo loaders without living in a datasets module.
_DEMO_CALLS = frozenset({
    "seaborn.load_dataset",
    "sns.load_dataset",            # the universal alias, even without the import
})
# The generic rule, for any package: a CALL of a member of a non-root `datasets` /
# `sampledata` submodule (`<pkg>.datasets.load_x()`) whose arguments name no file. A loader
# given a path (`mypkg.datasets.load_table("data/t.csv")`) reads real data and passes, and so
# does a call whose argument is not a literal (it may be a path). Importing such a module, or
# a member of it, is not by itself an offence here; a star import is followed to the bare
# calls it makes possible.
_DEMO_SEGMENTS = frozenset({"datasets", "sampledata"})
_PATHLIKE = re.compile(r"[/\\]|\.[A-Za-z0-9]{1,8}$")
# Members of a demo module that do NOT return bundled data: synthetic generators and
# generic readers pointed at a real path. Prefixes, then exact names.
_DEMO_ALLOWED_PREFIXES = {"sklearn.datasets": ("make_",)}
_DEMO_ALLOWED_NAMES = {
    "sklearn.datasets": ("load_svmlight_file", "load_svmlight_files",
                         "dump_svmlight_file", "get_data_home", "clear_data_home"),
    "torchvision.datasets": ("ImageFolder", "DatasetFolder", "VisionDataset"),
}


def demo_dataset_message(offender: str) -> str:
    """stderr text for a rejected script, naming the offending call."""
    return (
        f"[static check — not executed] This script loads a BUNDLED EXAMPLE dataset: "
        f"`{offender}`. That is library demo data, NOT this project's data, so every "
        f"number it would print describes the wrong data — and the run would look "
        f"completely clean. Read the project's OWN input files instead: list what is "
        f"in `data/` (and any input path the sub-task names), load those files, and "
        f"print the shape and the real column names you found before analysing them. "
        f"Remove the `{offender}` call. If a column the analysis needs is not in the "
        f"real data, say so in stdout — do not substitute example data for it."
    )


def _import_aliases(tree: ast.AST) -> dict:
    """Local name → fully-qualified module/member, from the script's own imports.

    `import statsmodels.api as sm` → `sm`: `statsmodels.api`; `from sklearn.datasets
    import load_iris` → `load_iris`: `sklearn.datasets.load_iris`. Lets a dotted call
    be canonicalised before it is matched, so aliasing cannot hide the accessor.
    """
    out: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    root = a.name.split(".")[0]
                    out.setdefault(root, root)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                if a.name == "*":
                    continue
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _demo_offence(dotted: str) -> Optional[str]:
    """`dotted` (already canonical, e.g. `sklearn.datasets.load_iris`) if it names a
    bundled-dataset accessor, else None. Longest module prefix wins."""
    if not dotted:
        return None
    if dotted in _DEMO_CALLS:
        return dotted
    parts = dotted.split(".")
    for i in range(len(parts) - 1, 0, -1):
        mod = ".".join(parts[:i])
        if mod not in _DEMO_MODULES:
            continue
        leaf = parts[i]
        # `startswith(())` is False, so a module with no prefix exemptions is unaffected.
        if leaf.startswith(_DEMO_ALLOWED_PREFIXES.get(mod, ())):
            return None
        if leaf in _DEMO_ALLOWED_NAMES.get(mod, ()):
            return None
        return dotted
    return None


def _generic_demo_module(dotted: str) -> bool:
    """Is a non-root segment of `dotted`, before its last one, a `_DEMO_SEGMENTS` name?"""
    parts = dotted.split(".")
    return any(p in _DEMO_SEGMENTS for p in parts[1:-1])


def _names_no_file(call: ast.Call) -> bool:
    """True when every argument of `call` is a literal that names no file (or there are none)."""
    args = list(call.args) + [k.value for k in call.keywords]
    for a in args:
        if not isinstance(a, ast.Constant):
            return False      # a variable, a Path(...), an f-string: may be a path
        if isinstance(a.value, str) and _PATHLIKE.search(a.value):
            return False
    return True


def _bound_names(tree: ast.AST) -> set:
    """Names the script binds itself: functions, classes, assignments, imports, arguments."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out.update((a.asname or a.name).split(".")[0] for a in node.names if a.name != "*")
    return out


def _dotted_name(node: ast.AST) -> Optional[str]:
    """`a.b.c` for a Name/Attribute chain, or None for anything else (a subscript, a
    call result, a literal) — those cannot be matched by name, so they are passed."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _canonical(dotted: str, aliases: dict) -> str:
    """Rewrite the chain's ROOT through the alias map (`sm.datasets.get_rdataset` →
    `statsmodels.api.datasets.get_rdataset`). Unknown roots are left as written."""
    root, _, rest = dotted.partition(".")
    target = aliases.get(root)
    if not target:
        return dotted
    return f"{target}.{rest}" if rest else target


def demo_dataset_offender(code: str) -> Optional[str]:
    """The first bundled-example-dataset import/call in the script, or None.

    Anything unparseable answers None: that is the syntax check's verdict to give, and
    this one must never add a second reason on top of it.
    """
    try:
        tree = ast.parse(code)
    except Exception:  # noqa: BLE001
        return None
    aliases = _import_aliases(tree)
    # Modules star-imported from a demo module: a bare call the script does not bind itself
    # is that module's member.
    star = [n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module and not n.level
            and any(a.name == "*" for a in n.names)
            and (n.module in _DEMO_MODULES or _generic_demo_module(n.module + ".x"))]
    bound = _bound_names(tree) if star else set()
    for node in ast.walk(tree):
        # The import can BE the load (`import bokeh.sampledata.autompg`,
        # `from keras.datasets import mnist`).
        if isinstance(node, ast.Import):
            for a in node.names:
                hit = _demo_offence(a.name)
                if hit:
                    return hit
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                if a.name == "*":
                    continue
                hit = _demo_offence(f"{node.module}.{a.name}")
                if hit:
                    return hit
        elif isinstance(node, ast.Call):
            dotted = _dotted_name(node.func)
            if dotted is None:
                continue
            if "." not in dotted and star and dotted not in bound and _names_no_file(node):
                return f"{star[0]}.{dotted}"
            canonical = _canonical(dotted, aliases)
            hit = _demo_offence(canonical)
            if hit:
                return hit
            if (_generic_demo_module(canonical) and _names_no_file(node)
                    and not _explicit_module(canonical)):
                return canonical
    return None


def _explicit_module(dotted: str) -> bool:
    """Does a prefix of `dotted` name one of `_DEMO_MODULES`? Its own exemptions then decide."""
    parts = dotted.split(".")
    return any(".".join(parts[:i]) in _DEMO_MODULES for i in range(1, len(parts)))


def _step_names_dataset(step, offender: str) -> bool:
    """Did the SUB-TASK itself ask for this dataset? Then it is the project's data for
    this run and the gate stands down — a plan may legitimately say "cluster sklearn's
    iris data". Matched on the accessor's own leaf name and on its module path."""
    text = " ".join(
        [getattr(step, "sub_task", "") or ""]
        + list(getattr(step, "bullet_points", None) or [])
    ).casefold()
    if not text:
        return False
    parts = offender.split(".")
    needles = {offender.casefold(), parts[-1].casefold()}
    if len(parts) > 1:
        needles.add(".".join(parts[:-1]).casefold())
    # The bare dataset name as a human would write it: `load_breast_cancer` → "breast
    # cancer" / "breast_cancer".
    leaf = parts[-1]
    for prefix in ("load_", "fetch_", "get_"):
        if leaf.startswith(prefix) and len(leaf) > len(prefix) + 2:
            stem = leaf[len(prefix):]
            needles.add(stem.casefold())
            needles.add(stem.replace("_", " ").casefold())
    return any(n and n in text for n in needles)


# ── the failure-shaped state ─────────────────────────────────────────────────


def _persist_failure(state: DebugState, code: str, stderr: str) -> None:
    """The on-disk record a rejected attempt would otherwise lack. Never raises.

    The executor writes `codebase/step_{N}.py` + `.log` and `execution_evaluator` demotes
    them to `step_{N}_failure_{attempt}.*` on a failure verdict; a script that never
    reached the executor gets the FINAL names directly, so the audit trail reads the same
    whether the attempt ran or not (the demotion is a no-op on absent canonical files).
    """
    try:
        codebase = _codebase_dir(state)
        if codebase is None:
            return
        n = state.get("step_number", 1) or 1
        attempt = state.get("attempts", 0) or 0
        # Code first: if the log write fails, the artifact that matters still exists.
        (codebase / f"step_{n}_failure_{attempt}.py").write_text(code)
        (codebase / f"step_{n}_failure_{attempt}.log").write_text(
            _run_log(1, False, 0.0, "", stderr)
        )
    except Exception as e:  # noqa: BLE001 — bookkeeping must never suppress the rejection
        print(f"[static_preflight] could not persist the rejected script (non-fatal): {e}",
              file=sys.stderr, flush=True)


def _rejection(state: DebugState, ctx: PlanContext, kind: str, code: str, stderr: str) -> DebugState:
    """Everything the executor would have set for a crashed run, minus the run."""
    _persist_failure(state, code, stderr)
    out: DebugState = {
        "static_preflight_rejected": kind,
        "execution_stdout": "",
        "execution_stderr": stderr,
        "execution_returncode": 1,
        "execution_timed_out": False,
        "execution_elapsed_s": 0.0,
        "data_manifest": [],
        # Nothing ran, so there is no listing; None keeps an earlier attempt's out.
        "data_manifest_info": None,
    }
    # The executor captures the `safe_loading` fact on attempt 1 only. If attempt 1 is
    # refused here, attempt 2's executor would never set it — so take the same reading
    # now, from the same untouched `data/` (nothing has run, so nothing was fabricated).
    # Guarded on its own: bookkeeping must never suppress the refusal.
    try:
        codebase = _codebase_dir(state)
        if codebase is not None:
            data_dir = codebase.parent / "data"
            fact = _initial_input_dir_empty(state, ctx, data_dir, _snapshot_data_dir(data_dir))
            if fact is not None:
                out["input_dir_empty"] = fact
    except Exception as e:  # noqa: BLE001
        print(f"[static_preflight] input_dir_empty not determined (non-fatal): {e}",
              file=sys.stderr, flush=True)
    return out


# ── the node ─────────────────────────────────────────────────────────────────


@timed_node("static_preflight")
def static_preflight(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Parse the formatted script and check it prints; refuse to execute if either fails.

    Engineer scripts only — the self_debug graph is only ever invoked for `engineer`
    steps (deep_research routes `researcher` steps to their own graph), but a caller
    driving it directly with another agent's step gets a pass-through, not a verdict.
    """
    ctx = runtime.context
    step = state.get("step")
    if getattr(step, "sub_task_agent", "engineer") != "engineer":
        return {"static_preflight_rejected": None}

    code_obj = state.get("current_code")
    code = getattr(code_obj, "python_code", None) if code_obj is not None else None
    code = code or ""

    try:
        report = syntax_error_report(code)
        if report is not None:
            print(f"[static_preflight] NOT executing — {report.splitlines()[0]}",
                  file=sys.stderr, flush=True)
            return _rejection(state, ctx, "syntax", code, report)
        # Before the print check: a script analysing the WRONG DATA is a worse defect
        # than a silent one, and its message is the more useful of the two.
        if getattr(ctx, "reject_demo_datasets", True):
            offender = demo_dataset_offender(code)
            if offender and not _step_names_dataset(step, offender):
                print(f"[static_preflight] NOT executing — the script loads the bundled "
                      f"example dataset {offender}", file=sys.stderr, flush=True)
                return _rejection(state, ctx, "demo_dataset", code,
                                  demo_dataset_message(offender))
        if getattr(ctx, "require_stdout", True) and not writes_stdout(code):
            print("[static_preflight] NOT executing — the script never writes to stdout",
                  file=sys.stderr, flush=True)
            return _rejection(state, ctx, "no_stdout", code, NO_STDOUT_MESSAGE)
    except Exception as e:  # noqa: BLE001 — a pre-flight check must never break the graph
        print(f"[static_preflight] check skipped (non-fatal): {e}", file=sys.stderr, flush=True)
    return {"static_preflight_rejected": None}


def route_after_static_preflight(state: DebugState, runtime: Runtime[PlanContext]) -> str:
    """A refused script goes straight to the code gate as a failed run; anything else
    continues to the API pre-flight (or the executor, when that check is off)."""
    if state.get("static_preflight_rejected"):
        return "execution_evaluator"
    return route_after_format_engineer(state, runtime)


__all__ = [
    "static_preflight",
    "route_after_static_preflight",
    "syntax_error_report",
    "writes_stdout",
    "demo_dataset_offender",
    "demo_dataset_message",
    "NO_STDOUT_MESSAGE",
    "STATIC_MARKER",
]
