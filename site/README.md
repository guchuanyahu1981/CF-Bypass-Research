# CF Solver public site

Static product site for `CF-Bypass-Research`. Next.js App Router, static export,
Framer-style motion (`motion`), and two WebGL scenes with a static fallback.

This directory is independent of the Python solver. It never calls `/solve`,
`/detect`, `/health`, or any other service endpoint. It is an entry point plus a
dated evidence surface; the repository `docs/` remains the source of truth.

## Stack

| Item | Version |
|---|---|
| Next.js | 16.4.5 |
| React / React DOM | 19.3.1 |
| motion | 14.9.1 |
| three / @react-three/fiber | 0.187.0 / 9.8.2 |
| Tailwind CSS | 4.3.4 |
| TypeScript | 5.9.3 |

`@react-three/drei` was removed: the scenes only needed HTML labels and one line
primitive, and the dependency pulled more than it saved. Labels are DOM overlays;
connectors are mesh boxes.

## Commands

```bash
npm ci
npm run dev            # local dev
npm run build          # prebuild env check -> static export -> postbuild OG freeze + artifact check
npm run preview        # python -m http.server 3000 --directory out
npm run lint
npm run typecheck
npm run test:content   # locale parity, title/description limits, benchmark schema, keyword freshness
npm run test:tokens    # no hardcoded colors outside the token / 3D whitelist
npm run test:contrast  # WCAG contrast for dark/light tokens
npm run test:seo       # keyword rows + 120-day freshness
npm run csp            # add CSP meta to every HTML, write out/_headers
npm run verify:csp     # reject unsafe-inline/eval, missing hashes, external resources
npm run verify:out     # canonical/hreflang/sitemap/robots/JSON-LD/OG/placeholder scan
npm run verify:site    # Playwright routes, hreflang, JSON-LD, axe, reduced-motion, no-WebGL
npm run test:e2e       # Playwright runtime errors, keyboard focus, reduced-motion, CLS
npm run lighthouse     # desktop preset（error 级硬门禁）
npm run lighthouse:mobile  # mobile preset（性能/TBT warn，a11y/BP/SEO error）
```

`npm run build` runs three gates in order: `scripts/check-env.mjs` (public origin),
`next build`, then `scripts/gen-og.mjs` + `scripts/check-out.mjs` (stable
`/og/{zh,en}.png` paths and SEO artifact assertions). A missing or placeholder
`NEXT_PUBLIC_SITE_URL` stops the build instead of silently shipping `localhost`
canonical URLs.

`verify:site` and `test:e2e` expect a server at `SITE_BASE_URL` (default
`http://127.0.0.1:3100`); Playwright reuses an already-running one. Set
`CHROME_PATH` to a Chromium binary if Playwright's own browser is not installed.
`verify:site` writes `../plans/证据/ui-v1/site-verify.json`.

## Environment

| Variable | Use |
|---|---|
| `NEXT_PUBLIC_SITE_URL` | Required for a production build. Canonical/OG/hreflang/sitemap origin, origin only (no path). |
| `SITE_ALLOW_PLACEHOLDER` | Set to `1` for a local-only build: `NEXT_PUBLIC_SITE_URL` may be unset, canonical uses `http://localhost:3000`, and the footer renders a "local preview" notice. |
| `SITE_BASE_PATH` | Build-time asset prefix for sub-path hosting. Canonical/hreflang/sitemap still assume an origin-root deployment; a non-empty base path is untested, and `check-env` rejects a path inside `NEXT_PUBLIC_SITE_URL`. |
| `SITE_BASE_URL` | Base URL used by `verify:site` and `test:e2e`. |
| `CHROME_PATH` | Chromium executable for Playwright/Lighthouse when not installed by the package. |

## Security model

- Static export: no server, no cookies, no accounts, no form, no third-party scripts.
- CSP is generated after the build. `scripts/gen-csp.mjs` hashes every inline script
  and injects a meta policy into each HTML file, then writes `out/_headers` for hosts
  that support response headers.
- `script-src` is `'self'` plus `'sha256-…'` hashes, with single quotes. An unquoted
  hash is ignored by the browser and blocks every inline script; `verify:csp` checks
  for the quoted form.
- `style-src` keeps `'unsafe-inline'` because Next/R3F write inline style attributes.
  No user-controlled content reaches the DOM.
- The two Python pages (`GET /`, `GET /console`) keep their own CSP and their
  "no `<link>`, no external resource" invariant. A favicon was deliberately not added
  there; the public site has one.

## Deployment status

