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

  `data_manifest`       list of `{"path", "bytes", "modified", "change"}` — EVERY file
                        written or modified by THIS attempt (bounded only by the walk's
                        file cap, never cut for display). `path` is relative to the work
                        directory (`answer.json`, `work/spectra.npz`, `data/plots/x.png`);
                        a file outside it that stdout named and this attempt wrote is
                        listed by its absolute path with `"source": "stdout"`. The field
                        keeps its old name: every entry the old top-level scan produced
                        (`data/<name>`) is produced unchanged.
  `data_manifest_info`  `{"scope", "complete", "incomplete_reason", "omitted",
                        "symlinked_dirs", "partial_dirs", "stdout_paths", "started_ns"}` —
                        how the listing was made and whether it is whole, which directories
                        it could not list file by file, and the classification of every
                        path stdout named: `written` / `pre_existing` / `missing` /
                        `not_found` (a bare file name found nowhere).

Only the evaluator's RENDERING is shortened (`MAX_RENDERED` entries, chosen so root
files, stdout-named files, declared outputs and figures come first, the rest collapsed per
directory). The checks read the whole list, so a step that also writes 250 chunk files
does not lose its `data/summary.csv` to a display cap.

SEMANTICS: "written or modified by THIS attempt". The snapshot is taken immediately
before the script starts and again after it ends, and a file is listed only when it is
new or its (size, mtime_ns, ctime_ns, inode) changed, so a stale file an earlier step or
attempt left on disk is never listed — the metabric case in `output_postcondition`'s
docstring. ctime is part of the key because userland cannot set it: `shutil.copy2`, tar
or zip extraction, `rsync -a` and `wget` preserve or backdate mtime, and an atomic
rename changes the inode.

BOUNDED: no symlink is followed (except a top-level `data/` that is itself a symlink,
which the old top-level scan read through), the walk stops at `max_files` files,
`max_depth` directory levels and a budget of directory entries read, and a directory
with more than `max_dir_entries` entries is not listed file by file (it is recorded in
`partial_dirs` and the walk goes on with the others, so one huge directory does not use
up the cap for the rest of the tree). A hit global cap marks the listing incomplete; a
path under a partial or symlinked directory is "unseen". No consumer reads an unseen or
absent file as "not written" on that basis alone. Engine-owned directories
(`ENGINE_DIRS`, top level only) and caches/hidden directories are never entered.

TIMES: "since the attempt started" is measured against a sentinel file the executor
touches in `logs/` right before the script starts (its filesystem timestamp, so a
networked filesystem whose server clock is skewed from the engine's is compared on its
own clock), or the engine clock, whichever is earlier; `MTIME_SLACK_NS` of slack covers
coarse-granularity filesystems. A file's change time is `max(mtime, ctime)`.

stdout paths are only ever `stat()`-ed, never opened: the classification is "is it in
this attempt's snapshot diff" when the walk covered it, otherwise "was it changed after
the attempt started". A relative token that does not resolve against the script's cwd is
matched by path suffix against the walk (`spectrum.png` printed for `data/spectrum.png`,
or a path printed after `os.chdir("work")`).

