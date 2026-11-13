"""cfsolve 装配向导（T-07）——回答"这台机器离纯协议路线还差哪几步"。

定位（刻意保守）：
  · **只诊断，不代劳**。构建需要 vcvars64 环境变量与网络拉 crate，自动化脚本在
    别人的机器上大概率失败；向导的职责是消歧——把"需要读三份文档才能拼出来的
    构建流程"变成"一条命令告诉你缺什么、下一步跑什么"。
  · 全程只读（cargo --version 这类探测命令也不改任何东西），不出站。
  · 产物与配置的判据和 doctor 保持一致：CFSOLVE_BIN + CFSOLVE_PROFILE 都在，
    doctor 才报"已配置"。

用法：
    python run.py cfsolve-setup              # 全量诊断
    python run.py cfsolve-setup --repo D:\\cfsolve-upstream   # 仓库不在默认位置时
    python run.py cfsolve-setup --json       # 机器可读

退出码：0=可构建（或已配置） / 1=缺工具链 / 2=仓库缺失
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK = "ok"
WARN = "warn"
MISS = "miss"


def _run_probe(cmd: list[str]) -> tuple[bool, str]:
    """探测一个工具链命令是否可执行，返回 (存在, 版本行)。只读探测。

    cl.exe 无参数时在中文系统上输出 GBK——不用 text=True 让 Python 按 locale 解码
    会被 errors=replace 打成乱码；这里拿 bytes 后按 GBK 兜底解。"""
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=15)
        raw = (out.stdout or out.stderr or b"").strip()
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
        first = text.splitlines()
        return out.returncode == 0 or bool(raw), (first[0] if first else "")
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def check_toolchain() -> list[dict]:
    """逐项探测构建工具链；缺哪个说哪个，给安装指向。"""
    checks: list[dict] = []

    ok, ver = _run_probe(["cargo", "--version"])
    checks.append({"name": "Rust 工具链 (cargo)", "level": OK if ok else MISS,
                   "detail": ver or "未找到",
                   "hint": "" if ok else "https://rustup.rs；cargo 不在 PATH 时在开发者命令行里跑"})

    # MSVC：cl.exe 在 PATH（vcvars64 环境）里才算数；只在系统 PATH 找 link.exe 不够准。
    ok, ver = _run_probe(["cl"])
    if not ok:
        # 给出"怎么进 vcvars64"的提示，而不是替用户进。
        checks.append({"name": "MSVC (cl.exe)", "level": MISS, "detail": "PATH 里没有 cl.exe",
                       "hint": "开 \"x64 Native Tools Command Prompt for VS\"（vcvars64.bat）再跑本向导"})
    else:
        first = ver.splitlines()[0] if ver else ""
        checks.append({"name": "MSVC (cl.exe)", "level": OK, "detail": first, "hint": ""})

    ok, ver = _run_probe(["cmake", "--version"])
    checks.append({"name": "CMake", "level": OK if ok else MISS,
                   "detail": (ver or "").splitlines()[0] if ver else "未找到",
                   "hint": "" if ok else "https://cmake.org/download/ 或 VS Installer 勾选 C++ CMake tools"})

    ok, ver = _run_probe(["nasm", "-v"])
    checks.append({"name": "NASM", "level": OK if ok else MISS,
                   "detail": ver or "未找到",
                   "hint": "" if ok else "https://www.nasm.us/；装完把目录加进 PATH"})

    # libclang：优先看 pip 的 clang 包自带（tools/cfsolve-patches/README 的做法），
    # 其次看系统 LLVM。
    libclang = None
    try:  # noqa: PLC0415 — 诊断工具里延迟导入
        import clang  # type: ignore
        native = Path(clang.__file__).parent / "native"
        if (native / "libclang.dll").is_file() or (native / "libclang.dylib").is_file():
            libclang = f"pip clang: {native}"
    except ImportError:
        pass
    if libclang is None and shutil.which("clang"):
        libclang = "系统 LLVM (clang 在 PATH)"
    if libclang is None:
        checks.append({"name": "libclang", "level": MISS, "detail": "未找到",
                       "hint": "pip install libclang（然后设 LIBCLANG_PATH=<site-packages>\\clang\\native），"
                               "或安装 LLVM 到 C:\\Program Files\\LLVM"})
    else:
        checks.append({"name": "libclang", "level": OK, "detail": libclang, "hint": ""})

    return checks


def check_repo(repo_arg: str) -> dict:
    """cfsolve 仓库 + 本地补丁可应用性。repo 缺失只 WARN（用户可能只想看产物状态）。"""
    repo = Path(repo_arg) if repo_arg else Path(os.environ.get("CFSOLVE_REPO", ""))
    result: dict = {"repo": str(repo), "exists": False, "patch": None, "artifacts": []}
    if not repo_arg and not str(repo):
        repo = Path.home() / "cfsolve-upstream"  # 纯协议文档的既定位置之一
    result["repo"] = str(repo)
    if not repo.is_dir():
        result["patch"] = {"level": WARN, "detail": "仓库不存在（--repo 指定或先 clone）",
                           "hint": "git clone <cfsolve-upstream> " + str(repo)}
        return result
    result["exists"] = True

    # 补丁可应用性：git apply --check 是只读操作。
    patches = sorted((ROOT / "tools" / "cfsolve-patches").glob("*.patch"))
    if patches:
        for p in patches:
            try:
                rc = subprocess.run(["git", "apply", "--check", str(p)], cwd=repo,
                                    capture_output=True, text=True, timeout=15).returncode
                state = "可干净应用" if rc == 0 else "冲突/已应用"
            except (OSError, subprocess.TimeoutExpired):
                state = "git 不可用，跳过检查"
            result["patch"] = {"level": OK if "可干净" in state else WARN,
                               "detail": f"{p.name}: {state}", "hint": ""}
    else:
        result["patch"] = {"level": WARN, "detail": "tools/cfsolve-patches/ 无 .patch 文件", "hint": ""}

    # 已构建产物（target/release/）
    for name in ("challenge.exe", "challenge", "capture-proxy.exe", "capture-proxy"):
        p = repo / "target" / "release" / name
        if p.is_file():
            result["artifacts"].append(str(p))
    return result


def check_service_config() -> dict:
    """服务侧配置（与 doctor 同判据）：CFSOLVE_BIN + PROFILE 是否都已就位。"""
    bin_path = (os.environ.get("CFSOLVE_BIN") or "").strip()
    prof = (os.environ.get("CFSOLVE_PROFILE") or "").strip()
    configured = bool(bin_path and prof and Path(bin_path).is_file() and Path(prof).is_file())
    detail = f"BIN={bin_path or '—'} / PROFILE={prof or '—'}"
    if configured:
        return {"level": OK, "detail": detail, "hint": ""}
    missing = []
    if not bin_path or not Path(bin_path).is_file():
        missing.append("CFSOLVE_BIN")
    if not prof or not Path(prof).is_file():
        missing.append("CFSOLVE_PROFILE")
    return {"level": WARN, "detail": detail, "hint":
            f"设 {' 与 '.join(missing)}（或 --cfsolve-bin/--cfsolve-profile）；见 docs/纯协议-cfsolve.md"}


def next_steps(toolchain: list[dict], repo: dict, config: dict) -> list[str]:
    """按依赖顺序输出"下一步跑什么"，不代劳。"""
    steps = []
    miss = [c for c in toolchain if c["level"] == MISS]
    if miss:
        steps.append("1) 在 x64 Native Tools Command Prompt 里补齐缺项：" +
                     "、".join(c["name"].split(" (")[0] for c in miss))
        steps.append("2) 重跑 `python run.py cfsolve-setup` 确认工具链全绿")
        return steps
    if not repo["exists"]:
        steps.append("1) clone cfsolve 上游到本机（git clone，位置随意）")
        steps.append("2) 重跑 `python run.py cfsolve-setup --repo <路径>`")
        return steps
    steps.append("1) 应用本地补丁：在仓库目录 git apply tools/cfsolve-patches/*.patch")
    if not repo["artifacts"]:
        steps.append("2) 在 vcvars64 环境构建：cargo build --release --bin challenge"
                     " && cargo build --release --bin capture-proxy")
    else:
        steps.append("2) 产物已存在：" + "、".join(Path(a).name for a in repo["artifacts"]))
    if config["level"] != OK:
        steps.append("3) 设 CFSOLVE_BIN=<challenge 产物> 与 CFSOLVE_PROFILE=<profiles/*.json>，"
                     "然后 `python run.py doctor` 应显示 cfsolve 已配置")
    else:
        steps.append("3) 服务配置已就位；doctor 会报 cfsolve 已配置")
    return steps


def gather(repo_arg: str = "") -> dict:
    """聚合全部诊断；纯函数，tests 可直接调。"""
    toolchain = check_toolchain()
    repo = check_repo(repo_arg)
    config = check_service_config()
    return {"toolchain": toolchain, "repo": repo, "config": config,
            "steps": next_steps(toolchain, repo, config)}


def _force_utf8_stdout() -> None:
    """GBK 控制台上 ✓/✗ 会 UnicodeEncodeError（doctor.py 同款处理）。"""
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="cfsolve 纯协议路线装配向导（只诊断，不代劳）")
    ap.add_argument("--repo", default="", help="cfsolve 上游仓库路径（默认 CFSOLVE_REPO 或 ~/cfsolve-upstream）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args(argv)

    report = gather(a.repo)

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("== cfsolve 装配向导（只诊断，不执行任何构建）==")
        icon = {OK: "✓", WARN: "△", MISS: "✗"}
        for c in report["toolchain"]:
            print(f"  {icon[c['level']]} {c['name']}: {c['detail']}")
            if c["hint"]:
                print(f"      {c['hint']}")
        r = report["repo"]
        print(f"  {icon[OK] if r['exists'] else icon[WARN]} cfsolve 仓库: {r['repo']}"
              + ("（存在）" if r["exists"] else "（不存在）"))
        if r.get("patch"):
            print(f"      补丁: {r['patch']['detail']}")
        if r["artifacts"]:
            print(f"      产物: {', '.join(r['artifacts'])}")
        cfg = report["config"]
        print(f"  {icon[cfg['level']]} 服务配置: {cfg['detail']}")
        if cfg["hint"]:
            print(f"      {cfg['hint']}")
        print()
        print("下一步（向导不替你执行）：")
        for s in report["steps"]:
            print(f"  {s}")

    # 退出码：配置已就位=0；仓库缺失=2；缺工具链=1
    if report["config"]["level"] == OK:
        return 0
    if not report["repo"]["exists"]:
        return 2
    if any(c["level"] == MISS for c in report["toolchain"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
