"""求解服务的本地 HTTP 客户端（MCP 适配器专用，纯 stdlib）。

为什么单独一层：MCP server 只做协议翻译，不把 urllib/JSON 细节塞进工具分发；
测试可以注入一个假 client，不用起真实服务。

安全边界：
  · token 只从 `CF_SOLVE_TOKEN` 或仓库根 `.env` **读取**，不自动生成（客户端生成的新 token
    与服务端不一致，只会制造 401）；
  · 返回结果不额外记录/回显 token；`/solve` 的凭证本身是工具产物，必须回给调用方；
  · `fetch` 只把正文前 N 字符回给模型，完整正文落临时文件，避免把整页 HTML 灌进上下文。
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = os.environ.get("CF_SOLVE_URL", "http://127.0.0.1:8878")
DEFAULT_TIMEOUT = 180.0
FETCH_HEAD_CHARS = 2000
MAX_HEAD_CHARS = 20000
FETCH_MAX_BYTES = 5 * 1024 * 1024


class SolverClientError(RuntimeError):
    """结构化失败：带 HTTP 状态与 error_code，便于 MCP 侧原样转成 isError。"""

    def __init__(self, message: str, *, status: int = 0, code: str = "", stage: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.stage = stage


def load_token() -> str:
    """读 token：环境变量优先，其次当前目录/仓库根 `.env`。不生成。"""
    token = (os.environ.get("CF_SOLVE_TOKEN") or "").strip()
    if token:
        return token
    for path in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        try:
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line.startswith("CF_SOLVE_TOKEN="):
                    value = line.split("=", 1)[1].strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                        value = value[1:-1]
                    if value:
                        return value
        except OSError:
            continue
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跟随 3xx：`cf_fetch` 必须逐跳校验目标，否则 302 到内网/元数据就被绕过。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def validate_public_url(url: str) -> None:
    """`cf_fetch` 的回放边界：只允许 http(s) 且解析结果必须是公网地址。

    为什么不依赖服务端：服务端的 SSRF 守卫可能被 `--allow-private-hosts` 或
    pydoll/cfsolve/FlareSolverr 的盲跟随绕过（见 `docs/项目规格.md` L11）。
    MCP 是独立进程，必须自己守一次；这也能挡住 `file://`、`gopher://` 和
    `http://169.254.169.254/...` 这类根本不该进入回放的 URL。
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SolverClientError(f"cf_fetch 只允许 http(s) URL，收到 {parts.scheme or '(空)'}")
    host = parts.hostname
    if not host:
        raise SolverClientError("cf_fetch URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(
            host, parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise SolverClientError(f"cf_fetch 无法解析主机 {host}: {type(e).__name__}") from e
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:  # 非 IP 的 addrinfo（极少数平台）——不当作可回放目标
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
                or ip.is_reserved or ip.is_multicast):
            raise SolverClientError(f"cf_fetch 拒绝非公网地址：{host} -> {ip}")


SPILL_KEEP = 20


