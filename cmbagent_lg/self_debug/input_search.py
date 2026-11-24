"""Where else could a "missing" input be? A bounded search before the missing-input gate.

`safe_loading`, the intent gate and the data_fixer end a step as UNFULFILLABLE when a script
fails with a file-not-found on a provided input that is absent from `data/`. A script that
opened a WRONG path (a sub-directory that does not hold the file, a user directory with a
letter dropped) while the file sits under a declared input root (`PlanContext.input_roots`)
was ended the same way, although the next attempt only needed the right path.

So before an input is declared missing, `search_missing_input` looks for it:

1. files with the same basename under `data/` and every existing input root, breadth-first
   (shallow hits first), following directory symlinks once each (no loops), bounded in
   depth, number of directory entries and wall time;
2. if none, a close name (`difflib`) among the files of the path's deepest EXISTING parent
   directory (a misspelt or mis-cased file name next to where the script looked).

A hit makes the attempt an ordinary failure: the engineer's next prompt says the path does
not exist and lists the locations found (`render_search_note`). Only a file found nowhere
keeps the terminal missing-input route. Every search is logged
(`logs/step_N_missing_input_search_A.json` and one stderr line). Never raises.
"""

from __future__ import annotations

import difflib
import os
import sys
import time
from collections import deque
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
    """Breadth-first over all roots together. Returns (hits, entries_seen, truncated_reason)."""
    deadline = time.monotonic() + limits["seconds"]
    queue = deque((r, 0) for r in roots)
    visited = set()
    hits: List[Path] = []
    seen = 0
    target = basename
    while queue:
        d, depth = queue.popleft()
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
                    if ent.is_dir(follow_symlinks=True):
                        if depth + 1 <= limits["depth"]:
                            queue.append((Path(ent.path), depth + 1))
                        continue
                    if ent.name == target and ent.is_file(follow_symlinks=True):
                        hits.append(Path(ent.path))
                        if len(hits) >= limits["hits"]:
                            return hits, seen, "hits"
                except OSError:
                    continue
        if time.monotonic() > deadline:
            return hits, seen, ("time" if queue else None)
    return hits, seen, None


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
                         limits: Optional[dict] = None) -> Optional[dict]:
    """Search for the file a script failed to open. None when there is no path to search for;
    otherwise a record with `found` (same basename), `close` (close names) and the bounds hit.
    Never raises."""
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
                  "roots": [str(r) for r in roots], "found": [], "n_found": 0, "close": [],
                  "close_parent": None, "entries_seen": 0, "truncated": None}
        from cmbagent_lg.self_debug.preflight import REMOTE_ENV

        if os.environ.get(REMOTE_ENV):
            # The engine cannot see the guest's file system; a local search would say nothing.
            record["skipped"] = "remote executor"
            return record
        if base:
            hits, seen, trunc = _walk(roots, base, lim) if roots else ([], 0, None)
            hits = [h for h in hits if h != wanted]
            hits.sort(key=lambda h: (-_score(h, wanted), len(h.parts), str(h)))
            record.update(found=[str(h) for h in hits[:SHOW_HITS]], n_found=len(hits),
                          entries_seen=seen, truncated=trunc)
            if not hits:
                close, parent = _close_names(wanted)
                record.update(close=[str(c) for c in close],
                              close_parent=str(parent) if parent else None)
        record["elapsed_s"] = round(time.monotonic() - t0, 3)
        return record
    except Exception as e:  # noqa: BLE001 — the search must never break the graph
        print(f"[input_search] search for {path!r} failed (non-fatal): {e}", file=sys.stderr, flush=True)
        return None


def search_found(record: Optional[dict]) -> bool:
    return bool(record and (record.get("found") or record.get("close")))


def current_search(state) -> Optional[dict]:
    """The search record of THIS attempt, else None (a record from an earlier attempt is stale)."""
    rec = state.get("missing_input_search")
    if isinstance(rec, dict) and rec.get("attempt") == state.get("attempts", 0):
        return rec
    return None


def found_elsewhere(state) -> bool:
    """Did this attempt's search find the "missing" input somewhere? Then it is a wrong path, not a
    missing input."""
    return search_found(current_search(state))


def summary_line(record: dict) -> str:
    if record.get("skipped"):
        return f"search skipped ({record['skipped']})"
    if record.get("found"):
        more = record["n_found"] - len(record["found"])
        return (f"found {record['n_found']} file(s) named {record['basename']!r}: "
                + ", ".join(record["found"]) + (f" (+{more} more)" if more > 0 else ""))
    if record.get("close"):
        return f"no file named {record['basename']!r}; close names: " + ", ".join(record["close"])
    bound = f", stopped at the {record['truncated']} bound" if record.get("truncated") else ""
    return (f"no file named {record['basename']!r} under {len(record.get('roots') or [])} root(s) "
            f"({record.get('entries_seen', 0)} entries{bound}) and no close name")


def render_search_note(record: Optional[dict]) -> str:
    """The engineer's retry note when the search found the input elsewhere. Empty otherwise."""
    if not search_found(record):
        return ""
    lines = [f"**WRONG INPUT PATH:** the previous script opened `{record['path']}`, which does "
             f"NOT exist."]
    if record.get("found"):
        lines.append(f"A file named `{record['basename']}` exists at:")
        lines += [f"  - `{p}`" for p in record["found"]]
        more = record["n_found"] - len(record["found"])
        if more > 0:
            lines.append(f"  - (and {more} more with the same name)")
        lines.append("Load the input from the location that matches the task (use the absolute "
                     "path), and check the path exists before reading it. Do not create or "
                     "fabricate the file.")
    else:
        lines.append(f"No file of that name exists, but these files with a close name are in "
                     f"`{record.get('close_parent')}`:")
        lines += [f"  - `{p}`" for p in record["close"]]
        lines.append("If one of them is the intended input, load it by its exact path. Do not "
                     "create or fabricate the file.")
    return "\n".join(lines)
