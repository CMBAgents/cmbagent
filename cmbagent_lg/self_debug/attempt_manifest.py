"""What one attempt wrote: a recursive before/after snapshot of the run directory, plus a
check of every file path the attempt's stdout names.

The executor used to snapshot only the top level of `data/`, so a file written anywhere
else (`answer.json` at the root of the run directory, `work/spectra.npz`,
`data/plots/x.png`) was invisible: the step evaluator was told "no output files were
produced" for steps whose files existed, and rejected them. Measured on the FLAMINGO
calibration set (cases 4, 6, 8, 15), where the task asks for `answer.json` at an absolute
path in the run directory and plans write under `work/`.

One representation, shared by every consumer (the step evaluator prompt,
`output_postcondition`, the image reviewer, `infeasibility_check`, the workspace
listing):

  `data_manifest`       list of `{"path", "bytes", "modified", "change"}` — every file
                        written or modified by THIS attempt. `path` is relative to the work
                        directory (`answer.json`, `work/spectra.npz`, `data/plots/x.png`);
                        a file outside it that stdout named and this attempt wrote is
                        listed by its absolute path with `"source": "stdout"`. The field
                        keeps its old name: every entry the old top-level scan produced
                        (`data/<name>`) is produced unchanged.
  `data_manifest_info`  `{"scope", "complete", "incomplete_reason", "omitted",
                        "symlinked_dirs", "stdout_paths", "started_ns"}` — how the listing
                        was made and whether it is whole, and the classification of every
                        path stdout named: `written` / `pre_existing` / `missing`.

SEMANTICS: "written or modified by THIS attempt". The snapshot is taken immediately
before the script starts and again after it ends, and a file is listed only when it is
new or its (size, mtime_ns) changed, so a stale file an earlier step or attempt left on
disk is never listed — the metabric case in `output_postcondition`'s docstring.

BOUNDED: no symlink is followed (except a top-level `data/` that is itself a symlink,
which the old top-level scan read through), the walk stops at `max_files` files and
`max_depth` directory levels, and a hit cap marks the listing incomplete so no consumer
reads an absent file as "not written". Engine-owned directories (`ENGINE_DIRS`, top level
only) and caches/hidden directories are never entered: the engine writes the first
itself, and neither is a step's output.

stdout paths are only ever `stat()`-ed, never opened: the classification is "is it in
this attempt's snapshot diff" when the walk covered it, otherwise "was its mtime set
after the attempt started" (with `MTIME_SLACK_NS` of slack for a coarse-granularity or
networked filesystem).

REMOTE (`CMBAGENT_LG_REMOTE_EXECUTOR`): the data lives guest-side and the guest reports
the top level of its `data/` exactly as before; `remote_info` marks that scope so the
evaluator is told what the listing covers, and no engine-side check runs.
"""

from __future__ import annotations

import os
import re
from collections import deque
import stat as _stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# Directories at the TOP LEVEL of the work dir that the engine itself writes:
#   codebase/  step scripts and run logs (self_debug executor)
#   logs/      verdicts, manifests, api cache, tool transcripts
#   planning/  the plan (persistence.save_final_plan)
#   reports/   researcher write-ups (researcher / react_researcher)
#   chats/     legacy cmbagent chat transcripts, when a run dir is shared with it
ENGINE_DIRS = frozenset({"codebase", "logs", "planning", "reports", "chats"})
# Top-level files the engine writes (persistence.save_langfuse_trace_id).
ENGINE_FILES = frozenset({"langfuse_trace_id.txt"})
# Never entered at any depth. Hidden directories (".git", ".venv", ".cache", ...) are
# skipped by the leading-dot rule as well.
CACHE_DIRS = frozenset({"__pycache__", ".cache", ".ipynb_checkpoints", "node_modules",
                        ".pytest_cache", ".mypy_cache", ".git"})
_JUNK_FILES = frozenset({".DS_Store"})

DEFAULT_MAX_FILES = 5000
DEFAULT_MAX_DEPTH = 8
# Entries kept in `data_manifest`; the rest are counted in `info["omitted"]`.
MAX_LISTED = 200
MAX_STDOUT_PATHS = 25
# Head + tail of stdout scanned for paths.
_STDOUT_SCAN_CHARS = 200_000
MTIME_SLACK_NS = 2_000_000_000

