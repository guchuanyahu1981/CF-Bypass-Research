# 独立代码复审

- generated_at: 2026-11-12T14:34:29
- duration_sec: 35.7
- diff_bytes: 7121
- untracked_included: (none)
- exit_code: 0

---

P0: none
P1: none

P2 | site/lighthouserc.json:29 | Desktop Performance is now an `error` gate (minScore 0.9) while the mobile config keeps perf at `warn` explicitly because "runner load moves those two" — CI machines exhibit the same load variance on desktop, so a noisy runner can turn a passing build into a false-failing hard gate with no remediation available | CI runs `lhci autorun` on a shared runner; perf dips to 0.88 once → build fails despite no code regression | Keep perf at `warn` on desktop too (match mobile rationale), or retry with numberOfRuns > 1

P2 | site/README.md:111 | README now asserts `test:content` is 12/12 with an `import httpx` check, but no such script change is included in this diff — the doc claims a gate the diff doesn't show existing | If scripts/check-content (or equivalent) still has 11 checks, docs mislead operators; if it exists elsewhere, the diff is incomplete | Include or verify the script change alongside the doc update

P2 | site/README.md:129-131 | Duplicated statement: "Desktop assertions are `error` (…) so the desktop Lighthouse assertions (…) are now a hard gate rather than a release note" says the same thing twice in one paragraph | Reader confusion, maintenance drift when thresholds change again | Collapse to one sentence

P2 | site/lighthouserc.mobile.json:77 | File ends without a newline (`\ No newline at end of file`) and the diff strips the trailing newline | Some tooling (POSIX text tools, editors with strict configs, future diffs) flags/touches the file unnecessarily | Restore trailing newline

P2 | site/lighthouserc.mobile.json:31-42 | Mobile `screenEmulation`/`throttling` values are hand-copied from Lighthouse defaults (412×823, 1.75, rtt 150, 1638.4 kbps, 4×) rather than omitted; a future default change makes this config silently diverge while still reading as "standard mobile" | Harder to audit; readers assume defaults when it's actually frozen values | Omit these keys to inherit defaults, or comment that they pin the defaults

P2 | site/package.json:24 | `lighthouse:mobile` introduces error-level A11y/BP/SEO gates but is not wired into `build`'s three-gate chain nor any CI step shown — nothing guarantees it runs | Gates silently rot; the "hard gate" claim in README only holds if someone remembers to run it locally | Add it to `build` or CI workflow alongside `lighthouse`

VERDICT: PASS
