"""Deterministic API digest — the environment's real API, BEFORE the engineer writes anything.

The earliest of three points at which an API bug can be intercepted:

    api_digest (this module)     facts in the prompt      → the stale call is never written
    api_preflight                check the emitted code   → the stale call is written but never RUN
    api_introspect (retry)       explain the traceback     → the stale call runs, fails, is explained

The known limit: the exact installed VERSION of the candidate packages is always available, but a
module's public names can only be listed for a module identified IN ADVANCE, guessed from the task
text before any code exists. `api_preflight` knows exactly which symbol was called; this does not,
so a task that names the package but not the submodule the fault lands in can be missed.

Design contract, shared with `preflight.py` and `api_preflight.py`:
  * **Zero model calls.** `importlib.metadata` for versions (which does not import the package),
    `pkgutil` for submodules, `api_introspect.introspect_local` for public names.
  * **Never raises.** Any failure yields a shorter digest, or none.
  * **Names no library in its prompt text.** Every package mentioned is one the run itself named
    or has installed; nothing is hard-coded.
  * **Hard token budget.** Content is assembled in value-per-token order and truncated at
    `PlanContext.api_digest_max_chars`, so the most useful facts survive the cap.

Opt-in via `PlanContext.enable_api_digest` or `CMBAGENT_LG_API_DIGEST=1`, default OFF and
independent of `enable_api_preflight`. Injected by the engineer node on the FIRST attempt only.
LOCAL EXECUTION ONLY — see `enabled`.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import importlib.util
import os
import pkgutil
import re
import sys
from typing import List, Optional, Tuple

from cmbagent_lg.context import DEFAULT_API_DIGEST_MAX_CHARS

from cmbagent_lg.self_debug.api_introspect import introspect_local


# Caps. The digest is paid on every clean step too, so the cost is bounded by construction
# rather than by hoping the candidate list stays short.
_MAX_PACKAGES = 4          # distinct top-level packages in the version manifest
_MAX_NAMED_MODULES = 2     # dotted modules whose full public-name inventory is listed
_MAX_ROOT_INVENTORIES = 2  # additionally, root packages used directly
_MAX_SUBMODULES = 40       # submodules listed per package
_MAX_PUBLIC_NAMES = 400    # public names listed per named module (scipy.special has 365)
_MAX_WORKSPACE_FILES = 10  # workspace .py files scanned for imports
_MAX_WORKSPACE_BYTES = 200_000

# A dotted path — the precise signal, e.g. `scipy.special` or `Bio.SeqIO` in a bullet point.
_DOTTED_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b")
# A bare word that might be a package name, e.g. "SciPy" in prose. Filtered hard below.
_BARE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")


def _installed_top_level() -> dict:
    """`{lowercased import name: real import name}` for every INSTALLED distribution.

    Deliberately excludes the standard library: a bare stdlib name in the task text is far more
    likely an English word ("time", "random", "code") than a deliberate reference, and a spurious
    entry spends budget a real package needed.
    """
    out: dict = {}
    try:
        mapping = importlib.metadata.packages_distributions()
    except Exception:  # noqa: BLE001
        return out
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    for top in mapping:
        if not top or top.startswith("_") or top in stdlib:
            continue
        out.setdefault(top.lower(), top)
    return out


def _version(root: str) -> Optional[str]:
    """The installed version of a top-level package WITHOUT importing it.

    The version is metadata on disk, so import side effects, import time and import failures are
    all avoided. Falls back to the distribution providing the import name, since the import name
    and the distribution name are frequently not the same string.
    """
    for probe in (root,):
        try:
            return importlib.metadata.version(probe)
        except Exception:  # noqa: BLE001
            pass
    try:
        dists = importlib.metadata.packages_distributions().get(root) or []
        for d in dists:
            try:
                return importlib.metadata.version(d)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    try:  # last resort: a package with no metadata but a __version__ attribute
        mod = importlib.import_module(root)
        v = getattr(mod, "__version__", None)
        return v if isinstance(v, str) else None
    except BaseException:  # noqa: BLE001
        return None


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except BaseException:  # noqa: BLE001
        return False


def _workspace_modules(work_dir: Optional[str]) -> Tuple[List[str], List[str]]:
    """`(named_modules, roots)` imported by Python files ALREADY in the workspace.

    The most precise source there is: a step told to reuse an existing helper imports whatever
    that helper imports, and the helper is on disk before the engineer writes a line — including
    packages the task text never mentions.

    Files are PARSED, never imported: they are engineer- or user-authored code, and importing
    them would execute it.
    """
    from cmbagent_lg.self_debug.api_preflight import _skip_root, extract_api_references

    named: List[str] = []
    roots: List[str] = []
    if not work_dir:
        return named, roots

    def _absorb(dotted: str) -> None:
        root = dotted.split(".")[0]
        if _skip_root(root, work_dir):
            return
        if "." in dotted and dotted not in named and _importable(dotted):
            named.append(dotted)
        if root not in roots:
            roots.append(root)
    try:
        from pathlib import Path

        base = Path(work_dir).expanduser()
        files = sorted(base.glob("data/*.py")) + sorted(base.glob("codebase/step_*.py"))
    except Exception:  # noqa: BLE001
        return named, roots

    for path in files[:_MAX_WORKSPACE_FILES]:
        try:
            code = path.read_text(errors="replace")[:_MAX_WORKSPACE_BYTES]
            tree = ast.parse(code)
        except Exception:  # noqa: BLE001 — an unparseable helper simply contributes nothing
            continue

        # What the file IMPORTS. `import joblib` used nowhere else yields no attribute access and
        # no from-import, so the import statements have to be read directly.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    _absorb(a.name)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                _absorb(node.module)
                for a in node.names:
                    if a.name != "*" and _importable(f"{node.module}.{a.name}"):
                        _absorb(f"{node.module}.{a.name}")

        # …plus the submodules reached only by attribute access (`import scipy` → `scipy.special`).
        try:
            chains, _from_imports = extract_api_references(code)
        except Exception:  # noqa: BLE001
            chains = []
        for base_mod, _parts in chains:
            _absorb(base_mod)
    return named, roots


def candidate_modules(
    task_text: str,
    hardware_constraints: str = "",
    work_dir: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """`(named_modules, roots)` — what to digest, in descending order of confidence.

    Sources, most precise first:
      1. Modules imported by Python files ALREADY IN THE WORKSPACE (`_workspace_modules`) — read
         off real code, so it works even when the task never names a library.
      2. DOTTED module paths in the step's task/bullets, which identify a module at SUBMODULE
         level, the level an inventory is useful at.
      3. BARE package names in the same text, matched case-insensitively against the installed
         distributions and never against the standard library.
      4. Package names in `hardware_constraints`, the field that advertises the execution
         environment to the engineer.

    Deterministic: ordered by source precedence then first appearance, deduplicated.
    """
    installed = _installed_top_level()
    named, roots = _workspace_modules(work_dir)

    def _add_root(r: str) -> None:
        if r and r not in roots:
            roots.append(r)

    for text, allow_named in ((task_text or "", True), (hardware_constraints or "", False)):
        if allow_named:
            for m in _DOTTED_RE.finditer(text):
                dotted = m.group(1)
                root = dotted.split(".")[0]
                if root.lower() not in installed and not _importable(root):
                    continue  # a filename like `legacy_loader.py`, not a module
                # Trim any trailing non-module component (`scipy.optimize.curve_fit` names a
                # FUNCTION; the module is what precedes it).
                parts = dotted.split(".")
                while len(parts) > 1 and not _importable(".".join(parts)):
                    parts.pop()
                mod = ".".join(parts)
                if len(parts) > 1 and mod not in named and _importable(mod):
                    named.append(mod)
                _add_root(parts[0])
        for m in _BARE_RE.finditer(text):
            hit = installed.get(m.group(1).lower())
            if hit:
                _add_root(hit)

    # A root package used DIRECTLY is worth a full name inventory too. Appended AFTER the dotted
    # modules so the budget drops the root-level listing — least targeted, largest — first.
    named = named[:_MAX_NAMED_MODULES]
    for r in roots:
        if len(named) >= _MAX_NAMED_MODULES + _MAX_ROOT_INVENTORIES:
            break
        if r not in named and not any(n.startswith(r + ".") for n in named):
            named.append(r)
    return named, roots[:_MAX_PACKAGES]


def _submodules(root: str) -> List[str]:
    """Public submodule names of `root`, or []. Requires importing the package itself."""
    try:
        mod = importlib.import_module(root)
        path = getattr(mod, "__path__", None)
        if not path:
            return []
        return sorted(
            m.name for m in pkgutil.iter_modules(list(path)) if not m.name.startswith("_")
        )[:_MAX_SUBMODULES]
    except BaseException:  # noqa: BLE001 — a digest must never break the run
        return []


_HEADER = (
    "**INSTALLED API DIGEST — a deterministic inventory of the packages this step is likely to "
    "use, read from the environment your code will actually run in (importlib + inspect, no "
    "model involved). The version numbers below are EXACT:**"
)
_STEER = (
    "Write the code against THIS inventory, not against your recollection of these libraries. "
    "Names are added and REMOVED between releases, so a call that was correct in an older "
    "version may not exist in the one installed here; if a name you were about to use is not "
    "listed above, use one that is. Where a function appears to have been renamed, check its "
    "signature and its documented meaning before substituting — a replacement may take its "
    "arguments in a DIFFERENT ORDER, or return a different quantity (a fraction where the older "
    "name returned a percentage), either of which runs cleanly and gives a wrong answer."
)


def build_digest(
    task_text: str,
    hardware_constraints: str = "",
    max_chars: int = DEFAULT_API_DIGEST_MAX_CHARS,
    work_dir: Optional[str] = None,
) -> Optional[str]:
    """The digest block, or None when there is nothing worth saying.

    Assembled in descending value-per-token order and truncated at `max_chars`, so running out
    of budget drops the cheapest-to-lose content rather than whatever happened to be last:
    version manifest (cheapest, most informative), then submodule inventories (the only tier
    that can point at a module the task never named), then public names of a named module (by
    far the most expensive, and the most precise).
    """
    named, roots = candidate_modules(task_text, hardware_constraints, work_dir)
    if not named and not roots:
        return None

    tiers: List[str] = []

    versions = []
    for r in roots:
        v = _version(r)
        versions.append(f"  {r} {v}" if v else f"  {r} (installed; version not reported)")
    if versions:
        tiers.append("INSTALLED VERSIONS:\n" + "\n".join(versions))

    for r in roots:
        subs = _submodules(r)
        if subs:
            tiers.append(f"SUBMODULES OF {r} ({len(subs)}):\n  " + ", ".join(subs))

    # A module the workspace's own code imports but which does NOT import here says the legacy
    # import path is dead in this environment, for one line. Reported BEFORE the name
    # inventories so the budget never drops it in favour of a much larger listing.
    inventories: List[str] = []
    for mod in named:
        try:
            report = introspect_local(mod, None, _MAX_PUBLIC_NAMES)
        except Exception:  # noqa: BLE001
            continue
        if report.startswith("introspection failed"):
            continue
        if "could NOT be imported" in report:
            tiers.append("NOT IMPORTABLE HERE: " + report.split("\n", 1)[0])
        else:
            inventories.append(report)
    tiers.extend(inventories)

    # Fill to the budget, whole tiers only — half a truncated name list is worse than none,
    # because a name cut in half reads as a name that exists.
    body: List[str] = []
    used = len(_HEADER) + len(_STEER) + 4
    for tier in tiers:
        if used + len(tier) + 2 > max_chars:
            continue
        body.append(tier)
        used += len(tier) + 2
    if not body:
        return None
    return "\n\n".join([_HEADER, "\n\n".join(body), _STEER])


def enabled(ctx) -> bool:
    """Flag (context field or env var) AND local execution.

    LOCAL ONLY, for the same reason `api_preflight` is: the digest is read from THIS process's
    venv, and under `CMBAGENT_LG_REMOTE_EXECUTOR` the code runs in a sandbox on different pins.
    A confidently-stated wrong version list is worse than no digest, and cannot be noticed.
    """
    on = bool(getattr(ctx, "enable_api_digest", False)) or (
        os.environ.get("CMBAGENT_LG_API_DIGEST") == "1"
    )
    if not on:
        return False
    if os.environ.get("CMBAGENT_LG_REMOTE_EXECUTOR"):
        print("[api_digest] remote executor configured — skipping the digest rather than "
              "describing the engine venv's packages as if they were the sandbox's",
              file=sys.stderr, flush=True)
        return False
    return True


def digest_for_step(ctx, step, work_dir: Optional[str] = None) -> Optional[str]:
    """The digest for one sub-task, or None. Never raises; no model call.

    The task text, its bullets and the workspace's existing code are the input because they are
    all that exists before the engineer has written anything.
    """
    if not enabled(ctx):
        return None
    try:
        bullets = "\n".join(getattr(step, "bullet_points", None) or [])
        task = f"{getattr(step, 'sub_task', '') or ''}\n{bullets}"
        digest = build_digest(
            task,
            getattr(ctx, "hardware_constraints", "") or "",
            int(getattr(ctx, "api_digest_max_chars", DEFAULT_API_DIGEST_MAX_CHARS)),
            work_dir,
        )
    except Exception as e:  # noqa: BLE001 — a prompt addition must never break the graph
        print(f"[api_digest] skipped (non-fatal): {e}", file=sys.stderr, flush=True)
        return None
    if digest:
        print(f"[api_digest] injected {len(digest)} chars (~{len(digest) // 4} tokens)",
              file=sys.stderr, flush=True)
    return digest


__all__ = ["build_digest", "candidate_modules", "digest_for_step", "enabled"]
