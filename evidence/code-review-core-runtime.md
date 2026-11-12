# 独立代码复审

- generated_at: 2026-11-12T22:51:34
- duration_sec: 163.2
- diff_bytes: 29370
- untracked_included: (none)
- exit_code: 0

---



P0: none

P1: none

P2 | solver/backends.py:548 | `_governed_solve` releases the claim only inside `if cacheable and isinstance(out, dict)`; a non-dict backend return skips release after `end_backend_call(True, ...)` already recorded success | A backend bug returns `None`/`str` for a cacheable want with an active claim → the claim is held until TTL, so peer workers wait/defer for nothing, and the broken request is counted as a backend success | Validate `isinstance(out, dict)` immediately after `backend.solve` (raise `UNSOLVED` so it flows through the existing failure/release path), or move the claim release into a `finally` covering the tail

P2 | solver/backends.py:273 | FlareSolverr rejects an unsupported `want` with `NOT_CONFIGURED` — a server-side/configuration code — and the path is counted as a backend failure via `end_backend_call(False)` | A `want=token` request that falls through to FlareSolverr (Camoufox skipped or failed) gets a 5xx-class code that clients may treat as retryable, and failure metrics tick up for what is a request-capability mismatch | Raise `INVALID_REQUEST` (or a dedicated unsupported-want code) so it maps to 4xx; keep the actionable hint text

P2 | solver/prewarm.py:141 | Claim-store exceptions are recorded via `_record_failure` and count toward `max_failures` | A transient governance-store outage makes `claim_solve` raise for every host; hosts accumulate failures and get dropped from renewal even though they are healthy, opening cold-solve windows | Defer without counting a failure when the claim itself raises (like the existing `rate-limited`/`busy` path), reserving `_record_failure` for solve failures

P2 | solver/errors.py:12 | The contract changed — `RATE_LIMITED` went from reserved to an active 429 and bootstrap gains `--rate-per-host`/`--rate-burst` — but no docs/API.md update appears in this changeset, although the errors.py comment itself requires docs to be synced in lockstep | Operators/clients reading the old "reserved, never produced" contract won't implement 429 backoff and won't know per-host limiting is on by default (1/30s, multiplied per worker process) | Update docs/API.md (and OpenAPI if present) for the 429 `rate-limited` + `stage=admit` semantics and the two new CLI flags, including the per-process multiplicative note

VERDICT: PASS
