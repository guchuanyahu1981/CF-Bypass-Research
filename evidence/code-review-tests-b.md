# 独立代码复审

- generated_at: 2026-11-12T16:44:21
- duration_sec: 46.0
- diff_bytes: 23547
- untracked_included: tests/test_captcha_pure.py
- exit_code: 0

---

P0: none

P1 | tests/test_captcha_pure.py:128 | `monkeypatch.setattr(arkose_predict.os, "urandom", lambda n: salt[:n])` replaces urandom with an 8-byte source for all requests | if `cryptojs_encrypt` draws 16 bytes for the IV (as the test itself asserts `len(iv) == 16`), the stub yields only 8 bytes, so the test fails or — worse, if implementation draws IV first — silently encrypts with a truncated IV, making the roundtrip assertion pass by accident while pinning broken behavior | stub per-length: `lambda n: (salt * 4)[:n]` or return a fixed 16-byte IV + 8-byte salt via a small callable

P2 | tests/test_record_traffic.py:530 | `test_redirect_info_stops_on_cycle` pins exact `count == 2` and terminal URL for a 2-node cycle | if the cycle guard counts the repeated node differently (or returns the first vs last URL), the test breaks on an implementation-equivalent refactor; the semantic contract ("no infinite loop, count bounded") isn't what's asserted | assert `count <= 2` and that the URL is one of the chain members, or document the exact pinned semantics

P2 | tests/test_prewarm.py:190 | `test_rate_limited_defers_without_penalty` constructs the error code as `type("C", (), {"value": "rate-limited"})()` instead of using the real `ErrorCode` enum | if the prewarmer switches to identity comparison (`e.code is ErrorCode.RATE_LIMITED`) or the enum gains a `code` attribute with different nesting, the fake silently stops matching and the test would exercise the failure path instead of rate-limit handling, asserting wrong stats | import and raise the real `SolveError`/`ErrorCode` used by the solver

P2 | tests/test_captcha_pure.py:170 | `test_onnx_preprocess_letterboxes_to_square` only checks shape/dtype/range, not that letterboxing preserved aspect ratio (pads vs. distort) | a regressions that stretches 80×40 to 64×64 without padding would pass, defeating the test's stated purpose ("letterboxes") | assert padding rows/cols are zero (e.g., check bottom rows are all 0)

P2 | tests/test_multicaptcha.py:531 | `probe_protection` mock now accepts `**kw`, silently tolerating any new keyword the caller passes | a future signature change (e.g., renamed `guard` kwarg) would no longer be caught by this and other probe mocks, hiding contract drift between caller and implementation | keep explicit parameters and fail loudly on unexpected kwargs, or add one test asserting the actual call signature

VERDICT: PASS
