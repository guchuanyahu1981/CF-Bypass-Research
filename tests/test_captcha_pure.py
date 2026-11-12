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