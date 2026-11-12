# 独立代码复审

- generated_at: 2026-11-12T13:29:34
- duration_sec: 55.8
- diff_bytes: 9133
- untracked_included: (none)
- exit_code: 0

---

P0: none
P1: none

P2 | gocfsolve/cfsolve.go:324 | `egressConfigured` is set unconditionally inside `WithEgressProxy`, including when `proxyURL` is blank/invalid | A caller that always invokes `WithEgressProxy(egressID, cfg.ProxyURL)` with an empty `ProxyURL` (or egress disabled) now flips every `replayClientFor("")`/lookup past the `len(c.proxies)==0` early return into the lookup path, so all direct replays fail `ErrNoEgressProxy` even though the app intended direct egress — a config that previously "worked by accident" now breaks all replay | Only set `egressConfigured` when a non-empty `egressID`/`proxyURL` was actually supplied, or add an explicit "no egress" opt-in, so an empty/unspecified proxy cannot silently disable direct replay.

P2 | gocfsolve/pool.go:344 | Removing `markFail` on `DoWith` error means a node whose replay transport is persistently broken (bad per-node egress proxy, node-specific TLS/connectivity failure) is never demoted; node health is now decided solely by the solve side | A node that solves fine but can never replay (e.g. its egress IP is blocked, giving connection errors rather than a 403) stays "healthy" forever and is selected ~1/N of the time, producing intermittent `Do` failures while the pool reports it as healthy — misleading operators and preventing self-healing | Keep a separate replay-failure counter with a higher threshold (and only for non-4xx transport errors) so a node that consistently fails replay is eventually demoted without conflating it with solve-side failures.

P2 | gocfsolve/cfsolve.go:621 | `ErrNoChallenge` is returned as an error and therefore is never cached by `solveShared` (only single-flight dedups concurrent callers) | For `route=auto` against a target that has no challenge, every sequential `Get`/`solveShared` call re-drives a full network probe to the solver service, so a high-traffic no-challenge site hammers the solver once per request | Cache a short-TTL "no challenge" sentinel (or return a distinct non-error result) so repeated probes are absorbed for a brief window.

VERDICT: PASS