REMOTE (`CMBAGENT_LG_REMOTE_EXECUTOR`): the data lives guest-side and the guest reports
the top level of its `data/` exactly as before; `remote_info` marks that scope so the
evaluator is told what the listing covers, and no engine-side check runs.
"""

from __future__ import annotations

import os
import re
import stat as _stat
import time
from collections import deque
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Directories at the TOP LEVEL of the work dir that the engine itself writes:
#   codebase/  step scripts and run logs (self_debug executor)
#   logs/      verdicts, manifests, api cache, tool transcripts
#   planning/  the plan (persistence.save_final_plan)
#   reports/   researcher write-ups (researcher / react_researcher)
#   chats/     legacy cmbagent chat transcripts, when a run dir is shared with it
ENGINE_DIRS = frozenset({"codebase", "logs", "planning", "reports", "chats"})
# Top-level files the engine writes (persistence.save_langfuse_trace_id).
ENGINE_FILES = frozenset({"langfuse_trace_id.txt"})
# Never entered at any depth: caches and tool scratch/tracking directories. Hidden
# directories (".git", ".venv", ".cache", ...) are skipped by the leading-dot rule too.
CACHE_DIRS = frozenset({"__pycache__", ".cache", ".ipynb_checkpoints", "node_modules",
                        ".pytest_cache", ".mypy_cache", ".git", "dask-worker-space",
                        "wandb", "mlruns", "lightning_logs"})

DEFAULT_MAX_FILES = 5000
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_DIR_ENTRIES = 1000
# Directory entries read in one walk, as a multiple of max_files (partial directories
# are read up to their cap and count against it).
_ENTRY_BUDGET_FACTOR = 4
_MAX_SYMLINKED = 500
# Entries the evaluator is shown; the rest are summarised per directory.
MAX_RENDERED = 40
_MAX_RENDERED_GROUPS = 15
MAX_STDOUT_PATHS = 25
# Unique path-shaped candidates considered before choosing which to check.
_MAX_CANDIDATES = 2000
# Head + tail of stdout scanned for paths.
_STDOUT_SCAN_CHARS = 200_000
MTIME_SLACK_NS = 2_000_000_000

# rel path -> (size, mtime_ns, ctime_ns, inode)
Snapshot = Dict[str, Tuple[int, int, int, int]]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    return default


def walk_limits() -> Tuple[int, int, int]:
    """(max_files, max_depth, max_dir_entries): the module defaults, overridable per
    fleet with `CMBAGENT_LG_MANIFEST_MAX_FILES` / `_MAX_DEPTH` / `_MAX_DIR_ENTRIES`."""
    return (_env_int("CMBAGENT_LG_MANIFEST_MAX_FILES", DEFAULT_MAX_FILES),
            _env_int("CMBAGENT_LG_MANIFEST_MAX_DEPTH", DEFAULT_MAX_DEPTH),
            _env_int("CMBAGENT_LG_MANIFEST_MAX_DIR_ENTRIES", DEFAULT_MAX_DIR_ENTRIES))


def changed_ns(st) -> int:
    """When a file was last written or had its inode changed: `max(mtime, ctime)`."""
    return max(st.st_mtime_ns, st.st_ctime_ns)


class WalkResult:
    """A bounded snapshot of a directory tree: rel posix path -> (size, mtime_ns,
    ctime_ns, inode)."""

    __slots__ = ("files", "truncated", "symlinked_dirs", "partial_dirs")

    def __init__(self):
        self.files: Snapshot = {}
        # None when the whole tree was covered, else why the walk stopped short.
        self.truncated: Optional[str] = None
        # Relative paths of symlinked directories that were not entered.
        self.symlinked_dirs: List[str] = []
        # Relative paths of directories with more than `max_dir_entries` entries, which
        # were not listed file by file ("." for the root itself).
        self.partial_dirs: List[str] = []


def _skip_dir(name: str, rel_parent: str) -> bool:
    if name.startswith(".") or name in CACHE_DIRS:
        return True
    return rel_parent == "" and name in ENGINE_DIRS


def _skip_file(name: str, rel_parent: str) -> bool:
    # Hidden files (.DS_Store, .nfsXXXX silly renames, .~lock.* office locks), office
    # owner files (~$x.xlsx), bytecode, and the engine's own root files.
    if name.startswith(".") or name.startswith("~$") or name.endswith(".pyc"):
        return True
    return rel_parent == "" and name in ENGINE_FILES


def snapshot_tree(root, max_files: Optional[int] = None, max_depth: Optional[int] = None,
                  max_dir_entries: Optional[int] = None) -> WalkResult:
    """Walk `root` (breadth-first, sorted within each directory) and record every regular
    file outside the excluded directories. Never raises: an unreadable directory is
    skipped."""
    lim_files, lim_depth, lim_entries = walk_limits()
    max_files = lim_files if max_files is None else max_files
    max_depth = lim_depth if max_depth is None else max_depth
    max_dir_entries = lim_entries if max_dir_entries is None else max_dir_entries
    entry_budget = max(max_files, 1) * _ENTRY_BUDGET_FACTOR + max_dir_entries
    res = WalkResult()
    root = Path(root).expanduser()
    if not root.is_dir():
        return res
    seen_dirs = set()
    entries_read = 0
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
                # Never read a huge directory whole: one entry past the cap is enough
                # to know it is over it.
                entries = list(islice(it, max_dir_entries + 1))
        except OSError:
            continue
        entries_read += len(entries)
        if len(entries) > max_dir_entries:
            res.partial_dirs.append(rel_d or ".")
            if entries_read > entry_budget:
                res.truncated = (f"the scan stopped after reading {entries_read} "
                                 "directory entries")
                return res
            continue
        entries.sort(key=lambda e: e.name)
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
                        if len(res.symlinked_dirs) >= _MAX_SYMLINKED:
                            res.truncated = res.truncated or (
                                f"more than {_MAX_SYMLINKED} symlinked directories")
                        else:
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
                if _skip_file(e.name, rel_d):
                    continue
                if len(res.files) >= max_files:
                    res.truncated = f"the scan stopped at the {max_files}-file cap"
                    return res
                s = e.stat(follow_symlinks=False)
                res.files[rel] = (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)
            except OSError:
                continue
        if entries_read > entry_budget:
            res.truncated = f"the scan stopped after reading {entries_read} directory entries"
            return res
    return res


def _iso(mtime_ns: int) -> str:
    return (datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def _under(rel: str, dirs: Iterable[str]) -> bool:
    for d in dirs or ():
        if d == "." or rel == d or rel.startswith(d.rstrip("/") + "/"):
            return True
    return False


def diff_snapshots(before: WalkResult, after: WalkResult, started_ns: int) -> List[dict]:
    """Files new or changed in `after` relative to `before`, sorted by path.

    A file absent from `before` where `before` could not see it (the walk was cut, or its
    directory was too large to list) may simply have been missed, so it is counted only
    when it changed after the attempt started (minus the slack)."""
    out = []
    for rel in sorted(after.files):
        cur = after.files[rel]
        size, mtime_ns = cur[0], cur[1]
        prior = before.files.get(rel)
        if prior is None:
            if ((before.truncated or _under(rel, before.partial_dirs))
                    and max(cur[1:3]) < started_ns - MTIME_SLACK_NS):
                continue
            change = "new"
        elif tuple(prior) != tuple(cur):
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
# Extensions that are also common attribute or module names (`np.log`, `model.fit`,
# `self.out`, `np.mat`, `ROOT.root`, `astropy.io.fits`, `norm.cdf`, `ruamel.yaml`). A
# bare token with one of them (no `/`) is kept only when it resolves to a real file;
# it is never reported as absent.
_AMBIGUOUS_EXTS = frozenset("log fit fits out mat root db ps md sav dat cdf yaml".split())

# A path-like token: optional ~, ./ or ../ or /, segments of [\w.+-], an extension.
# Not preceded by a path character (`:` is allowed: `Saved to:/abs/answer.json`); not
# followed by a path character or `(` (a method call: `np.save(`, `model.fit(`). No
# spaces. A `host:port/...` tail is dropped by `_PORT_RE`.
_STDOUT_PATH_RE = re.compile(
    r"(?<![\w/.\-~+])"
    r"((?:~/|\.{1,2}/|/)?(?:[\w.+\-]+/)*[\w+\-][\w.+\-]*\.([A-Za-z][A-Za-z0-9]{0,7}))"
    r"(?![\w/\-(])"
)
_PORT_RE = re.compile(r"\d+/")
# A scheme-less URL: the first segment is a host name (`www.example.com/data.csv`).
_HOST_RE = re.compile(r"(?:www\.[^/]+|[\w\-.]+\.(?:com|org|net|edu|gov|io|ai|int|info"
                      r"|uk|de|fr|eu|ch|jp|cn|us|ca|au|nl|es|it))/", re.IGNORECASE)
# A save verb earlier on the same line marks the token as a claimed output, which is
# checked first when stdout names more paths than are checked.
_SAVE_CTX_RE = re.compile(
    r"\b(sav(?:e|ed|es|ing)|wr(?:ote|ite|ites|iting|itten)|output|export(?:ed|ing)?"
    r"|dump(?:ed|ing)?|stor(?:e|ed|ing)|creat(?:e|ed|ing)|produc(?:e|ed|ing))\b|->|=>",
    re.IGNORECASE,
)


def _stdout_candidates(stdout: str) -> List[Tuple[str, bool, bool]]:
    """`(token, ambiguous, after_save_verb)` per unique path-shaped token, in order of
    first appearance."""
    if not stdout:
        return []
    text = stdout
    if len(text) > _STDOUT_SCAN_CHARS:
        half = _STDOUT_SCAN_CHARS // 2
        text = text[:half] + "\n" + text[-half:]
    out: List[Tuple[str, bool, bool]] = []
    seen = set()
    for m in _STDOUT_PATH_RE.finditer(text):
        tok, ext = m.group(1).rstrip("."), m.group(2).lower()
        if ext not in _FILE_EXTS or len(tok) > 300 or "//" in tok or tok in seen:
            continue
        if m.start() and text[m.start() - 1] == ":" and _PORT_RE.match(tok):
            continue  # localhost:8000/index.html
        if _HOST_RE.match(tok):
            continue  # www.example.com/data.csv
        seen.add(tok)
        line_start = text.rfind("\n", 0, m.start()) + 1
        saved = bool(_SAVE_CTX_RE.search(text, line_start, m.start()))
        out.append((tok, ext in _AMBIGUOUS_EXTS and "/" not in tok, saved))
        if len(out) >= _MAX_CANDIDATES:
            break
    return out


def _select(cands: List[Tuple[str, bool, bool]], max_paths: int) -> List[Tuple[str, bool]]:
    """At most `max_paths` candidates: those after a save verb first, then the LAST
    others (a script that lists its inputs first and saves its answer last still has the
    answer checked). Returned in order of appearance."""
    if len(cands) <= max_paths:
        return [(t, a) for t, a, _s in cands]
    idx = [i for i, c in enumerate(cands) if c[2]][-max_paths:]
    rest = [i for i, c in enumerate(cands) if not c[2]]
    room = max_paths - len(idx)
    keep = sorted(idx + (rest[-room:] if room > 0 else []))
    return [(cands[i][0], cands[i][1]) for i in keep]


def stdout_path_tokens(stdout: str, max_paths: int = MAX_STDOUT_PATHS) -> List[str]:
    """The file-path-shaped tokens in stdout that are checked, in order of appearance,
    de-duplicated and capped (see `_select`). Only tokens with a known file extension
    count."""
    return [t for t, _a in _select(_stdout_candidates(stdout), max_paths)]


def _rel_to(path: Path, root: Optional[Path]) -> Optional[str]:
    if root is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


class _Roots:
    """The work dir as given, resolved, and (when `data/` is a symlink) where data/
    really is, so a path printed through any alias maps to one relative path."""

    def __init__(self, work_dir):
        self.root = Path(work_dir).expanduser() if work_dir else None
        self.real = None
        self.data_real = None
        if self.root is None:
            return
        try:
            self.real = Path(os.path.realpath(self.root))
            data = self.root / "data"
            if data.is_symlink():
                self.data_real = Path(os.path.realpath(data))
        except OSError:
            self.real = self.root

    def rel(self, p: Path) -> Optional[str]:
        if self.root is None:
            return None
        r = _rel_to(p, self.root) or _rel_to(p, self.real)
        if r is not None:
            return r
        try:
            real = Path(os.path.realpath(p))
        except OSError:
            return None
        r = _rel_to(real, self.real)
        if r is None and self.data_real is not None:
            d = _rel_to(real, self.data_real)
            if d is not None:
                r = "data" if d == "." else f"data/{d}"
        return r


def _suffix_match(tok: str, pools: Sequence[Iterable[str]]) -> Tuple[Optional[str], int]:
    """`(rel, pool index)` of the first file in `pools` whose path ends with `tok`."""
    parts = [s for s in Path(tok).parts if s not in (".", "")]
    if not parts or ".." in parts:
        return None, -1
    suffix = "/".join(parts)
    for i, pool in enumerate(pools):
        for rel in pool:
            if rel == suffix or rel.endswith("/" + suffix):
                return rel, i
    return None, -1


def check_stdout_paths(stdout: str, run_cwd, work_dir, after: Optional[WalkResult],
                       written: Iterable[str], started_ns: int,
                       max_paths: int = MAX_STDOUT_PATHS) -> List[dict]:
    """Classify each path stdout names as `written` / `pre_existing` / `missing` /
    `not_found`.

    `{"path": <as printed>, "resolved": <absolute>, "status", "bytes"?, "in_work_dir",
    "rel"?, "matched_by"?}`. Relative tokens resolve against the script's cwd; one that
    is not there is matched by path suffix against the files this attempt wrote, then
    against the rest of the walk (`matched_by: "suffix"`). A file the walk covered is
    judged by the snapshot diff; anything else (outside the work dir, inside an excluded
    or partial directory, past a cap) by whether it changed after the attempt started.
    A bare name found nowhere is `not_found` (it may have been saved elsewhere), never
    `missing`; an ambiguous bare name (`np.log`) found nowhere is dropped. Only `stat()`
    is called; nothing is opened, no directory is listed, and a directory named in
    stdout is not classified."""
    written = set(written)
    cwd = Path(run_cwd).expanduser() if run_cwd else None
    roots = _Roots(work_dir)
    walked = after.files if after is not None else {}
    written_rel = sorted(w for w in written if not w.startswith("/"))
    out: List[dict] = []
    for tok, ambiguous in _select(_stdout_candidates(stdout), max_paths):
        p = Path(tok).expanduser()
        relative = not p.is_absolute()
        if relative:
            if cwd is None:
                continue
            p = cwd / p
        try:
            p = Path(os.path.normpath(p))
            rel = roots.rel(p)
            try:
                st = p.stat()
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                st = None
        except OSError:
            continue
        entry = {"path": tok, "resolved": str(p)}
        if st is None and relative and roots.root is not None:
            match, pool = _suffix_match(tok, (written_rel, sorted(walked)))
            if match is not None:
                rel = match
                entry["resolved"] = str(roots.root / match)
                entry["matched_by"] = "suffix"
                entry["status"] = "written" if pool == 0 else "pre_existing"
                if match in walked:
                    entry["bytes"] = walked[match][0]
                    entry["mtime_ns"] = walked[match][1]
                else:
                    try:
                        s2 = (roots.root / match).stat()
                        entry["bytes"], entry["mtime_ns"] = s2.st_size, s2.st_mtime_ns
                    except OSError:
                        entry["bytes"], entry["mtime_ns"] = 0, started_ns
        if "status" not in entry:
            if st is None:
                if ambiguous:
                    continue
                entry["status"] = "missing" if ("/" in tok) else "not_found"
            elif _stat.S_ISDIR(st.st_mode):
                continue
            else:
                entry["bytes"] = st.st_size
                if rel is not None and rel in walked:
                    entry["status"] = "written" if rel in written else "pre_existing"
                else:
                    entry["status"] = ("written"
                                       if changed_ns(st) >= started_ns - MTIME_SLACK_NS
                                       else "pre_existing")
                entry["mtime_ns"] = st.st_mtime_ns
        entry["in_work_dir"] = rel is not None
        if rel is not None:
            entry["rel"] = rel
        out.append(entry)
    return out


# ── building the attempt's manifest ─────────────────────────────────────────


def build(before: WalkResult, after: WalkResult, started_ns: int, stdout: str,
          run_cwd, work_dir) -> Tuple[List[dict], dict]:
    """`(data_manifest, data_manifest_info)` for a local attempt (see module docstring).
    The manifest is the whole diff; nothing is cut for display here."""
    files = diff_snapshots(before, after, started_ns)
    written = {f["path"] for f in files}
    checks = check_stdout_paths(stdout, run_cwd, work_dir, after, written, started_ns)
    # A stdout-named file this attempt wrote that the walk could not see (outside the
    # work dir, an excluded, partial or symlinked directory, past a cap) joins the list.
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
    reason = before.truncated or after.truncated
    info = {
        "scope": "work_dir",
        "complete": reason is None,
        "incomplete_reason": reason,
        "omitted": 0,
        "symlinked_dirs": sorted(set(before.symlinked_dirs) | set(after.symlinked_dirs)),
        "partial_dirs": sorted(set(before.partial_dirs) | set(after.partial_dirs)),
        "stdout_paths": checks,
        "started_ns": started_ns,
    }
    return files, info


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
                   "omitted": 0, "symlinked_dirs": [], "partial_dirs": [],
                   "stdout_paths": checks, "started_ns": started_ns}


def remote_info() -> dict:
    """The guest reports the top level of its own data/; nothing is checked engine-side."""
    return {"scope": "remote_data", "complete": True, "incomplete_reason": None,
            "omitted": 0, "symlinked_dirs": [], "partial_dirs": [], "stdout_paths": []}


def attempt_start_ns(logs_dir: Optional[Path]) -> int:
    """The reference time for "changed since the attempt started": the earlier of the
    engine clock and the filesystem timestamp of a sentinel touched in `logs/` (then
    removed), so a networked filesystem whose clock is skewed from the engine's is
    compared on its own clock. Never raises."""
    now = time.time_ns()
    if logs_dir is None:
        return now
    sentinel = Path(logs_dir) / ".attempt_start"
    try:
        sentinel.write_text("")
        fs_ns = sentinel.stat().st_mtime_ns
    except OSError:
        return now
    finally:
        try:
            sentinel.unlink()
        except OSError:
            pass
    return min(now, fs_ns)


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
    """False when the walk hit a cap, or (an older state) entries were cut from the
    manifest. A missing info (old checkpoint, a test building state by hand) counts as
    whole, which is what the old top-level listing assumed."""
    if not isinstance(info, dict):
        return True
    return bool(info.get("complete", True)) and not info.get("omitted")


def written_since_start(path: Path, info: Optional[dict]) -> bool:
    """Is `path` a regular file changed (mtime or ctime) since the attempt started? Used
    only where the snapshot could not see the path."""
    started = (info or {}).get("started_ns") if isinstance(info, dict) else None
    if started is None:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return _stat.S_ISREG(st.st_mode) and changed_ns(st) >= started - MTIME_SLACK_NS


def under_unscanned_dir(rel: str, info: Optional[dict]) -> bool:
    """Is `rel` under a directory the walk did not list file by file (symlinked, or
    with more entries than the per-directory cap)?"""
    if not isinstance(info, dict):
        return False
    return _under(rel, info.get("symlinked_dirs") or ()) or _under(
        rel, info.get("partial_dirs") or ())


# Kept for callers of the earlier name.
under_symlinked_dir = under_unscanned_dir


_EXCLUDED_NOTE = ("engine directories " + ", ".join(f"{d}/" for d in sorted(ENGINE_DIRS))
                  + " and hidden/cache directories excluded")

_STDOUT_STATUS = {
    "written": "exists; written or modified by this attempt",
    "pre_existing": "exists, but was NOT written or modified by this attempt "
                    "(it was already on disk before the run)",
    "missing": "does NOT exist",
    "not_found": "no file at this name in the script's working directory, and no file "
                 "of this name in the scanned run directory (a bare name; it may refer "
                 "to a file saved elsewhere)",
}

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf",
                         ".eps", ".tif", ".tiff", ".bmp"})


def _human(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def _rank(entry: dict, stdout_named: set, priority: set) -> int:
    path = str(entry.get("path", ""))
    if path in priority or path.rsplit("/", 1)[-1] in priority:
        return 0
    if entry.get("source") == "stdout" or path in stdout_named:
        return 1
    if "/" not in path:
        return 2
    if Path(path).suffix.lower() in _IMAGE_EXTS:
        return 3
    return 4


def _choose(manifest: List[dict], info: Optional[dict], priority: Iterable[str],
            limit: int) -> Tuple[List[dict], List[dict]]:
    """`(shown, rest)`: declared outputs, stdout-named files, root files and figures
    first, then the others in path order; `shown` keeps the manifest's order."""
    if len(manifest) <= limit:
        return list(manifest), []
    stdout_named = {c.get("rel") for c in ((info or {}).get("stdout_paths") or [])
                    if isinstance(info, dict)} - {None}
    prio = set()
    for p in priority or ():
        prio.add(p)
        prio.add(p.rsplit("/", 1)[-1])
    order = sorted(range(len(manifest)),
                   key=lambda i: (_rank(manifest[i], stdout_named, prio), i))
    keep = set(order[:limit])
    return ([e for i, e in enumerate(manifest) if i in keep],
            [e for i, e in enumerate(manifest) if i not in keep])


