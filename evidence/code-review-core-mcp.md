# 独立代码复审

- generated_at: 2026-11-12T22:48:43
- duration_sec: 144.6
- diff_bytes: 33704
- untracked_included: solver/client.py, solver/mcp_server.py, solver/ratelimit.py
- exit_code: 0

---



P0: none
P1: none

P2 | solver/client.py:83 | `parts.port` raises a raw `ValueError` for invalid ports before the guarded `getaddrinfo` try block (same pattern in `validate_proxy_url` at :152) | `cf_fetch`/`cf_solve` with `http://example.com:abc/` or `:99999` surfaces as a generic `ValueError` tool error instead of a structured `SolverClientError` | read the port inside try/except ValueError (or pre-validate it) and raise `SolverClientError`

P2 | solver/client.py:285 | cross-host cookie drop compares exact hostnames, so same-site apex↔www redirects also strip `cf_clearance` | fetching `https://example.com/a` that 301s to `https://www.example.com/a` drops the Cookie header; the replay then hits Cloudflare with no clearance and returns `ok:false`/challenge for a completely normal redirect | compare registrable domain (eTLD+1) instead of hostname, or keep a per-origin cookie jar that still never sends cookies to a different site

P2 | solver/client.py:278 | on a redirect hop, `validate_connected_peer(e)` runs before `e.close()`; if the peer check raises, the `HTTPError` response/socket is never explicitly closed | repeated `cf_fetch` calls stopped at a redirect-to-private-IP leak sockets until GC | wrap the check in try/finally that guarantees `e.close()`

P2 | solver/client.py:185 | `_request` handles non-JSON bodies only on the HTTPError path; the 2xx path calls `json.loads` unguarded | a 200 HTML error page from a misconfigured proxy in front of the solver raises `JSONDecodeError`, reported by MCP as a generic internal error rather than a structured solver error | wrap `json.loads(body)` and raise `SolverClientError("求解服务返回非 JSON 响应", status=resp.status)`

P2 | solver/mcp_server.py:114 | `fresh=bool(_arg(args, "fresh", False))` coerces any non-empty string to True | a client sending `"fresh": "false"` or `"0"` flips cache-busting on, opposite to intent (schema says boolean but MCP does not enforce it) | accept only real bools, explicitly parse "true"/"false" strings, or reject other types

P2 | solver/mcp_server.py:202 | `resources/read` always returns `mimeType: "text/markdown"` while `resources/list` advertises `cf://notice` as `text/plain` (:189) | a client fetching NOTICE gets wrong media-type metadata/rendering; the two endpoints disagree | return the advertised mimeType per URI

P2 | solver/client.py:50 | `.env` fallback matches only lines starting exactly with `CF_SOLVE_TOKEN=`; it ignores `export ` prefixes and `#` comment lines | a `.env` containing `export CF_SOLVE_TOKEN=xxx` is silently skipped, so the client runs tokenless and every solve 401s — looks like bad credentials instead of a parse miss | strip an optional `export ` prefix and skip `#` lines before matching

P2 | solver/client.py:306 | the body read on the success path (`resp.read` inside `with resp:`) is not wrapped, unlike the `open()` path | a target that stalls mid-body raises raw `TimeoutError`/`IncompleteRead` after a successful (paid-for) solve; the tool returns a generic error losing the step/credential context | wrap the read and raise `SolverClientError("回放读取失败: …")`

VERDICT: PASS
