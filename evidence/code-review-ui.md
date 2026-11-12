# 独立代码复审

- generated_at: 2026-11-12T10:49:53
- duration_sec: 150.1
- diff_bytes: 16098
- untracked_included: (none)
- exit_code: 0

---

P0: none
P1: none
P2 | solver/console_html.py:437 | `forget` removes the token and clears `tokenInput.value`, but never resets the placeholder from "已保存 token（重新输入可替换）" | after clicking 清除 token, the field still claims a token is saved, contradicting the "未鉴权" badge/banner | reset `tokenInput.placeholder` to `"Bearer token（来自 .env）"` in the forget handler.
P2 | solver/console_html.py:355 | the audit 401 branch calls `token()` at response time instead of using the token that was sent | save a bad token, then quickly save a corrected token; the old in-flight `/audit` 401 arrives after the save, `token()` is now truthy, and the UI reports the new token as rejected and empties the audit table | snapshot the token/request generation and ignore responses for superseded requests.
P2 | solver/statuspage.py:399 | the non-200 `/health` failure branch requires `hb.state`; a non-200 response without `state` falls through | a 503 JSON error/proxy body lacking `state` is treated like a successful refresh and, with valid auth, `setBanner("")` clears the warning | handle `!healthOk` unconditionally, with a generic message when `state` is absent.
P2 | solver/statuspage.py:400 | `tokenHint` does not consider `hasToken` | when `/health` is non-200 and the saved token is invalid, the banner says a token is needed and to enter/save one, rather than saying the token was rejected; operators may retry the same bad token | branch the hint on `hasToken` and report token rejection separately.

VERDICT: PASS
