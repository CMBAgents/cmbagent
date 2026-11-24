"""Observed API absences — the facts one failed run leaves for every later step.

A fleet shares one environment and one model, so the same invented name recurs: the traceback
of the first failure names the exact absent symbol (and, for a method, the exact class), and
that fact is as true for every other project as it was for the one that hit it. This module is
the store: an append-only JSONL file that the executor writes AFTER a run (`record_from_run`),
the engineer prompt reads BEFORE the next script is written (`prompt_tier`), and the pre-flight
consults for the one shape it cannot prove from a signature (`kwarg_facts`).

Contract:
  * **Observed, not recalled.** A record is written only for a fault the interpreter actually
    raised, re-verified against the installed package at record time (the name is still
    absent, the class resolves inside the distribution it names, no class of that name is
    defined by the script, the site is not try-guarded, and — for an instance attribute — the
    script CALLED it and the name is not one an instance may carry although its class does
    not: a `coef_` read before `fit` is a not-fitted mistake, not an API fact).
  * **Never executes engineer code.** Every module the recorder imports or locates goes
    through the pre-flight's own `_workspace_root` rule first: a `step_N` script or anything
    resolving inside the workspace is refused before `import_module`/`find_spec` is called.
  * **Version-pinned.** Every fact carries `(dist, ver)`; a reader uses it only when the
    executing environment has the same distribution at the same version — and a module or
    class fact is dropped at read time when the (already loaded) module or class now HAS the
    attribute, so a registration another package makes at import can never leave a stale line.
  * **Env over context.** Every knob here (`CMBAGENT_LG_API_CACHE_DIR`,
    `CMBAGENT_LG_API_HISTORY_PROMPT`) outranks its `PlanContext` twin when set, the rule every
    `CMBAGENT_LG_API_*` variable follows: an operator's environment wins over a caller's field.
  * **Never raises.** Every entry point swallows everything and returns nothing/[] — a store
    that cannot be read or written is the same as an empty one.
  * **Concurrency by construction.** One `os.write` of one line ≤ 4 KiB per record, opened
    `O_APPEND|O_CREAT` (atomic on local filesystems), `flock` best-effort, never rewritten or
    compacted; readers skip unparsable (torn) lines. Four workers and restarts need nothing else.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cmbagent_lg.self_debug import api_introspect as _ai

STORE_NAME = "api_absent.jsonl"
ENV_CACHE_DIR = "CMBAGENT_LG_API_CACHE_DIR"
_MAX_LINE = 4096
_MAX_PROMPT_FACTS = 24
_MAX_PROMPT_CHARS = 3000
_DEDUPE_KEYS = ("kind", "dist", "cls", "module", "attr", "kwarg")

# The bare top-level form CPython's own regex in `api_introspect` deliberately leaves alone (a
# missing PACKAGE is a different failure for the retry path); here it is a fact worth keeping.
_NO_MODULE_RE = re.compile(r"ModuleNotFoundError: No module named '(?P<name>[\w.]+)'")


def func_identity(fn) -> str:
    """`module.qualname` of a callable — the identity a keyword fact is keyed on. Boost.Python
    functions carry a `__name__` but no `__qualname__`, hence the fallback."""
    qual = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or ""
    return f"{getattr(fn, '__module__', '') or ''}.{qual}"


# ── where ────────────────────────────────────────────────────────────────────


def cache_dir(ctx, work_dir: Optional[str]) -> Optional[Path]:
    """The shared directory: the env twin `CMBAGENT_LG_API_CACHE_DIR`, else
    `PlanContext.api_preflight_cache_dir`, else `{work_dir}/logs/api_cache/` (per RUN — a fleet
    that wants one store must set one of the first two). None when nothing names one."""
    raw = os.environ.get(ENV_CACHE_DIR) or getattr(ctx, "api_preflight_cache_dir", None)
    if raw:
        return Path(str(raw)).expanduser()
    if work_dir:
        return Path(work_dir).expanduser() / "logs" / "api_cache"
    return None


def store_path(ctx, work_dir: Optional[str]) -> Optional[Path]:
    d = cache_dir(ctx, work_dir)
    return d / STORE_NAME if d is not None else None


# ── the environment's own identity ───────────────────────────────────────────


def dist_of(root: str) -> Optional[str]:
    """The distribution providing import name `root` (`sklearn` → `scikit-learn`), or None."""
    try:
        dists = importlib.metadata.packages_distributions().get(root) or []
    except Exception:  # noqa: BLE001
        dists = []
    if dists:
        return sorted(dists)[0]
    try:
        importlib.metadata.version(root)
        return root
    except Exception:  # noqa: BLE001
        return None


def version_of(dist: str) -> Optional[str]:
    try:
        return importlib.metadata.version(dist)
    except Exception:  # noqa: BLE001
        return None


def dist_versions(dists) -> Dict[str, Optional[str]]:
    return {d: version_of(d) for d in dists}


# ── the store ────────────────────────────────────────────────────────────────


def _append(path: Path, record: dict) -> bool:
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    if len(line) + 1 > _MAX_LINE:
        return False
    data = (line + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except Exception:  # noqa: BLE001 — best effort; O_APPEND is the real guarantee
            pass
        os.write(fd, data)
        return True
    except OSError:
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


_read_cache: Dict[Tuple[str, int, int], List[dict]] = {}


def read_facts(path: Optional[Path]) -> List[dict]:
    """Every fact in the store, deduplicated on its identity with `seen` = occurrences,
    most-seen first. Torn or foreign lines are skipped. Cached per (path, size, mtime)."""
    if path is None:
        return []
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key in _read_cache:
        return _read_cache[key]
    merged: Dict[tuple, dict] = {}
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:  # noqa: BLE001 — a torn line is not a fact
                    continue
                if not isinstance(rec, dict) or not rec.get("kind") or not rec.get("dist"):
                    continue
                k = tuple(str(rec.get(f) or "") for f in _DEDUPE_KEYS)
                if k in merged:
                    merged[k]["seen"] += 1
                    if rec.get("ts", 0) > merged[k].get("ts", 0):
                        merged[k]["ts"] = rec.get("ts", 0)
                else:
                    rec = dict(rec)
                    rec["seen"] = 1
                    merged[k] = rec
    except OSError:
        return []
    facts = sorted(merged.values(), key=lambda r: (-r["seen"], -(r.get("ts") or 0)))
    _read_cache.clear()
    _read_cache[key] = facts
    return facts


def facts_for(path: Optional[Path]) -> List[dict]:
    """The facts that apply to THIS environment: `(dist, ver)` equal to what is installed, and
    — costing no import — not contradicted by a module or class that is ALREADY loaded here
    (`_still_absent`)."""
    facts = read_facts(path)
    if not facts:
        return []
    versions: Dict[str, Optional[str]] = {}
    out = []
    for f in facts:
        dist = f.get("dist", "")
        if f.get("ver") == "absent":
            # A missing top-level package: the fact holds while it is still missing.
            if _spec_missing(f.get("module", "")):
                out.append(f)
            continue
        if dist not in versions:
            versions[dist] = version_of(dist)
        if versions[dist] is not None and versions[dist] == f.get("ver") and _still_absent(f):
            out.append(f)
    return out


def _still_absent(f: dict) -> bool:
    """False when a module/class fact is contradicted by what is loaded NOW: another package
    may have registered the attribute at import (a different venv at the same version, or an
    import this run made that the recording run did not). Only `sys.modules` is consulted —
    a module that is not loaded cannot contradict anything, and is not imported to ask."""
    kind, attr = f.get("kind"), f.get("attr") or ""
    try:
        if kind == "module_attr":
            mod = sys.modules.get(f.get("module") or "")
            return mod is None or not hasattr(mod, attr)
        if kind == "instance_attr":
            cls = _loaded_class(f.get("cls") or "")
            return cls is None or not hasattr(cls, attr)
    except Exception:  # noqa: BLE001 — an unanswerable re-check keeps the fact
        return True
    return True


def _loaded_class(cls_path: str):
    """The class at `module.qualname`, from `sys.modules` only (no import), or None."""
    parts = cls_path.split(".")
    for i in range(len(parts) - 1, 0, -1):
        mod = sys.modules.get(".".join(parts[:i]))
        if mod is None:
            continue
        obj = mod
        for p in parts[i:]:
            obj = getattr(obj, p, None)
            if obj is None:
                return None
        return obj if inspect.isclass(obj) else None
    return None


def _spec_missing(name: str) -> bool:
    try:
        return bool(name) and importlib.util.find_spec(name) is None
    except BaseException:  # noqa: BLE001
        return False


# ── recording ────────────────────────────────────────────────────────────────


def _worker_id() -> str:
    """Provenance stamp on each record: `CMBAGENT_LG_WORKER` when the operator sets one (a
    stable per-worker label; informational only, nothing reads it back), else host:pid."""
    return os.environ.get("CMBAGENT_LG_WORKER") or f"{socket.gethostname()}:{os.getpid()}"


def _engineer_module(name: str, work_dir: Optional[str]) -> bool:
    """Would importing or locating `name` touch engineer-written code? The pre-flight's rule,
    applied to the TOP-LEVEL part (`find_spec("a.b")` imports `a`)."""
    from cmbagent_lg.self_debug import api_preflight as _apf
    return _apf._workspace_root(name.split(".")[0], work_dir)


def _called_attrs(tree: ast.AST) -> set:
    return {n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def _script_class_names(tree: ast.AST) -> set:
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def _closest(names, symbol: str, n: int = 3) -> List[str]:
    public = [x for x in names if not x.startswith("_")]
    return difflib.get_close_matches(symbol, public, n=n, cutoff=0.5)


def _guarded_sites(tree: ast.AST) -> set:
    out: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.body:
                for child in ast.walk(stmt):
                    out.add(id(child))
    return out


def _attr_is_guarded(tree: ast.AST, attr: str, guarded: set) -> bool:
    """Every use of `.attr` in the script sits inside a `try:` — then the failure was the
    script probing for the API, which is not a fact about the API."""
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == attr]
    return bool(uses) and all(id(n) in guarded for n in uses)


def _module_attr_record(module: str, symbol: str, work_dir: Optional[str]) -> Optional[dict]:
    if _engineer_module(module, work_dir):
        return None
    try:
        mod = importlib.import_module(module)
    except BaseException:  # noqa: BLE001
        return None
    if hasattr(mod, symbol):
        return None
    # A submodule that merely has not been imported yet is not absent. Only a PACKAGE can have
    # submodules: `find_spec("pkg.mod.Symbol")` on a plain or extension module raises
    # `ModuleNotFoundError: __path__ attribute not found`, which is the answer "not a
    # submodule", not "cannot record".
    if getattr(mod, "__path__", None) is not None:
        try:
            if importlib.util.find_spec(f"{module}.{symbol}") is not None:
                return None
        except ModuleNotFoundError:
            pass
        except BaseException:  # noqa: BLE001
            return None
    root = module.split(".")[0]
    dist = dist_of(root)
    ver = version_of(dist) if dist else None
    if not dist or not ver:
        return None
    return {"kind": "module_attr", "dist": dist, "ver": ver, "cls": "", "module": module,
            "attr": symbol, "kwarg": "", "suggest": _closest(dir(mod), symbol)}


def _no_module_record(name: str, tree: ast.AST, work_dir: Optional[str]) -> Optional[dict]:
    root = name.split(".")[0]
    if root in getattr(sys, "stdlib_module_names", ()):
        return None
    if _engineer_module(name, work_dir):
        return None
    if not _spec_missing(name):
        return None
    if "." in name:
        # A removed submodule of an INSTALLED parent is a module_attr-shaped fact.
        parent, _, leaf = name.rpartition(".")
        try:
            pmod = importlib.import_module(parent)
        except BaseException:  # noqa: BLE001
            return None
        if hasattr(pmod, leaf):
            return None
        dist = dist_of(root)
        ver = version_of(dist) if dist else None
        if not dist or not ver:
            return None
        return {"kind": "no_module", "dist": dist, "ver": ver, "cls": "", "module": name,
                "attr": "", "kwarg": "", "suggest": _closest(dir(pmod), leaf)}
    # A missing top-level package: pinned to "absent" and re-verified by every reader.
    if work_dir and _resolves_under(work_dir, root):
        return None
    return {"kind": "no_module", "dist": root, "ver": "absent", "cls": "", "module": root,
            "attr": "", "kwarg": "", "suggest": []}


def _resolves_under(work_dir: str, root: str) -> bool:
    try:
        base = Path(work_dir).expanduser()
        for sub in (base, base / "codebase", base / "data"):
            if (sub / f"{root}.py").exists() or (sub / root / "__init__.py").exists():
                return True
    except OSError:
        pass
    return False


def _class_record(type_name: str, attr: str, code: str, tree: ast.AST,
                  work_dir: Optional[str]) -> Optional[dict]:
    """Resolve the class the traceback names from the script's OWN imports (the same rule
    `CLASS_SNIPPET._resolve` uses, minus any module that is engineer code) and confirm the
    absence on it.

    Refused — not a fact about the API — when the script only READ the attribute (a
    not-yet-computed instance attribute), or when an instance of that class may legitimately
    carry the name although the class does not (`api_types._instance_may_carry`: a trailing-
    underscore name, an `__init__` parameter, a keyword into an open `__init__`)."""
    if attr not in _called_attrs(tree):
        return None
    mods, froms = _ai._imported_modules(code)
    mods = [m for m in mods if not _engineer_module(m, work_dir)]
    froms = [(m, n) for m, n in froms if not _engineer_module(m, work_dir)]
    ns: dict = {}
    try:
        exec(compile(_ai.CLASS_SNIPPET, "<api_history_class>", "exec"), ns)
        resolved = ns["_resolve"](type_name, mods, froms)
    except Exception:  # noqa: BLE001
        return None
    if resolved is None:
        return None
    _label, cls = resolved
    if ns["_synthesises_attributes"](cls) or hasattr(cls, attr):
        return None
    from cmbagent_lg.self_debug import api_types as _at
    if _at._instance_may_carry(cls, attr, _at.Typer(tree, set(), {}, set(), set(), set(),
                                                    lambda _d: None)):
        return None
    root = (cls.__module__ or "").split(".")[0]
    dist = dist_of(root)
    ver = version_of(dist) if dist else None
    if not dist or not ver:
        return None
    return {"kind": "instance_attr", "dist": dist, "ver": ver,
            "cls": f"{cls.__module__}.{cls.__qualname__}", "module": "", "attr": attr,
            "kwarg": "", "suggest": _closest(dir(cls), attr)}


def _class_from_imports(cls_name: str, code: str, work_dir: Optional[str]):
    """The ONE installed class named `cls_name` that the script's own imports export
    (`import pkg` → `pkg.<cls_name>`, `from m import <cls_name>`), by identity; else None.
    Modules that are engineer code are never asked."""
    from cmbagent_lg.self_debug import api_preflight as _apf
    modules, froms = _ai._imported_modules(code)
    paths = [f"{m}.{cls_name}" for m in modules if not _engineer_module(m, work_dir)]
    paths += [f"{m}.{n}" for m, n in froms if n == cls_name and not _engineer_module(m, work_dir)]
    found = {}
    for p in paths:
        obj = _apf.resolve_dotted_object(p, work_dir)
        if inspect.isclass(obj) and obj.__name__ == cls_name:
            found.setdefault(id(obj), obj)
    return next(iter(found.values())) if len(found) == 1 else None


def _kwarg_record(cls_name: Optional[str], func: str, kw: str, code: str,
                  tree: ast.AST, work_dir: Optional[str]) -> Optional[dict]:
    """The function the TypeError names, found through the script's own call sites — a
    module-level callable (`pkg.Thing` for `Thing.__init__`) or a method on a typed receiver
    (`model.score` for `Mixin.score`) — and checked by qualname so the fact is about exactly
    the function that raised."""
    from cmbagent_lg.self_debug import api_preflight as _apf

    want_qual = f"{cls_name}.{func}" if cls_name else func
    candidates = []
    if func == "__init__" and cls_name:
        dotted = _apf.resolve_dotted_call_by_func_name(code, cls_name)
        if dotted:
            obj = _apf.resolve_dotted_object(dotted, work_dir)
            if inspect.isclass(obj) and obj.__name__ == cls_name:
                candidates.append((obj, f"{obj.__module__}.{obj.__qualname__}", "__init__",
                                   getattr(obj, "__init__", None)))
    else:
        dotted = _apf.resolve_dotted_call_by_func_name(code, func)
        if dotted:
            obj = _apf.resolve_dotted_object(dotted, work_dir)
            if callable(obj) and not inspect.isclass(obj):
                candidates.append((obj, "", dotted, obj))
        for cls, attr in _apf.typed_method_calls(code, func, work_dir):
            candidates.append((cls, f"{cls.__module__}.{cls.__qualname__}", attr,
                               inspect.getattr_static(cls, attr, None)))
        if cls_name and not candidates:
            # The receiver is untyped (a factory's result, a variable the script assigns to), but
            # CPython named the class that defines the method: resolve it from the script's own
            # imports. The qualname and signature checks below still have to agree.
            cls = _class_from_imports(cls_name, code, work_dir)
            if cls is not None:
                candidates.append((cls, f"{cls.__module__}.{cls.__qualname__}", func,
                                   inspect.getattr_static(cls, func, None)))
    for owner, cls_path, attr, fn in candidates:
        qual = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
        if not qual or qual.split(".")[-2:] != want_qual.split(".")[-2:]:
            if not (cls_name is None and qual and qual.split(".")[-1] == func):
                continue
        # If the signature is introspectable and NAMES the keyword, or takes `**kwargs` (call
        # binding never raises this for one), the crash came from somewhere else; do not
        # record a contradiction.
        try:
            sig = inspect.signature(fn)
            if kw in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD
                                           for p in sig.parameters.values()):
                continue
        except (TypeError, ValueError):
            pass
        root_obj = owner if cls_path else fn
        root = (getattr(root_obj, "__module__", "") or "").split(".")[0]
        dist = dist_of(root)
        ver = version_of(dist) if dist else None
        if not dist or not ver:
            continue
        return {"kind": "kwarg", "dist": dist, "ver": ver, "cls": cls_path,
                "module": "" if cls_path else attr.rpartition(".")[0], "attr": attr if cls_path
                else attr.rpartition(".")[2], "kwarg": kw,
                "func": func_identity(fn), "suggest": []}
    return None


def record_from_run(ctx, code: Optional[str], stderr: Optional[str], stdout: Optional[str],
                    work_dir: Optional[str]) -> Optional[dict]:
    """Turn a finished run's traceback into a stored fact, when it names one. Never raises."""
    try:
        return _record_from_run(ctx, code, stderr, stdout, work_dir)
    except Exception as e:  # noqa: BLE001 — bookkeeping must never touch the run
        print(f"[api_history] not recorded (non-fatal): {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return None


def _record_from_run(ctx, code, stderr, stdout, work_dir) -> Optional[dict]:
    path = store_path(ctx, work_dir)
    if path is None or not code:
        return None
    if os.environ.get(_ai.REMOTE_ENV):
        return None  # the guest's environment is not this process's; nothing to verify against
    text = stderr or ""
    try:
        tree = ast.parse(code)
    except Exception:  # noqa: BLE001
        return None
    guarded = _guarded_sites(tree)
    rec: Optional[dict] = None
    hit = _ai.classify_api_error(text) or _ai.classify_api_error(stdout, allow_bare=True)
    if hit is not None and hit[0] == "module":
        module, symbol = hit[1], hit[2]
        if f"No module named '{module}.{symbol}'" in text:
            rec = _no_module_record(f"{module}.{symbol}", tree, work_dir)
        elif not _attr_is_guarded(tree, symbol, guarded):
            rec = _module_attr_record(module, symbol, work_dir)
    elif hit is not None and hit[0] == "instance":
        if hit[1] not in _script_class_names(tree) and not _attr_is_guarded(tree, hit[2], guarded):
            rec = _class_record(hit[1], hit[2], code, tree, work_dir)
    elif hit is not None and hit[0] == "signature" and hit[2]:
        rec = _kwarg_record(_ai.kwarg_error_qualifier(text), hit[1], hit[2], code, tree, work_dir)
    else:
        m = None
        for m in _NO_MODULE_RE.finditer(text):
            pass
        if m is not None:
            rec = _no_module_record(m.group("name"), tree, work_dir)
    if rec is None:
        return None
    rec.update({"imports": sorted(set(_ai._imported_modules(code)[0]))[:12],
                "src": "run", "ts": int(time.time()), "worker": _worker_id()})
    return rec if _append(path, rec) else None


# ── reading for the pre-flight ───────────────────────────────────────────────


def kwarg_facts(path: Optional[Path]) -> Dict[str, List[dict]]:
    """`{function qualname (module.qualname): [facts]}` for this environment's kwarg facts."""
    out: Dict[str, List[dict]] = {}
    for f in facts_for(path):
        if f.get("kind") == "kwarg" and f.get("func") and f.get("kwarg"):
            out.setdefault(f["func"], []).append(f)
    return out


# ── reading for the prompt ───────────────────────────────────────────────────

_TIER_HEADER = (
    "**KNOWN ABSENT IN THIS ENVIRONMENT (observed, not recalled)** — each line below is an API "
    "fault a previous run in this SAME environment actually hit, re-verified against the "
    "installed package at the version shown. Do not write any of these; where a closest "
    "existing name is given, check its signature before using it:"
)


def render_facts(facts: List[dict], max_facts: int = _MAX_PROMPT_FACTS,
                 max_chars: int = _MAX_PROMPT_CHARS) -> Optional[str]:
    lines = []
    for f in facts[:max_facts]:
        kind = f.get("kind")
        tag = f"{f.get('dist')} {f.get('ver')}"
        sugg = f.get("suggest") or []
        hint = f" (closest existing: {', '.join(sugg)})" if sugg else ""
        if kind == "module_attr":
            line = f"  - {tag}: `{f['module']}.{f['attr']}` does not exist{hint}"
        elif kind == "instance_attr":
            cls = (f.get("cls") or "").split(".")[-1]
            line = f"  - {tag}: `{cls}` has no attribute `{f['attr']}`{hint}"
        elif kind == "kwarg":
            owner = (f.get("cls") or "").split(".")[-1]
            fn = f"{owner}.{f['attr']}" if owner else f"{f.get('module')}.{f['attr']}".strip(".")
            line = f"  - {tag}: `{fn}()` accepts no keyword `{f['kwarg']}`"
        elif kind == "no_module":
            if f.get("ver") == "absent":
                line = f"  - package `{f['module']}` is NOT installed here"
            else:
                line = f"  - {tag}: module `{f['module']}` does not exist{hint}"
        else:
            continue
        if f.get("seen", 1) > 1:
            line += f"  [seen {f['seen']}x]"
        lines.append(line)
    if not lines:
        return None
    body = "\n".join(lines)
    while len(body) > max_chars and lines:
        lines.pop()
        body = "\n".join(lines)
    return _TIER_HEADER + "\n" + body if lines else None


def prompt_tier(ctx, work_dir: Optional[str]) -> Optional[str]:
    """The KNOWN ABSENT block for the engineer's first attempt, or None (an empty store, a
    disabled flag, an unreadable file — all the same: nothing is added). Never raises.
    Selection is store-global, most-seen first, capped by `_MAX_PROMPT_FACTS`/`_MAX_PROMPT_CHARS`;
    it is not narrowed to the step's own libraries."""
    try:
        raw = os.environ.get("CMBAGENT_LG_API_HISTORY_PROMPT")
        on = raw != "0" if raw is not None else bool(getattr(ctx, "api_history_in_prompt", True))
        if not on:
            return None
        return render_facts(facts_for(store_path(ctx, work_dir)))
    except Exception as e:  # noqa: BLE001
        print(f"[api_history] prompt tier skipped (non-fatal): {e}", file=sys.stderr, flush=True)
        return None


def session_key(work_dir: Optional[str]) -> str:
    return hashlib.sha1((work_dir or "").encode("utf-8")).hexdigest()


__all__ = ["record_from_run", "read_facts", "facts_for", "kwarg_facts", "prompt_tier",
           "render_facts", "store_path", "cache_dir", "dist_of", "version_of", "dist_versions"]
