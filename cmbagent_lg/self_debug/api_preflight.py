"""Deterministic API pre-flight — check the code's third-party names BEFORE running it.

The PROACTIVE counterpart of `api_introspect.retry_report`: it runs between `format_engineer`
and `executor`, verifies every third-party symbol the code references against the INSTALLED
packages, and if one is provably absent the code is NOT executed — the engineer is handed the
real API facts and re-emits.

Shapes checked, in the order they are reported:
  * MODULE — `mod.gone(...)`, `from mod import gone`, `import mod.gone`, resolved by walking
    imported aliases (`absent_symbols`), plus `mod.Class.gone(...)` one step past a class;
  * TYPED RECEIVER — `obj.gone(...)` / `obj.method(bad=1)` where the CLASS of `obj` is provable
    from the script and the package's own introspectable facts (`api_types`);
  * MISSING PACKAGE — `import notinstalled` at module level, only under a policy that says this
    run has no way to install it (`missing_packages`);
  * INSTANCE — `obj.gone(...)` where `obj` came out of a library call, resolved by asking the
    environment what CLASS that call produces (`absent_instance_attributes`);
  * CALL SHAPE — `mod.exists(good=1, invented=2)`: the callable is real, a keyword is not
    (`absent_signature_mismatches`), provable only without `**kwargs` on the signature;
  * HISTORY — a keyword a previous run in this SAME environment proved invalid on this exact
    function (`api_history`), for callables whose signature cannot be introspected.

Design contract:
  * **Zero model calls.** `ast` for extraction, `importlib` + `inspect` for existence, and
    `api_introspect` for the report.
  * **Conservative to a fault.** A FALSE POSITIVE blocks correct code and burns an attempt,
    which is far worse than a miss. A symbol is ABSENT only when the module imports cleanly and
    the attribute genuinely is not on it — plus the one case where the failing import is itself
    the proof (`_removed_submodule`). Star-imports, a name rebound anywhere in the file, imports
    or accesses inside a `try:`, dynamic `getattr`, monkeypatching, a module that exists and
    merely fails to execute, and a module resolving inside the workspace are all "cannot prove
    absent" and pass silently. Each check lists its own silences.
  * **Never raises.** Any failure is a no-op that lets the code run exactly as it would have.
  * **Bounded.** Imports of the modules the script names are charged against a wall-clock
    ledger (`_ImportLedger`); an exhausted budget answers "unknown", never "absent", and the
    class tiers stay silent when any import went unanswered. What the ledger does NOT bound:
    the submodule a call-shape probe or a report renderer resolves once its root is loaded
    (`SIGNATURE_SNIPPET`, `SNIPPET`), and a curated entry's own submodule (`_import_quiet`) —
    both cheap once the root is in, and a probe whose root the ledger could not import is
    never asked. The p95 gate (< 200 ms) is a WARM-process figure: a fresh worker pays each
    heavy package's cold import once, on the first step that names it. Scripts over
    `_MAX_CODE_BYTES` / `_MAX_TREE_NODES` are not examined. At most
    `max_api_preflight_bounces` bounces per step, only while a finding is NEW to the step
    (`api_preflight_bounced`), and none once the attempt budget is spent.

Opt-out via `CMBAGENT_LG_API_PREFLIGHT=0`. Default ON (`PlanContext.enable_api_preflight`),
wired `format_engineer → static_preflight → api_preflight → executor|engineer`.
"""

from __future__ import annotations

import ast
import difflib
import importlib
import importlib.metadata
import importlib.util
import inspect
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
# Imported as a MODULE, never `from ... import introspect_local`: callers can redirect
# introspection at a different interpreter (e.g. a pinned venv) by REBINDING these names on
# `api_introspect` itself. A `from`-import would snapshot the original at import time and the
# redirection would silently observe nothing.
from cmbagent_lg.self_debug import api_introspect as _ai
from cmbagent_lg.self_debug import api_types as _at
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


# Caps. The injected block has to stay prompt-friendly, so the findings rendered are bounded;
# the questions asked are bounded by the import ledger (time) and a sanity ceiling (count).
_MAX_REFS = 60
_MAX_REPORTED = 6
# Instance-shape probes per script, each an import IN THE TARGET INTERPRETER.
_MAX_CLASS_PROBES = 2
# Call-shape probes: distinct CALLABLES per script (dedupe first, then the ceiling).
_MAX_SIGNATURE_PROBES = 200
# Under a remote executor a probe is a sandbox container launch, not an in-process import, and
# they are issued BEFORE every step — the local fan-out would add tens of seconds to each one.
_MAX_REMOTE_SIGNATURE_PROBES = 12
# Module-shape candidates per script under a remote executor. Locally this shape costs nothing to
# decide (the import IS the answer, so only findings travel); remotely every candidate is a
# container launch, and `_MAX_REPORTED` cannot serve as the budget because it caps FINDINGS.
_MAX_REMOTE_MODULE_PROBES = 6
# Wall time allowed for imports that are NOT already loaded in this process, per node call.
DEFAULT_IMPORT_BUDGET_S = 6.0
ENV_IMPORT_BUDGET = "CMBAGENT_LG_API_IMPORT_BUDGET_S"
ENV_MISSING_PACKAGE = "CMBAGENT_LG_API_MISSING_PACKAGE"
ENV_CAN_INSTALL = "CMBAGENT_LG_API_CAN_INSTALL"
ENV_EXTENSIONS = "CMBAGENT_LG_API_EXTENSIONS"
# Size ceiling: the check is linear in the number of statements (~1 ms each, three walks and a
# signature lookup per call site), so a script far beyond anything an engineer writes (fleet
# scripts are 2-30 KB) is let through unexamined rather than examined for a minute.
_MAX_CODE_BYTES = 256 * 1024
_MAX_TREE_NODES = 50_000

# Generated per-step scripts (`codebase/step_{N}.py`) are importable by name under remote/
# cross-step execution. Importing one would EXECUTE the engineer's own code inside the engine
# process — pre-flight must never do that.
_STEP_MODULE_RE = re.compile(r"^step_\d+$")

# Nothing to ask the environment about.
_SKIP_ROOTS = frozenset({"__future__", "__main__"})


# ── AST extraction ───────────────────────────────────────────────────────────


def _target_names(node, out: set) -> None:
    """Names bound by an assignment target (recursing through tuple/list/star)."""
    if isinstance(node, ast.Name):
        out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _target_names(elt, out)
    elif isinstance(node, ast.Starred):
        _target_names(node.value, out)


def _binding_counts(tree: ast.AST) -> dict:
    """`name -> how many times` the module binds it by any means OTHER than a plain import.

    The COUNT, not just the set, because the instance path needs a variable bound exactly ONCE:
    `x = mod.make()` followed by `x = x[:, keep]` is a different object by the time its
    attributes are read. The module path only ever asks "bound at all?" (`_rebound_names`).
    """
    out: dict = {}

    def _bump(names) -> None:
        for n in names:
            out[n] = out.get(n, 0) + 1

    for node in ast.walk(tree):
        hit: set = set()
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _target_names(t, hit)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _target_names(node.target, hit)
        elif isinstance(node, ast.NamedExpr):
            _target_names(node.target, hit)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            _target_names(node.target, hit)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _target_names(item.optional_vars, hit)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                hit.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            hit.add(node.name)
        elif isinstance(node, ast.arg):
            hit.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            hit.update(node.names)
        elif isinstance(node, ast.Call):
            # Monkeypatching: `setattr(mod, "foo", ...)` makes an attribute that is genuinely
            # absent from the installed package legitimately present at run time.
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "setattr" and node.args:
                root = node.args[0]
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name):
                    hit.add(root.id)
        _bump(hit)
    return out


def _rebound_names(tree: ast.AST) -> set:
    """Every name the module binds by any means OTHER than a plain import.

    A name in here is UNTRUSTED as a module alias (`import time` then `time = ...`, a parameter
    called `np`, a helper `def json(...)`): the attribute access at the point of use may not be
    on the imported module at all.
    """
    return set(_binding_counts(tree))


# Names a branch condition uses to ask the ENVIRONMENT what it has: attribute probes, version
# strings and tuples, the typing-time flag, signature and spec lookups, version parsers. A
# branch guarded by one of these runs only in SOME environments, so neither arm is a static
# fact about this one — exactly the standing of a `try:` body.
_PROBE_NAMES = frozenset({
    "hasattr", "getattr", "callable", "TYPE_CHECKING", "find_spec", "signature",
    "import_module", "Version", "LooseVersion", "StrictVersion", "parse", "parse_version",
    "version", "__version__", "version_info", "hexversion",
})


def _environment_conditional(test: ast.AST, derived: frozenset = frozenset()) -> bool:
    """Does this `if` condition consult the installed environment (see `_PROBE_NAMES`), directly
    or through a name computed from such a probe (`derived`, see `_environment_names`)?"""
    for n in ast.walk(test):
        if isinstance(n, ast.Name) and (n.id in _PROBE_NAMES or n.id in derived):
            return True
        if isinstance(n, ast.Attribute) and n.attr in _PROBE_NAMES:
            return True
    return False


def _environment_names(tree: ast.AST) -> frozenset:
    """Names bound (anywhere) from an expression that probes the environment — `major =
    int(np.__version__.split(".")[0])`, `ver = tuple(...__version__...)`, `has_new =
    hasattr(m, "new")` — closed transitively, so a condition on such a name is recognised."""
    assigns = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names: set = set()
            for t in node.targets:
                _target_names(t, names)
            assigns.append((names, node.value))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            names = set()
            _target_names(node.target, names)
            assigns.append((names, node.value))
    derived: set = set()
    changed = True
    while changed:
        changed = False
        for names, value in assigns:
            if names - derived and _environment_conditional(value, frozenset(derived)):
                derived |= names
                changed = True
    return frozenset(derived)


def _try_guarded(tree: ast.AST) -> set:
    """`id()` of every node whose execution depends on what the environment turns out to have.

    Three shapes, none statically adjudicable, all skipped:
      * a `try:` body — probing an API and falling back is idiomatic;
      * an `except:` handler body — it runs only when the try body FAILED, so an import or a
        call inside it is exactly as conditional as the one it replaces (`try: from m import
        new / except ImportError: from m import old as new` is the canonical shape);
      * both arms of an `if` (or `x if c else y`) whose condition asks the environment a
        question — `hasattr(np, "trapezoid")`, `np.__version__ >= "2"`, `sys.version_info`,
        `TYPE_CHECKING`, `inspect.signature(...)`, `find_spec(...)`, `Version(...)`.
    """
    out: set = set()
    derived = _environment_names(tree)

    def _mark(stmts) -> None:
        for stmt in stmts:
            for child in ast.walk(stmt):
                out.add(id(child))

    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            _mark(node.body)
            for handler in node.handlers:
                _mark(handler.body)
        elif isinstance(node, ast.If) and _environment_conditional(node.test, derived):
            _mark(node.body)
            _mark(node.orelse)
        elif isinstance(node, ast.IfExp) and _environment_conditional(node.test, derived):
            _mark([node.body, node.orelse])
    return out


