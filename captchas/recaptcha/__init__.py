"""reCAPTCHA solver package — v3, invisible, and v2 checkbox."""
from __future__ import annotations

#: 包级导出。**惰性**：`import captchas.hcaptcha.image_solve` 不该因为这里
#: 而把 `solve.py`（进而 `cloakbrowser`）拖进来 —— 否则离线可测的纯逻辑
#: （图像预处理 / ONNX 前后处理）连导入都做不到，覆盖率永远是 0。
_EXPORTS = (
    "solve_recaptcha_v2",
    "solve_recaptcha_v2_realpage",
    "solve_recaptcha_v3",
    "solve_recaptcha_v3_realpage",
    "solve_recaptcha_invisible",
    "solve_recaptcha_invisible_realpage",
)


def __getattr__(name: str):
    if name in _EXPORTS:
        from . import solve as _solve
        return getattr(_solve, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_EXPORTS))
