"""四类验收汇总脚本（scripts/acceptance_report.py）的契约测试。

验收汇总本身也是代码：缺一份、失败一份、复审 FAIL，必须让 `run.py accept` 变红。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance_report.py"


def _write(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _track(n: int, ok: bool = True) -> dict:
    return {"checks": [{"name": f"c{i}", "ok": ok, "detail": ""} for i in range(n)]}


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=30)


def test_acceptance_report_passes_when_all_four_tracks_pass(tmp_path):
    ui = _write(tmp_path / "ui.json", {**_track(3), "axe": [{"violations": []}],
                                       "screenshots": ["a.png", "b.png"]})
    rc = _write(tmp_path / "rc.json", _track(4))
    e2e = _write(tmp_path / "e2e.json", {**_track(5), "result": "passed"})
    review = tmp_path / "review.md"
    review.write_text("P0: none\nP1: none\nP2: none\nVERDICT: PASS\n", encoding="utf-8")
    out = tmp_path / "acceptance.json"
    proc = _run(tmp_path, "--ui", ui, "--realcalls", rc, "--e2e", e2e,
                "--review", str(review), "--out", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "passed"
    assert data["tracks"]["ui"]["axe_violations"] == 0
    assert data["tracks"]["code_review"]["verdict"] == "PASS"


def test_acceptance_report_fails_when_review_fails_or_missing(tmp_path):
    ui = _write(tmp_path / "ui.json", {**_track(1), "axe": []})
    rc = _write(tmp_path / "rc.json", _track(1))
    e2e = _write(tmp_path / "e2e.json", {**_track(1), "result": "passed"})
    out = tmp_path / "acceptance.json"

    review = tmp_path / "review.md"
    review.write_text("P1 | a.py:1 | bug | crash | fix\nVERDICT: FAIL\n", encoding="utf-8")
    proc = _run(tmp_path, "--ui", ui, "--realcalls", rc, "--e2e", e2e,
                "--review", str(review), "--out", str(out))
    assert proc.returncode == 1
    assert json.loads(out.read_text(encoding="utf-8"))["overall"] == "failed"

    proc = _run(tmp_path, "--ui", ui, "--realcalls", rc, "--e2e", e2e,
                "--review", str(tmp_path / "missing.md"), "--out", str(out))
    assert proc.returncode == 1
    assert json.loads(out.read_text(encoding="utf-8"))["overall"] == "partial"


def test_acceptance_report_fails_when_axe_has_violations(tmp_path):
    ui = _write(tmp_path / "ui.json", {**_track(1), "axe": [{"violations": [{"id": "contrast"}]}]})
    rc = _write(tmp_path / "rc.json", _track(1))
    e2e = _write(tmp_path / "e2e.json", {**_track(1), "result": "passed"})
    review = tmp_path / "review.md"
    review.write_text("VERDICT: PASS\n", encoding="utf-8")
    proc = _run(tmp_path, "--ui", ui, "--realcalls", rc, "--e2e", e2e,
                "--review", str(review), "--out", str(tmp_path / "a.json"))
    assert proc.returncode == 1

def test_acceptance_report_partial_when_review_skipped(tmp_path):
    ui = _write(tmp_path / "ui.json", {**_track(1), "axe": []})
    rc = _write(tmp_path / "rc.json", _track(1))
    e2e = _write(tmp_path / "e2e.json", {**_track(1), "result": "passed"})
    review = tmp_path / "code-review-SKIPPED.md"
    review.write_text("# 独立代码复审\n\n- skipped: true\n- reason: accept --skip-review\n",
                      encoding="utf-8")
    out = tmp_path / "a.json"
    proc = _run(tmp_path, "--ui", ui, "--realcalls", rc, "--e2e", e2e,
                "--review", str(review), "--out", str(out))
    assert proc.returncode == 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "partial"
    assert data["tracks"]["code_review"]["status"] == "skipped"


def test_acceptance_report_takes_last_verdict(tmp_path):
    ui = _write(tmp_path / "ui.json", {**_track(1), "axe": []})
    rc = _write(tmp_path / "rc.json", _track(1))
    e2e = _write(tmp_path / "e2e.json", {**_track(1), "result": "passed"})
    review = tmp_path / "review.md"
    review.write_text("VERDICT: PASS\n中间又写了一句 VERDICT: FAIL\nVERDICT: PASS\n",
                      encoding="utf-8")
    out = tmp_path / "a.json"
    proc = _run(tmp_path, "--ui", ui, "--realcalls", rc, "--e2e", e2e,
                "--review", str(review), "--out", str(out))
    assert proc.returncode == 0, proc.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["overall"] == "passed"


def test_acceptance_report_partial_when_ui_skipped_flag(tmp_path):
    ui = _write(tmp_path / "ui.json", {**_track(1), "axe": []})
    rc = _write(tmp_path / "rc.json", _track(1))
    e2e = _write(tmp_path / "e2e.json", {**_track(1), "result": "passed"})
    review = tmp_path / "review.md"
    review.write_text("VERDICT: PASS\n", encoding="utf-8")
    out = tmp_path / "a.json"
    proc = _run(tmp_path, "--ui", ui, "--realcalls", rc, "--e2e", e2e,
                "--review", str(review), "--ui-skipped", "true", "--out", str(out))
    assert proc.returncode == 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "partial"
