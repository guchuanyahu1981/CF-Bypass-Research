"""`captchas/` 嫁接层的**离线**测试（不装 cloakbrowser、不联网、不起浏览器）。

为什么要有这个文件：
  2026-11-06 审计实测 `pytest --cov=captchas` 的覆盖率是 **2%**（2512/2556 语句未覆盖），
  而 `captchas/` 有 5,006 行、40 个文件、11 个厂商求解器 —— 它完全在 `--cov=solver`
  与 `check-coverage.mjs` 之外，CI 对它只有一句 `compileall`。
  这与 v5.0.1 那轮"整体 82% 掩盖 bootstrap.py 38%"是同一类结构：门禁有边界，而边界外
  还在长代码。

这里能测什么、不能测什么：
  · **能测**：包结构（11 个求解器都能以 `captchas.<vendor>.solve` 导入）、纯函数
    （cookie 解析、判据、选择器解析、签名、key 轮转）。
  · **不能测**：真正驱动浏览器的 `solve_*` 主体 —— 那需要 cloakbrowser 二进制 + 真实
    挑战页，属于集成/真机验证，不在模块覆盖率口径内。
    所以 `solve.py` 的覆盖率一定会停在低位，这是**能力边界**，不是"懒得写测试"。

桩模块只用一次（`fake_cloakbrowser`）：求解器在模块级 `import cloakbrowser`，
没有它连导入都失败 —— 这也是为什么"包结构测试"必须先有桩。
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CAPTCHAS = ROOT / "captchas"

#: 11 个厂商包 → 它们必须导出的求解入口。名字写死，改名时这里会红。
VENDOR_SOLVERS = {
    "turnstile": "solve_turnstile",
    "recaptcha": "solve_recaptcha_v3",
    "hcaptcha": "solve_hcaptcha",
    "cloudflare": "solve_cf_clearance",
    "awswaf": "solve_aws_waf",
    "botguard": "solve_botguard",
    "datadome": "solve_datadome",
    "perimeterx": "solve_perimeterx",
    "akamai": "solve_akamai",
    "aliyun": "solve_aliyun",
    "arkose": "solve_arkose",
}


@pytest.fixture()
def fake_cloakbrowser(monkeypatch):
    """把 `cloakbrowser` 换成一个**永不启动浏览器**的桩。

    求解器在模块级只用 `cloakbrowser.launch_async`（18 处）；桩的 `__getattr__`
    兜住其余属性，免得上游 cherry-pick 引入新符号时这里莫名其妙地红。
    """
    mod = types.ModuleType("cloakbrowser")

    async def launch_async(*a, **k):  # pragma: no cover - 离线测试不该走到这里
        raise AssertionError("离线测试不许启动真实浏览器")

    mod.launch_async = launch_async

    def _any(name):  # pragma: no cover - 只有上游新增符号时才会被用到
        return launch_async

    mod.__getattr__ = _any
    monkeypatch.setitem(sys.modules, "cloakbrowser", mod)
    return mod


# ── 包结构 ──────────────────────────────────────────────


VENDOR_OPTIONAL_DEPS = {
    "aliyun": ("cv2",),
    "arkose": ("onnxruntime",),
    "recaptcha": ("PIL",),
}


def _require(*modules: str) -> None:
    """可选依赖缺失时 skip，而不是把 CI 拖红。

    cv2 / PIL / onnxruntime 是研究环境才装的重量级依赖；CI 只装运行必需项。
    本地装了就跑真的，CI 没装就明确 skip —— 不伪装成通过。
    """
    for name in modules:
        pytest.importorskip(name)


@pytest.mark.parametrize(("vendor", "entry"), sorted(VENDOR_SOLVERS.items()))
def test_vendor_solver_imports_as_package(fake_cloakbrowser, vendor, entry):
    """每个求解器都必须能以 `captchas.<vendor>.solve` 导入。

    这条曾经是红的：`arkose` 与 `cloudflare` 用裸包名（`from arkose.predict` /
    `from turnstile.solve`）—— 只有把 `captchas/` 自己塞进 sys.path 才能工作，
    等于同一个模块有两套名字。模块级状态（`_solve_lock`、KeyPool）会因此各有一份。
    """
    _require(*VENDOR_OPTIONAL_DEPS.get(vendor, ()))
    module = __import__(f"captchas.{vendor}.solve", fromlist=["*"])
    assert callable(getattr(module, entry))


def test_no_bare_intra_package_imports():
    """静态守门：包内互相引用必须是 `captchas.*`。

    给未来的 cherry-pick 设的护栏 —— 上游一旦新增 `from common.x import`，
    这里立刻红，而不是等到"某个类型在多线程下莫名其妙地不串行了"才发现。
    """
    local = {p.name for p in CAPTCHAS.iterdir() if p.is_dir() and not p.name.startswith("__")}
    bad = []
    for path in sorted(CAPTCHAS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
            elif isinstance(node, ast.Import):
                top = ""
            else:
                continue
            names = [node.module] if isinstance(node, ast.ImportFrom) and node.module else [
                a.name for a in node.names
            ] if isinstance(node, ast.Import) else []
            for name in names:
                if name.split(".")[0] in local:
                    bad.append(f"{path.relative_to(ROOT)}: {name}")
    assert not bad, "这些文件仍在用裸包名导入（应为 captchas.*）：" + ", ".join(bad)


def test_package_init_is_lazy_and_does_not_pull_the_solver():
    """`import captchas.<vendor>` 不该把 `solve.py`（进而 cloakbrowser）拖进来。

    否则离线可测的纯逻辑（图像预处理 / ONNX 前后处理 / key 池）连导入都做不到。
    用子进程测：当前进程里 solve 可能已被别的用例导入过，断言会失真。
    """
    code = (
        "import sys\n"
        "import captchas.hcaptcha\n"
        "import captchas.recaptcha\n"
        "assert 'captchas.hcaptcha.solve' not in sys.modules, 'hcaptcha 被急切导入'\n"
        "assert 'captchas.recaptcha.solve' not in sys.modules, 'recaptcha 被急切导入'\n"
        "assert 'cloakbrowser' not in sys.modules, '把 cloakbrowser 也拖进来了'\n"
        "print('lazy-ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert "lazy-ok" in out.stdout


def test_selfcheck_helpers_are_importable(fake_cloakbrowser):
    """`_selfcheck.py` 是运维自检入口，跟着包结构一起管，别留成孤儿。"""
    for vendor in ("awswaf", "cloudflare", "datadome"):
        mod = __import__(f"captchas.{vendor}._selfcheck", fromlist=["*"])
        assert mod is not None


# ── 纯函数：cookie / 判据 ────────────────────────────────


def test_parse_dd_cookie_extracts_value_domain_and_age(fake_cloakbrowser):
    mod = __import__("captchas.datadome.solve", fromlist=["*"])
    body = '{"status":200,"cookie":"datadome=abc123; Max-Age=86400; Domain=.example.com; Path=/; Secure"}'
    out = mod._parse_dd_cookie(body)
    assert out["value"] == "abc123"
    assert out["domain"] == ".example.com"
    assert out["max_age"] == 86400
    assert out["status"] == 200


def test_parse_dd_cookie_returns_empty_on_garbage_and_no_cookie(fake_cloakbrowser):
    mod = __import__("captchas.datadome.solve", fromlist=["*"])
    assert mod._parse_dd_cookie("not json") == {}
    # 合法 JSON 但没有 datadome cookie：只回 status，绝不编一个 value
    assert mod._parse_dd_cookie('{"status":403,"cookie":"other=1"}') == {"status": 403}


def test_aws_waf_block_title_predicate(fake_cloakbrowser):
    mod = __import__("captchas.awswaf.solve", fromlist=["*"])
    assert mod._title_is_block("403 Forbidden") is True
    assert mod._title_is_block("Access Denied") is True
    assert mod._title_is_block("Example Domain") is False
    assert mod._title_is_block("") is False


def test_akamai_abck_validated_uses_the_invalidated_sentinel(fake_cloakbrowser):
    """判据是"不含 `~-1~-1~-1~` 三连哨兵"，不是看第二个字段。

    上游注释里有实证：bestbuy 的**新鲜** cookie 带着这个三连、第二字段也是 '-1'，
    所以按第二字段判断会把"未验证"读成"已验证"。
    """
    mod = __import__("captchas.akamai.solve", fromlist=["*"])
    assert mod._abck_validated("") is False
    assert mod._abck_validated("1~-1~-1~-1~1700000000~") is False
    assert mod._abck_validated("0~0~0~1700000000~1~2~3~") is True


def test_cookie_pickers_return_the_whole_record_or_none(fake_cloakbrowser):
    cf = __import__("captchas.cloudflare.solve", fromlist=["*"])
    ak = __import__("captchas.akamai.solve", fromlist=["*"])
    cookies = [
        {"name": "other", "value": "x"},
        {"name": "cf_clearance", "value": "v", "domain": ".t"},
        {"name": "_abck", "value": "a"},
        {"name": "bm_sz", "value": "b"},
    ]
    assert cf._clearance(cookies)["value"] == "v"
    assert ak._abck_cookie(cookies)["value"] == "a"
    assert ak._bm_sz_cookie(cookies)["value"] == "b"
    assert cf._clearance([{"name": "other"}]) is None
    assert ak._abck_cookie([]) is None


# ── 纯函数：选择器 / 路由 glob / 浏览器参数 ────────────────


class _FakePage:
    """记录调用形状的最小替身；断言的是"解析成了哪一类 locator"。"""

    def locator(self, selector):
        return ("css" if not selector.startswith("xpath=") else "xpath", selector)

    def get_by_text(self, text, exact=False):
        return ("text", text, exact)

    def get_by_role(self, role, **kwargs):
        return ("role", role, kwargs)


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("input[type=email]", ("css", "input[type=email]")),
        ("//button[@type='submit']", ("xpath", "xpath=//button[@type='submit']")),
        ("(//div)[1]", ("xpath", "xpath=(//div)[1]")),
        ("text=Continue with Email", ("text", "Continue with Email", False)),
        ("regex=Continue.*Email", ("css", "text=/Continue.*Email/")),
        ("role=button[name='Submit']", ("role", "button", {"name": "Submit"})),
        ("role=link", ("role", "link", {})),
    ],
)
def test_resolve_selector_covers_every_supported_prefix(fake_cloakbrowser, selector, expected):
    browser = __import__("captchas.common.browser", fromlist=["*"])
    import asyncio

    assert asyncio.run(browser.resolve_selector(_FakePage(), selector)) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://ex.com", "https://ex.com/**"),
        ("https://ex.com/", "https://ex.com/**"),
        ("https://ex.com:8443", "https://ex.com:8443/**"),
        ("https://ex.com/a/b?q=1", "https://ex.com/a/b?q=1"),
    ],
)
def test_route_glob_only_rewrites_origin_only_urls(url, expected):
    """`goto("https://ex.com")` 实际请求的是 `https://ex.com/`：裸 URL 的 route 会静默漏掉。"""
    browser = __import__("captchas.common.browser", fromlist=["*"])
    assert browser.route_glob(url) == expected


def test_browser_kwargs_headless_defaults_by_prefix(monkeypatch):
    browser = __import__("captchas.common.browser", fromlist=["*"])
    monkeypatch.delenv("BROWSER_HEADLESS", raising=False)
    assert browser.browser_kwargs("TURNSTILE")["headless"] is True
    assert browser.browser_kwargs("RECAPTCHA")["headless"] is False
    monkeypatch.setenv("BROWSER_HEADLESS", "0")
    assert browser.browser_kwargs("TURNSTILE")["headless"] is False


def test_browser_kwargs_proxy_and_geoip(monkeypatch):
    browser = __import__("captchas.common.browser", fromlist=["*"])
    monkeypatch.delenv("BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("TURNSTILE_GEOIP", raising=False)
    assert "proxy" not in browser.browser_kwargs("TURNSTILE")
    assert browser.browser_kwargs("TURNSTILE", proxy="http://p:1")["proxy"] == "http://p:1"
    monkeypatch.setenv("TURNSTILE_GEOIP", "1")
    assert browser.browser_kwargs("TURNSTILE")["geoip"] is True


# ── 纯函数：签名 / 反解 / 分类映射 ────────────────────────


def test_aliyun_signature_is_order_insensitive_and_secret_dependent(fake_cloakbrowser, monkeypatch):
    _require("cv2")
    aliyun = __import__("captchas.aliyun.solve", fromlist=["*"])
    a = aliyun._sign({"b": "2", "a": "1"})
    assert a == aliyun._sign({"a": "1", "b": "2"})
    assert a == aliyun._sign({"a": "1", "b": "2"})  # 稳定
    # base64(20 字节 SHA1) 恒为 28 字符
    assert len(a) == 28
    monkeypatch.setattr(aliyun, "_AK_SECRET", "different-secret")
    assert aliyun._sign({"a": "1", "b": "2"}) != a


def test_aliyun_invert_nails_the_quadratic(fake_cloakbrowser):
    _require("cv2")
    aliyun = __import__("captchas.aliyun.solve", fromlist=["*"])
    a, b, c = aliyun._COEF
    for x in (0.0, 50.0, 250.0, 310.0):
        assert aliyun._invert(a * x * x + b * x + c) == pytest.approx(x, abs=1e-6)


def test_recaptcha_normalize_target_strips_articles_and_aliases(fake_cloakbrowser):
    _require("onnxruntime")
    onnx = __import__("captchas.recaptcha.onnx_classifier", fromlist=["*"])
    alias_key, alias_cls = next(iter(onnx._TARGET_ALIASES.items()))
    assert onnx._normalize_target(alias_key.upper()) == alias_cls
    assert onnx._normalize_target("a " + alias_key) == alias_cls
    assert onnx._normalize_target("") is None
    assert onnx._normalize_target("definitely not a class") is None


# ── KeyPool：轮转 / 去重 / 冷却 ────────────────────────────


def _keypool(tmp_path, keys):
    keyfile = tmp_path / "apikey.txt"
    keyfile.write_text("\n".join(keys) + "\n", encoding="utf-8")
    mistral = __import__("captchas.common.mistral", fromlist=["*"])
    return mistral, mistral.KeyPool(str(keyfile))


def test_keypool_dedupes_and_round_robins(tmp_path):
    _, pool = _keypool(tmp_path, ["k1", "k2", "k1", "k3"])
    assert pool.keys == ["k1", "k2", "k3"]
    seen = [pool._next_live_key() for _ in range(6)]
    assert seen == ["k1", "k2", "k3", "k1", "k2", "k3"]


def test_keypool_start_index_spreads_load(tmp_path):
    _, pool = _keypool(tmp_path, ["k1", "k2", "k3"])
    pool._cursor = __import__("itertools").cycle(pool.keys[1:] + pool.keys[:1])
    assert pool._next_live_key() == "k2"


def test_keypool_skips_parked_keys_and_clears_when_all_dead(tmp_path, monkeypatch):
    mistral, pool = _keypool(tmp_path, ["k1", "k2"])
    pool._park("k1", cooldown=3600)
    assert pool._next_live_key() == "k2"
    # 把 k2 也停掉：全死时必须清空冷却继续服务，而不是抛异常/死循环
    pool._park("k2", cooldown=3600)
    assert pool._next_live_key() in ("k1", "k2")
    assert pool._dead == {}


def test_keypool_rejects_empty_file(tmp_path):
    keyfile = tmp_path / "empty.txt"
    keyfile.write_text("\n\n", encoding="utf-8")
    mistral = __import__("captchas.common.mistral", fromlist=["*"])
    with pytest.raises(ValueError):
        mistral.KeyPool(str(keyfile))

# ── aliyun 子进程入口 ─────────────────────────────────────


def test_aliyun_runner_emits_single_result_line(fake_cloakbrowser, monkeypatch, capsys):
    """`python -m captchas.aliyun._run` 的契约：最后一行必须是 `__ALIYUN_RESULT__<json>`。

    主服务靠这行前缀从 stdout 里捞结果；换前缀/换模块路径都会让 aliyun 静默超时。
    """
    _require("cv2")
    runner = __import__("captchas.aliyun._run", fromlist=["*"])

    async def ok(**kwargs):
        return {"solved": True, "certifyId": "cid"}

    monkeypatch.setattr(runner, "solve_aliyun", ok)
    monkeypatch.setattr(sys, "argv", ["_run", "scene", "prefix", "sgp", "30", ""])
    runner.main()
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("__ALIYUN_RESULT__")
    assert '"solved": true' in out[-1]


def test_aliyun_runner_turns_exceptions_into_result(fake_cloakbrowser, monkeypatch, capsys):
    """求解器抛异常也必须回一行结果（否则父进程只能等超时）。"""
    _require("cv2")
    runner = __import__("captchas.aliyun._run", fromlist=["*"])

    async def boom(**kwargs):
        raise RuntimeError("cdp 掉了")

    monkeypatch.setattr(runner, "solve_aliyun", boom)
    monkeypatch.setattr(sys, "argv", ["_run", "scene", "prefix"])
    runner.main()
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert '"solved": false' in line and "cdp" in line


# ── perimeterx renderer 的降级链 ──────────────────────────


class _FlakyPage:
    """前 n 个选择器失败，之后成功 —— 用来验"逐个试"的降级链。"""

    def __init__(self, fail_first=1):
        self.fail_first = fail_first
        self.calls = []

    async def fill(self, sel, val, timeout=0):
        self.calls.append(("fill", sel, val))
        if len(self.calls) <= self.fail_first:
            raise RuntimeError("selector not found")

    async def click(self, sel, timeout=0):
        self.calls.append(("click", sel))
        if len(self.calls) <= self.fail_first:
            raise RuntimeError("selector not found")


def test_renderer_fill_falls_back_to_the_next_selector(fake_cloakbrowser):
    import asyncio

    outlook = __import__("captchas.perimeterx.renderers.outlook", fromlist=["*"])
    page = _FlakyPage(fail_first=1)
    assert asyncio.run(outlook._fill(page, ["#a", "#b"], "v")) is True
    assert page.calls == [("fill", "#a", "v"), ("fill", "#b", "v")]


def test_renderer_fill_returns_false_when_every_selector_fails(fake_cloakbrowser):
    import asyncio

    outlook = __import__("captchas.perimeterx.renderers.outlook", fromlist=["*"])
    page = _FlakyPage(fail_first=99)
    assert asyncio.run(outlook._fill(page, ["#a", "#b"], "v")) is False


def test_renderer_next_returns_true_on_first_working_button(fake_cloakbrowser):
    import asyncio

    outlook = __import__("captchas.perimeterx.renderers.outlook", fromlist=["*"])
    page = _FlakyPage(fail_first=2)
    assert asyncio.run(outlook._next(page)) is True
    assert [c[1] for c in page.calls] == ["#iSignupAction", 'button:has-text("Next")',
                                         "button[type=submit]"]


# ── hcaptcha 图像链路里可离线的那一段 ──────────────────────


def test_cell_center_maps_index_to_canvas_pixel(fake_cloakbrowser):
    _require("PIL", "numpy")
    img = __import__("captchas.hcaptcha.image_solve", fromlist=["*"])
    cbox = {"x": 10.0, "y": 20.0, "width": 300.0, "height": 300.0}
    # grid=3：每格 100px；idx=0 是第一格中心，idx=4 是正中，idx=8 是右下
    assert img._cell_center(cbox, 0, 3) == (60.0, 70.0)
    assert img._cell_center(cbox, 4, 3) == (160.0, 170.0)
    assert img._cell_center(cbox, 8, 3) == (260.0, 270.0)


def test_grid_overlay_preserves_size_and_returns_png(fake_cloakbrowser):
    _require("PIL", "numpy")
    import base64
    import io

    import numpy as np
    from PIL import Image

    img = __import__("captchas.hcaptcha.image_solve", fromlist=["*"])
    src = Image.new("RGB", (90, 90), "white")
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    out = Image.open(io.BytesIO(base64.b64decode(img._grid_overlay(b64, 3))))
    assert out.size == (90, 90)
    # 网格线必须是红的 —— 否则"编号网格"这套提示对视觉模型无效
    assert (np.asarray(out) == (255, 0, 0)).all(axis=2).any()


def test_pick_cells_dedupes_and_drops_out_of_range(fake_cloakbrowser):
    _require("PIL")
    import asyncio
    import base64
    import io

    from PIL import Image

    mod = __import__("captchas.hcaptcha.image_solve", fromlist=["*"])

    buf = io.BytesIO()
    Image.new("RGB", (90, 90), "white").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    class _Frame:
        async def evaluate(self, _js):
            return b64

    class _Pool:
        def ask(self, _img, _prompt):
            return "1, 4, 4, 99, none"

    assert asyncio.run(mod._pick_cells(_Frame(), _Pool(), "buses", 3)) == [1, 4]