def _chain(node: ast.Attribute) -> Optional[Tuple[str, List[str]]]:
    """`(root_name, [attr, ...])` for an attribute chain rooted at a bare Name, else None.

    `np.linalg.norm` → `("np", ["linalg", "norm"])`. Anything rooted at a call, subscript or
    literal (`np.array(x).sum`, `d["k"].foo`) returns None: the object at that point is not the
    imported module, so its attributes say nothing about the installed API.
    """
    parts: List[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.reverse()
    return cur.id, parts


class _Analysis(NamedTuple):
    """What every consumer reads off one parse of the module.

    Shared, not duplicated: the module walk, the instance path and the typed-receiver tier must
    agree on WHICH aliases are trustworthy — a drift there becomes a false positive.
    """

    tree: ast.AST
    guarded: set                        # id() of every node inside a `try:` body
    counts: dict                        # name -> non-import bindings (see `_binding_counts`)
    alias_hits: dict                    # local name -> how many DISTINCT bindings imports made
    trusted: dict                       # local name -> dotted module path (unambiguous ones)
    from_aliases: set                   # locals bound by `from X import Y`, not by `import X`
    from_imports: List[Tuple[str, str]]  # (module, symbol) per `from <module> import <symbol>`
    dotted_imports: List[str]           # unguarded `import a.b.c` names, dotted ones only
    roots: List[str]                    # every top-level package an unguarded import names
    top_level_imports: List[str]        # roots imported by a statement in the module body
    dynamic: set                        # names whose attribute set is decided at run time
    assigned: set                       # (root, first_attr) pairs assigned to or deleted
    assigned_attrs: set                 # the LAST attribute name of every such target
    all_modules: List[str]              # every module ANY import names, guarded or not
    namespace_dynamic: bool             # exec/eval/globals()/locals(): namespaces not static
    literal_imports_only: bool          # no `import_module(<non-literal>)` / `__import__`
    all_from: List[Tuple[str, str]]     # (module, name) of EVERY from-import, guarded or not


def _analyze(code: str) -> Optional[_Analysis]:
    """Parse and resolve the module's imports, or None when NOTHING about it can be proven.

    None for unparseable code (it will fail on its own terms) and for a star-import anywhere,
    which can silently supply any bare name — including one that shadows a module alias.

    Alias merge: `import a` and `import a.b` (both plain, both unguarded) bind the same `a` to
    the same package, so they count as ONE binding; only a different target (`import a as x`
    beside `import a.b as x`, or a version-branch pair) makes the name ambiguous. A from-import
    keeps its `(module, symbol)` entry however its local name is REBOUND afterwards (the import
    statement itself is what raises) — but not when another IMPORT binds the same local name
    (`from m import new` beside a guarded `from m import old as new`): the statement is then
    one of two alternatives the script tries, not a fact about the environment.

    A name handed to `hasattr`/`getattr`/`vars`/... anywhere (`dynamic`) is not a trusted
    alias either: the script itself says that object's attribute set is decided at run time.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return None

    guarded = _try_guarded(tree)
    counts = _binding_counts(tree)
    dynamic = _dynamic_attr_names(tree)
    assigned = _assigned_attributes(tree)
    assigned_attrs = _assigned_attr_names(tree)

    aliases: dict = {}
    bound_to: Dict[str, set] = {}       # local name -> set of targets it was bound to
    from_aliases: set = set()
    pending_from: List[Tuple[str, str, str]] = []   # (local, module, symbol), unguarded
    nested_locals: set = set()                       # locals an import binds OUTSIDE the body
    dotted_imports: List[str] = []
    roots: List[str] = []
    top_level: List[str] = []
    all_modules: List[str] = []
    all_from: List[Tuple[str, str]] = []
    body_ids = {id(n) for n in tree.body}

    def _bind(local: str, target: str, node) -> None:
        # A guarded import is still a binding of the name — an ambiguous one.
        bound_to.setdefault(local, set()).add(target if id(node) not in guarded else f"?{target}")
        if id(node) not in guarded:
            aliases[local] = target
        if id(node) not in body_ids:
            nested_locals.add(local)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                # `import a.b.c` binds `a`; `import a.b.c as x` binds `x` to `a.b.c`.
                target = a.name if a.asname else a.name.split(".")[0]
                _bind(local, target, node)
                all_modules.append(a.name)
                if id(node) not in guarded:
                    if "." in a.name:
                        dotted_imports.append(a.name)
                    roots.append(a.name.split(".")[0])
                    if id(node) in body_ids:
                        top_level.append(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # relative import — the module path is not statically knowable here
            all_modules.append(node.module)
            for a in node.names:
                if a.name == "*":
                    return None  # a star-import can supply anything; prove nothing
                local = a.asname or a.name
                # `from a import b` may bind a SUBMODULE, so `b.c` is still a module access.
                _bind(local, f"{node.module}.{a.name}", node)
                all_from.append((node.module, a.name))
                if id(node) in guarded:
                    continue
                pending_from.append((local, node.module, a.name))
                from_aliases.add(local)
                roots.append(node.module.split(".")[0])
                if id(node) in body_ids:
                    top_level.append(node.module.split(".")[0])

    alias_hits = {name: len(targets) for name, targets in bound_to.items()}
    # A from-import whose local name another import ALSO binds is judged only when every one
    # of those imports is an unconditional module-level statement (they all execute, so each
    # is a fact); one guarded or nested alternative makes the pair a fallback the script tries.
    from_imports = [
        (m, s) for local, m, s in pending_from
        if alias_hits.get(local, 0) == 1
        or (not any(t.startswith("?") for t in bound_to[local]) and local not in nested_locals)
    ]
    # `Alias.attr = ...` on a from-imported object mutates THAT object (a class handed a new
    # method, a function given a flag): what the installed package says about it no longer
    # holds, so the alias is rebound for every purpose — the same standing `setattr(Alias, ...)`
    # already has. Module aliases are handled per attribute instead (`_references`), because
    # `plt.rcParams[...] = ...` must not untrust `plt` wholesale.
    for root, _attr in _direct_attr_targets(tree):
        if root in from_aliases:
            counts[root] = counts.get(root, 0) + 1
    trusted = {
        name: mod
        for name, mod in aliases.items()
        if alias_hits.get(name, 0) == 1 and name not in counts and name not in dynamic
    }
    seen_roots: set = set()
    roots = [r for r in roots if not (r in seen_roots or seen_roots.add(r))]
    seen_roots = set()
    top_level = [r for r in top_level if not (r in seen_roots or seen_roots.add(r))]
    seen_roots = set()
    all_modules = [m for m in all_modules if not (m in seen_roots or seen_roots.add(m))]
    literal_only = True
    namespace_dynamic = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
        if name in ("exec", "eval", "globals", "locals") and isinstance(fn, ast.Name):
            namespace_dynamic = True
        if name in ("import_module", "__import__"):
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                all_modules.append(first.value)
            else:
                literal_only = False
    return _Analysis(tree, guarded, counts, alias_hits, trusted, from_aliases, from_imports,
                     dotted_imports, roots, top_level, dynamic, assigned, assigned_attrs,
                     all_modules, namespace_dynamic, literal_only, all_from)


def extract_api_references(code: str) -> Tuple[List[Tuple[str, List[str]]], List[Tuple[str, str]]]:
    """Deterministically extract what the code asks of third-party modules.

    Returns `(chains, from_imports)`:
      * `chains`      — `(dotted_module, [attr, ...])` for each attribute access made on an
                        imported module alias, e.g. `("scipy", ["special", "foo"])`.
      * `from_imports`— `(module, symbol)` for each `from <module> import <symbol>`.

    Both empty when nothing can be proven: unparseable code, or a star-import anywhere (which
    can silently supply any bare name, including one that shadows a module alias).
    """
    an = _analyze(code)
    if an is None:
        return [], []
    return _references(an)


def _references(an: _Analysis) -> Tuple[List[Tuple[str, List[str]]], List[Tuple[str, str]]]:
    chains: List[Tuple[str, List[str]]] = []
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Attribute) or id(node) in an.guarded:
            continue
        got = _chain(node)
        if got is None:
            continue
        root, parts = got
        if root not in an.trusted or not parts:
            continue
        if (root, parts[0]) in an.assigned:
            # `np.trapz = np.trapezoid` (a compatibility shim): the script puts that attribute on
            # the module at run time, so the installed package cannot answer for it.
            continue
        chains.append((an.trusted[root], parts))
        if len(chains) >= _MAX_REFS:
            break
    return chains, list(an.from_imports[:_MAX_REFS])


# ── existence, answered by the environment ───────────────────────────────────


# `find_spec` per call site is the single most expensive thing a large script makes the check
# do, and its answer cannot change within one node call. Cleared at the top of every call
# (`api_preflight`) — never kept across calls, because an installer agent may add a package
# between two steps.
_skip_cache: Dict[Tuple[str, Optional[str]], bool] = {}
_workspace_cache: Dict[Tuple[str, Optional[str]], bool] = {}


def _workspace_root(root: str, work_dir: Optional[str]) -> bool:
    """True when importing `root` would EXECUTE engineer-written code: a generated per-step
    script (`step_N`), or any module resolving inside the workspace. Also True when the
    question cannot be answered (`find_spec` raising) — unanswerable is never "safe"."""
    if not root or _STEP_MODULE_RE.match(root):
        return True
    key = (root, work_dir)
    if key in _workspace_cache:
        return _workspace_cache[key]
    ans = False
    try:
        spec = importlib.util.find_spec(root)
    except BaseException:  # noqa: BLE001 — find_spec can raise anything for a broken package
        spec, ans = None, True
    # Only a FILE origin says where the code lives. A frozen or built-in module (`os`, `sys`,
    # `time`, `itertools` in CPython 3.12) has `origin` "frozen" / "built-in" and
    # `has_location` False; resolving that string as a path lands in the CURRENT directory, so
    # a run launched with cwd = the run dir took `import os` for engineer code and silenced
    # every class-level tier through `_import_everything`.
    # A relative file origin (only a custom finder gives one) is resolved against cwd, which is
    # where the loader reads it.
    origin = getattr(spec, "origin", None) if spec is not None else None
    if spec is not None and not getattr(spec, "has_location", False):
        origin = None
    # A namespace package has no origin at all; its directories say where it lives.
    try:
        locations = list(getattr(spec, "submodule_search_locations", None) or ())
    except BaseException:  # noqa: BLE001 — a _NamespacePath recomputes itself when iterated
        locations, ans = [], True
    if work_dir and (origin or locations):
        try:
            wd = Path(work_dir).expanduser().resolve()
        except (OSError, RuntimeError):
            wd, ans = None, True
        for place in ([origin] if origin else []) + [str(x) for x in locations]:
            if wd is None:
                break
            try:
                Path(place).resolve().relative_to(wd)
                ans = True  # lives in the workspace → engineer-written, never import it
                break
            except (ValueError, OSError, RuntimeError):
                pass
    _workspace_cache[key] = ans
    return ans


def _skip_root(root: str, work_dir: Optional[str]) -> bool:
    """True when the top-level module must not be imported or reasoned about.

    Covers the generated per-step scripts and anything resolving INSIDE the workspace (importing
    those would execute engineer-written code in the engine process), plus anything not
    installed at all — a missing PACKAGE is a different failure (`missing_packages` owns it).
    """
    if not root or root in _SKIP_ROOTS:
        return True
    key = (root, work_dir)
    if key in _skip_cache:
        return _skip_cache[key]
    if _workspace_root(root, work_dir):
        ans = True
    else:
        try:
            spec = importlib.util.find_spec(root)
        except BaseException:  # noqa: BLE001
            spec = None
        # Under a remote executor "absent HERE" says nothing about the sandbox, which is the
        # environment being asked about — and the backend image carries no scientific stack, so
        # this rule alone made every probe unreachable. The guest answers it, or nobody does.
        ans = spec is None and not os.environ.get(_ai.REMOTE_ENV)
    _skip_cache[key] = ans
    return ans


# "The import budget is spent, so this question was never asked" — deliberately distinct from
# None ("asked, and it provably is not a module"). Collapsing the two would turn running out of
# budget into a fabricated ABSENT verdict, which is exactly the false positive this check must
# never produce.
_UNKNOWN = object()


class _ImportLedger:
    """Where the import cost of one node call is accounted.

    Importing a module has a real cost (torch: seconds) and real side effects, so the budget is
    WALL TIME rather than a count: a script touching thirteen already-loaded modules costs
    nothing, one cold heavy import costs what it costs, once per process. Only modules absent
    from `sys.modules` are charged. Over budget, every further question answers `_UNKNOWN`.

    Process-global memory: `entries` keeps `{module: (ok, wall_s)}` for the log; failed imports
    are memoised per `sys.path` (a package that raises on import raises again), and a success
    is simply in `sys.modules`.
    """

    entries: Dict[str, Tuple[bool, float]] = {}
    failed: Dict[str, int] = {}

    def __init__(self, budget_s: float):
        self.budget = float(budget_s)
        self.spent = 0.0
        self.local: dict = {}          # this call's answers, incl. `_UNKNOWN`

    @staticmethod
    def _path_key() -> int:
        return hash(tuple(sys.path))

    def get(self, name: str):
        """The module, None (does not import), or `_UNKNOWN` (budget spent). Never raises."""
        if name in self.local:
            return self.local[name]
        mod = sys.modules.get(name)
        if mod is not None:
            self.local[name] = mod
            return mod
        if self.failed.get(name) == self._path_key():
            self.local[name] = None
            return None
        if self.spent >= self.budget:
            self.local[name] = _UNKNOWN
            return _UNKNOWN
        t0 = time.perf_counter()
        try:
            mod = importlib.import_module(name)
        except BaseException:  # noqa: BLE001 — an import can raise anything, incl. SystemExit
            mod = None
        wall = time.perf_counter() - t0
        self.spent += wall
        self.entries[name] = (mod is not None, wall)
        if mod is None:
            self.failed[name] = self._path_key()
        self.local[name] = mod
        return mod


def _import_budget(ctx) -> float:
    raw = os.environ.get(ENV_IMPORT_BUDGET)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        return float(getattr(ctx, "api_preflight_import_budget_s", DEFAULT_IMPORT_BUDGET_S))
    except (TypeError, ValueError):
        return DEFAULT_IMPORT_BUDGET_S


def _safe_import(name: str, cache):
    """Import `name`, or None if it does not import. Never raises.

    `cache` is an `_ImportLedger` (budgeted) or a plain dict (unbudgeted, for callers that
    already bound their own question count).
    """
    if isinstance(cache, _ImportLedger):
        got = cache.get(name)
        return None if got is _UNKNOWN else got
    if name in cache:
        return cache[name]
    try:
        mod = importlib.import_module(name)
    except BaseException:  # noqa: BLE001
        mod = None
    cache[name] = mod
    return mod


def _submodule(mod, part: str, ledger):
    """`mod.part` as a MODULE, None if it provably is not one, `_UNKNOWN` if unanswerable.

    Asked separately from `hasattr` because a submodule that has not been imported yet is absent
    from `dir(pkg)` while `pkg.sub.fn` is still perfectly valid code.
    """
    return ledger.get(f"{mod.__name__}.{part}")


def _is_module(obj) -> bool:
    import types

    return isinstance(obj, types.ModuleType)


def _submodule_on_disk(dotted: str) -> Optional[bool]:
    """Does `dotted` EXIST as a module, regardless of whether importing it WORKS?

    `find_spec` LOCATES a module without executing its body, which is the only way to separate
    the two reasons an import can fail — False = the name is genuinely gone from the installed
    release; True = it is there and its BODY raised (a broken dependency inside it, where
    reporting an absent name would be a false positive). A plain `try: import` conflates them.

    None when the question cannot be answered at all: `find_spec` raises `ModuleNotFoundError`
    when a PARENT is missing, and can raise anything for a broken package.
    """
    try:
        return importlib.util.find_spec(dotted) is not None
    except BaseException:  # noqa: BLE001 — find_spec can raise anything for a broken package
        return None


def _removed_submodule(module: str, root_import) -> Optional[Tuple[str, str]]:
    """`(parent, leaf)` when a DOTTED module is provably not a submodule of its installed parent.

    A whole submodule removed from a release (`from Bio.SeqUtils.CodonUsage import ...`) does
    not import, so the caller's "absent, broken, or unasked → not ours to call" rule would wave
    it through. That rule is right for a missing top-level PACKAGE and wrong here: the parent IS
    installed and imports cleanly, so the leaf's absence is provable from the environment.

    Every condition is required, and any unanswerable one means silence:
      * there IS a parent — a bare `import foo` / `from foo import x` is left alone;
      * the parent imports cleanly through the caller's budgeted `root_import` — "budget spent"
        comes back as None there and must never turn into an ABSENT verdict;
      * the leaf is neither an attribute of the parent nor a module on disk (`_submodule_on_disk`
        is the discriminator).

    Reported as `(parent, leaf)` because that is the pair `introspect_local` can answer about.
    """
    parent, _, leaf = module.rpartition(".")
    if not parent or not leaf:
        return None
    pmod = root_import(parent)
    if pmod is None:
        return None  # parent missing, broken, or never asked — prove nothing
    if hasattr(pmod, leaf):
        return None  # the import failed, but the NAME is there; that is a different failure
    if _submodule_on_disk(module) is not False:
        return None  # present-but-broken, or unanswerable
    return (parent, leaf)


def _module_walk(an: _Analysis, ledger: _ImportLedger, work_dir: Optional[str],
                 remote: bool) -> Tuple[List[Tuple[str, str]], List[Tuple[type, str, str]]]:
    """`(absent (module, symbol) pairs, class-step (cls, attr, dotted) triples)`."""
    chains, from_imports = _references(an)
    found: List[Tuple[str, str]] = []
    class_steps: List[Tuple[type, str, str]] = []
    seen: set = set()

    def _record(module: str, symbol: str) -> None:
        if (module, symbol) not in seen:
            seen.add((module, symbol))
            found.append((module, symbol))

    def _root_import(module: str):
        """Import a reference's module, honouring the budget (None = do not judge)."""
        got = ledger.get(module)
        return None if got is _UNKNOWN else got

    for module, symbol in from_imports:
        if _skip_root(module.split(".")[0], work_dir):
            continue
        if remote:
            _record(module, symbol)
            continue
        mod = _root_import(module)
        if mod is None:
            # Module absent, broken, or unasked → a different failure, not ours to call. The
            # one exception is a REMOVED SUBMODULE of a package that IS installed, where the
            # environment can still prove the absence; that verdict is about `(parent, leaf)`,
            # not about `symbol`, which cannot be judged while its module will not import.
            gone = _removed_submodule(module, _root_import)
            if gone is not None:
                _record(*gone)
            continue
        if hasattr(mod, symbol):
            continue
        if _submodule(mod, symbol, ledger) is None:  # None, not _UNKNOWN: proven not a submodule
            _record(module, symbol)

    # `import a.b.c` raises on its own when `c` is gone from an installed `a.b` — the statement
    # is the reference, whether or not the name is used afterwards.
    if not remote:
        for dotted in an.dotted_imports:
            if _skip_root(dotted.split(".")[0], work_dir):
                continue
            if _root_import(dotted) is None:
                gone = _removed_submodule(dotted, _root_import)
                if gone is not None:
                    _record(*gone)

    for base, parts in chains:
        if _skip_root(base.split(".")[0], work_dir):
            continue
        if remote:
            # The walk below is what decides which component is the symbol, and it needs the
            # package. Assume every part but the last is a module — `SNIPPET` verifies that where
            # the packages are: a dotted name that does not import is reported as such, not ABSENT.
            _record(".".join([base, *parts[:-1]]), parts[-1])
            continue
        mod = _root_import(base)
        if mod is None:
            continue
        # Walk while the chain is still MODULES; the first non-module component is the symbol
        # to judge, and everything past it is an attribute of an arbitrary object we know
        # nothing about — except a CLASS, whose next attribute is one more provable step.
        for i, part in enumerate(parts):
            nxt = getattr(mod, part, None)
            if _is_module(nxt):
                mod = nxt
                continue
            if nxt is not None:
                if inspect.isclass(nxt) and i + 1 < len(parts):
                    attr = parts[i + 1]
                    if (not attr.startswith("_") and not _at.synthesises_attributes(nxt)
                            and attr not in an.assigned_attrs
                            and not hasattr(nxt, attr)
                            and nxt.__name__ not in _script_class_names(an.tree)
                            and not _at._subclass_has(nxt, attr)):
                        class_steps.append((nxt, attr, f"{mod.__name__}.{part}"))
                break  # the attribute is there; whatever hangs off it is not a module question
            sub = _submodule(mod, part, ledger)
            if sub is _UNKNOWN:
                break  # out of import budget — the question was never asked, so prove nothing
            if sub is not None:
                mod = sub
                continue
            # `hasattr` because an attribute whose VALUE is None is present, and the `getattr`
            # above cannot tell that apart from a missing one.
            #
            # `_submodule` came back None, i.e. importing `mod.part` RAISED — the removed-
            # submodule and broken-dependency shapes wearing the same face. `find_spec` separates
            # them, used here purely as a VETO: only a submodule that demonstrably EXISTS
            # silences the report (a non-module answers False, a non-package parent answers None).
            if not hasattr(mod, part) and _submodule_on_disk(f"{mod.__name__}.{part}") is not True:
                _record(mod.__name__, part)
            break

    return found[:_MAX_REMOTE_MODULE_PROBES if remote else _MAX_REPORTED], class_steps


def _script_class_names(tree: ast.AST) -> set:
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def absent_symbols(code: str, work_dir: Optional[str] = None) -> List[Tuple[str, str]]:
    """`(module, symbol)` pairs the code references that the INSTALLED packages do not have.

    Deduplicated, order-preserving, and empty whenever nothing can be PROVEN absent. Every
    uncertain case returns nothing for that reference — see the module docstring.

    Locally the import IS the proof, so only findings come back. Under `CMBAGENT_LG_REMOTE_EXECUTOR`
    this process is NOT the environment the code runs in — different packages, different versions —
    so it judges nothing: every reference comes back as a CANDIDATE for the sandbox to rule on
    (`build_report` bounces only on the guest's own `ABSENT:`), bounded by `_MAX_REMOTE_MODULE_PROBES`.
    """
    an = _analyze(code)
    if an is None:
        return []
    remote = bool(os.environ.get(_ai.REMOTE_ENV))
    ledger = _ImportLedger(_import_budget(None))
    found, _steps = _module_walk(an, ledger, work_dir, remote)
    return found


# ── the instance shape: `obj.method()` where obj came out of a library call ──
#
# The module walk cannot see this: `adata.concatenate(...)` is an attribute of an OBJECT, and
# `_chain` refuses to reason about objects. Resolving it needs the environment to say what class
# the producing call returns, which `api_introspect` does inside the TARGET interpreter; the
# engine's job is only to decide which questions are honest ones.
#
# The bar is far higher than on the module path, because an attribute absent from a CLASS can
# still be present on an INSTANCE (anything `__init__` assigns to `self`) — so `hasattr(cls,
# attr)` alone is not proof. Hence:
#   * the variable must be bound EXACTLY ONCE, by a plain `name = <Call>`;
#   * the call must resolve to a dotted path rooted in an unambiguous, never-rebound import;
#   * the attribute must be the one being CALLED, i.e. exactly `var.attr(...)`. A bare
#     `var.attr` read is the instance-attribute case and not judgeable; in `var.attr.other(...)`
#     it is `other` that is called, so that chain is not a question either;
#   * nothing may be try-guarded, star-imported, `getattr`/`setattr`/`hasattr`-touched, or
#     assigned to.
# The residual risk — a library assigning a CALLABLE to `self` in `__init__` — is left to the
# introspector, which reports only a PROVABLE absence and refuses custom-`__getattr__` classes.


def _dynamic_attr_names(tree: ast.AST) -> set:
    """Names whose attribute set is decided at RUN TIME — not statically adjudicable, same as
    the `try:` skip.

    Handed to `getattr` / `setattr` / `hasattr` / `delattr` / `vars` / `object.__setattr__`
    (or any `X.__setattr__`) anywhere in the module; whose `__dict__` is read (`obj.__dict__
    .update(...)`); or the RIGHT-hand side of a plain `alias = name` — the object then has a
    second name, and a mutation through either lands on the same object.
    """
    out: set = set()

    def _root(node) -> None:
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name):
            out.add(root.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            hit = (isinstance(fn, ast.Name)
                   and fn.id in ("getattr", "setattr", "hasattr", "delattr", "vars"))
            hit = hit or (isinstance(fn, ast.Attribute) and fn.attr in ("__setattr__", "__delattr__"))
            if hit:
                for arg in node.args:
                    _root(arg)
        elif isinstance(node, ast.Attribute) and node.attr == "__dict__":
            _root(node)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            if any(isinstance(t, ast.Name) for t in node.targets):
                out.add(node.value.id)
    return out


def _assigned_attributes(tree: ast.AST) -> set:
    """`(root_name, first_attr)` for every attribute that is ASSIGNED TO or deleted.

    `obj.foo = ...` puts `foo` on the instance whatever the class says, so the class can no
    longer answer the question. Deliberately coarse — `obj.foo[...] = ...` also lands here,
    which only makes the check quieter.
    """
    out: set = set()

    def _scan(target: ast.AST) -> None:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Attribute):
                got = _chain(sub)
                if got and got[1]:
                    out.add((got[0], got[1][0]))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _scan(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _scan(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _scan(node.target)
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                _scan(t)
    return out


def _attr_targets(tree: ast.AST):
    """Every `ast.Attribute` that is (part of) an assignment or deletion TARGET."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        else:
            continue
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, ast.Attribute):
                    yield sub


def _assigned_attr_names(tree: ast.AST) -> set:
    """The LAST attribute name of every attribute target: `Ridge.explain = fn` and
    `axes_cls.hline_band = fn` put `explain` / `hline_band` SOMEWHERE at run time, so no class
    can be asked whether an instance has it. Coarse (the name, not the object) but honest."""
    return {sub.attr for sub in _attr_targets(tree)}


def _direct_attr_targets(tree: ast.AST) -> set:
    """`(root, attr)` for targets of the exact shape `Name.attr` (not `Name.attr[k]`, not
    `Name.a.b`): the object bound to `Name` itself is being mutated."""
    out: set = set()
    for sub in _attr_targets(tree):
        if isinstance(sub.value, ast.Name):
            out.add((sub.value.id, sub.attr))
    return out


def _resolve_call(call: ast.Call, an: _Analysis) -> Optional[str]:
    """Dotted path of the callable in `call`, or None when it is not statically knowable.

    Resolved as the module path resolves an attribute chain. A bare name is accepted only when
    it came from a `from X import Y` — `import x as y` binds a MODULE, so `y(...)` would not be
    a call into the library at all.
    """
    fn = call.func
    if isinstance(fn, ast.Attribute):
        got = _chain(fn)
        if got is None:
            return None
        root, parts = got
        if root not in an.trusted or not parts:
            return None
        return ".".join([an.trusted[root]] + parts)
    if isinstance(fn, ast.Name) and fn.id in an.from_aliases and fn.id in an.trusted:
        return an.trusted[fn.id]
    return None


def _instance_probes(code: str, work_dir: Optional[str] = None,
                     an: Optional[_Analysis] = None) -> List[Tuple[str, str]]:
    """`(callable_dotted, attr)` questions worth asking the environment. Possibly empty."""
    an = an if an is not None else _analyze(code)
    if an is None:
        return []

    dynamic = an.dynamic
    assigned = an.assigned

    # Variables bound exactly once, by a plain `name = <Call>` into a resolvable library path.
    produced: dict = {}
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Assign) or id(node) in an.guarded:
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        name = node.targets[0].id
        # `an.counts[name] == 1` is this very assignment and nothing else. A name that is also
        # imported, or touched dynamically, is not a single provable object.
        if an.counts.get(name, 0) != 1 or name in an.alias_hits or name in dynamic:
            continue
        dotted = _resolve_call(node.value, an)
        if dotted is None or _skip_root(dotted.split(".")[0], work_dir):
            continue
        produced[name] = dotted

    if not produced:
        return []

    # Only attributes that are CALLED — `id()` of each Call's own func node.
    called = {id(n.func) for n in ast.walk(an.tree) if isinstance(n, ast.Call)}

    out: List[Tuple[str, str]] = []
    seen: set = set()
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Attribute) or id(node) in an.guarded:
            continue
        if id(node) not in called:
            continue
        got = _chain(node)
        if got is None:
            continue
        root, parts = got
        if root not in produced or len(parts) != 1:
            continue
        attr = parts[0]
        if attr.startswith("__") or (root, attr) in assigned or attr in an.assigned_attrs:
            continue
        key = (produced[root], attr)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= _MAX_CLASS_PROBES:
            break
    return out


def absent_instance_attributes(
    code: str, work_dir: Optional[str] = None, an: Optional[_Analysis] = None,
) -> List[Tuple[str, str, str]]:
    """`(callable_dotted, attr, report)` for methods the produced CLASS provably does not have.

    Empty whenever nothing is provable, including the introspector not being reachable at all.
    The verdict is entirely the introspector's: it returns "" for anything it cannot judge.
    """
    probes = _instance_probes(code, work_dir, an)
    if not probes:
        return []

    # Looked up on the module, not bound at import, so a caller can redirect it at a different
    # interpreter by rebinding the name. `getattr` so a build without it degrades to "no
    # instance checks" instead of raising.
    ask = getattr(_ai, "introspect_callable_class_local", None)
    if ask is None:
        return []

    out: List[Tuple[str, str, str]] = []
    for dotted, attr in probes:
        spec = {"callable": dotted, "attr": attr, "static_only": True}
        try:
            report = _ai.env_report(_ai.CLASS_SNIPPET, spec, work_dir, lambda: ask(spec))
        except Exception as e:  # noqa: BLE001 — an unprovable question must never raise
            print(f"[api_preflight] class probe skipped ({dotted}.{attr}): {e}",
                  file=sys.stderr, flush=True)
            continue
        if report and not report.startswith("introspection failed"):
            out.append((dotted, attr, report))
    return out


# ── the call shape: `mod.exists(good=1, invented=2)` — a real callable, wrong arguments ──────
#
# Neither walk above can see this: the SYMBOL is real, and this is not an attribute lookup. What
# can be checked is the CALL — the keyword names and positional count passed — against the
# callable's own resolved `inspect.signature`. Provable ONLY when that signature has no
# `**kwargs`: a permissive signature forwards arguments frames deeper into code
# `inspect.signature` cannot see, so any keyword MAY be valid. That case must stay SILENT, and
# does, because `_signature_report` (`api_introspect.py`) refuses to judge such a callable — a
# hard boundary, not a missed case.
# The verdict is entirely the introspector's, as for the instance shape; this module only
# decides which (callable, keywords, positional count) questions are honest ones to ask.


def _call_signature_probes(
    code: str, work_dir: Optional[str] = None, an: Optional[_Analysis] = None,
    ledger: Optional["_ImportLedger"] = None,
) -> List[Tuple[str, List[str], Optional[int], bool]]:
    """`(dotted_callable, kwarg_names, n_positional, has_starargs)` per call worth asking about.

    Resolved through the SAME trusted-alias machinery as `_resolve_call`; a call whose function
    is not statically knowable is skipped. `fn(**extra)` is skipped entirely — the keyword SET is
    then not a static fact. `n_positional` is None when the call unpacks `*args`, since the count
    is unknowable, but the keyword check can still be honest about that call. One probe per
    distinct `(callable, keywords, count)`: a loop body's call is asked once.

    With a `ledger`, a callable whose root package the ledger could not or did not import
    (`_UNKNOWN`, or a failed import) is not asked at all — the probe's own `import_module`
    would otherwise import it outside the budget.
    """
    an = an if an is not None else _analyze(code)
    if an is None:
        return []

    out: List[Tuple[str, List[str], Optional[int], bool]] = []
    seen: set = set()
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Call) or id(node) in an.guarded:
            continue
        dotted = _resolve_call(node, an)
        if dotted is None or _skip_root(dotted.split(".")[0], work_dir):
            continue
        if ledger is not None and ledger.get(dotted.split(".")[0]) in (None, _UNKNOWN):
            continue  # over budget, or the root does not import: not this call's to judge
        if any(kw.arg is None for kw in node.keywords):
            continue  # `fn(**extra)` — the keyword set is not a static fact
        kwnames = [kw.arg for kw in node.keywords]
        has_star = any(isinstance(a, ast.Starred) for a in node.args)
        n_positional = None if has_star else len(node.args)
        key = (dotted, tuple(kwnames), n_positional, has_star)
        if key in seen:
            continue
        seen.add(key)
        out.append((dotted, kwnames, n_positional, has_star))
        if len(out) >= _MAX_SIGNATURE_PROBES:
            break
    return out


def absent_signature_mismatches(code: str, work_dir: Optional[str] = None,
                                an: Optional[_Analysis] = None,
                                ledger: Optional["_ImportLedger"] = None) -> List[Tuple[str, str]]:
    """`(callable_dotted, report)` for calls PROVEN to pass a keyword or positional count the
    RESOLVED signature does not accept.

    Empty whenever nothing is provable — including every callable whose signature accepts
    `**kwargs`. The verdict is entirely the introspector's, as in `absent_instance_attributes`.
    """
    probes = _call_signature_probes(code, work_dir, an, ledger)
    if os.environ.get(_ai.REMOTE_ENV):
        probes = probes[:_MAX_REMOTE_SIGNATURE_PROBES]
    if not probes:
        return []

    # Same seam as `introspect_callable_class_local` above: looked up on the module so a caller
    # redirecting to a different interpreter covers this question too.
    ask = getattr(_ai, "introspect_call_signature_local", None)
    if ask is None:
        return []

    out: List[Tuple[str, str]] = []
    for dotted, kwnames, n_positional, has_star in probes:
        spec = {"callable": dotted, "kwargs": kwnames, "has_starargs": has_star}
        if n_positional is not None:
            spec["n_positional"] = n_positional
        try:
            report = _ai.env_report(_ai.SIGNATURE_SNIPPET, spec, work_dir, lambda: ask(spec))
        except Exception as e:  # noqa: BLE001 — an unprovable question must never raise
            print(f"[api_preflight] signature probe skipped ({dotted}): {e}",
                  file=sys.stderr, flush=True)
            continue
        if report:
            out.append((dotted, report))
        if len(out) >= _MAX_REPORTED:
            break
    return out


def resolve_dotted_call_by_func_name(code: str, func_name: str) -> Optional[str]:
    """Dotted path of the (unique) call in `code` whose function's bare name is `func_name`.

    For `api_introspect.retry_report`'s call-shape branch, whose traceback names only the bare
    function. None whenever zero or more than one DISTINCT dotted path matches that bare name —
    same "prove or stay silent" contract as everything else here.
    """
    an = _analyze(code)
    if an is None:
        return None
    found: set = set()
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Call) or id(node) in an.guarded:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else None)
        if name != func_name:
            continue
        dotted = _resolve_call(node, an)
        if dotted is not None:
            found.add(dotted)
    return next(iter(found)) if len(found) == 1 else None


def calls_on_line(code: str, line: int) -> List[dict]:
    """Every call whose source span covers `line`, for `api_introspect.retry_report`'s shapes
    that name no function (the traceback names the line instead).

    Each entry: `dotted` — the installed callable a module-alias call resolves to, else None;
    `attr` — the attribute name when the callee is `<expr>.attr` on a NON-module receiver, else
    None; `keywords` — the literal keyword names; `starstar` — whether `**` is unpacked; `start`
    — the call's first line. [] when the script does not parse.
    """
    an = _analyze(code)
    if an is None:
        return []
    # The call already RAN when this is asked, so the pre-run reasons to distrust an alias
    # (`getattr(camb, "__version__", ...)` making `camb` "dynamic") do not apply. Still
    # required: the name is bound exactly once, by an import, and never assigned.
    loose: Dict[str, str] = {}
    from_locals: set = set()
    for node in ast.walk(an.tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                loose[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                loose[a.asname or a.name] = f"{node.module}.{a.name}"
                from_locals.add(a.asname or a.name)
    loose = {n: t for n, t in loose.items() if an.alias_hits.get(n, 0) == 1 and n not in an.counts}
    out: List[dict] = []
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Call):
            continue
        lo, hi = getattr(node, "lineno", None), getattr(node, "end_lineno", None)
        if lo is None or not (lo <= line <= (hi or lo)):
            continue
        fn = node.func
        dotted = None
        if isinstance(fn, ast.Attribute):
            got = _chain(fn)
            if got is not None and got[0] in loose and got[1]:
                dotted = ".".join([loose[got[0]]] + got[1])
        elif isinstance(fn, ast.Name) and fn.id in loose and fn.id in from_locals:
            dotted = loose[fn.id]
        attr = None
        if dotted is None and isinstance(fn, ast.Attribute):
            attr = fn.attr
        out.append({"dotted": dotted, "attr": attr, "start": lo,
                    "keywords": [k.arg for k in node.keywords if k.arg],
                    "starstar": any(k.arg is None for k in node.keywords)})
    return out


# ── typed receivers (api_types) ──────────────────────────────────────────────


def _dotted_resolver(ledger: _ImportLedger, work_dir: Optional[str]):
    """`dotted -> object|None` through the budgeted ledger: longest importable prefix, then
    `getattr` — a class attribute is read statically so a descriptor is never triggered."""
    cache: dict = {}

    def resolve(dotted: str):
        if dotted in cache:
            return cache[dotted]
        parts = [p for p in dotted.split(".") if p]
        obj = None
        if parts and not _skip_root(parts[0], work_dir):
            for i in range(len(parts), 0, -1):
                got = ledger.get(".".join(parts[:i]))
                if got is _UNKNOWN:
                    break
                if got is None:
                    continue
                obj = got
                for p in parts[i:]:
                    try:
                        obj = (inspect.getattr_static(obj, p) if inspect.isclass(obj)
                               else getattr(obj, p))
                    except AttributeError:
                        obj = None
                        break
                    except Exception:  # noqa: BLE001
                        obj = None
                        break
                break
        cache[dotted] = obj
        return obj

    return resolve


def resolve_dotted_object(dotted: str, work_dir: Optional[str] = None):
    """The installed object a dotted path names, or None. For `api_history`'s recorder."""
    return _dotted_resolver(_ImportLedger(2.0), work_dir)(dotted)


def _typer(an: _Analysis, ledger: _ImportLedger, work_dir: Optional[str],
           ctx=None) -> _at.Typer:
    """The typed-receiver machinery over one analysis. `exec`/`eval`/`globals()`/`locals()`
    anywhere make it INERT (types nothing): the namespaces are then not static, so no name's
    class is provable. Extension entries (a fleet's declarative table, see
    `api_types.load_extensions`) are loaded here, from the context field or its env twin."""
    _at.load_extensions(_extensions_path(ctx))
    return _at.Typer(
        an.tree, an.guarded, an.trusted, an.from_aliases,
        an.dynamic, {root for root, _a in an.assigned},
        _dotted_resolver(ledger, work_dir),
        assigned_attrs=an.assigned_attrs, inert=an.namespace_dynamic,
    )


def _extensions_path(ctx) -> Optional[str]:
    """Env twin first (an operator's setting outranks a caller's), then the context field."""
    raw = os.environ.get(ENV_EXTENSIONS)
    if raw is None:
        raw = getattr(ctx, "api_preflight_extensions", None)
    return str(raw) if raw else None


def typed_method_calls(code: str, attr: str, work_dir: Optional[str] = None) -> List[Tuple[type, str]]:
    """`(class, attr)` for every `name.attr(...)` whose receiver `name` is typed — the
    recorder's way of naming the function a `Class.method()` TypeError refers to."""
    an = _analyze(code)
    if an is None:
        return []
    typer = _typer(an, _ImportLedger(2.0), work_dir)
    out: List[Tuple[type, str]] = []
    seen: set = set()
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != attr:
            continue
        sc = typer.scope_of(node)
        t = typer.receiver_type(node.func.value, sc) if sc is not None else None
        if t is None or t.kind != "instance" or id(t.cls) in seen:
            continue
        seen.add(id(t.cls))
        out.append((t.cls, attr))
    return out


_snippet_ns: Dict[str, dict] = {}


def _ns(name: str) -> dict:
    """The exec'd namespace of one of `api_introspect`'s snippets, so the typed tier renders
    with the SAME text the environment-side reports use (one renderer, no drifting copy)."""
    if name not in _snippet_ns:
        ns: dict = {}
        exec(compile(getattr(_ai, name), f"<{name}>", "exec"), ns)
        _snippet_ns[name] = ns
    return _snippet_ns[name]


def _render_absent(f: _at.AbsentFinding) -> str:
    cls = f.cls
    label = f"{cls.__module__}.{cls.__qualname__}  (the class of `{f.name}`)"
    return _ns("CLASS_SNIPPET")["_render"](cls, label, cls.__name__, f.attr)


def _render_kwarg(f: _at.KwargFinding) -> str:
    reason = "INVALID: keyword(s) not in the INSTALLED signature: " + ", ".join(f.unknown)
    if f.forwarded_to:
        head = (f"CALLABLE {f.label}(*args, **kwargs)  (forwards every keyword to "
                f"{f.forwarded_to}, whose signature is judged below)")
        body = _ns("SIGNATURE_SNIPPET")["_render"](f.forwarded_to, f.obj, f.sig, reason)
        return head + "\n" + body
    return _ns("SIGNATURE_SNIPPET")["_render"](f.label, f.obj, f.sig, reason)


def _render_class_step(cls: type, attr: str, dotted: str) -> str:
    return _ns("CLASS_SNIPPET")["_render"](cls, dotted, cls.__name__, attr)


# ── missing top-level packages ───────────────────────────────────────────────


def _missing_policy(ctx) -> str:
    raw = os.environ.get(ENV_MISSING_PACKAGE) or getattr(ctx, "api_preflight_missing_package", "auto")
    return raw if raw in ("auto", "report", "pass") else "auto"


def _can_install(ctx) -> bool:
    """Can THIS run add a package? `PlanContext.api_preflight_can_install` (env twin
    `CMBAGENT_LG_API_CAN_INSTALL`, `1`/`0`) says so explicitly; unset, the only thing the
    engine knows is whether escalation is on. No agent-naming convention is consulted:
    cmbagent_lg does not define one."""
    raw = os.environ.get(ENV_CAN_INSTALL)
    if raw in ("0", "1"):
        return raw == "1"
    explicit = getattr(ctx, "api_preflight_can_install", None)
    if explicit is not None:
        return bool(explicit)
    return bool(getattr(ctx, "enable_escalation", False))


def _touches_installation(tree: ast.AST) -> bool:
    """The script manages its own imports at run time: `sys.path`, pip, `importlib.import_module`,
    `__import__`, `subprocess` — then what is importable is not a static fact."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            got = _chain(node)
            if got and got[0] == "sys" and got[1][:1] == ["path"]:
                return True
            if got and got[0] == "importlib" and "import_module" in got[1]:
                return True
        if isinstance(node, ast.Name) and node.id == "__import__":
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            if any(n.split(".")[0] in ("pip", "subprocess") for n in names):
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "pip install" in node.value:
            return True
    return False


def _resolves_in_workspace(root: str, work_dir: Optional[str]) -> bool:
    if not work_dir:
        return True  # cannot tell where the script will run from — do not judge
    try:
        base = Path(work_dir).expanduser()
        for sub in (base, base / "codebase", base / "data"):
            if (sub / f"{root}.py").exists() or (sub / root / "__init__.py").exists():
                return True
    except OSError:
        return True
    return False


def missing_packages(an: _Analysis, ctx, work_dir: Optional[str],
                     known_missing: Optional[set] = None) -> List[Tuple[str, str]]:
    """`(package, report)` for top-level packages the script imports at MODULE level that are
    not installed.

    A missing PACKAGE is, by default, not this check's call: an installer agent or the
    escalation path may add it, and the engine cannot know a name is not simply a package
    nobody asked for yet. So `pass` is silent; `report` bounces every one; `auto` bounces only
    when this run has no way to install anything (escalation off, no installer agent) AND a
    previous run in this same environment already died on that import (`known_missing`, from
    the observed-absence store, re-verified with `find_spec` here) — "we tried, it is not
    there, and nobody installed it" is then a fact, not a guess.

    Silent when: policy `pass`; the import is try-guarded (not in `top_level_imports`), or
    sits inside a function/branch; the script touches `sys.path`/pip/`importlib`/`subprocess`;
    the name is a stdlib module; the name resolves in the workspace or `work_dir` is unknown;
    under a remote executor (this venv is not the one asked about).
    """
    policy = _missing_policy(ctx)
    if policy == "pass" or (policy == "auto" and (_can_install(ctx) or not known_missing)):
        return []
    if os.environ.get(_ai.REMOTE_ENV):
        return []
    if _touches_installation(an.tree):
        return []
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    out: List[Tuple[str, str]] = []
    for root in an.top_level_imports:
        if root in stdlib or root in _SKIP_ROOTS or _STEP_MODULE_RE.match(root):
            continue
        if policy == "auto" and root not in (known_missing or ()):
            continue
        try:
            if importlib.util.find_spec(root) is not None:
                continue
        except BaseException:  # noqa: BLE001 — a broken package is installed, not missing
            continue
        if _resolves_in_workspace(root, work_dir):
            continue
        out.append((root, _render_missing(root, ctx)))
    return out


def _render_missing(root: str, ctx) -> str:
    try:
        installed = sorted(importlib.metadata.packages_distributions())
    except Exception:  # noqa: BLE001
        installed = []
    close = difflib.get_close_matches(root, installed, n=5, cutoff=0.6)
    lines = [
        f"PACKAGE {root!r} is NOT INSTALLED in the environment this script runs in "
        f"(importlib.util.find_spec returned None).",
        "ABSENT: the import will raise ModuleNotFoundError before any of the script runs.",
        "This run cannot install packages (no installer, no escalation), so the package must "
        "be treated as unavailable: implement the step with a library that IS installed.",
    ]
    if close:
        lines.append("INSTALLED TOP-LEVEL PACKAGES WITH A SIMILAR NAME: " + ", ".join(close))
    return "\n".join(lines)


# ── history: keyword facts a previous run proved ─────────────────────────────


def _same_class_or_subclass(cls: type, recorded: str) -> bool:
    """`recorded` (`module.qualname`) names `cls` or one of its bases."""
    for klass in getattr(cls, "__mro__", (cls,)):
        if f"{getattr(klass, '__module__', '')}.{getattr(klass, '__qualname__', '')}" == recorded:
            return True
    return False


def _history_kwargs(an: _Analysis, typer: _at.Typer, resolve, facts: Dict[str, List[dict]]
                    ) -> List[Tuple[str, str, str]]:
    """`(finding_id, label, report)` for calls that pass a keyword a previous run in THIS
    environment proved invalid on THIS exact function — identity by `module.qualname` of the
    object the call resolves to, never by a bare name.

    A qualname string is shared by many distinct objects in a large environment (every
    pybind11 `__init__`, every generated accessor), so two more things are required before a
    fact bounces a call: a method fact names the receiver's class or one of its bases, and an
    INTROSPECTABLE signature does not accept the keyword (a collision partner that names the
    keyword is a different function, whatever its qualname says)."""
    if not facts:
        return []
    out: List[Tuple[str, str, str]] = []
    seen: set = set()
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Call) or id(node) in an.guarded or not node.keywords:
            continue
        if any(kw.arg is None for kw in node.keywords):
            continue
        fn = node.func
        obj = None
        label = None
        owner_cls: Optional[type] = None     # the receiver's class, for a method call
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, (ast.Name, ast.Subscript)):
            root = fn.value.id if isinstance(fn.value, ast.Name) else None
            if root in an.trusted:
                label = an.trusted[root] + "." + fn.attr
                obj = resolve(label)
            else:
                sc = typer.scope_of(node)
                t = typer.receiver_type(fn.value, sc) if sc is not None else None
                if t is not None and t.kind == "instance":
                    obj = _at._static_attr(t.cls, fn.attr)
                    label = f"{t.cls.__name__}.{fn.attr}"
                    owner_cls = t.cls
        elif isinstance(fn, ast.Name) and fn.id in an.from_aliases and fn.id in an.trusted:
            label = an.trusted[fn.id]
            obj = resolve(label)
        if obj is None:
            continue
        target = getattr(obj, "__init__", None) if inspect.isclass(obj) else obj
        if inspect.isclass(obj):
            owner_cls = obj
        from cmbagent_lg.self_debug.api_history import func_identity
        qual = func_identity(target)
        try:
            sig_params = set(inspect.signature(target).parameters)
        except (TypeError, ValueError):
            sig_params = None
        for fact in facts.get(qual, []):
            kw = fact.get("kwarg")
            if kw not in [k.arg for k in node.keywords]:
                continue
            if sig_params is not None and kw in sig_params:
                continue  # the installed signature names it: not the function that raised
            recorded_cls = fact.get("cls") or ""
            if recorded_cls and (owner_cls is None
                                 or not _same_class_or_subclass(owner_cls, recorded_cls)):
                continue
            fid = f"{label}#{kw}"
            if fid in seen:
                continue
            seen.add(fid)
            out.append((fid, label, (
                f"CALLABLE {label}(...)  ({qual})\n"
                f"INVALID: keyword '{kw}' — a previous run in THIS environment "
                f"({fact.get('dist')} {fact.get('ver')}) raised "
                f"`TypeError: {qual.split('.')[-1]}() got an unexpected keyword argument "
                f"'{kw}'` for this exact function (observed {fact.get('seen', 1)}x, not "
                f"recalled). Drop it or use the parameter the installed signature names.")))
    return out


_loaded_classes_cache: Tuple[int, Optional[list]] = (-1, None)
_LOADED_CLASS_LIMIT = 250_000


def _loaded_classes() -> Optional[list]:
    """Every class in this process (the transitive subclasses of `object`), or None when there
    are too many to finish. Cached per `len(sys.modules)`: a new import invalidates it."""
    global _loaded_classes_cache
    stamp = len(sys.modules)
    if _loaded_classes_cache[0] == stamp:
        return _loaded_classes_cache[1]
    seen: set = set()
    out: list = []
    stack = [object]
    while stack:
        c = stack.pop()
        try:
            subs = c.__subclasses__()
        except Exception:  # noqa: BLE001
            continue
        for s in subs:
            if id(s) in seen:
                continue
            seen.add(id(s))
            out.append(s)
            stack.append(s)
            if len(out) >= _LOADED_CLASS_LIMIT:
                _loaded_classes_cache = (stamp, None)
                return None
    _loaded_classes_cache = (stamp, out)
    return out


def _any_loaded_class_has(attr: str) -> bool:
    """Does ANY class in this process answer `attr`? True also when the question cannot be
    finished (too many classes) — "cannot prove absent". A class whose lookup RAISES something
    other than AttributeError (a placeholder class for an uninstalled optional dependency,
    whose metaclass raises ImportError for every name) does not answer it: an instance of it
    would raise the same way, so it is no home for the attribute either."""
    classes = _loaded_classes()
    if classes is None:
        return True
    for c in classes:
        try:
            if hasattr(c, attr):
                return True
        except Exception:  # noqa: BLE001 — the lookup itself fails: not an answer of "yes"
            continue
    return False


def _history_instance_attrs(an: _Analysis, typer: _at.Typer, facts: List[dict],
                            max_findings: int = 3) -> List[Tuple[str, str, str]]:
    """`(finding_id, label, report)` for `name.attr(...)` calls whose `attr` a previous run in
    THIS environment saw raise `'Cls' object has no attribute 'attr'`, and that NOTHING loaded
    here could answer.

    The receiver is UNTYPED (a typed one is the typed tier's business), so the class is not
    known; what makes the verdict honest is the other direction: no class in the whole process
    — every loaded module of every package, the builtins, the stdlib — has an attribute of
    that name, the script defines no class of its own with it, never assigns or `getattr`s
    it, and imports (unguarded) the package the fact is about. A name that exists on ANY
    loaded class (`upper` on `str`, `values` on `DataFrame`, a method a package registers on
    another's class at import) is left alone.
    """
    if not facts or typer.inert:
        return []
    by_attr: Dict[str, List[dict]] = {}
    for f in facts:
        attr = f.get("attr") or ""
        root = (f.get("cls") or "").split(".")[0]
        if attr and not attr.startswith("_") and root in an.roots:
            by_attr.setdefault(attr, []).append(f)
    if not by_attr:
        return []
    # A method or function the SCRIPT defines under that name may be what the call reaches
    # (its own class, a closure stored on an object): not the library's to answer.
    script_defs = {n.name for n in ast.walk(an.tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    assigned_roots = {root for root, _a in an.assigned}
    out: List[Tuple[str, str, str]] = []
    seen: set = set()
    for node in ast.walk(an.tree):
        if not isinstance(node, ast.Call) or id(node) in an.guarded:
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)):
            continue
        attr, name = fn.attr, fn.value.id
        if attr not in by_attr or attr in seen or attr in an.assigned_attrs or attr in script_defs:
            continue
        if name in an.dynamic or name in assigned_roots or name in an.trusted:
            continue
        sc = typer.scope_of(node)
        if sc is None or typer.receiver_type(fn.value, sc) is not None:
            continue
        if _any_loaded_class_has(attr):
            continue
        fact = by_attr[attr][0]
        cls_short = (fact.get("cls") or "").split(".")[-1]
        if cls_short in script_defs:
            continue
        seen.add(attr)
        sugg = fact.get("suggest") or []
        hint = (" CLOSEST EXISTING ATTRIBUTES on that class: " + ", ".join(sugg)) if sugg else ""
        out.append((f"{cls_short}.{attr}", f"{name}.{attr}", (
            f"CALL `{name}.{attr}(...)`  (receiver class not provable from the script)\n"
            f"ABSENT: a previous run in THIS environment ({fact.get('dist')} {fact.get('ver')}) "
            f"raised `AttributeError: '{cls_short}' object has no attribute '{attr}'` "
            f"(observed {fact.get('seen', 1)}x, not recalled), and no class loaded in this "
            f"environment — in {fact.get('dist')} or anywhere else — has an attribute named "
            f"`{attr}`.{hint}")))
        if len(out) >= max_findings:
            break
    return out


# ── the message handed back to the engineer ──────────────────────────────────
#
# Deliberately names no library and no function: the facts come from the report, generated from
# the installed package itself. Naming a library here would memorise one fault.

_HEADER = (
    "**API PRE-FLIGHT — your script was NOT executed. Before running it, a deterministic "
    "check (importlib + inspect, no model involved) verified every third-party name it "
    "references against the packages ACTUALLY INSTALLED in the environment it would run in, "
    "and found name(s) that do not exist there:**"
)
_STEER = (
    "This is an API mismatch, not a logic bug: the name is simply not in the installed "
    "release, so running the script would only reproduce the same error. Rewrite it using "
    "names that EXIST, as listed above. Check each replacement's SIGNATURE before "
    "substituting — a renamed function may take its arguments in a DIFFERENT ORDER, so "
    "positional arguments that were correct for the old name can be silently wrong for the "
    "new one, giving a plausible but incorrect result rather than an error. Trust the report "
    "above over your recollection of the library. Do not resubmit the same call, and do not "
    "work around it by inventing data."
)


def build_report(
    absent: List[Tuple[str, str]],
    instance_absent: Optional[List[Tuple[str, str, str]]] = None,
    signature_mismatches: Optional[List[Tuple[str, str]]] = None,
    work_dir: Optional[str] = None,
    extra_blocks: Optional[List[str]] = None,
) -> Optional[str]:
    """The full block to inject, or None if no report could be produced.

    The per-symbol facts come from `api_introspect`, so pre-flight and the reactive retry hook
    say the same thing about the same environment. Instance- and call-shape entries arrive
    ALREADY RENDERED (a full report or ""), so there is no second verdict to take here;
    `extra_blocks` are the typed-receiver, class-step, missing-package and history renderings.

    Under `CMBAGENT_LG_REMOTE_EXECUTOR` the module-shape candidates from `absent_symbols` were
    proven against the ENGINE venv, so the guest's own report is the verdict: anything it does
    not call ABSENT is dropped rather than bounced.
    """
    blocks = _module_blocks(absent, work_dir)
    for _dotted, _attr, report in instance_absent or []:
        if report:
            blocks.append(report)
    for _dotted, report in signature_mismatches or []:
        if report:
            blocks.append(report)
    blocks += [b for b in (extra_blocks or []) if b]
    if not blocks:
        return None
    return "\n\n".join([_HEADER, "\n\n".join(blocks[:_MAX_REPORTED]), _STEER])


def _module_blocks(absent: List[Tuple[str, str]], work_dir: Optional[str]) -> List[str]:
    remote = bool(os.environ.get(_ai.REMOTE_ENV))
    blocks = []
    for module, symbol in absent:
        report = _ai.env_report(_ai.SNIPPET, {"module": module, "symbol": symbol}, work_dir,
                                lambda: _ai.introspect_local(module, symbol))
        if report.startswith("introspection failed"):
            continue
        if remote and "ABSENT:" not in report:
            continue
        blocks.append(report)
    return blocks


# ── the node ─────────────────────────────────────────────────────────────────


class _Finding(NamedTuple):
    """One provable fault: `fid` is its per-step identity for the bounce budget
    (`module.symbol`, `Class.attr`, `callable#kw`, `import:name`)."""

    fid: str
    kind: str
    named: str          # the log line's spelling
    block: str          # the rendered report, or "" for module findings rendered later


def _enabled(ctx) -> bool:
    """Flag: context field or env var.

    Every verdict comes from the environment the code will RUN in — the guest sandbox under
    `CMBAGENT_LG_REMOTE_EXECUTOR` (`api_introspect.env_report`), which answers "" when it cannot,
    so an unreachable guest degrades to normal execution rather than blocking correct code.
    """
    raw = os.environ.get("CMBAGENT_LG_API_PREFLIGHT")
    if raw == "0":
        return False
    if raw == "1":
        return True
    return bool(getattr(ctx, "enable_api_preflight", False))


def _persist_bounce(state: DebugState, code: str, report: str, index: int) -> None:
    """Keep the REJECTED script (and why it was rejected) on disk. Never raises.

    `codebase/step_{N}*.py` is written by the executor, which a bounced step never reaches, so
    without this a bounce leaves no record of what was refused. Naming mirrors
    `step_{N}_failure_{I}.py` (see the artifact map in `nodes.py`).
    """
    try:
        work_dir = state.get("work_dir")
        if not work_dir:
            return
        n = state.get("step_number", 1) or 1
        codebase = Path(work_dir).expanduser() / "codebase"
        codebase.mkdir(parents=True, exist_ok=True)
        # Code first: if the log write fails, the artifact that matters still exists.
        (codebase / f"step_{n}_bounced_{index}.py").write_text(code)
        (codebase / f"step_{n}_bounced_{index}.log").write_text(report)
    except Exception as e:  # noqa: BLE001 — bookkeeping must never break or suppress the bounce
        print(f"[api_preflight] could not persist the bounced script (non-fatal): {e}",
              file=sys.stderr, flush=True)


def persist_engineer_report(state: DebugState, attempt: int, kind: str, report: str,
                            index: Optional[int] = None) -> None:
    """Keep an API report the engineer is about to RECEIVE on disk, and say so on stderr.
    Never raises.

    Prompts are not stored in the run dir, so without this nothing records which report an
    attempt was given. `kind` is "retry" (`api_introspect.retry_report`, built from the last
    traceback) or "bounce" (`api_preflight_report`, the refused script's report); the file is
    `codebase/step_{N}_attempt_{K}.{kind}.log`, K being the attempt that receives it, or
    `step_{N}_attempt_{K}_b{index}.{kind}.log` when `index` (the step's bounce count) is given:
    a bounce of the last attempt is re-asked at the same K, so K alone would overwrite the
    report of the bounce before it. Same pattern as `_persist_bounce`.
    """
    try:
        n = state.get("step_number", 1) or 1
        print(f"[api_report] step {n} attempt {attempt}: {kind} report injected "
              f"({len(report)} chars)", file=sys.stderr, flush=True)
        work_dir = state.get("work_dir")
        if not work_dir:
            return
        codebase = Path(work_dir).expanduser() / "codebase"
        codebase.mkdir(parents=True, exist_ok=True)
        tag = f"_b{index}" if index else ""
        (codebase / f"step_{n}_attempt_{attempt}{tag}.{kind}.log").write_text(report)
    except Exception as e:  # noqa: BLE001 — bookkeeping must never break the attempt
        print(f"[api_report] could not persist the {kind} report (non-fatal): {e}",
              file=sys.stderr, flush=True)


def _import_everything(an: _Analysis, ledger: _ImportLedger, work_dir: Optional[str]) -> bool:
    """Import, through the budgeted ledger, EVERY module the script names — unguarded or
    guarded, by statement or by a literal `import_module("...")` — and say whether the class
    tiers may then be trusted.

    Why every one: a package may register methods on another package's classes when one of
    its submodules is imported (an accessor, a Boost extension class receiving methods), and
    the script's own try-guarded or `importlib` import of that submodule is what makes the
    method exist at run time. Judging a class BEFORE that import would bounce correct code.

    Returns False — "the class tiers must stay silent" — when the environment the script will
    run in cannot be reproduced here: a module the ledger could not import within budget
    (`_UNKNOWN`); a module resolving in the workspace or a `step_N` script (engineer code,
    never imported here, and able to patch anything); the script managing its own imports
    (`sys.path`, pip, a non-literal `import_module`). A module that simply is not installed
    changes nothing: the script cannot import it either.
    """
    complete = an.literal_imports_only and not _touches_installation(an.tree)
    for name in an.all_modules:
        root = name.split(".")[0]
        if root in _SKIP_ROOTS:
            continue
        if _workspace_root(root, work_dir):
            complete = False
            continue
        if _skip_root(root, work_dir):
            continue
        if ledger.get(name) is _UNKNOWN:
            complete = False
    for module, name in an.all_from:
        root = module.split(".")[0]
        if root in _SKIP_ROOTS or _workspace_root(root, work_dir) or _skip_root(root, work_dir):
            continue
        mod = ledger.get(module)
        if mod is _UNKNOWN:
            complete = False
            continue
        if mod is None:
            continue
        # `from pkg import sub` may name a SUBMODULE that has not been imported yet: importing
        # it is what the script would do. A name that is already a non-module attribute is
        # not a module (a failed attempt would be memoised, but there is no need to make one).
        try:
            got = getattr(mod, name, None)
        except Exception:  # noqa: BLE001
            got = None
        if got is not None and not _is_module(got):
            continue
        if ledger.get(f"{module}.{name}") is _UNKNOWN:
            complete = False
    return complete


def collect_findings(code: str, ctx, work_dir: Optional[str]) -> Tuple[List[_Finding], List[Tuple[str, str]]]:
    """Every provable fault in `code`, in report order, plus the raw module pairs (rendered by
    `build_report`, which asks the guest under a remote executor).

    Order of the checks is load-bearing: every module the script names is imported FIRST
    (`_import_everything` — a package may register attributes on another's classes at
    import), then the module walks, the class-step and typed-receiver tiers (only when that
    import pass was complete), the environment probes, the missing packages and the history
    facts. A script beyond `_MAX_CODE_BYTES` / `_MAX_TREE_NODES` is not examined at all.
    """
    if len(code) > _MAX_CODE_BYTES:
        return [], []
    an = _analyze(code)
    if an is None:
        return [], []
    if sum(1 for _ in ast.walk(an.tree)) > _MAX_TREE_NODES:
        return [], []
    remote = bool(os.environ.get(_ai.REMOTE_ENV))
    ledger = _ImportLedger(_import_budget(ctx))
    classes_ok = False
    if not remote:
        classes_ok = _import_everything(an, ledger, work_dir)

    findings: List[_Finding] = []
    absent, class_steps = _module_walk(an, ledger, work_dir, remote)
    for module, symbol in absent:
        findings.append(_Finding(f"{module}.{symbol}", "module", f"{module}.{symbol}", ""))

    typer = None
    if classes_ok:
        for cls, attr, dotted in class_steps:
            findings.append(_Finding(f"{cls.__name__}.{attr}", "class_step",
                                     f"{dotted}.{attr}", _render_class_step(cls, attr, dotted)))
        typer = _typer(an, ledger, work_dir, ctx)
        for f in _at.absent_on_typed_receivers(typer):
            findings.append(_Finding(f"{f.cls.__name__}.{f.attr}", "typed_attr",
                                     f"{f.name}: {f.cls.__name__}.{f.attr}", _render_absent(f)))
        for k in _at.kwarg_mismatch_on_typed_receivers(typer):
            findings.append(_Finding(f"{k.label}#{','.join(k.unknown)}", "typed_kwarg",
                                     f"{k.label}({', '.join(k.unknown)}=)", _render_kwarg(k)))

    # The environment-side instance probe is the typed tier's ancestor; locally the typed tier
    # subsumes it (same questions, more guards), so it is only asked of a remote guest.
    if remote:
        for dotted, attr, report in absent_instance_attributes(code, work_dir, an):
            m = re.search(r"ABSENT: (\w+\.\w+)", report)
            fid = m.group(1) if m else f"{dotted}(...).{attr}"
            findings.append(_Finding(fid, "instance", f"{dotted}(...).{attr}", report))
    for dotted, report in absent_signature_mismatches(code, work_dir, an, None if remote else ledger):
        kws = re.search(r"INVALID: keyword\(s\) not in the INSTALLED signature: (.*)", report)
        fid = f"{dotted}#{kws.group(1).strip() if kws else 'arity'}"
        findings.append(_Finding(fid, "signature", f"{dotted}(...) call shape", report))

    facts: Dict[str, List[dict]] = {}
    instance_facts: List[dict] = []
    known_missing: set = set()
    if not remote:
        try:
            from cmbagent_lg.self_debug import api_history as _ah
            store = _ah.store_path(ctx, work_dir)
            facts = _ah.kwarg_facts(store)
            applicable = _ah.facts_for(store)
            known_missing = {f["module"] for f in applicable
                             if f.get("kind") == "no_module" and f.get("ver") == "absent"}
            instance_facts = [f for f in applicable if f.get("kind") == "instance_attr"]
        except Exception:  # noqa: BLE001 — the store is optional
            facts, known_missing, instance_facts = {}, set(), []

    for root, report in missing_packages(an, ctx, work_dir, known_missing):
        findings.append(_Finding(f"import:{root}", "missing_package", f"import {root}", report))

    if typer is not None:
        if facts:
            for fid, label, report in _history_kwargs(an, typer, typer.resolve_dotted, facts):
                findings.append(_Finding(fid, "history", f"{label} (observed)", report))
        if instance_facts:
            for fid, label, report in _history_instance_attrs(an, typer, instance_facts):
                findings.append(_Finding(fid, "history_attr", f"{label} (observed)", report))

    # One finding per identity; the first (highest-ranked) rendering wins.
    seen: set = set()
    unique = [f for f in findings if not (f.fid in seen or seen.add(f.fid))]
    return unique, absent


@timed_node("api_preflight")
def api_preflight(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Verify the formatted code's third-party names against the environment, before running.

    Sets `api_preflight_report` (and bumps `api_preflight_bounces`) when a symbol is provably
    absent — the router then sends the step back to the engineer INSTEAD of the executor. Every
    other outcome clears the field and the code runs untouched.
    """
    ctx = runtime.context
    if not _enabled(ctx):
        return {}

    # Both bounds mirror how `max_n_attempts` is respected elsewhere: a pathological case
    # degrades to normal execution rather than spinning, and the last attempt is bounced at
    # most ONCE — the engineer's re-ask after that bounce does not count as an attempt
    # (`api_preflight_last_bounced`, read in `engineer`), so the step still runs a script and
    # a real traceback still beats a step that never ran at all. With a single-attempt budget
    # there is no retry prompt to carry a bounce report, so that attempt is never bounced.
    bounces = state.get("api_preflight_bounces", 0) or 0
    if bounces >= getattr(ctx, "max_api_preflight_bounces", 4):
        return {"api_preflight_report": None}
    attempts = state.get("attempts", 0) or 0
    at_last = attempts >= getattr(ctx, "max_n_attempts", 3)
    if at_last and (attempts <= 1 or state.get("api_preflight_last_bounced")):
        return {"api_preflight_report": None}

    code_obj = state.get("current_code")
    code = getattr(code_obj, "python_code", None) if code_obj is not None else None
    if not code:
        return {"api_preflight_report": None}

    try:
        _skip_cache.clear()
        _workspace_cache.clear()
        work_dir = state.get("work_dir")
        findings, absent = collect_findings(code, ctx, work_dir)
        report = None
        if findings:
            report = build_report(absent, None, None, work_dir,
                                  [f.block for f in findings if f.kind != "module"])
        if report:
            # Under a remote executor the guest may have overruled every module candidate.
            module_ok = {f"{m}.{s}" for m, s in absent if f"ABSENT: {m}.{s}" in report}
            findings = [f for f in findings if f.kind != "module" or f.fid in module_ok]
            if not findings:
                report = None
        if not report:
            return {"api_preflight_report": None}

        # Per-symbol budget: a step is bounced only while at least one finding is NEW to it.
        # The same finding coming back means the engineer could not act on the report, and a
        # real traceback then beats a third copy of it. A foreign checkpoint may carry anything
        # in the slot; only a dict is a record.
        raw = state.get("api_preflight_bounced")
        bounced = dict(raw) if isinstance(raw, dict) else {}
        if not any(bounced.get(f.fid, 0) == 0 for f in findings):
            print("[api_preflight] every finding was already reported to this step — executing",
                  file=sys.stderr, flush=True)
            return {"api_preflight_report": None}
        for f in findings:
            bounced[f.fid] = bounced.get(f.fid, 0) + 1

        print(
            "[api_preflight] NOT executing — absent in the installed environment: "
            + ", ".join(f.named for f in findings),
            file=sys.stderr, flush=True,
        )
        _persist_bounce(state, code, report, bounces + 1)
    except Exception as e:  # noqa: BLE001 — a pre-flight check must never break the graph
        print(f"[api_preflight] check skipped (non-fatal): {e}", file=sys.stderr, flush=True)
        return {"api_preflight_report": None}
    out = {"api_preflight_report": report, "api_preflight_bounces": bounces + 1,
           "api_preflight_bounced": bounced}
    if at_last:
        out["api_preflight_last_bounced"] = True
    return out


def route_after_format_engineer(state: DebugState, runtime: Runtime[PlanContext]) -> str:
    """Entry gate for the check. OFF → straight to the executor, exactly as before."""
    return "api_preflight" if _enabled(runtime.context) else "executor"


def route_after_api_preflight(state: DebugState, runtime: Runtime[PlanContext]) -> str:
    """A proven-absent symbol sends the code back to the engineer WITHOUT executing it;
    anything else executes normally."""
    return "engineer" if state.get("api_preflight_report") else "executor"


__all__ = [
    "api_preflight",
    "route_after_format_engineer",
    "route_after_api_preflight",
    "extract_api_references",
    "absent_symbols",
    "absent_instance_attributes",
    "absent_signature_mismatches",
    "resolve_dotted_call_by_func_name",
    "calls_on_line",
    "resolve_dotted_object",
    "typed_method_calls",
    "missing_packages",
    "collect_findings",
    "build_report",
]
