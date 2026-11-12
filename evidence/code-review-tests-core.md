# 独立代码复审

- generated_at: 2026-11-12T23:11:56
- duration_sec: 167.1
- diff_bytes: 34330
- untracked_included: tests/test_auth_token_race.py, tests/test_mcp_server.py, tests/test_ratelimit.py
- exit_code: 0

---



Scope note: this diff is test-only; the `tests/test_governance.py` rewrite's new content (+1260 lines) is cut off by the 30000-byte truncation, so that file is reviewed only on what its deletion removes.

P0: none
P1: none

P2 | tests/test_auth_probe_gaps.py:4 | The new docstring still claims coverage of "token 文件不可读降级", but the rewrite deletes `test_token_file_unreadable_degrades_to_generated` and no replacement appears anywhere in this file's (fully visible) diff | A regression in the new lock-based read path (e.g., PermissionError propagating instead of degrading to a generated token) would ship with a green suite while the docs assert the case is covered | Re-add the unreadable-file degradation test here (or wherever it moved) and correct the docstring pointer

P2 | tests/test_auth_probe_gaps.py:125 | `test_read_bounded_stops_at_deadline` does not actually bind deadline enforcement: with `limit=100` and `Slow.read1` returning 1 byte per call, an implementation that ignores the deadline entirely stops at 100 bytes, which satisfies `len(out) <= PROBE_READ_CHUNK * 2` for any chunk size ≥ 50 | Remove the deadline check from `_read_bounded`: the loop then ends at the byte limit instead of the time limit, `100 <= 2*PROBE_READ_CHUNK` still holds for typical constants (≥ 4096), and the test named "stops_at_deadline" passes — unbounded-time reads become untestable | Assert on the read count instead, e.g. `assert Slow.calls["n"] <= 4` (the fake clock only allows 4 in-deadline monotonic calls), and/or `assert len(out) < 100`

P2 | tests/test_auth_probe_gaps.py:148 | `test_probe_reads_httperror_body_and_reports_status` claims to verify the HTTPError body is read, but `assert out["protected"] in (True, False)` accepts any bool and nothing observes that `b"cf-chl blocked"` was consumed | Revert the body-reading fix (skip `_read_bounded` on non-3xx HTTPError): the status is still echoed as 403 and `protected` remains a bool, so the test passes while the body-derived detection signal is silently gone | Spy on `probe._read_bounded` (assert it was called with the HTTPError) or assert a body-derived effect (e.g., provider detection triggered by the injected body)

P2 | tests/test_governance.py:1 | The file is fully rewritten (1068 → 1260 lines) but the replacement content is not visible in the provided diff; the deleted content contains the only visible coverage for the entry SSRF guard running before any backend, `/shutdown` token auth, inflight gate/ticket release, metrics failure + host-cap accounting, and graceful-close behavior | If any of these were dropped in the rewrite, a future regression (e.g., router calling the backend before the SSRF guard — the v4.0 P0, or an unauthenticated `/shutdown`) would no longer fail CI | Verify the new 1260-line file retains an equivalent for each deleted security/governance test before merge; the larger line count suggests they were kept, but that cannot be confirmed from the visible diff

P2 | tests/test_cache.py:222 | `test_filestore_enforce_limit_skips_unstatable_file` only asserts no-crash and `calls["n"] >= 2`; it never asserts that eviction actually completed | `_enforce_limit` regresses to stat-everything-then-delete-nothing (or deletes the wrong files): the test still passes, and FileStore disk/file usage grows unbounded | After `_enforce_limit()`, assert the outcome, e.g. `len(fs._list_files()) <= 1` for the 3-files/max_files=1 setup

P2 | tests/test_auth_probe_gaps.py:52 | `test_token_race_peer_writes_under_same_lock` injects the peer token inside our own `release()` call — under a real exclusive lock no peer can write the token file while we hold the lock, so the test can only pass if `load_or_create_token` unconditionally re-reads the file after unlocking; the "peer writes under the same lock" narrative doesn't match the mechanics | If the implementation lacks (and doesn't need) a post-unlock re-read, the test fails despite correct locking; conversely it pressures the implementation toward an unnecessary extra read on every cold start | Simulate the real interleaving instead: write the peer token under a first `_TokenLock`, release it, then call `load_or_create_token` and assert it sees the peer token on its first read (source="file")

VERDICT: PASS
