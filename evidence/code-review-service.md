# 独立代码复审

- generated_at: 2026-11-12T22:53:43
- duration_sec: 121.6
- diff_bytes: 29153
- untracked_included: (none)
- exit_code: 0

---



**Review of solver/service.py changes (wedge detection, want-aware cache keys, per-host rate limiting, fail-closed auth):**

The core logic is sound: `want` is consistently normalized and threaded through keys/claims/lookup/remember; the refund path only fires when the Solver itself charged and only for admission-level rejections; auth comparisons are constant-time on bytes and fail closed; `guard_hop` is now strictly stronger (scheme check runs even with `--allow-private-hosts`); cache reads are exception-guarded; claim release happens on all failure paths including store-write failure.

P2 | solver/service.py:1673 | Refund of the rate token for admission rejections matches Chinese substrings of `str(e)` ("并发求解数已达上限" / "求解队列已满" / "浏览器启动失败") instead of a structured flag | If any of these messages is reworded, refunds silently stop; under sustained queue-full/BUSY conditions the per-host bucket drains and the limiter starts 429-ing solves that never ran | Set an explicit attribute when raising (e.g. `err.admission_rejected = True` in `_submit`/launch-failure paths) and test that flag instead of message text

P2 | solver/service.py:379 | `_cache_usable` validates dict/expiry/want but not the credential field itself, while `remember`/`_solve_one` now guarantee non-empty fields only at write time | A hand-seeded or half-written row like `{"want":"clearance","expires_at":<future>}` with an empty `cf_clearance` passes `_cache_usable` and is served by `cache_lookup`/`_wait_for_peer` as `solved:true` with a useless credential | After `_cache_usable` passes, also require `str(cached.get(RESULT_FIELD[Want.parse(want)]) or "").strip()` before treating the entry as a hit

P2 | solver/service.py:1225 | `cache_key` now hashes `host|egress|want`, so every entry persisted under the old `host|egress` key becomes unreachable the moment this deploys | Post-deploy the store appears cold: hit rate drops to zero until new-format entries are written, and orphaned old-format rows linger on disk until TTL cleanup — operators may misread this as a cache regression | Either accept and document the one-time cold-cache rollover in release notes, or add a temporary legacy-key fallback lookup (read-only) during the rollout window

P0: none
P1: none
P2: solver/service.py:1673 (fragile refund matching), solver/service.py:379 (`_cache_usable` skips credential-field check), solver/service.py:1225 (key-format change orphans persisted entries on deploy)

VERDICT: PASS
