"""One-line schemas of data files, for the workspace manifest.

A later step loads what an earlier step wrote. Given only a path, the engineer guesses the column names and JSON keys,
and a wrong guess is a KeyError that costs a debugging round (three in a row on one step of a Gemini test run). So the
manifest shows, under each data file, what is in it: the columns and row count of a table, the keys and value types of
a JSON object, the arrays of an .npz, the datasets of an HDF5 file. Reads only headers and metadata where the format
allows, caps every line, and never raises: a file it cannot read gets no schema line.
"""
from __future__ import annotations

import json
from pathlib import Path

LINE_CHARS = 400          # one file's schema line
FULL_READ_BYTES = 20_000_000   # count a table's rows, or parse a JSON file, only below this size
MAX_ITEMS = 40


def _cap(s: str) -> str:
    return s if len(s) <= LINE_CHARS else s[: LINE_CHARS - 12] + " …(more)"


def _names(items, n=MAX_ITEMS) -> str:
    items = list(items)
    more = f", … {len(items) - n} more" if len(items) > n else ""
    return ", ".join(str(x) for x in items[:n]) + more


def _table(p: Path, sep: str) -> str:
    import csv
    with p.open(newline="", errors="replace") as f:
        header = next(csv.reader(f, delimiter=sep), None)
    if not header:
        return "empty"
    rows = ""
    if p.stat().st_size <= FULL_READ_BYTES:
        with p.open("rb") as f:
            rows = f"; {max(0, sum(1 for _ in f) - 1)} rows"
    return f"columns: {_names(header)}{rows}"


def _type(v) -> str:
    if isinstance(v, dict):
        return "object{" + _names(v.keys(), 8) + "}"
    if isinstance(v, list):
        inner = _type(v[0]) if v else ""
        return f"list[{len(v)}]" + (f" of {inner}" if inner else "")
    return type(v).__name__


def _json(p: Path) -> str:
    if p.stat().st_size > FULL_READ_BYTES:
        return "large JSON (not parsed)"
    d = json.loads(p.read_text(errors="replace"))
    if isinstance(d, dict):
        return "keys: " + _names(f"{k} ({_type(v)})" for k, v in d.items())
    return _type(d)


def _npy(p: Path) -> str:
    import numpy as np
    a = np.load(p, mmap_mode="r", allow_pickle=False)
    return f"array shape {tuple(a.shape)}, dtype {a.dtype}"


def _npz(p: Path) -> str:
    import numpy as np
    with np.load(p, allow_pickle=False) as z:
        return "arrays: " + _names(f"{k} {tuple(z[k].shape)} {z[k].dtype}" for k in z.files)


def _parquet(p: Path) -> str:
    import pyarrow.parquet as pq
    f = pq.ParquetFile(p)
    return f"columns: {_names(f.schema_arrow.names)}; {f.metadata.num_rows} rows"


def _hdf5(p: Path) -> str:
    import h5py
    out = []
    with h5py.File(p, "r") as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset) and len(out) <= MAX_ITEMS:
                out.append(f"{name} {tuple(obj.shape)} {obj.dtype}")
        f.visititems(visit)
    return "datasets: " + _names(out)


READERS = {".csv": lambda p: _table(p, ","), ".tsv": lambda p: _table(p, "\t"), ".json": _json, ".npy": _npy,
           ".npz": _npz, ".parquet": _parquet, ".h5": _hdf5, ".hdf5": _hdf5}


def schema_line(p: Path) -> str:
    """'columns: a, b; 120 rows' and the like, or '' when the format is not known or the file cannot be read."""
    reader = READERS.get(p.suffix.lower())
    if reader is None:
        return ""
    try:
        return _cap(reader(p))
    except Exception:  # noqa: BLE001  (a missing library, a corrupt or half-written file)
        return ""