Snapshot = Dict[str, Tuple[int, int]]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    return default


def walk_limits() -> Tuple[int, int]:
    """(max_files, max_depth): the module defaults, overridable per fleet with
    `CMBAGENT_LG_MANIFEST_MAX_FILES` / `CMBAGENT_LG_MANIFEST_MAX_DEPTH`."""
    return (_env_int("CMBAGENT_LG_MANIFEST_MAX_FILES", DEFAULT_MAX_FILES),
            _env_int("CMBAGENT_LG_MANIFEST_MAX_DEPTH", DEFAULT_MAX_DEPTH))


class WalkResult:
    """A bounded snapshot of a directory tree: rel posix path -> (size, mtime_ns)."""

    __slots__ = ("files", "truncated", "symlinked_dirs")

    def __init__(self):
        self.files: Snapshot = {}
        # None when the whole tree was covered, else why it stopped.
        self.truncated: Optional[str] = None
        # Top-level-relative paths of symlinked directories that were not entered.
        self.symlinked_dirs: List[str] = []


def _skip_dir(name: str, rel_parent: str) -> bool:
    if name.startswith(".") or name in CACHE_DIRS:
        return True
    return rel_parent == "" and name in ENGINE_DIRS


def snapshot_tree(root, max_files: Optional[int] = None,
                  max_depth: Optional[int] = None) -> WalkResult:
    """Walk `root` (sorted, breadth-first) and record every regular file outside the
    excluded directories. Never raises: an unreadable directory is skipped."""
    lim_files, lim_depth = walk_limits()
    max_files = lim_files if max_files is None else max_files
    max_depth = lim_depth if max_depth is None else max_depth
    res = WalkResult()
    root = Path(root).expanduser()
    if not root.is_dir():
        return res
    seen_dirs = set()
    queue = deque([(root, "", 0)])
    while queue:
        d, rel_d, depth = queue.popleft()
        try:
            st = d.stat()
            key = (st.st_dev, st.st_ino)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            rel = f"{rel_d}/{e.name}" if rel_d else e.name
            try:
                is_link = e.is_symlink()
                if e.is_dir(follow_symlinks=False) or (is_link and e.is_dir()):
                    if _skip_dir(e.name, rel_d):
                        continue
                    # The one symlink followed: a top-level data/ that IS a link (the
                    # engine's output dir; the old top-level scan read through it).
                    if is_link and not (rel_d == "" and e.name == "data"):
                        if len(res.symlinked_dirs) < 20:
                            res.symlinked_dirs.append(rel)
                        continue
                    if depth + 1 > max_depth:
                        res.truncated = res.truncated or (
                            f"directories deeper than {max_depth} levels were not scanned")
                        continue
                    queue.append((Path(e.path), rel, depth + 1))
                    continue
                if is_link or not e.is_file(follow_symlinks=False):
                    continue
                if e.name in _JUNK_FILES or e.name.endswith(".pyc"):
                    continue
                if rel_d == "" and e.name in ENGINE_FILES:
                    continue
                if len(res.files) >= max_files:
                    res.truncated = f"the scan stopped at the {max_files}-file cap"
                    return res
                s = e.stat(follow_symlinks=False)
                res.files[rel] = (s.st_size, s.st_mtime_ns)
            except OSError:
                continue
    return res


