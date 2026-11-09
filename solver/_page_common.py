"""两页（`GET /` 与 `GET /console`）共用的前端内核（v6.4 D1）。

**为什么需要**：这两页都是"单文件 HTML + 原生 JS、零构建零依赖"，但它们各自抄了一份
`token()` / `getJSON()` / `setKV()` / `setBanner()` / `setState()` —— 约占各自脚本的三分之一。
两份副本已经开始漂移，而且是**行为不同**的漂移：

  · `getJSON()`：状态页用 `r.text()` + `JSON.parse`，控制台用 `r.json()`。后者在服务端回
    非 JSON body（例如代理中间层塞进来的 HTML 错误页）时直接抛，前端只能显示一句
    "连不上"，看不出到底返回了什么。收敛到 text 版本后两页行为一致，且非 JSON 也能显示。
  · `setKV()`：状态页用 `el.innerHTML = ""` 清空，控制台用 `removeChild` 循环。
    控制台的 CSP `default-src 'none'` + "DOM 只用 textContent/createElement" 是硬约束
    （`tests/test_console.py` 逐条锁死），所以统一到 removeChild 版本 —— 两边都合规。

**约束（改这个文件前先读）**：
  · 它只是**一段 JS 源码字符串**，由两个页面在 import 时替换进各自的 `"__COMMON_JS__"`
    占位符。不引入构建步骤、不产生额外请求（CSS 走 `ui_tokens.py` 的老路，同一个理由）。
  · 里面**不许**出现 `innerHTML` / `outerHTML` / `document.write` / 内联事件属性：
    控制台页的测试会因为这一个词把整页判红（这是有意的，它守的是 XSS 面）。
  · 只放**两页真的都用**的东西。只有一个页面用的（`esc()`、`_fmtTime()`、表格渲染、
    审计区块…）留在各自的页面里 —— 共用文件变成杂物箱之后就没人敢动它了。
"""

from __future__ import annotations

#: 注入到两页 `<script>` 里的共用内核。
#:
#: 依赖调用方在同一作用域里已经声明了这些 DOM 变量并调用 `bindPage()`；
#: 其余状态（横幅定时器）由本模块自己持有，因为每页只有一条横幅。
COMMON_JS = """  // ── 两页共用的前端内核（solver/_page_common.py）─────────────────
  // 令牌读写 / JSON 拉取 / DOM 写入 / 横幅，四个页面都长一样的东西只有这一份。
  var TOKEN_KEY = "cfsolve_token";
  var _stateEl = null;
  var _bannerEl = null;
  var _bannerTimer = null;

  // 元素绑定：两页的 state/banner 元素是各自文档里的，函数体共用但目标由页面给。
  function bindPage(opts) {
    opts = opts || {};
    _stateEl = opts.state || null;
    _bannerEl = opts.banner || null;
  }

  function token() { return sessionStorage.getItem(TOKEN_KEY) || ""; }

  function setState(text, cls) {
    if (!_stateEl) { return; }
    _stateEl.textContent = text;
    _stateEl.className = "badge " + (cls || "");
  }

  // autoHideMs 是可选的（状态页的"总数变化"提示会自己消失，控制台的常驻提示不会）。
  function setBanner(msg, isError, autoHideMs) {
    if (!_bannerEl) { return; }
    if (_bannerTimer) { clearTimeout(_bannerTimer); _bannerTimer = null; }
    if (!msg) { _bannerEl.hidden = true; return; }
    _bannerEl.hidden = false;
    _bannerEl.textContent = msg;
    _bannerEl.className = isError ? "error" : "empty";
    if (autoHideMs) {
      _bannerTimer = setTimeout(function () { _bannerEl.hidden = true; }, autoHideMs);
    }
  }

  // 只用 textContent / createElement —— DOM 写入面收敛在这两个 API 上。
  function setKV(el, pairs) {
    while (el.firstChild) { el.removeChild(el.firstChild); }
    // 必须把 class 还原成 kv：setEmpty() 会把它改成 "empty"，不还原就丢掉 display:grid
    // → 键值从两列退化成上下堆叠（真 Chrome 截图里看到过）。
    el.className = "kv";
    pairs.forEach(function (p) {
      var dt = document.createElement("dt"); dt.textContent = p[0];
      var dd = document.createElement("dd"); dd.textContent = p[1] == null ? "—" : String(p[1]);
      if (p[2]) dd.className = p[2];
      el.appendChild(dt); el.appendChild(dd);
    });
  }

  // 第三个参数是容器原本的 class（状态页的"扩展类型"卡要 chips，其余是 empty）。
  function setEmpty(el, msg, cls) {
    while (el.firstChild) { el.removeChild(el.firstChild); }
    el.className = (cls && cls !== "empty") ? (cls + " empty") : "empty";
    el.textContent = msg;
  }

  function getJSON(path) {
    var headers = {};
    var t = token();
    if (t) { headers["Authorization"] = "Bearer " + t; }
    return fetch(path, { headers: headers, cache: "no-store" }).then(function (r) {
      // 按文本读再自己解析：服务端/中间层回非 JSON（HTML 错误页）时也要能把原文显示出来，
      // 否则前端只能报"连不上"，而真正的原因就藏在那个 body 里。
      return r.text().then(function (text) {
        var body;
        try { body = text ? JSON.parse(text) : {}; } catch (e) { body = { raw: text }; }
        return { status: r.status, body: body };
      });
    });
  }
"""