def cleanup_spills(keep: int = SPILL_KEEP) -> None:
    """只保留最近 `keep` 个 cf-fetch-*.html，防止长跑 MCP 会话把临时目录塞满。"""
    files = []
    try:
        for p in Path(tempfile.gettempdir()).glob("cf-fetch-*.html"):
            try:
                files.append((p.stat().st_mtime, p))
            except OSError:
                continue
    except OSError:
        return
    files.sort(key=lambda item: item[0], reverse=True)
    for _, old in files[max(0, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass


def validate_connected_peer(resp) -> None:
    """再校验**实际连上的 IP**：防 DNS rebinding（校验时公网、连接时私网）。

    urllib 会在 open 时重新解析域名，所以只校验 `validate_public_url` 的解析结果
    存在 TOCTOU。这里从响应底层 socket 取 `getpeername()`；取不到时跳过（不假装校验过）。
    代理模式跳过：连接的是用户显式提供的代理，目标解析在代理侧。
    """
    try:
        peer = resp.fp.raw._sock.getpeername()[0]
    except (AttributeError, OSError, IndexError, TypeError):
        return
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return
    if not ip.is_global:
        raise SolverClientError(f"cf_fetch 实际连到了非公网地址：{ip}")


def validate_proxy_url(proxy: str) -> None:
    """MCP 的 proxy 参数不能成为新的 SSRF 入口。

    允许本机回环代理（127.0.0.1/::1 是常见本地代理），拒绝私网、链路本地、
    云元数据、保留地址和非 http(s) scheme。真实出口策略仍由服务端代理池负责。
    """
    if not proxy:
        return
    parts = urllib.parse.urlsplit(proxy)
    if parts.scheme not in ("http", "https"):
        raise SolverClientError(f"cf_fetch proxy 只允许 http(s)，收到 {parts.scheme or '(空)'}")
    host = parts.hostname
    if not host:
        raise SolverClientError("cf_fetch proxy 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, parts.port or 8080, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise SolverClientError(f"cf_fetch proxy 无法解析 {host}: {type(e).__name__}") from e
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback:
            continue
        if (ip.is_private or ip.is_link_local or ip.is_unspecified
                or ip.is_reserved or ip.is_multicast):
            raise SolverClientError(f"cf_fetch proxy 指向非公网地址：{host} -> {ip}")


def _replay_opener(proxy: str = ""):
    # 空的 ProxyHandler 是刻意的：不传它时 build_opener 会读取 HTTP_PROXY/HTTPS_PROXY，
    # “直连”会静默走系统代理，peer 校验与出口语义都会失真。
    handlers = [urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {})]
    handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers)


class SolverClient:
    def __init__(self, base_url: str = "", token: str = "", timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.token = token if token else load_token()
        self.timeout = float(timeout)

    # -- HTTP ---------------------------------------------------------
    def _request(self, path: str, payload: dict | None = None, *,
                 timeout: float | None = None, auth: bool = True) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data,
                                     method="GET" if payload is None else "POST")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with _replay_opener().open(req, timeout=timeout or self.timeout) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                body = json.loads(raw)
            except Exception:  # noqa: BLE001
                body = {}
            raise SolverClientError(
                str(body.get("error") or e.reason or f"HTTP {e.code}"),
                status=e.code, code=str(body.get("error_code") or ""),
                stage=str(body.get("stage") or ""),
            ) from e
        except urllib.error.URLError as e:
            raise SolverClientError(f"求解服务不可达: {e.reason}") from e

    # -- tools --------------------------------------------------------
    def health(self) -> dict:
        return self._request("/health", auth=False)

    def detect(self, url: str, fresh: bool = False) -> dict:
        validate_public_url(url)
        return self._request(f"/detect?url={urllib.parse.quote(url, safe='')}"
                             + ("&fresh=1" if fresh else ""))

    def solve(self, url: str, *, want: str = "clearance", sitekey: str = "",
              route: str = "fixed", timeout: float = 45.0) -> dict:
        # 与服务端同一套公网边界：MCP 不能借 --allow-private-hosts 去打内网。
        validate_public_url(url)
        payload = {"url": url, "want": want, "route": route, "timeout": timeout}
        if sitekey:
            payload["sitekey"] = sitekey
        return self._request("/solve", payload)

    def fetch(self, url: str, *, want: str = "clearance", sitekey: str = "",
              route: str = "fixed", timeout: float = 45.0,
              head_chars: int = FETCH_HEAD_CHARS, proxy: str = "") -> dict:
        """先取凭证，再用同 UA/Cookie 回放；完整正文落临时文件，只回头部片段。

        `proxy` 必须与求解时的出口一致：cf_clearance 绑出口 IP。MCP 进程默认走本机直连，
        当 solver 用代理池求解时，调用方必须把同一个代理传进来，否则回放会 403。

        安全边界：只允许公网 http(s)；每一跳重定向都重新校验，跨主机时丢弃 Cookie。
        完整正文只保留最近 20 份 spill 文件（`cleanup_spills`），不是无限增长。
        """
        # MCP 的回放边界：即使服务端放行（--allow-private-hosts / 盲跟随后端），
        # 这里也不允许 file://、回环、私网、链路本地或云元数据地址。
        validate_public_url(url)
        validate_proxy_url(proxy)
        cred = self.solve(url, want=want, sitekey=sitekey, route=route, timeout=timeout)
        if not cred.get("solved"):
            return {"ok": False, "step": "solve", "response": cred}
        ua = str(cred.get("user_agent") or "")
        cookies = []
        for c in cred.get("cookie_list") or []:
            name, value = c.get("name"), c.get("value")
            if name and value is not None:
                cookies.append(f"{name}={value}")
        if not cookies and cred.get("cf_clearance"):
            cookies.append(f"cf_clearance={cred['cf_clearance']}")
        headers = {
            "User-Agent": ua,
            "Cookie": "; ".join(cookies),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        replay_timeout = min(self.timeout, max(1.0, float(timeout)))
        opener = _replay_opener(proxy)
        current = url
        resp = None
        status = 0
        final = current
        truncated_body = False
        for _hop in range(6):
            validate_public_url(current)
            req = urllib.request.Request(current, headers=headers)
            e_truncated = False
            try:
                resp = opener.open(req, timeout=replay_timeout)
                try:
                    if not proxy:
                        validate_connected_peer(resp)
                except BaseException:
                    try:
                        resp.close()
                    except Exception:  # noqa: BLE001
                        pass
                    raise
                status = resp.status
                final = resp.geturl()
                break
            except urllib.error.HTTPError as e:
                location = e.headers.get("Location") if e.headers else None
                if e.code in (301, 302, 303, 307, 308) and location:
                    if not proxy:
                        validate_connected_peer(e)
                    try:
                        e.close()
                    except Exception:  # noqa: BLE001
                        pass
                    nxt = urllib.parse.urljoin(current, location)
                    # 跨主机跳转时不要把 cf_clearance 带给另一个域。
                    if urllib.parse.urlsplit(nxt).hostname != urllib.parse.urlsplit(current).hostname:
                        headers = {k: v for k, v in headers.items() if k.lower() != "cookie"}
                    current = nxt
                    continue
                raw = e.read(FETCH_MAX_BYTES + 1)
                e_truncated = len(raw) > FETCH_MAX_BYTES
                if e_truncated:
                    raw = raw[:FETCH_MAX_BYTES]
                status = e.code
                final = current
                truncated_body = e_truncated
                try:
                    e.close()
                except Exception:  # noqa: BLE001
                    pass
                break
            except urllib.error.URLError as e:
                raise SolverClientError(f"回放不可达: {e.reason}") from e
        else:
            raise SolverClientError("回放重定向次数超过 5 次")
        if resp is not None:
            with resp:
                raw = resp.read(FETCH_MAX_BYTES + 1)
                truncated_body = len(raw) > FETCH_MAX_BYTES
                if truncated_body:
                    raw = raw[:FETCH_MAX_BYTES]
                status = resp.status
                final = resp.geturl()
        text = raw.decode("utf-8", "replace")
        head_chars = min(max(0, int(head_chars)), MAX_HEAD_CHARS)
        spill = None
        cleanup_spills()          # 保留最近 20 份，避免长期会话无限堆积
        try:
            fd, spill = tempfile.mkstemp(prefix="cf-fetch-", suffix=".html")
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
        except OSError:
            spill = None
        low = text.lower()
        title = ""
        if "<title>" in low:
            try:
                title = text[low.index("<title>") + 7: low.index("</title>")].strip()[:200]
            except ValueError:
                title = ""
        return {
            "ok": status == 200,
            "step": "replay",
            "status": status,
            "final_url": final,
            "title": title,
            "challenge_like": bool("cf-chl" in low or "challenge-platform" in low),
            "head": text[:head_chars],
            "truncated": len(text) > head_chars,
            "spill_path": spill,
            "spill_truncated": bool(truncated_body),
            "credential": {k: cred.get(k) for k in ("want", "egress_id", "expires_in", "cached")},
            "warning": (
                None if proxy or str(cred.get("egress_id") or "default") == "default"
                else f"求解用了出口 {cred.get('egress_id')}，但 MCP 回放走本机直连；"
                     "请给 cf_fetch 传同一个 proxy，否则 cf_clearance 会因出口不一致失效"
            ),
        }


def capabilities() -> dict:
    """机器可读能力清单（从 solver/vendors.py 与 challenges 派生）。"""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solver"))
        import vendors  # type: ignore
        from challenges import Want  # type: ignore
        from multicaptcha import CAPTCHA_TYPES  # type: ignore
    except Exception:  # noqa: BLE001
        return _capabilities_fallback()
    return {
        "main_chain": [w.value for w in Want],
        "graft_layer": sorted(CAPTCHA_TYPES),
        "solvable_providers": sorted(vendors.SOLVABLE_PROVIDERS),
        "unsupported_examples": ["kasada", "incapsula", "shape", "sucuri",
                                 "geetest", "qcloud", "captchaeu", "friendlycaptcha"],
        "notes": ("检测可识别 34 个厂商；只有 main_chain/graft_layer 可解，"
                  "其余明确拒绝（route=auto 不会白等超时）。"),
    }


def _capabilities_fallback() -> dict:
    return {
        "main_chain": ["clearance", "awswaf", "turnstile", "recaptcha_v3"],
        "graft_layer": ["turnstile_x", "recaptcha_x", "hcaptcha", "cloudflare_x",
                        "awswaf_x", "botguard", "datadome", "perimeterx", "akamai",
                        "aliyun", "arkose"],
        "solvable_providers": [],
        "unsupported_examples": ["kasada", "incapsula", "shape", "sucuri", "geetest",
                                 "qcloud", "captchaeu", "friendlycaptcha"],
        "notes": "检测可识别 34 个厂商；只有上表 main_chain/graft_layer 可解，其余明确拒绝。",
    }