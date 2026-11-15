"""The escalation agent asks the SANDBOX what an API is, not this machine.

Under `CMBAGENT_LG_REMOTE_EXECUTOR` the agent's shell is taken away precisely because this venv is
not the package set the code ran against — so the one tool that answers "does this symbol exist"
has to go through the same guest seam as the pre-flight and the retry injection. If it ever answers
from the engine venv it is confidently wrong, which is the failure R-17 exists to remove.

Run:  python tests/test_escalation_vm_api.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.self_debug import api_introspect as _ai
from cmbagent_lg.self_debug.escalation import _vm_api_report
from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"

_TMP = Path(tempfile.mkdtemp(prefix="esc-api-"))
GUEST_DIR = _TMP / "guest"
WORK_DIR = _TMP / "runs" / "esc-session"
for d in (GUEST_DIR, WORK_DIR):
    d.mkdir(parents=True, exist_ok=True)
# Installed in the sandbox and NOWHERE here — the shape of every scientific package on the backend
# image, which carries none of them.
(GUEST_DIR / "sandboxonly.py").write_text("VERSION = 1\n\n\ndef current():\n    pass\n")

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS {label}")
    else:
        FAILS.append(f"{label}{' — ' + detail if detail else ''}")
        print(f"  FAIL {label}{' — ' + detail if detail else ''}")


class _Guest:
    """`RemoteExecutor._request`, served by running the snippet in a subprocess that can see the
    GUEST copy of the module and nothing of this process."""

    def __init__(self, raises=None):
        self.raises, self.seen = raises, []

    def __enter__(self):
        self.saved = RemoteExecutor._request
        guest = self

        def _request(self_, msg, timeout=60):
            guest.seen.append(msg)
            if guest.raises is not None:
                raise guest.raises
            proc = subprocess.run(
                [sys.executable, "-c", msg["code"], *msg["argv"]],
                capture_output=True, text=True, cwd=str(_TMP), timeout=60,
                env={**os.environ, "PYTHONPATH": str(GUEST_DIR)},
            )
            return {"type": "introspect_result", "stdout": proc.stdout, "stderr": proc.stderr,
                    "returncode": proc.returncode}

        RemoteExecutor._request = _request
        return self

    def __exit__(self, *exc):
        RemoteExecutor._request = self.saved


class _NoLocal:
    NAMES = ("introspect_local", "introspect_class_local", "introspect_callable_class_local",
             "introspect_call_signature_local", "introspect_artist_kwarg_local")

    def __enter__(self):
        self.saved = {n: getattr(_ai, n) for n in self.NAMES}

        def _boom(*a, **k):
            raise AssertionError("the escalation tool introspected the ENGINE venv")

        for n in self.NAMES:
            setattr(_ai, n, _boom)
        return self

    def __exit__(self, *exc):
        for n, fn in self.saved.items():
            setattr(_ai, n, fn)


os.environ[REMOTE_ENV] = "10.0.0.5:8790"

print("=== the report comes from the sandbox ===")
with _Guest() as g, _NoLocal():
    report = _vm_api_report(WORK_DIR, "sandboxonly", "current")
check("a module absent from this venv is still answered", "EXISTS: sandboxonly.current" in report,
      report[:200])
check("it went out as an introspect op", bool(g.seen) and g.seen[0]["type"] == "introspect",
      str(g.seen[:1])[:200])
check("addressed to the run's own session", g.seen[0].get("session") == "esc-session",
      str(g.seen[0].get("session")))
with _Guest() as g, _NoLocal():
    absent = _vm_api_report(WORK_DIR, "sandboxonly", "gone")
check("an absent symbol is reported as ABSENT", "ABSENT: sandboxonly.gone" in absent, absent[:200])
check("...with the closest real names", "current" in absent, absent[:300])

print("=== a guest that cannot answer is not a crash ===")
with _Guest(raises=ConnectionRefusedError("guest down")) as g, _NoLocal():
    check("an unreachable guest yields the empty report", _vm_api_report(WORK_DIR, "sandboxonly") == "")

print("=== the tool is actually wired into the remote agent ===")
src = (Path(__file__).resolve().parent.parent / "cmbagent_lg/self_debug/escalation.py").read_text()
check("registered in vm_tools", "_inspect_vm_api]" in src)
check("allowed for the agent", '"mcp__vmexec__inspect_vm_api"' in src)
check("named in the remote prompt", "call `inspect_vm_api`" in src)
check("the local (non-remote) path is untouched",
      '["WebSearch", "WebFetch", "Read", "Edit", "Bash"]' in src)

print()
if FAILS:
    print("=== FAILURES ===")
    for f in FAILS:
        print(" ", f)
    sys.exit(1)
print("=== OK — escalation asks the sandbox, never this venv ===")
