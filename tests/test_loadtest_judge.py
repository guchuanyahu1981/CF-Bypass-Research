"""压测 verdict 口径（P2-12）：错误率必须参与判定，不能只看 QPS。"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "loadtest.py"
    spec = importlib.util.spec_from_file_location("loadtest_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hot_zero_errors_passes():
    mod = _load()
    passed, reasons = mod.judge("hot", {"qps": 100.0, "requests": 1000, "errors": 0})
    assert passed and reasons == []


def test_hot_all_errors_fails_even_with_high_qps():
    mod = _load()
    passed, reasons = mod.judge("hot", {"qps": 9999.0, "requests": 1000, "errors": 1000})
    assert not passed
    assert any("error_ratio" in r for r in reasons), reasons


def test_cold_solve_amplification_fails():
    mod = _load()
    passed, reasons = mod.judge("cold", {
        "accepted_ratio": 1.0, "solves_per_request": 2.0, "requests": 10, "errors": 0,
    })
    assert not passed
    assert any("solves_per_request" in r for r in reasons), reasons


def test_share_failure_count_fails():
    mod = _load()
    passed, reasons = mod.judge("share", {
        "sequential": {"solves": 1, "expected_solves": 1, "failures": 1},
    })
    assert not passed
    assert any("failures" in r for r in reasons), reasons
