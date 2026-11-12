# 独立代码复审

- generated_at: 2026-11-12T13:08:13
- duration_sec: 130.7
- diff_bytes: 15446
- untracked_included: (none)
- exit_code: 0

---

P0: none

P2 | solver/backends.py `_governed_solve` (success/release block) | claim release lives *inside* `if cacheable and isinstance(out, dict):`; if a cacheable solve returns a non-dict (e.g. `None`) without raising, `end_backend_call(True)` runs and the claim is never released | a buggy backend returning `None` leaves the claim held until TTL, so peers block in `wait_for_peer` and re-solve / degrade latency | release the claim in an outer `finally` that fires whenever `claimed`, independent of `out` being a dict.
P2 | solver/backends.py `_governed_solve` empty-artifact check | `not str(out.get(field) or "").strip()` treats empty list/dict artifacts as non-empty (`"[]"`/`"{}"`), so the new "solved:true but no artifact" guard never fires for list-valued results (e.g. cookies) | a backend returns `solved:true` with `cookies: []`; validation passes and the empty value is cached/returned as success | test truthiness of the raw value (`if not out.get(field)`) before/alongside the string check.
P2 | solver/backends.py `solve` Camoufox branch | the new `want`/`RESULT_FIELD` validation is only applied in `_governed_solve`; the Camoufox main path forwards backend output unvalidated | Camoufox/Solver returns mismatched `want` or an empty artifact; it is cached/returned as success — exactly the silent-wrong-value case the diff targets for governed backends | apply the same want/field validation on the Camoufox output (or inside `Solver.solve`).
P2 | solver/backends.py `_governed_solve` rate-check except | `rate_state["charged"]` is set only *after* `check_rate(url)` returns; when it raises, the flag stays false, so every subsequent backend in the fallback chain re-runs `check_rate` for the same host | a rate-limited host causes N claim/get/release churn and N check_rate hits (inflating rejection metrics) before the request fails | set a `rate_state` "checked/limited" marker before/at the raise so the chain short-circuits on the first rate-limit instead of retrying each backend.
P2 | solver/prewarm.py claim try/except | a claim-store error is fed to `_record_failure`, so transient infrastructure errors (redis/lock blip) count as host failures | a momentary claim outage increments failures for every host and can permanently disable renewal of healthy hosts | treat claim exceptions like the rate-limited/busy case (short `next_attempt_at`, no failure count) or distinguish infra vs. per-host failures.
P2 | solver/backends.py rate-limit abort path | `begin_backend_call()` ticket is returned via `abort_backend_call` only if it is `callable`; if a governance impl lacks it the code silently skips the abort and raises | ticket leak → service eventually reports "all busy" with no error surfaced | fall back to `end_backend_call(False, ...)` (or assert) when `abort_backend_call` is missing so begin/end stay paired.

VERDICT: PASS
