# 独立代码复审

- generated_at: 2026-11-12T14:37:48
- duration_sec: 21.2
- diff_bytes: 3840
- untracked_included: (none)
- exit_code: 0

---

P2 | site/tests/content/content.test.mjs:181 | Test asserts on Home.tsx contents but claims to check "requirements.txt only ships httpx" without ever reading requirements.txt | If httpx is removed from requirements.txt (or another file starts using `requests`), the test still passes and the doc/code drift it guards against reappears | Also read requirements.txt and assert `httpx` presence / `requests` absence, or scan more broadly than Home.tsx

P2 | site/tests/e2e/site.spec.ts:186 | Language-switch test hardcodes `/how-it-works/` and assumes exactly one `a.icon-button[hrefLang]` link via `.first()` | If the header contains other hreflang links (e.g. alternates for SEO) or the route set changes, `.first()` may resolve to a different element and the test fails or silently validates the wrong link | Scope the locator to the header/nav container, or filter by the current pathname

P2 | site/tests/e2e/site.spec.ts:203 | `await expect(details).not.toHaveAttribute('open', '')` may not assert absence correctly depending on Playwright version semantics for empty-value attributes | On some versions `toHaveAttribute(name, '')` matches mere presence; the negation could pass/fail inconsistently and the "menu starts closed" assertion becomes weak | Use `await expect(details.locator('summary')).toBeVisible()` plus checking `details` has no `open` attribute via `getAttribute` comparison, or assert the nav links are hidden initially

P1: none — see above; no correctness/security issue introduced by the diff itself. Note: the theme-persistence test depends on `cf-theme` and `data-theme` contract existing in code not shown in the diff; if that contract is not implemented, the test will fail (test coverage gap, not a code bug).

VERDICT: PASS
