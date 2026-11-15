# API-bug mitigations — what to ship

Short ranking for reviewers of the product PR. Condensed from local evaluation of the
candidate flags against a corpus of API-bug scenarios. The full evaluation tree stays
**local-only** (not part of this PR).

**Merge behaviour:** `enable_api_preflight` and `api_introspect_retry` default **ON**.
`enable_api_digest` and `enable_bypass_after_fault_guard` stay **OFF**. `CMBAGENT_LG_API_PREFLIGHT=0`
(or the retry twin) disables without a rebuild.

This is **not** `enable_preflight` (already on `main`, default `True`) — that is a
different gate.

---

## The problem

Generated code often calls library APIs that were **removed** in the installed release.
The code looks plausible, so the loop debugs its own logic instead of the environment.
Today’s production hint only says “suspect a version difference”; it cannot supply the
real name from the live package.

---

## Ranking (ship order)

| Rank | Flag | Verdict | Advantage | Limitation | Evidence |
| ---: | --- | --- | --- | --- | --- |
| 1 | **`enable_api_preflight`** (formerly arm `E`) | **Ship first** | Validates emitted code *before* run; cheapest correct path on unguessable renames; **free when inert**; bouncing beats crashing even when it cannot fully solve | Does not by itself fix silent library bypass after a fault | Beat the prompt-hint baseline on an unguessable rename (baseline failed after several attempts; preflight succeeded in fewer attempts with one bounce); closed the reference-set gap post-fix |
| 2 | **`api_introspect_retry`** (formerly arm `D`) | **Ship second** | After an API-shaped failure, injects live package facts; fully attributable; nearly as cheap as `enable_api_preflight` on decisive cells | Acts only *after* a failure — does not prevent the first crash; classifier was strengthened mid-evaluation | Closed the same reference-set gap as `enable_api_preflight` |
| 3 | **`enable_bypass_after_fault_guard`** | **Ship as separate opt-in** | Detects “API fault → drop library → success with wrong science”; one nudge with full module/class API; recovers correct science on the reference set | Orthogonal to `enable_api_preflight` (not included in it); needs a real prior API fault to arm | Recovered correct science on the reference set, consistently across repeated runs; inert on the control case as expected |
| — | **Prompt-hint baseline** (prod today) | **Keep** | Makes the model suspicious | Cannot invent a name it does not know; often fails the hard cell | Fails the same hard case that `enable_api_preflight` recovers |
| — | **Version-facts prompt** (eval-only) | **Do not ship as-is** | Cheap prevention when the version line implies the rename | Disqualified by a silent-wrong bypass observed in evaluation | — |
| — | **`enable_api_digest`** (formerly arm `G`) | **Do not enable** | Proactive inventory idea | Digest **content** was not persisted → not causally attributable; token tax; rare useful hits | Not causally attributable in the recorded evaluation — leave code OFF |

**Prevention vs recovery:** `enable_api_preflight` blocks bad calls before execution;
`api_introspect_retry` repairs after failure; the bypass guard refuses a *false* success
after a real API fault. Prefer preflight, then introspect-retry, then the bypass guard —
each was measured separately (do not assume preflight implies the bypass guard).

---

## Caveats (read before trusting the ranking)

- **Corpus under-fires** — only ~25% of faulted evaluation cells engaged the removed API.
- **`enable_api_digest`** is not causally attributable in the recorded evaluation (injection size only).
- **Small sample sizes** on many decisive cells; the strongest story for `enable_api_preflight` is *consistent cost properties across different cases*, still few observations.
- **Cost figures** in early runs can be unreliable (trace-collection race); prefer re-fetched usage / per-cell outcomes over raw token means.

**Post-evaluation updates (supersede older “ceiling reached / all flags fail” claims):**

| Update | Notes |
| --- | --- |
| `api_introspect_retry` and `enable_api_preflight` both close the reference-set gap (ModuleNotFoundError + class API) | single-cell pilot |
| `enable_bypass_after_fault_guard` recovers the reference-set case with correct science | robust across repeated runs |
| `enable_api_preflight` outperforms the prompt-hint baseline on an unguessable rename | baseline fails, preflight succeeds with one bounce |

Harder-corpus pilots after that (multiple-sequence-alignment / RMSE cases) often **did not
fire** the absent API (the model hand-rolled instead). They do not weaken the evidence
above; they show most new cases still fail the earlier pilot gates.

---

## How to enable (when ready)

| Flag | `PlanContext` | Env |
| --- | --- | --- |
| `enable_api_preflight` | `enable_api_preflight=True` | `CMBAGENT_LG_API_PREFLIGHT=1` |
| `api_introspect_retry` | `api_introspect_retry=True` | `CMBAGENT_LG_API_INTROSPECT_RETRY=1` |
| `enable_bypass_after_fault_guard` | `enable_bypass_after_fault_guard=True` | `CMBAGENT_LG_BYPASS_AFTER_FAULT_GUARD=1` |
| `enable_api_digest` | `enable_api_digest=True` | `CMBAGENT_LG_API_DIGEST=1` — **not recommended** |

Local-only execution seams: several checks read the *installed* interpreter; remote executor paths intentionally stay inert (see comments on `PlanContext`).

---

## Fast review / test (no matrix)

```bash
python -m pytest \
  tests/test_api_preflight.py \
  tests/test_api_introspect_retry.py \
  tests/test_api_digest.py \
  tests/test_bypass_after_fault_guard.py \
  -q
```

Expect the preflight false-positive guard green (clean snippets, 0 FP). The full
evaluation matrix requires the local evaluation tree and a pinned virtual environment —
out of scope for this PR.
