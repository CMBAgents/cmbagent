"""Remote code-execution backend for the self_debug executor (opt-in).

By default the `executor` node runs generated code as a local subprocess. When
``CMBAGENT_LG_REMOTE_EXECUTOR=host:port`` is set, it instead ships the code to a **guest
agent** on that address, which runs it in an isolated sandbox and returns stdout + a data
manifest — the engine keeps the code/logs/manifest, the data lives wherever the guest puts
it (e.g. object storage). This is the seam a remote execution service can use to keep the
engine source off the execution machine.

Wire format: length-prefixed JSON over TCP (a production build swaps this for gRPC / WS+mTLS
without touching the executor). The guest agent must speak the same `execute` → `result`
protocol.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time


def _send(sock, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recvn(sock, n: int):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv(sock):
    hdr = _recvn(sock, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    body = _recvn(sock, n)
    return json.loads(body.decode("utf-8")) if body else None


class RemoteExecutor:
    """Engine-side client. `run_code` ships the step (plus prior steps, so cross-step
    imports resolve on a disposable sandbox) and returns the guest's result dict:
    {stdout, stderr, returncode, timed_out, elapsed_s, manifest, ...}."""

    def __init__(self, host: str, port: int, secret: str | None = None):
        self.host, self.port = host, int(port)
        self._secret = secret

    @classmethod
    def from_env(cls, spec: str) -> "RemoteExecutor":
        host, _, port = spec.partition(":")
        return cls(host or "127.0.0.1", int(port or "8790"))

    def _authed(self, msg: dict) -> dict:
        """Stamp the guest's shared secret and the current store token on.

        A guest with no GUEST_SECRET ignores the auth field, so this is safe against every backend;
        one that has it rejects an unstamped message outright.

        Both are read PER CALL, never cached at construction. The secret, because the broker exports
        GUEST_SECRET around the run and an executor built before that would cache "" and be refused
        by every request. The token, because the guest otherwise keeps whatever it was given at
        prepare time — good for ~3600s — and a step that runs longer than that leaves the guest
        unable to reach the store: `RefreshError: credentials do not contain the necessary fields
        need to refresh the access token`, observed on a remote run after a ~45 min step.
        The broker refreshes the value in this process's environment; reading it here is what
        carries the refresh across the seam. Mirrors the guest agent's own client."""
        out = dict(msg)
        secret = self._secret if self._secret is not None else os.environ.get("GUEST_SECRET", "")
        if secret:
            out["auth"] = secret
        token = os.environ.get("GCS_TOKEN", "")
        if token:
            out["gcs_token"] = token
        return out

    def run_code(self, session: str, step_number: int, code_files: dict, timeout: int = 120) -> dict:
        """Ship a step to the guest and return its result.

        Retries ONCE on a connection-level failure. Measured on the deployed premium tier: a
        single transport blip on this socket aborted an entire multi-stage run at ~9 minutes in
        — the run was healthy, several stages had completed, and everything was lost to one
        `ConnectionResetError`. Long modes are the most exposed simply because they hold the
        socket longest.

        Re-sending is safe in the sense that matters here: re-running a step is exactly what the
        self_debug loop already does on a failed attempt, so the guest is required to tolerate
        it. The residual risk is double execution when the reset happens AFTER the guest ran the
        code but before its reply arrived — acceptable against losing the whole run, and the
        alternative (fail immediately) loses it every time.

        A TIMEOUT is deliberately NOT retried: it means the step consumed its full budget, so a
        retry would silently double the wall clock and could exceed the caller's deadline. Only
        connection errors — reset, refused, broken pipe, aborted — are retried.
        """
        msg = self._authed({
            "type": "execute", "session": session, "step_number": step_number,
            "code_files": code_files, "timeout": timeout,
        })
        last_err: Exception | None = None
        for attempt in (0, 1):
            try:
                s = self._connect(timeout + 30)
                with s:
                    _send(s, msg)
                    reply = _recv(s)
                if reply is None or reply.get("type") == "error":
                    raise RuntimeError(f"remote executor error: {reply}")
                return reply
            except socket.timeout:
                raise                       # budget already spent — never retry
            except ConnectionError as e:    # reset / refused / aborted / broken pipe
                last_err = e
                if attempt == 0:
                    print(f"[remote_exec] step {step_number}: {e!r} — reconnecting once",
                          file=sys.stderr, flush=True)
                    time.sleep(2)
                    continue
                raise
        raise last_err  # unreachable; keeps type checkers honest

    def _request(self, msg: dict, timeout: int = 60) -> dict:
        """One request/reply round-trip for the non-execute ops (list_files/read_file/install) the
        escalation agent's scoped tools use.

        Retries on connection errors for the same reason as `run_code` — and with LESS risk, since
        these are reads (`list_files`/`read_file`) or an idempotent package install. Measured: adding
        the retry to `run_code` alone was not enough. A full_paper run recovered from a reset during
        step 2 (the retry fired and the step then succeeded) and still died minutes later, because a
        step exited non-zero and the escalation agent's tools came through THIS path unprotected.
        Half a resilience fix reads exactly like no fix from the user's side: the run is still lost.
        """
        msg = self._authed(msg)
        last_err: Exception | None = None
        for attempt in (0, 1):
            try:
                s = self._connect(timeout + 10)
                with s:
                    _send(s, msg)
                    reply = _recv(s)
                if reply is None:
                    raise RuntimeError("remote executor: no reply")
                return reply
            except socket.timeout:
                raise                       # budget already spent — never retry
            except ConnectionError as e:
                last_err = e
                if attempt == 0:
                    print(f"[remote_exec] {msg.get('type')}: {e!r} — reconnecting once",
                          file=sys.stderr, flush=True)
                    time.sleep(2)
                    continue
                raise
        raise last_err  # unreachable

    def list_files(self, session: str) -> dict:
        return self._request({"type": "list_files", "session": session})

    def read_file(self, session: str, path: str) -> dict:
        return self._request({"type": "read_file", "session": session, "path": path})

    def introspect(self, session: str, code: str, argv: list | None = None,
                   timeout: int = 30) -> dict:
        """Run a self-contained introspection snippet in the session's SANDBOX and return
        {type, stdout, stderr, returncode} — so `api_introspect` reports the API of the
        environment the generated code actually runs in, not the engine venv's."""
        return self._request({"type": "introspect", "session": session, "code": code,
                              "argv": argv or [], "timeout": timeout}, timeout=timeout)

    def install(self, package: str) -> dict:
        return self._request({"type": "install", "package": package}, timeout=300)

    def _connect(self, sock_timeout: int):
        deadline = time.time() + 15
        while True:
            try:
                return socket.create_connection((self.host, self.port), timeout=sock_timeout)
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.25)
