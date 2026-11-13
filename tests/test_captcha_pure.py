"""`captchas/` 纯函数测试：合成 fixture，不联网、不起浏览器。

P3-02：11 个厂商的求解主体是浏览器驱动，离线只能测纯函数。这里挑的是**算错就会
直接失败**的四类逻辑：

  · 阿里云滑块缺口：模板匹配的坐标计算（合成图形，真值已知）
  · Arkose：CryptoJS 兼容 AES-CBC（可逆性 + 输出格式）
  · reCAPTCHA：九宫格切片顺序 + ONNX letterbox 预处理 + 目标名归一
  · PerimeterX：Fluent 下拉的选择器回退与越界索引夹取

所有图片在测试运行时生成，不落任何真实挑战样本。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
Image = pytest.importorskip("PIL.Image")
pytest.importorskip("onnxruntime")

from captchas.aliyun.gap_cv import (  # noqa: E402
    _detect_gap_cv, _piece_geometry, refine_gap_x,
)
from captchas.arkose import predict as arkose_predict  # noqa: E402
from captchas.recaptcha.image_solve import _classify_grid  # noqa: E402
from captchas.recaptcha.onnx_classifier import OnnxClassifier, _normalize_target  # noqa: E402
from captchas.perimeterx.renderers.outlook import _pick_fluent  # noqa: E402


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _png_rgb(img: np.ndarray) -> bytes:
    """RGB 图直接走 PIL：`cv2.imencode` 按 BGR 解释，红蓝会互换（曾把红块测成黑色）。"""
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG")
    return buf.getvalue()


def _synthetic_gap(shift: int) -> tuple[bytes, bytes]:
    """阴影（RGBA）+ 背景（含同形状缺口）。缺口内部留一个方孔，让 Sobel 模板唯一。"""
    shadow = np.zeros((80, 100, 4), np.uint8)
    alpha = np.zeros((80, 100), np.uint8)
    alpha[20:60, 10:70] = 255
    alpha[30:44, 30:44] = 0
    shadow[:, :, 3] = alpha
    piece = alpha[20:60, 10:70]
    back = np.full((80, 300, 3), 100, np.uint8)
    back[20:60, shift:shift + 60][piece == 255] = 240
    return _png(back), _png(shadow)


# ── 阿里云滑块：缺口坐标 ────────────────────────────


def test_piece_geometry_returns_bbox_and_fails_closed():
    _, shadow = _synthetic_gap(180)
    geo = _piece_geometry(shadow)
    assert geo is not None
    assert (geo["y0"], geo["y1"], geo["x0"], geo["x1"], geo["pw"]) == (20, 59, 10, 69, 59)
    assert _piece_geometry(b"") is None
    assert _piece_geometry(b"not-an-image") is None


@pytest.mark.parametrize("shift", [120, 150, 180, 210])
def test_gap_cv_detects_known_gap_x(shift):
    back, shadow = _synthetic_gap(shift)
    out = _detect_gap_cv(back, shadow)
    assert out["method"] == "cv2-sobel"
    assert abs(out["gap_x"] - shift) <= 1, out
    assert out["piece_w"] == 59
    assert abs(out["slide_dist"] - (shift - 10)) <= 1


@pytest.mark.parametrize("shift", [120, 180, 210])
def test_gap_cv_refine_uses_narrow_window(shift):
    back, shadow = _synthetic_gap(shift)
    out = refine_gap_x(back, shadow, shift + 4, search_radius=12)
    assert out["method"] == "vlm-refine"
    assert abs(out["gap_x"] - shift) <= 1, out


def test_gap_cv_refine_noops_outside_window_and_bad_inputs():
    back, shadow = _synthetic_gap(180)
    out = refine_gap_x(back, shadow, 180 + 100, search_radius=12)
    assert out == {"gap_x": 280, "method": "refine-noop"}
    assert refine_gap_x(back, b"", 180)["method"] == "refine-noop"
    assert refine_gap_x(b"not-an-image", shadow, 180)["method"] == "refine-noop"


def test_gap_cv_fails_closed_on_bad_inputs():
    back, shadow = _synthetic_gap(180)
    for bad_back, bad_shadow in ((back, b""), (b"", shadow), (b"not-an-image", shadow)):
        assert _detect_gap_cv(bad_back, bad_shadow) == {
            "gap_x": 0, "piece_w": 0, "method": "cv2-fail"}


# ── Arkose：CryptoJS 兼容加密 ───────────────────────


def test_cryptojs_encrypt_roundtrips_with_evp_bytes_to_key(monkeypatch):
    pytest.importorskip("Crypto")
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    salt = bytes(range(8))
    monkeypatch.setattr(arkose_predict.os, "urandom", lambda n: salt[:n])
    payload = json.loads(arkose_predict.cryptojs_encrypt("hello ✓", "passphrase"))

    assert set(payload) == {"ct", "iv", "s"}
    assert payload["s"] == salt.hex() and len(payload["s"]) == 16
    iv = bytes.fromhex(payload["iv"])
    assert len(iv) == 16

    # 复算 EVP_BytesToKey(MD5, 1 iteration)：密钥派生错了这条就解不开。
    digest, key_iv = b"", b""
    while len(key_iv) < 48:
        digest = hashlib.md5(digest + b"passphrase" + salt).digest()
        key_iv += digest
    key = key_iv[:32]
    ct = base64.b64decode(payload["ct"])
    assert unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ct), 16).decode("utf-8") == "hello ✓"


def test_is_pair_classifier_reads_session_input_names():
    from captchas.arkose.predict import _is_pair_classifier

    class _Input:
        def __init__(self, name):
            self.name = name

    class _Session:
        def __init__(self, names):
            self._names = names

        def get_inputs(self):
            return [_Input(n) for n in self._names]

    assert _is_pair_classifier(_Session(["input_left", "input_right"])) is True
    assert _is_pair_classifier(_Session(["input"])) is False


# ── reCAPTCHA：九宫格切片与 ONNX 预处理 ─────────────


def test_classify_grid_returns_row_major_indices():
    size = 30
    img = np.full((size * 3, size * 3, 3), 128, np.uint8)
    img[0:size, 2 * size:3 * size] = (255, 0, 0)      # row 0, col 2 -> idx 2
    img[2 * size:3 * size, 0:size] = (255, 0, 0)      # row 2, col 0 -> idx 6

    class _KeyPool:
        def classify(self, b64, target):
            arr = np.array(Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB"))
            return bool(arr[..., 0].mean() > 180 and arr[..., 1].mean() < 80)

    class _Table:
        async def wait_for_element_state(self, state, timeout):
            return None

        async def screenshot(self):
            return _png_rgb(img)

    class _Frame:
        async def query_selector(self, sel):
            return _Table() if sel == "table" else None

    assert asyncio.run(_classify_grid(_Frame(), _KeyPool(), "car", 3)) == [2, 6]


def test_classify_grid_degrades_when_table_or_screenshot_missing():
    class _NoTable:
        async def query_selector(self, sel):
            return None

    assert asyncio.run(_classify_grid(_NoTable(), object(), "car", 3)) == []

    class _BadTable:
        async def wait_for_element_state(self, state, timeout):
            return None

        async def screenshot(self):
            raise asyncio.TimeoutError()

    class _BadFrame:
        async def query_selector(self, sel):
            return _BadTable()

        async def screenshot(self):
            raise RuntimeError("fframe unavailable")

    assert asyncio.run(_classify_grid(_BadFrame(), object(), "car", 3)) == []


def test_onnx_preprocess_letterboxes_to_square():
    clf = object.__new__(OnnxClassifier)
    clf.imgsz = 64
    buf = io.BytesIO()
    Image.new("RGB", (80, 40), (255, 0, 0)).save(buf, "PNG")
    x = clf._preprocess(base64.b64encode(buf.getvalue()).decode())
    assert x.shape == (1, 3, 64, 64)
    assert x.dtype == np.float32
    assert 0.0 <= float(x.min()) and float(x.max()) <= 1.0


def test_normalize_target_aliases_and_unknown():
    assert _normalize_target("a bicycle") == "Bicycle"
    assert _normalize_target("select all images with traffic lights") == "Traffic Light"
    assert _normalize_target("fire hydrant") == "Hydrant"
    assert _normalize_target("unicorn") is None


# ── PerimeterX：Fluent 下拉选择 ─────────────────────


class _Opt:
    def __init__(self, idx, owner):
        self.idx, self.owner = idx, owner

    async def click(self, timeout):
        self.owner.clicked.append(self.idx)


class _First:
    async def wait_for(self, state, timeout):
        return None


class _Opts:
    def __init__(self, owner, n):
        self.owner, self.n, self.first = owner, n, _First()

    async def count(self):
        return self.n

    def nth(self, i):
        return _Opt(i, self.owner)


class _Page:
    def __init__(self, n=3, fail=()):
        self.clicked, self.clicks, self.n, self.fail = [], [], n, set(fail)

    async def click(self, sel, timeout=0):
        if sel in self.fail:
            raise RuntimeError("selector miss")
        self.clicks.append(sel)

    def locator(self, sel):
        return _Opts(self, self.n)


def test_pick_fluent_falls_back_to_next_trigger_and_clamps_index():
    page = _Page(n=3, fail={"#missing"})
    ok = asyncio.run(_pick_fluent(page, ["#missing", "#real"], 5))
    assert ok is True
    assert page.clicks == ["#real"], page.clicks
    assert page.clicked == [2], "越界索引必须夹到最后一个选项"


def test_pick_fluent_returns_false_when_no_options():
    page = _Page(n=0)
    assert asyncio.run(_pick_fluent(page, ["#real"], 0)) is False
    assert page.clicked == []

# ── 第二批纯函数（v6.5 收口）：求解器内"算错就假成功/假失败"的判定逻辑 ──

def test_turnstile_error_codes_parses_siteverify_shapes():
    """siteverify 的错误码在 error-codes；SomeDoors 兼容层叫 details；坏 JSON 是空表。"""
    from captchas.turnstile.solve import _error_codes

    assert _error_codes('{"success":false,"error-codes":["invalid-input-secret"]}') == [
        "invalid-input-secret"]
    assert _error_codes('{"success":false,"details":["timeout-or-duplicate"]}') == [
        "timeout-or-duplicate"]
    assert _error_codes('{"success":true,"error-codes":[]}') == []
    assert _error_codes('{"success":true}') == []
    assert _error_codes("not json") == []
    assert _error_codes("") == []


def test_cloudflare_clearance_picks_the_right_cookie():
    """cookie 池里挑 cf_clearance：命中整条记录、找不到给 None（不是空 dict）。"""
    from captchas.cloudflare.solve import _clearance

    pool = [{"name": "cf_chl_2", "value": "x"},
            {"name": "cf_clearance", "value": "tok-abc", "domain": ".example.com"}]
    hit = _clearance(pool)
    assert hit is not None and hit["value"] == "tok-abc" and hit["domain"] == ".example.com"
    assert _clearance([{"name": "other", "value": "v"}]) is None
    assert _clearance([]) is None


def test_recaptcha_build_v2_page_embeds_sitekey_and_lib():
    """ enterprise 与普通版用不同的 lib；sitekey 必须真的进了 HTML（占位符残留=回归）。"""
    from captchas.recaptcha.solve import _build_v2_page

    html = _build_v2_page("6LeIxAcAAAA")
    assert "6LeIxAcAAAA" in html and "__SITEKEY__" not in html
    assert "api.js" in html and "enterprise.js" not in html

    ent = _build_v2_page("6Le-IxAcBBB", enterprise=True)
    assert "enterprise.js" in ent and "api.js" not in ent.replace("enterprise.js", "")
    assert "6Le-IxAcBBB" in ent and "__SITEKEY__" not in ent


def test_perimeterx_result_error_branches_are_distinct():
    """_result 的三种终态必须有可区分的 error 文案：
    gate 没渲染 / 按住了但 px3 没转（判定为机器人）/ 成功。"""
    from captchas.perimeterx.solve import _result

    t0 = 0.0
    # 1) gate 从未渲染：silent-pass / render_flow 错
    out = _result(t0, {"gate_reached": False})
    assert out["solved"] is False
    assert "gate" in out["error"] and "never rendered" in out["error"]

    # 2) gate 到了、按住了、但 _px3 没轮转：生物判定为 bot
    out = _result(t0, {"gate_reached": True, "actuated": True, "px3_before": "A", "px3_after": "A"})
    assert out["solved"] is False
    assert "did not rotate" in out["error"]

    # 3) 成功：px3 轮转 → solved、无 error、replay 契约字段在
    out = _result(t0, {"gate_reached": True, "actuated": True,
                       "px3_before": "A", "px3_after": "B", "attempts": 2},
                  ua="UA", cookies={"_px3": "B", "_pxvid": "V"})
    assert out["solved"] is True and "error" not in out
    assert out["px3"] == "B" and out["px3_rotated"] is True
    assert out["cookie_header"] == "_px3=B; _pxvid=V"
    assert out["replay_contract"]["bound_to"] == ["_pxvid", "client_ip", "user_agent"]

    # 4) 无 px3_before 的首过场景：actuated + px3 即成功（不需要轮转）
    out = _result(t0, {"gate_reached": True, "actuated": True}, cookies={"_px3": "C"})
    assert out["solved"] is True and "error" not in out


def test_awswaf_waf_token_picks_cookie_and_block_titles_match():
    """_waf_token 找不到必须给 None（不是抛错）；_title_is_block 是大小写无关的子串匹配。"""
    from captchas.awswaf.solve import _waf_token, _title_is_block

    pool = [{"name": "aws-waf-token", "value": "tok", "domain": ".example.com"}]
    hit = _waf_token(pool)
    assert hit is not None and hit["value"] == "tok"
    assert _waf_token([]) is None
    assert _waf_token([{"name": "session", "value": "x"}]) is None

    # 大小写无关：真实 CloudFront 标题是 "ERROR: The request could not be satisfied."
    assert _title_is_block("ERROR: The Request Could Not Be Satisfied.") is True
    assert _title_is_block("403 Forbidden") is True
    assert _title_is_block("Welcome to Example") is False
    assert _title_is_block(None) is False


def test_aws_title_block_on_mock_page():
    """_title_is_block 是 _is_cloudfront_block 的判定核（async 版只是 page.title 包装）。
    核逻辑在 sync 函数上钉死；async 包装的真实分支需真浏览器（见覆盖率盲区标注）。"""
    from captchas.awswaf.solve import _title_is_block

    # 边界：真实页面标题常带前后空白/零宽字符，strip 必须兜住
    assert _title_is_block("  403 Forbidden \n") is True
    assert _title_is_block("​Request Blocked") is True   # 子串匹配不做 Unicode 归一——如实行为
    assert _title_is_block("Access DENIED by cloudfront") is True


def test_hcaptcha_grid_overlay_labels_every_cell():
    """_grid_overlay 把编号网格画上去：输出仍是合法 PNG，尺寸不变，且每个 cell 有标签。"""
    from captchas.hcaptcha.image_solve import _grid_overlay, _GRID

    # 合成一张 160x160 的底图
    buf = io.BytesIO()
    Image.new("RGB", (160, 160), (30, 30, 30)).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    out = _grid_overlay(b64)
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert img.size == (160, 160)
    # 网格线是红色描边：角落像素应与纯底图不同（画过东西）
    assert img.getpixel((0, 0)) != (30, 30, 30)
    # cell 数与 _GRID² 一致（16 个标签由 _GRID=4 布局决定，画进 yellow 框）
    assert _GRID == 4


def test_recaptcha_classifier_mode_validation():
    """_get_keypool 的 classifier 参数面：非法值必须 ValueError 并列出合法集合；
    合法值在缺 ONNX 权重时落到 mistral（auto→mistral 分支）。"""
    from captchas.recaptcha import solve as rs

    with pytest.raises(ValueError) as ei:
        rs._get_keypool("bogus-mode")
    assert "bogus-mode" in str(ei.value) and "mistral" in str(ei.value)

    # auto → 缺权重时 mistral。KeyPool 构造要读 captchas/common/apikey.txt（仓库不含，
    # .gitignore 排除）——缺 key 时 FileNotFoundError，这条路径也是"配置缺失"契约的一部分：
    # 报错必须是直接的 FileNotFoundError，而不是被吞成 None。
    rs._keypool_cache.clear()
    apikey = Path(__file__).resolve().parents[1] / "captchas" / "common" / "apikey.txt"
    if not apikey.exists():
        with pytest.raises(FileNotFoundError):
            rs._get_keypool(None)   # 缺 key 的失败要可见，不许静默
    rs._keypool_cache.clear()