def _summarise(rest: List[dict]) -> List[str]:
    groups: Dict[str, List[int]] = {}
    for e in rest:
        path = str(e.get("path", ""))
        parent = path.rsplit("/", 1)[0] + "/" if "/" in path else "./"
        g = groups.setdefault(parent, [0, 0])
        g[0] += 1
        g[1] += int(e.get("bytes") or 0)
    ranked = sorted(groups.items(), key=lambda kv: (-kv[1][0], kv[0]))
    lines = [f"- {d}: {n} more file(s) written by this attempt, {_human(b)}"
             for d, (n, b) in ranked[:_MAX_RENDERED_GROUPS]]
    if len(ranked) > _MAX_RENDERED_GROUPS:
        extra = ranked[_MAX_RENDERED_GROUPS:]
        lines.append(f"- ... and {sum(v[0] for _d, v in extra)} more file(s) in "
                     f"{len(extra)} other director(ies)")
    return lines


def render_for_evaluator(manifest: List[dict], info: Optional[dict],
                         priority: Iterable[str] = ()) -> str:
    """The step evaluator's OUTPUT FILES section: exact about what was checked.
    `priority` (the step's declared outputs) is listed first when the manifest is longer
    than `MAX_RENDERED`; the rest is summarised per directory."""
    manifest = manifest or []
    has_info = isinstance(info, dict)
    scope = info.get("scope") if has_info else None
    partial = (info.get("partial_dirs") or []) if has_info else []
    whole = has_info and bool(info.get("complete", True)) and not partial
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
        elif whole:
            lines.append("No file in the run directory was written or modified by this "
                         f"attempt (checked recursively; {_EXCLUDED_NOTE}).")
        else:
            lines.append("No written file was found in the scanned part of the run "
                         f"directory (checked recursively; {_EXCLUDED_NOTE}).")
    else:  # no info: a state built before this field existed
        if not manifest:
            lines.append("No file was recorded as written or modified by this attempt.")
    shown, rest = _choose(manifest, info, priority, MAX_RENDERED)
    for f in shown:
        where = ""
        if f.get("source") == "stdout":
            where = (", outside the run directory; named in stdout"
                     if str(f.get("path", "")).startswith("/") else ", named in stdout")
        lines.append(f"- {f['path']} ({f.get('bytes', '?')} bytes{where})")
    lines.extend(_summarise(rest))
    if has_info:
        if info.get("omitted"):
            lines.append(f"- ... and {info['omitted']} more file(s) written by this attempt, "
                         "not listed")
        if scope == "work_dir" and not info.get("complete", True):
            lines.append(f"NOTE: this listing is INCOMPLETE ({info.get('incomplete_reason')}). "
                         "A file this attempt wrote may be missing from it, so do not "
                         "conclude from its absence alone that a file was not written.")
        if partial:
            names = ", ".join("the run directory itself" if d == "." else f"{d}/"
                              for d in partial[:20])
            more = f" and {len(partial) - 20} more" if len(partial) > 20 else ""
            lines.append("Directories too large to list file by file (files this attempt "
                         f"wrote in them are not listed): {names}{more}.")
        sym = info.get("symlinked_dirs") or []
        if sym:
            more = f" and {len(sym) - 20} more" if len(sym) > 20 else ""
            lines.append("Symlinked directories were not scanned: "
                         + ", ".join(f"{d}/" for d in sym[:20]) + more + ".")
        checks = info.get("stdout_paths") or []
        if checks:
            lines.append("")
            lines.append("File paths named in stdout, checked on disk (stat only, "
                         "contents not read):")
            for c in checks:
                status = _STDOUT_STATUS.get(c["status"], c["status"])
                if c.get("matched_by") == "suffix" and c.get("rel"):
                    status += f" (found as {c['rel']})"
                elif c["status"] == "missing" and not str(c["path"]).startswith(("/", "~")):
                    status += " (resolved against the script's working directory)"
                lines.append(f"- {c['path']}: {status}")
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
    engine directories, for the workspace listings: `([(rel, bytes)], truncated)`, where
    `truncated` also names the directories too large to list."""
    snap = snapshot_tree(work_dir, max_files=max_files)
    skip = set(skip_top)
    rows = []
    for rel, meta in sorted(snap.files.items()):
        parts = rel.split("/")
        if len(parts) == 2 and parts[0] in skip:
            continue
        rows.append((rel, meta[0]))
    note = snap.truncated
    if snap.partial_dirs:
        big = ", ".join(f"{d}/" for d in snap.partial_dirs[:10])
        note = "; ".join(x for x in (note, f"too large to list: {big}") if x)
    return rows, note


__all__ = [
    "ENGINE_DIRS", "CACHE_DIRS", "MAX_RENDERED", "MTIME_SLACK_NS",
    "WalkResult", "snapshot_tree", "diff_snapshots", "stdout_path_tokens",
    "check_stdout_paths", "build", "no_work_dir_info", "remote_info", "attempt_start_ns",
    "changed_ns", "written_names", "listing_is_whole", "written_since_start",
    "under_unscanned_dir", "under_symlinked_dir", "render_for_evaluator", "image_paths",
    "other_workspace_files", "walk_limits",
]
