"""Where else could a "missing" input be? A bounded search before the missing-input gate.

`safe_loading`, the intent gate and the data_fixer end a step as UNFULFILLABLE when a script
fails with a file-not-found on a provided input that is absent from `data/`. A script that
opened a WRONG path (a sub-directory that does not hold the file, a user directory with a
letter dropped) while the file sits under a declared input root (`PlanContext.input_roots`)
was ended the same way, although the next attempt only needed the right path.

So before an input is declared missing, `search_missing_input` looks for it:

1. files (or directories) with the same basename under `data/` and every existing input root,
   breadth-first (shallow hits first), following directory symlinks once each (no loops),
   bounded in depth, number of directory entries and wall time;
2. if none, a close name (`difflib`) among the files of the path's deepest EXISTING parent
   directory (a misspelt or mis-cased file name next to where the script looked).

Not every hit is the same input. A hit is STRONG when every directory between its root and the
file also appears in the path the script used (`<root>/<run>/output_list_ps.txt` for a script
that opened `<root>/<run>/power_spectra/output_list_ps.txt`, or a path with the user directory
mistyped); it is WEAK when it sits under a directory the script did not name (another run,
snapshot or redshift: a different input of the same name). A close name whose numbers differ
(`snapshot_0078` for `snapshot_0077`, `z1.0` for `z0.5`) is never a hit, only a hint in the
terminal note: it is the neighbouring input, not a misspelling.

A STRONG hit makes the attempt an ordinary failure (`veto`). A WEAK hit or a close name does so
at most once per step and basename (checked against the step's earlier searches), so a task
naming an input that is genuinely absent still reaches the missing-input route after one retry.
The engineer's next prompt says the path does not exist, lists the locations found and says not
to substitute a different input (`render_search_note`). Every search is logged
(`logs/step_N_missing_input_search_A.json` and one stderr line). Never raises.
"""

from __future__ import annotations

import difflib
import os
import sys
import time
from collections import deque
import re
from pathlib import Path
from typing import Iterable, List, Optional

# Bounds. The input roots can be large shared mirrors on a network file system.
MAX_DEPTH = 8           # directory levels below each root
MAX_ENTRIES = 60_000    # directory entries looked at, all roots together
MAX_SECONDS = 5.0       # wall time of one search
MAX_HITS = 200          # same-basename files collected before ranking
SHOW_HITS = 5           # locations named to the engineer
CLOSE_CUTOFF = 0.75     # difflib ratio for a close name
MAX_PARENT_ENTRIES = 5_000


def _roots(work_dir: Optional[Path], ctx) -> List[Path]:
    """`work_dir/data` (when it exists) then every existing declared input root, de-duplicated."""
    out: List[Path] = []
    if work_dir is not None:
        d = Path(work_dir).expanduser() / "data"
        if d.is_dir():
            out.append(d.resolve())
    try:
        from cmbagent_lg.self_debug.preflight import existing_input_roots

        out += existing_input_roots(ctx)
    except Exception:  # noqa: BLE001
        pass
    seen, uniq = set(), []
    for r in out:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def _abs(path: str, work_dir: Optional[Path]) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute() and work_dir is not None:
        p = Path(work_dir).expanduser() / p      # scripts run with cwd = work_dir
    return p


def _deepest_existing_parent(p: Path) -> Optional[Path]:
    for parent in p.parents:
        try:
            if parent.is_dir():
                return parent
        except OSError:
            continue
    return None


def _score(hit: Path, wanted: Path) -> int:
    """How many of the hit's directory names also appear in the path the script used: the hit
    under `<root>/<run>/` beats one under another run when the script named `<run>`."""
    parts = {s.lower() for s in wanted.parts[:-1]}
    return sum(1 for s in hit.parts[:-1] if s.lower() in parts)


def _walk(roots: Iterable[Path], basename: str, limits: dict) -> tuple:
    """Breadth-first over all roots together. Returns (hits, entries_seen, truncated_reason),
    each hit a (path, root) pair. A directory of that name is a hit too (a script listing or
    globbing a directory that is not where it looked)."""
    deadline = time.monotonic() + limits["seconds"]
    queue = deque((r, r, 0) for r in roots)
    visited = set()
    hits: List[tuple] = []
    seen = 0
    target = basename
    while queue:
        d, root, depth = queue.popleft()
        try:
            st = d.stat()
            key = (st.st_dev, st.st_ino)
        except OSError:
            continue
        if key in visited:          # a symlink loop, or two roots sharing a tree
            continue
        visited.add(key)
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for ent in it:
                seen += 1
                if seen > limits["entries"]:
                    return hits, seen, "entries"
                if (seen & 0x3FF) == 0 and time.monotonic() > deadline:
                    return hits, seen, "time"
                try:
                    is_dir = ent.is_dir(follow_symlinks=True)
                    if ent.name == target and (is_dir or ent.is_file(follow_symlinks=True)):
                        hits.append((Path(ent.path), root))
                        if len(hits) >= limits["hits"]:
                            return hits, seen, "hits"
                    if is_dir and depth + 1 <= limits["depth"]:
                        queue.append((Path(ent.path), root, depth + 1))
                except OSError:
                    continue
        if time.monotonic() > deadline:
            return hits, seen, ("time" if queue else None)
    return hits, seen, None


