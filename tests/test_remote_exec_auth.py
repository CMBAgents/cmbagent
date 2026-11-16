"""Gate test for the guest handshake on the ENGINE side of the seam.

A remote executor pool may start each guest agent with a per-claim `GUEST_SECRET` and refuse any
message whose `auth` does not match. The pool's own client was taught to send it; THIS client —
the one that actually ships generated code — was not, so every execute came back
`{'type': 'error', 'message': 'unauthorized'}` after a run had already provisioned, tunnelled and
written its code. A guest with no secret configured ignores the field, so stamping it is safe
against every other backend (GCE, TPU, a local guest).
"""

import json
import os
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

ENV = "GUEST_SECRET"
TOKEN_ENV = "GCS_TOKEN"


class FakeGuest:
    """Speaks the length-prefixed JSON protocol, records what it was sent, and enforces a secret
    the way skeleton/guest_agent.py does — unset means no check."""

    def __init__(self, secret=""):
        self.secret, self.seen = secret, []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                hdr = conn.recv(4)
                if len(hdr) < 4:
                    continue
                (n,) = struct.unpack(">I", hdr)
                body = b""
                while len(body) < n:
                    chunk = conn.recv(n - len(body))
                    if not chunk:
                        break
                    body += chunk
                msg = json.loads(body.decode())
                self.seen.append(msg)
                if self.secret and msg.get("auth") != self.secret:
                    reply = {"type": "error", "message": "unauthorized"}
                else:
                    reply = {"type": "result", "stdout": "", "returncode": 0}
                payload = json.dumps(reply).encode()
                conn.sendall(struct.pack(">I", len(payload)) + payload)

    def close(self):
        self._stop = True
        self.sock.close()


def _run(fn, secret_env):
    """Run fn() with GUEST_SECRET set to secret_env (None → unset), restoring the environment."""
    prior = os.environ.get(ENV)
    if secret_env is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = secret_env
    try:
        return fn()
    finally:
        os.environ.pop(ENV, None) if prior is None else os.environ.update({ENV: prior})


def _with_token(fn, token):
    """Run fn() with GCS_TOKEN set (None → unset), restoring the environment."""
    prior = os.environ.get(TOKEN_ENV)
    if token is None:
        os.environ.pop(TOKEN_ENV, None)
    else:
        os.environ[TOKEN_ENV] = token
    try:
        return fn()
    finally:
        os.environ.pop(TOKEN_ENV, None) if prior is None else os.environ.update({TOKEN_ENV: prior})


def test_every_message_carries_the_current_store_token():
    """The guest keeps whatever token it was handed at PREPARE time, good for ~3600s. This client
    never sent one, so a step running longer than that left the guest unable to reach the store:
    a remote run died on `RefreshError: credentials do not contain the necessary fields
    need to refresh the access token` after a ~45 min Monte Carlo step. Both send paths must carry
    it — run_code and the _request tools."""
    g = FakeGuest(secret="")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)
        _with_token(lambda: _run(lambda: ex.run_code("sess", 1, {"step_1.py": ""}, timeout=5), None),
                    "ya29.fresh")
        _with_token(lambda: _run(lambda: ex.list_files("sess"), None), "ya29.fresh")
        _with_token(lambda: _run(lambda: ex.introspect("sess", "print(1)", ["{}"]), None), "ya29.fresh")
        assert [m["type"] for m in g.seen] == ["execute", "list_files", "introspect"], g.seen
        assert all(m.get("gcs_token") == "ya29.fresh" for m in g.seen), g.seen
    finally:
        g.close()


def test_the_token_is_read_per_call_so_a_refresh_crosses_the_seam():
    """The whole point: the broker rewrites GCS_TOKEN in this process's environment as the old one
    nears expiry. Caching it on the instance would pin the guest to the token the run started with,
    which is the bug. A second call after the env changes must carry the NEW value."""
    g = FakeGuest(secret="")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)           # built before either token exists
        _with_token(lambda: _run(lambda: ex.list_files("sess"), None), "ya29.first")
        _with_token(lambda: _run(lambda: ex.list_files("sess"), None), "ya29.refreshed")
        assert g.seen[0]["gcs_token"] == "ya29.first", g.seen[0]
        assert g.seen[1]["gcs_token"] == "ya29.refreshed", g.seen[1]
    finally:
        g.close()