def _iso(mtime_ns: int) -> str:
    return (datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def diff_snapshots(before: WalkResult, after: WalkResult, started_ns: int) -> List[dict]:
    """Files new or changed in `after` relative to `before`, sorted by path.

    A file absent from a TRUNCATED `before` may simply have been past the cap, so it is
    counted only when its mtime is not older than the attempt (minus the slack)."""
    out = []
    for rel in sorted(after.files):
        size, mtime_ns = after.files[rel]
        prior = before.files.get(rel)
        if prior is None:
            if before.truncated and mtime_ns < started_ns - MTIME_SLACK_NS:
                continue
            change = "new"
        elif prior != (size, mtime_ns):
            change = "modified"
        else:
            continue
        out.append({"path": rel, "bytes": size, "modified": _iso(mtime_ns), "change": change})
    return out


# ── stdout path checks ───────────────────────────────────────────────────────

# Extensions a printed token must carry to count as a file path: data, figures, text and
# archives. Not `.py`: a library path in a printed warning is not an output. A dotted
# module name (`np.linalg`, `scipy.optimize`) or a number never matches.
_FILE_EXTS = frozenset("""
csv tsv txt dat json jsonl ndjson yaml yml toml xml html htm md rst tex log out
npy npz h5 hdf5 hdf he5 nc cdf fits fit fz parquet feather arrow pkl pickle joblib
mat sav zarr db sqlite asdf ecsv vot votable root
png jpg jpeg gif webp svg pdf eps ps tif tiff bmp
gz bz2 xz zip tar tgz
""".split())

# A path-like token: optional ~, ./ or ../ or /, segments of [\w.+-], an extension.
# Not preceded by a path character or ':' (keeps URLs out); no spaces (conservative).
_STDOUT_PATH_RE = re.compile(
    r"(?<![\w/.\-~:+])"
    r"((?:~/|\.{1,2}/|/)?(?:[\w.+\-]+/)*[\w+\-][\w.+\-]*\.([A-Za-z][A-Za-z0-9]{0,7}))"
    r"(?![\w/\-])"
)


def stdout_path_tokens(stdout: str, max_paths: int = MAX_STDOUT_PATHS) -> List[str]:
    """File-path-shaped tokens in stdout, in order of first appearance, de-duplicated and
    capped. Only tokens with a known file extension count."""
    if not stdout:
        return []
    text = stdout
    if len(text) > _STDOUT_SCAN_CHARS:
        half = _STDOUT_SCAN_CHARS // 2
        text = text[:half] + "\n" + text[-half:]
    out: List[str] = []
    for m in _STDOUT_PATH_RE.finditer(text):
        tok, ext = m.group(1).rstrip("."), m.group(2).lower()
        if ext not in _FILE_EXTS or len(tok) > 300 or "//" in tok:
            continue
        if tok not in out:
            out.append(tok)
            if len(out) >= max_paths:
                break
    return out


def _rel_to(path: Path, root: Optional[Path]) -> Optional[str]:
    if root is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def check_stdout_paths(stdout: str, run_cwd, work_dir, after: Optional[WalkResult],
                       written: Iterable[str], started_ns: int,
                       max_paths: int = MAX_STDOUT_PATHS) -> List[dict]:
    """Classify each path stdout names as `written` / `pre_existing` / `missing`.

    `{"path": <as printed>, "resolved": <absolute>, "status", "bytes"?, "in_work_dir"}`.
    Relative tokens resolve against the script's cwd. A file the walk covered is judged
    by the snapshot diff (`written`); anything else (outside the work dir, inside an
    excluded directory, past a cap) by its mtime against the attempt's start. Only
    `stat()` is called; nothing is opened, no directory is listed, and a directory
    named in stdout is not classified."""
    written = set(written)
    cwd = Path(run_cwd).expanduser() if run_cwd else None
    root = Path(work_dir).expanduser() if work_dir else None
    try:
        root_r = root.resolve() if root is not None else None
    except OSError:
        root_r = root
    out: List[dict] = []
    for tok in stdout_path_tokens(stdout, max_paths):
        p = Path(tok).expanduser()
        if not p.is_absolute():
            if cwd is None:
                continue
            p = cwd / p
        try:
            p = Path(os.path.normpath(p))
            rel = _rel_to(p, root) or _rel_to(p, root_r)
            try:
                st = p.stat()
            except FileNotFoundError:
                st = None
            except (NotADirectoryError, PermissionError):
                st = None
        except OSError:
            continue
        entry = {"path": tok, "resolved": str(p), "in_work_dir": rel is not None}
        if st is None:
            entry["status"] = "missing"
        elif _stat.S_ISDIR(st.st_mode):
            continue
        else:
            entry["bytes"] = st.st_size
            if rel is not None and after is not None and rel in after.files:
                entry["status"] = "written" if rel in written else "pre_existing"
            else:
                entry["status"] = ("written" if st.st_mtime_ns >= started_ns - MTIME_SLACK_NS
                                   else "pre_existing")
            entry["mtime_ns"] = st.st_mtime_ns
        if rel is not None:
            entry["rel"] = rel
        out.append(entry)
    return out


# ── building the attempt's manifest ─────────────────────────────────────────


def build(before: WalkResult, after: WalkResult, started_ns: int, stdout: str,
          run_cwd, work_dir) -> Tuple[List[dict], dict]:
    """`(data_manifest, data_manifest_info)` for a local attempt (see module docstring)."""
    files = diff_snapshots(before, after, started_ns)
    written = {f["path"] for f in files}
    checks = check_stdout_paths(stdout, run_cwd, work_dir, after, written, started_ns)
    # A stdout-named file this attempt wrote that the walk could not see (outside the
    # work dir, an excluded directory, a symlinked directory, past a cap) joins the list.
    for c in checks:
        if c["status"] != "written":
            continue
        key = c.get("rel") or c["resolved"]
        if key in written:
            continue
        written.add(key)
        files.append({"path": key, "bytes": c.get("bytes", 0),
                      "modified": _iso(c["mtime_ns"]), "change": "written",
                      "source": "stdout"})
    for c in checks:
        c.pop("mtime_ns", None)
    omitted = max(0, len(files) - MAX_LISTED)
    reason = before.truncated or after.truncated
    info = {
        "scope": "work_dir",
        "complete": reason is None,
        "incomplete_reason": reason,
        "omitted": omitted,
        "symlinked_dirs": sorted(set(before.symlinked_dirs) | set(after.symlinked_dirs)),
        "stdout_paths": checks,
        "started_ns": started_ns,
    }
    return files[:MAX_LISTED], info


def no_work_dir_info(stdout: str, run_cwd, started_ns: int) -> Tuple[List[dict], dict]:
    """A run with no work dir (ephemeral tempfile): nothing to walk, only stdout checks."""
    checks = check_stdout_paths(stdout, run_cwd, None, None, (), started_ns)
    files = [{"path": c["resolved"], "bytes": c.get("bytes", 0),
              "modified": _iso(c["mtime_ns"]), "change": "written", "source": "stdout"}
             for c in checks if c["status"] == "written"]
    for c in checks:
        c.pop("mtime_ns", None)
    return files, {"scope": "none", "complete": False,
                   "incomplete_reason": "no run directory was set, so it was not scanned",
                   "omitted": 0, "symlinked_dirs": [], "stdout_paths": checks,
                   "started_ns": started_ns}


def remote_info() -> dict:
    """The guest reports the top level of its own data/; nothing is checked engine-side."""
    return {"scope": "remote_data", "complete": True, "incomplete_reason": None,
            "omitted": 0, "symlinked_dirs": [], "stdout_paths": []}


# ── readers shared by the consumers ─────────────────────────────────────────


def written_names(manifest) -> set:
    """Every path and basename in a manifest, for presence tests by path or name."""
    names = set()
    for entry in manifest or []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not path:
            continue
        names.add(str(path))
        names.add(str(path).rsplit("/", 1)[-1])
    return names


def listing_is_whole(info: Optional[dict]) -> bool:
    """False when the walk hit a cap. A missing info (old checkpoint, a test building
    state by hand) counts as whole, which is what the old top-level listing assumed."""
    if not isinstance(info, dict):
        return True
    return bool(info.get("complete", True))


def written_since_start(path: Path, info: Optional[dict]) -> bool:
    """Is `path` a file whose mtime is not older than the attempt's start? Used only
    where the snapshot could not see the path."""
    started = (info or {}).get("started_ns") if isinstance(info, dict) else None
    if started is None:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return _stat.S_ISREG(st.st_mode) and st.st_mtime_ns >= started - MTIME_SLACK_NS


def under_symlinked_dir(rel: str, info: Optional[dict]) -> bool:
    for d in (info or {}).get("symlinked_dirs", []) if isinstance(info, dict) else []:
        if rel == d or rel.startswith(d.rstrip("/") + "/"):
            return True
    return False


_EXCLUDED_NOTE = ("engine directories " + ", ".join(f"{d}/" for d in sorted(ENGINE_DIRS))
                  + " and hidden/cache directories excluded")

_STDOUT_STATUS = {
    "written": "exists; written or modified by this attempt",
    "pre_existing": "exists, but was NOT written or modified by this attempt "
                    "(it was already on disk before the run)",
    "missing": "does NOT exist",
}


def render_for_evaluator(manifest: List[dict], info: Optional[dict]) -> str:
    """The step evaluator's OUTPUT FILES section: exact about what was checked."""
    manifest = manifest or []
    scope = (info or {}).get("scope") if isinstance(info, dict) else None
    lines: List[str] = []
    if scope == "remote_data":
        if manifest:
            lines.append("Files this attempt wrote or modified in data/ on the execution "
                         "sandbox (top level of data/ only; files elsewhere are not "
                         "visible to the engine):")
        else:
            lines.append("No file in data/ on the execution sandbox was written or modified "
                         "by this attempt (top level of data/ only; files elsewhere are not "
                         "visible to the engine).")
    elif scope == "none":
        lines.append("No run directory was set, so written files were not tracked; only "
                     "the file paths named in stdout were checked.")
    elif scope == "work_dir":
        if manifest:
            lines.append("Files in the run directory written or modified by this attempt "
                         f"(checked recursively; paths relative to the run directory; "
                         f"{_EXCLUDED_NOTE}):")
        else:
            lines.append("No file in the run directory was written or modified by this "
                         f"attempt (checked recursively; {_EXCLUDED_NOTE}).")
    else:  # no info: a state built before this field existed
        if not manifest:
            lines.append("No file was recorded as written or modified by this attempt.")
    for f in manifest:
        where = ""
        if f.get("source") == "stdout":
            where = (", outside the run directory; named in stdout"
                     if str(f.get("path", "")).startswith("/") else ", named in stdout")
        lines.append(f"- {f['path']} ({f.get('bytes', '?')} bytes{where})")
    if isinstance(info, dict):
        if info.get("omitted"):
            lines.append(f"- ... and {info['omitted']} more file(s) written by this attempt, "
                         "not listed")
        if scope == "work_dir" and not info.get("complete", True):
            lines.append(f"NOTE: this listing is INCOMPLETE ({info.get('incomplete_reason')}). "
                         "A file this attempt wrote may be missing from it, so do not "
                         "conclude from its absence alone that a file was not written.")
        if info.get("symlinked_dirs"):
            lines.append("Symlinked directories were not scanned: "
                         + ", ".join(f"{d}/" for d in info["symlinked_dirs"]) + ".")
        checks = info.get("stdout_paths") or []
        if checks:
            lines.append("")
            lines.append("File paths named in stdout, checked on disk (stat only, "
                         "contents not read):")
            for c in checks:
                lines.append(f"- {c['path']}: {_STDOUT_STATUS.get(c['status'], c['status'])}")
        elif scope in ("work_dir", "none"):
            lines.append("stdout names no file path to check.")
    return "\n".join(lines)


def image_paths(work_dir, manifest) -> List[Path]:
    """Absolute paths of the manifest's entries, in order (relative ones resolve against
    the work dir; a stdout-named absolute path stays as it is)."""
    wd = Path(work_dir).expanduser() if work_dir else None
    out = []
    for entry in manifest or []:
        rel = (entry or {}).get("path", "") if isinstance(entry, dict) else str(entry or "")
        if not rel:
            continue
        p = Path(rel)
        if not p.is_absolute():
            if wd is None:
                continue
            p = wd / p
        out.append(p)
    return out


def other_workspace_files(work_dir, skip_top: Iterable[str] = ("data",),
                          max_files: Optional[int] = None) -> Tuple[List[Tuple[str, int]], Optional[str]]:
    """Files in the work dir beyond the top level of `skip_top` dirs and outside the
    engine directories, for the workspace listings: `([(rel, bytes)], truncated)`."""
    snap = snapshot_tree(work_dir, max_files=max_files)
    skip = set(skip_top)
    rows = []
    for rel, (size, _m) in sorted(snap.files.items()):
        parts = rel.split("/")
        if len(parts) == 2 and parts[0] in skip:
            continue
        rows.append((rel, size))
    return rows, snap.truncated


__all__ = [
    "ENGINE_DIRS", "CACHE_DIRS", "MAX_LISTED", "MTIME_SLACK_NS",
    "WalkResult", "snapshot_tree", "diff_snapshots", "stdout_path_tokens",
    "check_stdout_paths", "build", "no_work_dir_info", "remote_info", "written_names",
    "listing_is_whole", "written_since_start", "under_symlinked_dir",
    "render_for_evaluator", "image_paths", "other_workspace_files", "walk_limits",
]