def _is_strong(hit: Path, root: Path, wanted: Path) -> bool:
    """Every directory between the hit's root and the hit also appears in the path the script
    used: the same input reached by a wrong path, not a same-named file of another run."""
    try:
        rel_dirs = hit.relative_to(root).parts[:-1]
    except ValueError:
        return False
    parts = {s.lower() for s in wanted.parts[:-1]}
    return all(s.lower() in parts for s in rel_dirs)


_DIGITS = re.compile(r"\d+")


def numbers_differ(a: str, b: str) -> bool:
    """Do two names differ in their numbers (`snapshot_0077` / `snapshot_0078`, `z0.5` / `z1.0`,
    `L1000N1800` / `L1000N3600`)? Such names are neighbouring inputs, not misspellings."""
    return _DIGITS.findall(a) != _DIGITS.findall(b)


def _close_names(wanted: Path) -> tuple:
    """(close-name files in the deepest existing parent, that parent)."""
    parent = _deepest_existing_parent(wanted)
    if parent is None:
        return [], None
    names = []
    try:
        with os.scandir(parent) as it:
            for i, ent in enumerate(it):
                if i >= MAX_PARENT_ENTRIES:
                    break
                try:
                    if ent.is_file(follow_symlinks=True):
                        names.append(ent.name)
                except OSError:
                    continue
    except OSError:
        return [], parent
    base = wanted.name
    lower = {n.lower(): n for n in names}
    matches = difflib.get_close_matches(base.lower(), list(lower), n=3, cutoff=CLOSE_CUTOFF)
    return [parent / lower[m] for m in matches if lower[m] != base], parent


def search_missing_input(path: Optional[str], work_dir, ctx, *, attempt: int = 0,
                         limits: Optional[dict] = None, prior: Optional[list] = None) -> Optional[dict]:
    """Search for the file a script failed to open. None when there is no path to search for;
    otherwise a record with `found` (same basename, strong hits first), `strong`, `close` (close
    names that may be misspellings), `close_hint` (close names that differ in their numbers), the
    bounds hit, and `veto`: why this attempt is NOT a missing input ("strong", "weak", "close"),
    or None. `prior` is the step's earlier search briefs (`missing_input_searches`): a weak hit
    or a close name vetoes at most once per basename. Never raises."""
    if not path:
        return None
    try:
        lim = {"depth": MAX_DEPTH, "entries": MAX_ENTRIES, "seconds": MAX_SECONDS, "hits": MAX_HITS}
        lim.update(limits or {})
        wd = Path(work_dir).expanduser() if work_dir else None
        wanted = _abs(path, wd)
        base = wanted.name
        t0 = time.monotonic()
        roots = _roots(wd, ctx)
        record = {"attempt": attempt, "path": path, "basename": base,
                  "roots": [str(r) for r in roots], "found": [], "n_found": 0, "strong": [],
                  "n_strong": 0, "close": [], "close_hint": [], "close_parent": None,
                  "entries_seen": 0, "truncated": None, "veto": None}
        from cmbagent_lg.self_debug.preflight import REMOTE_ENV

        if os.environ.get(REMOTE_ENV):
            # The engine cannot see the guest's file system; a local search would say nothing.
            record["skipped"] = "remote executor"
            return record
        if base:
            hits, seen, trunc = _walk(roots, base, lim) if roots else ([], 0, None)
            hits = [(h, r) for h, r in hits if h != wanted]
            ranked = sorted(((h, _is_strong(h, r, wanted)) for h, r in hits),
                            key=lambda hs: (not hs[1], -_score(hs[0], wanted), len(hs[0].parts), str(hs[0])))
            strong = [h for h, st in ranked if st]
            record.update(found=[str(h) for h, _ in ranked[:SHOW_HITS]], n_found=len(ranked),
                          strong=[str(h) for h in strong[:SHOW_HITS]], n_strong=len(strong),
                          entries_seen=seen, truncated=trunc)
            if not hits:
                close, parent = _close_names(wanted)
                record.update(close=[str(c) for c in close if not numbers_differ(c.name, base)],
                              close_hint=[str(c) for c in close if numbers_differ(c.name, base)],
                              close_parent=str(parent) if parent else None)
        record["veto"] = _veto(record, prior)
        record["elapsed_s"] = round(time.monotonic() - t0, 3)
        return record
    except Exception as e:  # noqa: BLE001 — the search must never break the graph
        print(f"[input_search] search for {path!r} failed (non-fatal): {e}", file=sys.stderr, flush=True)
        return None


def _veto(record: dict, prior: Optional[list]) -> Optional[str]:
    """"strong" for a strong hit (every attempt); "weak" / "close" for a weak hit or a close name,
    unless an earlier search of this step already vetoed on a weak hit or close name for the same
    basename; None otherwise."""
    if record.get("strong"):
        return "strong"
    kind = "weak" if record.get("found") else ("close" if record.get("close") else None)
    if kind is None:
        return None
    for b in prior or []:
        if (isinstance(b, dict) and b.get("basename") == record.get("basename")
                and b.get("veto") in ("weak", "close")):
            return None
    return kind


