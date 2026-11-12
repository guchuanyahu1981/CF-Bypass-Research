#!/usr/bin/env python3
"""四类验收汇总：UI / 真实调用 / 真实 E2E / 独立代码复审 → 一份 acceptance.json。

为什么要这个脚本：四个验收脚本各自能跑，但它们的结果是四份互不相干的输出。
"四类都做了"不能靠人记得看四遍 —— 缺一份、失败一份、复审说 FAIL，都必须让
`python run.py accept` 的退出码变红，并在同一份证据里说明缺的是哪一类。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        return {"_missing": f"{type(e).__name__}: {e}"}
    except json.JSONDecodeError as e:
        return {"_missing": f"JSONDecodeError: {e}"}


def _checks_summary(data: dict) -> dict:
    checks = data.get("checks")
    if not isinstance(checks, list):
        checks = data.get("tests") if isinstance(data.get("tests"), list) else []
    total = len(checks)
    failed = sum(1 for c in checks if not c.get("ok", c.get("status") == "pass"))
    return {"total": total, "failed": failed, "passed": total - failed,
            "missing": data.get("_missing", "")}


def _review_summary(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"status": "missing", "verdict": "", "path": path,
                "error": f"{type(e).__name__}: {e}"}
    if "skipped: true" in text:
        return {"status": "skipped", "verdict": "", "path": path,
                "note": "代码复审轨道被 --skip-review 跳过；overall 只能给 partial"}
    matches = re.findall(r"VERDICT:\s*(PASS|FAIL)", text, re.I)
    verdict = matches[-1].upper() if matches else ""
    return {"status": "ok" if verdict else "invalid-format",
            "verdict": verdict, "path": path,
            "bytes": len(text.encode("utf-8")),
            "p0_mentions": len(re.findall(r"\bP0\b", text)),
            "p1_mentions": len(re.findall(r"\bP1\b", text)),
            "p2_mentions": len(re.findall(r"\bP2\b", text))}


def main() -> int:
    ap = argparse.ArgumentParser(description="四类验收汇总")
    ap.add_argument("--ui", default="evidence/ui-acceptance.json")
    ap.add_argument("--realcalls", default="evidence/realcalls.json")
    ap.add_argument("--e2e", default="evidence/e2e-local.json")
    ap.add_argument("--review", default="evidence/code-review.md")
    ap.add_argument("--ui-skipped", default="", help="accept 传来的 UI 轨道跳过标记（true/false）")
    ap.add_argument("--out", default="evidence/acceptance.json")
    args = ap.parse_args()

    ui = _load(args.ui)
    if (args.ui_skipped or "").strip().lower() == "true":
        ui = {"_missing": "", "skipped": True,
              "reason": "accept --skip-browser：UI 轨道被跳过"}
    rc = _load(args.realcalls)
    e2e = _load(args.e2e)
    review = _review_summary(args.review)

    ui_s = _checks_summary(ui)
    rc_s = _checks_summary(rc)
    e2e_s = _checks_summary(e2e)
    if e2e.get("result") == "failed" or e2e.get("status") == "failed":
        e2e_s["failed"] = max(e2e_s["failed"], 1)
    axe_violations = sum(len(x.get("violations") or []) for x in (ui.get("axe") or []))
    ui_s["axe_violations"] = axe_violations

    tracks = {
        "ui": {**ui_s, "screenshots": len(ui.get("screenshots") or []),
               "path": args.ui},
        "realcalls": {**rc_s, "path": args.realcalls},
        "e2e": {**e2e_s, "path": args.e2e},
        "code_review": review,
    }

    ui_skipped = bool(ui.get("skipped"))
    ui_ok = (not ui_skipped) and not ui_s.get("missing") and ui_s["total"] > 0 \
        and ui_s["failed"] == 0 and axe_violations == 0
    rc_ok = not rc_s.get("missing") and rc_s["total"] > 0 and rc_s["failed"] == 0
    e2e_ok = not e2e_s.get("missing") and e2e_s["total"] > 0 and e2e_s["failed"] == 0
    review_ok = review.get("verdict") == "PASS"

    if all((ui_ok, rc_ok, e2e_ok, review_ok)):
        overall = "passed"
    elif review.get("status") in ("missing", "skipped") or ui_skipped:
        # 复审/UI 轨道缺或被跳过：其余轨全绿也只能算 partial，不能冒充完整验收。
        overall = "partial"
    else:
        overall = "failed"

    payload = {
        "schema_version": 1,
        "overall": overall,
        "tracks": tracks,
        "ok": {"ui": ui_ok, "realcalls": rc_ok, "e2e": e2e_ok, "code_review": review_ok},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    print(f"\n=== 四类验收汇总：{overall} ===")
    print(f"  UI          {ui_s['passed']}/{ui_s['total']}  failed={ui_s['failed']}  "
          f"axe={axe_violations}  shots={tracks['ui']['screenshots']}")
    print(f"  真实调用    {rc_s['passed']}/{rc_s['total']}  failed={rc_s['failed']}")
    print(f"  真实 E2E    {e2e_s['passed']}/{e2e_s['total']}  failed={e2e_s['failed']}")
    print(f"  代码复审    {review.get('verdict') or review.get('status')}")
    print(f"证据：{out}")
    return 0 if overall == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())