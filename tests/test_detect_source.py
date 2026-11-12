"""T-77：detect 特征表的「新鲜度」元数据 + fixture 矩阵 + 生成器 --check 模式。

跑法：python -m pytest tests/test_detect_source.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))

import detect  # noqa: E402

CASES_PATH = ROOT / "tests" / "fixtures" / "detect" / "cases.json"
GEN = ROOT / "scripts" / "gen_detect_signatures.py"


# ── 1. 元数据存在且 count 与表长一致 ──────────────────
def test_signatures_source_recorded():
    meta = getattr(detect, "SIGNATURES_SOURCE", None)
    assert isinstance(meta, dict), "detect.py 必须有 SIGNATURES_SOURCE 常量（特征表从哪生成的说不清=新鲜度不可判断）"
    for key in ("origin", "upstream_consulted", "commit", "generated_at", "count"):
        assert key in meta, f"SIGNATURES_SOURCE 缺 {key}"
    assert "own-curated" in meta["origin"], "origin 必须声明本表是自有整理（不是上游数据）"
    assert "Antibot-Detector" in meta["upstream_consulted"], "upstream_consulted 应记录对照来源"
    assert "NPOSL-3.0" in meta["upstream_consulted"], "必须写明上游是 NPOSL-3.0，不能冒充 MIT"
    assert meta["commit"], "commit 不可为空（未知也必须是 'n/a'，不能撒谎成假 sha）"
    assert meta["generated_at"], "generated_at 不可为空"
    assert meta["count"] == len(detect.SIGNATURES), (
        f"count={meta['count']} 与表长 {len(detect.SIGNATURES)} 不一致——元数据说谎比没有更糟"
    )


# ── 2. fixture 矩阵把 recommend() 分类结论固化成断言 ──
def test_fixture_matrix_classifies_each_vendor():
    """复用 tests/fixtures/detect/cases.json：每类厂商的 route 结论不许漂。

    与 test_detect_golden.py 的差别：这条按**厂商类**聚合断言——
    只要某类厂商（CF/Akamai/AWS WAF/DataDome/无盾）的推荐路由变了，立刻红。
    """
    with CASES_PATH.open(encoding="utf-8") as fh:
        cases = json.load(fh)["cases"]
    assert cases, "fixture 矩阵为空=形同虚设"

    seen_vendors: set[str] = set()
    for case in cases:
        inp = case["input"]
        detections = detect.detect(
            status=inp.get("status", 0), headers=inp.get("headers"),
            body=inp.get("body", ""), url=inp.get("url", ""), cookies=inp.get("cookies"),
        )
        rec = detect.recommend(detections, detect.cf_challenge_type(
            inp.get("body", ""), inp.get("headers"), inp.get("url", "")))
        assert rec["route"] == case["expect"]["route"], (
            f"[{case['name']}] route 从 {case['expect']['route']} 漂到 {rec['route']}"
        )
        if case["expect"]["detections"]:
            seen_vendors.add(case["expect"]["detections"][0][0])

    # 每类厂商至少 1 个样本：这是「矩阵」的最低要求
    assert {"detect-cloudflare", "detect-akamai", "detect-aws-waf",
            "detect-datadome"} <= seen_vendors, f"矩阵缺厂商样本：{seen_vendors}"


# ── 3. 生成器 --check：上游缺失必须 SKIP（不许静默通过）──
def _run_gen(*args: str) -> tuple[int, str]:
    # 子进程必须显式跑在 UTF-8 上：Windows 上 Python 的 stdout 默认跟随**控制台代码页**
    # （本机 cp936），生成器打的中文会以 GBK 字节写出，这里按 utf-8 解码就是一堆 U+FFFD —
    # 于是"找『待验证』"这类断言的失败与生成器本身无关，只与跑测试的机器有关。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, str(GEN), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        cwd=str(ROOT), env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_gen_check_mode_reports_skip_when_upstream_missing():
    rc, out = _run_gen("--check", str(ROOT / "tests" / "fixtures" / "no-such-upstream-dir"))
    assert rc == 0, f"上游不在时应 skip 而非报错。输出：{out}"
    assert "SKIP" in out, f"必须显式报告 SKIP，不许静默通过。输出：{out}"
    assert "待验证" in out, f"skip 必须标注「待验证」——CI 绿不等于表还是新鲜的。输出：{out}"


def test_gen_check_mode_fails_on_mismatch():
    """伪造一个只含 1 个假厂商的上游 → 重新生成的表必然与当前 33 家不一致 → exit 1。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        det_dir = Path(tmp) / "detectors" / "fake"
        det_dir.mkdir(parents=True)
        (det_dir / "fake.json").write_text(json.dumps({
            "id": "detect-fake", "name": "Fake WAF", "category": "Anti-Bot",
            "difficulty": "Medium",
            "detection": {"cookie": [{"name": "fake_cookie", "confidence": 100}]},
        }), encoding="utf-8", newline="\n")
        rc, out = _run_gen("--check", tmp)
        assert rc == 1, f"表与上游不一致必须 exit 1。输出：{out}"
        assert "不一致" in out or "diff" in out.lower(), f"应说明不一致。输出：{out}"


def test_gen_check_without_path_is_skip_not_pass():
    """--check 不带上游路径：同样必须 SKIP + 待验证，不能当「检查通过」混过去。"""
    rc, out = _run_gen("--check")
    assert rc == 0 and "SKIP" in out and "待验证" in out, f"输出：{out}"


# ── 4. 本地表自检：CI 没有上游 checkout 时也有东西可守 ──
def _load_gen():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_detect_signatures", GEN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_detect_signatures"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_local_problems_accepts_current_table():
    gen = _load_gen()
    assert gen._local_problems() == [], gen._local_problems()


DETECT = ROOT / "solver" / "detect.py"


def test_local_problems_flags_duplicate_ids_and_bad_confidence(tmp_path):
    gen = _load_gen()
    source = DETECT.read_text(encoding="utf-8")
    broken = source.replace("'id': 'detect-aws-waf'", "'id': 'detect-akamai'", 1)
    broken = broken.replace("('cookie', '_abck', 100)", "('cookie', '_abck', 180)", 1)
    path = tmp_path / "detect.py"
    path.write_text(broken, encoding="utf-8", newline="\n")
    problems = gen._local_problems(str(path))
    assert any("id 重复" in p for p in problems), problems
    assert any("confidence 越界" in p for p in problems), problems


def test_local_problems_flags_metadata_count_and_bad_regex(tmp_path):
    gen = _load_gen()
    source = DETECT.read_text(encoding="utf-8")
    broken = source.replace('"count": 34,', '"count": 33,', 1)
    broken = broken.replace(r'^x-[a-z0-9]{8}-[a-d]$', r'^x-[a-z', 1)
    path = tmp_path / "detect.py"
    path.write_text(broken, encoding="utf-8", newline="\n")
    problems = gen._local_problems(str(path))
    assert any("count=33" in p for p in problems), problems
    assert any("正则非法" in p for p in problems), problems


def test_check_runs_local_integrity_before_skip():
    """上游缺失时也必须先打「本地一致」——本地校验不是被 SKIP 跳过的附赠品。"""
    rc, out = _run_gen("--check")
    assert rc == 0 and "本地一致" in out, out