def search_found(record: Optional[dict]) -> bool:
    """Does this search make the attempt a wrong path rather than a missing input?"""
    return bool(record and record.get("veto"))


def current_search(state) -> Optional[dict]:
    """The search record of THIS attempt, else None (a record from an earlier attempt is stale)."""
    rec = state.get("missing_input_search")
    if isinstance(rec, dict) and rec.get("attempt") == state.get("attempts", 0):
        return rec
    return None


def found_elsewhere(state) -> bool:
    """Did this attempt's search find the "missing" input somewhere (and may it veto)? Then it is
    a wrong path, not a missing input."""
    return search_found(current_search(state))


def brief(record: dict) -> dict:
    """The short entry kept per search in `missing_input_searches` (and the step outcome)."""
    out = {"attempt": record.get("attempt"), "path": record.get("path"),
           "basename": record.get("basename"), "found": bool(record.get("found")),
           "strong": bool(record.get("strong")), "close": bool(record.get("close")),
           "veto": record.get("veto"),
           "locations": (record.get("found") or record.get("close") or record.get("close_hint") or [])[:3]}
    if record.get("truncated"):
        out["truncated"] = record["truncated"]
    if record.get("skipped"):
        out["skipped"] = record["skipped"]
    return out


def summary_line(record: dict) -> str:
    if record.get("skipped"):
        return f"search skipped ({record['skipped']})"
    if record.get("found"):
        more = record["n_found"] - len(record["found"])
        return (f"found {record['n_found']} file(s) named {record['basename']!r} "
                f"({record.get('n_strong', 0)} under directories the path named): "
                + ", ".join(record["found"]) + (f" (+{more} more)" if more > 0 else ""))
    if record.get("close") or record.get("close_hint"):
        bits = []
        if record.get("close"):
            bits.append("close names: " + ", ".join(record["close"]))
        if record.get("close_hint"):
            bits.append("close names with other numbers (not the same input): "
                        + ", ".join(record["close_hint"]))
        return f"no file named {record['basename']!r}; " + "; ".join(bits)
    bound = f", stopped at the {record['truncated']} bound" if record.get("truncated") else ""
    return (f"no file named {record['basename']!r} under {len(record.get('roots') or [])} root(s) "
            f"({record.get('entries_seen', 0)} entries{bound}) and no close name")


_NO_SUBSTITUTE = ("A file of the same name under another run, snapshot, redshift or resolution "
                  "directory is a DIFFERENT input: do not substitute it. If no location corresponds "
                  "to the input the task names, report that the input is missing and exit with an "
                  "error. Do not create or fabricate the file.")


def render_search_note(record: Optional[dict]) -> str:
    """The engineer's retry note when the search vetoed the missing-input route. Empty otherwise."""
    if not search_found(record):
        return ""
    lines = [f"**WRONG INPUT PATH:** the previous script opened `{record['path']}`, which does "
             f"NOT exist."]
    if record.get("found"):
        more = record["n_found"] - len(record["found"])
        if record.get("strong"):
            lines.append(f"A file named `{record['basename']}` exists at (the locations whose "
                         f"directories the path named come first):")
        else:
            lines.append(f"A file named `{record['basename']}` exists, but only under directories "
                         f"the path did not name:")
        lines += [f"  - `{p}`" for p in record["found"]]
        if more > 0:
            lines.append(f"  - (and {more} more with the same name)")
        lines.append("Load the input from the location that matches the task (use the absolute "
                     "path), and check the path exists before reading it. " + _NO_SUBSTITUTE)
    else:
        lines.append(f"No file of that name exists, but these files with a close name are in "
                     f"`{record.get('close_parent')}`:")
        lines += [f"  - `{p}`" for p in record["close"]]
        lines.append("If one of them is the intended input (a misspelt or mis-cased name), load it "
                     "by its exact path. " + _NO_SUBSTITUTE)
    return "\n".join(lines)


def terminal_note(record: Optional[dict]) -> str:
    """What the search saw, for the terminal missing-input note: a bound that stopped it, the
    same-named files that were not taken as the input, and close names. Empty when it saw
    nothing worth saying."""
    if not isinstance(record, dict) or record.get("skipped"):
        return ""
    bits = []
    if record.get("truncated"):
        bits.append(f"The search stopped at its {record['truncated']} bound after "
                    f"{record.get('entries_seen', 0)} directory entries, so the input roots were "
                    f"not searched in full.")
    if record.get("found"):
        bits.append(f"Files named '{record['basename']}' exist only under directories the path did "
                    f"not name (" + ", ".join(record["found"][:3]) + "); they were not taken as this "
                    f"input.")
    close = (record.get("close") or []) + (record.get("close_hint") or [])
    if close:
        bits.append("Files with a close name exist (" + ", ".join(close[:3]) + "); they were not "
                    "taken as this input.")
    return " ".join(bits)
