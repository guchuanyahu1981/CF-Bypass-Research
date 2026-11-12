# 独立代码复审

- generated_at: 2026-11-12T14:20:29
- duration_sec: 393.0
- diff_bytes: 81070
- untracked_included: (none)
- exit_code: 0

---

P1 | site/data/benchmarks.ts:103 | `tests.python` now claims 1200 tests while site.ts changelog (zh/en) and the call-site facts say 1127 | The benchmarks page and the changelog page show different test counts for the same release; the content gate that syncs site copy to docs/facts.json either passes with contradictory on-site numbers or goes red on rerun | Set the benchmark value to the number in docs/facts.json (1127 per this changeset's own changelog copy) or update both surfaces and the facts file to the same figure

P2 | site/tests/content/content.test.mjs:176 | The httpx gate reads `components/pages/Home.tsx` only via `process.cwd()` and hard-codes the relative path | If the CWD isn't `site/` the test fails with a confusing "missing Python example", and any Python snippet added to another page (e.g. the docs page or how-it-works) escapes the requests-ban entirely | Resolve the path relative to the test file (`new URL('../../components/...', import.meta.url)`) and scan the components directory instead of one file

P2 | site/lighthouserc.mobile.json:61 | Mobile CLS assertion loosened 0.05 → 0.1 while the desktop E2E test still enforces < 0.05, and README documents the mobile threshold — but the same file also dropped Performance minScore 0.9 → 0.8 and TBT stays warn, all justified as "runner load", without recording mobile CLS evidence in the dated table | A real mobile CLS regression between 0.05 and 0.1 now passes CI silently while the desktop budget misleads operators into thinking CLS is capped at 0.05 site-wide | Either add the mobile CLS measurement to the README's dated numbers or keep the 0.05 mobile assertion given the diff itself reports measured mobile CLS = 0

P2 | site/content/site.ts:1 | The whole 661-line file is rewritten (every line deleted and re-added) for ~10 changed strings, and the result ends without a trailing newline | Full-file churn destroys blame/history for content edits and will produce noisy diffs on every future copy tweak; missing EOF newline will fight formatters | Restore unchanged lines (likely a line-ending change) and add the trailing newline

P0: none

VERDICT: FAIL