def test_no_token_in_the_environment_sends_no_field():
    """A local guest and the skeleton run with no GCS_TOKEN at all. An empty string would be a
    behaviour change — `_refresh_store_token` treats any truthy value as a token to install — so
    the field must be absent, not empty."""
    g = FakeGuest(secret="")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)
        _with_token(lambda: _run(lambda: ex.run_code("sess", 1, {"step_1.py": ""}, timeout=5), None),
                    None)
        _with_token(lambda: _run(lambda: ex.list_files("sess"), None), "")
        assert "gcs_token" not in g.seen[0], g.seen[0]
        assert "gcs_token" not in g.seen[1], g.seen[1]
    finally:
        g.close()


def test_the_secret_and_the_token_travel_together():
    """Both are stamped in one place (`_authed`), so neither may displace the other."""
    g = FakeGuest(secret="s3kret")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)
        _with_token(lambda: _run(lambda: ex.list_files("sess"), "s3kret"), "ya29.both")
        assert g.seen[0]["auth"] == "s3kret", g.seen[0]
        assert g.seen[0]["gcs_token"] == "ya29.both", g.seen[0]
    finally:
        g.close()


def test_execute_carries_the_secret():
    g = FakeGuest(secret="s3kret")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)
        reply = _run(lambda: ex.run_code("sess", 1, {"step_1.py": "print(1)"}, timeout=5), "s3kret")
        assert reply.get("type") == "result", reply
        assert g.seen[0]["auth"] == "s3kret", g.seen[0]
    finally:
        g.close()


def test_the_secret_is_read_per_call_not_at_construction():
    """The regression that caused the outage was an ORDERING one: broker.connected() exports
    GUEST_SECRET around the run, so an executor built before that must still pick it up."""
    g = FakeGuest(secret="late")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)          # built BEFORE the env exists
        reply = _run(lambda: ex.run_code("sess", 1, {"step_1.py": ""}, timeout=5), "late")
        assert reply.get("type") == "result", reply
    finally:
        g.close()


def test_scoped_tools_carry_the_secret_too():
    """list_files / read_file / install go through _request, a different path from run_code. Half a
    handshake reads exactly like none: the escalation agent's tools would 'succeed' with an error
    dict, since _request does not raise on {'type': 'error'}."""
    g = FakeGuest(secret="s3kret")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)
        _run(lambda: ex.list_files("sess"), "s3kret")
        _run(lambda: ex.read_file("sess", "a.txt"), "s3kret")
        _run(lambda: ex.install("numpy"), "s3kret")
        _run(lambda: ex.introspect("sess", "print(1)", ["{}"]), "s3kret")
        assert [m["type"] for m in g.seen] == [
            "list_files", "read_file", "install", "introspect"], g.seen
        assert all(m.get("auth") == "s3kret" for m in g.seen), g.seen
    finally:
        g.close()


def test_no_secret_configured_sends_no_auth_field():
    """GCE and TPU guests run without GUEST_SECRET. Sending an empty auth would be a behaviour
    change for them, so the field must be absent, not empty."""
    g = FakeGuest(secret="")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)
        reply = _run(lambda: ex.run_code("sess", 1, {"step_1.py": ""}, timeout=5), None)
        assert reply.get("type") == "result", reply
        assert "auth" not in g.seen[0], g.seen[0]
    finally:
        g.close()


def test_a_wrong_secret_is_still_refused():
    """The guard must fail closed — proving the fake guest actually checks, so the tests above are
    not passing vacuously."""
    g = FakeGuest(secret="right")
    try:
        ex = RemoteExecutor("127.0.0.1", g.port)
        try:
            _run(lambda: ex.run_code("sess", 1, {"step_1.py": ""}, timeout=5), "wrong")
        except RuntimeError as e:
            assert "unauthorized" in str(e), e
        else:
            assert False, "a wrong secret was accepted"
    finally:
        g.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nALL REMOTE-EXEC AUTH TESTS PASSED")