The site is verified locally only. GitHub Pages is not enabled, and no Cloudflare
Pages token is configured. That means the hosting-specific header behaviour
(`_headers`, HSTS, `frame-ancestors` as a response header) is **not** verified.
GitHub Pages would ignore `_headers`; if it is used, the meta CSP is the only
policy and `frame-ancestors` is not enforced. Prefer a host that supports response
headers.

## Content rules

- Every number on `/benchmarks/` comes from `data/benchmarks.ts`, with `measuredAt`,
  `conditions`, and `source`. Do not hardcode a number in a page component.
- Known limitations stay visible: managed challenge replay can return the challenge
  page; the 2026-11-03 script has a stage2 blocker; clearance is bound to the egress IP.
- English copy is model-written. `content/EN-REVIEW.md` is the human review sheet; it
  has not been signed off by a native speaker.
- No search-volume numbers are claimed. `seo/keywords.json` is an intent matrix;
  Search Console backfill is pending.

## Verification snapshot (2026-11-08)

| Gate | Result |
|---|---|
| `npm run build` (no env) | fails: `NEXT_PUBLIC_SITE_URL is required for a production build` |
| `npm run build` (`NEXT_PUBLIC_SITE_URL=https://cf-solver.dev`) | 20 static routes, `out/404.html`, no `localhost`/placeholder leakage |
| `npm run verify:csp` | 17 HTML files, 20 inline-script hashes |
| `npm run verify:out` | 12 indexable pages, 2 noindex pages, 404 noindex, OG 1200×630 |
| `npm run test:content` | 12/12 (legal copy, benchmark-id references, hardcoded-number scan, `import httpx`, version sync) |
| `npm run test:tokens` | 34 files, 0 hardcoded colors |
| `npm run test:contrast` | dark/light text ≥ 4.5, focus ≥ 3 |
| `npm run verify:site` | 14 routes, axe 0, reduced-motion static, no-WebGL static |
| `npm run test:e2e` | 22/22 (runtime errors, keyboard focus ring, reduced-motion, scene controls, static controls, 360px controls, pointer-triggered + idle 3D, CLS, theme persistence, language routes, 360px mobile menu) |
| Lighthouse desktop (2026-11-12 re-run) | Perf 100 / A11y 100 / BP 100 / SEO 100; LCP 0.32s/0.33s, CLS 0, TBT 0ms |
| Lighthouse mobile (2026-11-12 re-run) | Perf 98–99 / A11y 100 / BP 100 / SEO 100; LCP 1.09–1.20s, CLS 0, TBT 113–139ms |
| Initial JS gzip | 227.7 KB (budget 250 KB) |
| 3D chunk gzip | 236.2 KB (budget 260 KB) |
| CSS gzip | 6.7 KB |
| `npm audit --omit=dev` | 0 vulnerabilities |

The initial-JS and 3D-chunk numbers exceed the first draft's 150/220 KB estimates.
The measured framework baseline (Next 16 + React 19 + motion) and three.js make those
caps unattainable without dropping the chosen stack; the budgets were revised to
250/260 KB with no regression allowed.

The Lighthouse numbers above were re-measured on 2026-11-12. Desktop assertions are
`error` (Performance/A11y/BP/SEO ≥ 0.9/0.95/0.9/1.0, LCP ≤ 2s, CLS ≤ 0.05,
TBT ≤ 200ms), so the desktop Lighthouse assertions (thresholds 0.9/0.95/0.9/1.0; measured 100) are now a hard gate rather than a release note.
Mobile keeps Performance/TBT at `warn` because runner load moves those two; its
A11y/BP/SEO assertions are still `error`. Mobile baseline thresholds: Performance 0.8,
CLS 0.1, TBT 300ms.

## Known gaps

- Real low-end hardware was not tested. The 3D fallback was exercised with Chromium's
  SwiftShader software renderer and CPU throttling; see `plans/证据/ui-v1/3d-fps.json`.
- Hosting headers were not tested because deployment was intentionally left local.
  `NEXT_PUBLIC_SITE_URL` must be supplied at build time before any public build; the
  build fails without it.
- `npm audit --omit=dev` is clean. The Lighthouse toolchain (`@lhci/cli` →
  `lighthouse` → `puppeteer-core`/`proxy-agent`/`basic-ftp`) carries 9 high advisories
  that only ship as dev dependencies and never reach `out/`; the published fix is a
  breaking downgrade to 0.13.0, so it is recorded instead of applied.
- Search volumes were not purchased or scraped; only the intent matrix exists.
- The English copy has not had a native-speaker review.