"""Schema lines of data files in the workspace manifest (deep_research/file_schema.py).

    pytest tests/test_manifest_schemas.py
"""
import json

import numpy as np
import pytest

from cmbagent_lg.deep_research.file_schema import schema_line
from cmbagent_lg.deep_research.nodes import _build_workspace_manifest


@pytest.fixture
def work(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "halos.csv").write_text("halo_id,m200c,fgas\n1,1e14,0.1\n2,2e14,0.12\n")
    (d / "bins.tsv").write_text("lo\thi\n1\t2\n")
    (d / "fit.json").write_text(json.dumps({"slope": 1.2, "bins": [[1, 2], [3, 4]], "meta": {"run": "L1_m9", "z": 0}}))
    (d / "list.json").write_text(json.dumps([{"a": 1, "b": 2}] * 3))
    (d / "broken.json").write_text("{not json")
    np.save(d / "grid.npy", np.zeros((4, 3), dtype=np.float32))
    np.savez(d / "arrs.npz", k=np.arange(5), pk=np.ones((5, 2)))
    (d / "notes.bin").write_bytes(b"\x00\x01")
    (tmp_path / "codebase").mkdir()
    (tmp_path / "codebase" / "step_1.log").write_text("ok")
    return tmp_path


def test_schema_lines(work):
    d = work / "data"
    assert schema_line(d / "halos.csv") == "columns: halo_id, m200c, fgas; 2 rows"
    assert schema_line(d / "bins.tsv") == "columns: lo, hi; 1 rows"
    j = schema_line(d / "fit.json")
    assert j.startswith("keys: slope (float), bins (list[2] of list[2] of int), meta (object{run, z})")
    assert schema_line(d / "list.json") == "list[3] of object{a, b}"
    assert schema_line(d / "grid.npy") == "array shape (4, 3), dtype float32"
    assert schema_line(d / "arrs.npz") == "arrays: k (5,) int64, pk (5, 2) float64"
    assert schema_line(d / "broken.json") == "" and schema_line(d / "notes.bin") == ""


def test_parquet_and_hdf5(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"x": [1, 2, 3], "y": [0.1, 0.2, 0.3]}), tmp_path / "t.parquet")
    assert schema_line(tmp_path / "t.parquet") == "columns: x, y; 3 rows"
    h5py = pytest.importorskip("h5py")
    with h5py.File(tmp_path / "f.hdf5", "w") as f:
        f["SO/200_crit/TotalMass"] = np.zeros(7, dtype=np.float32)
    assert schema_line(tmp_path / "f.hdf5") == "datasets: SO/200_crit/TotalMass (7,) float32"


def test_manifest_shows_schemas_under_data_files_only(work):
    m = _build_workspace_manifest(str(work))
    lines = m.splitlines()
    i = lines.index("- data/halos.csv")
    assert lines[i + 1] == "    columns: halo_id, m200c, fgas; 2 rows"
    assert "- codebase/step_1.log" in lines           # the invariant: every step log is listed
    assert lines[lines.index("- codebase/step_1.log") + 1] == ""


def test_long_lines_are_capped(tmp_path):
    (tmp_path / "wide.csv").write_text(",".join("column_%d" % i for i in range(300)) + "\n")
    line = schema_line(tmp_path / "wide.csv")
    assert len(line) <= 400 and line.endswith("…(more)")
