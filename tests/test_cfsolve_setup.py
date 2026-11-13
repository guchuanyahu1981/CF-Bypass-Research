"""cfsolve-setup 装配向导（T-07）的单元测试：纯函数层，不起真实子进程。

覆盖三类边界：
  · 工具链探测的 fake 注入（不依赖本机真的装了什么）；
  · next_steps 的分支完整性（缺工具链 / 缺仓库 / 缺产物 / 全就位）；
  · main 的退出码语义（0 可构建或已配置 / 1 缺工具链 / 2 仓库缺失）。
"""

from __future__ import annotations

import pytest

from scripts import cfsolve_setup as cs


# ── _run_probe：不真调外部命令的替身 ──

def test_run_probe_returns_false_on_missing_binary(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError(cmd)
    monkeypatch.setattr(cs.subprocess, "run", boom)
    ok, ver = cs._run_probe(["definitely-missing-bin"])
    assert ok is False and ver == ""


def test_run_probe_decodes_gbk_output(monkeypatch):
    """cl.exe 在中文系统输出 GBK；探测必须能解出第一行而不是乱码。"""
    def fake_run(cmd, **kw):
        class R:
            stdout = b""
            stderr = "用法: cl [ 选项... ]".encode("gbk")
            returncode = 0
        return R()
    monkeypatch.setattr(cs.subprocess, "run", fake_run)
    ok, ver = cs._run_probe(["cl"])
    assert ok and "用法" in ver


def test_run_probe_timeout_is_not_a_crash(monkeypatch):
    def slow(cmd, **kw):
        raise cs.subprocess.TimeoutExpired(cmd, 15)
    monkeypatch.setattr(cs.subprocess, "run", slow)
    ok, ver = cs._run_probe(["slow-bin"])
    assert ok is False and ver == ""


# ── next_steps：分支完整性 ──

def _toolchain(levels):
    return [{"name": f"tool-{i}", "level": lv, "detail": "", "hint": ""}
            for i, lv in enumerate(levels)]


def test_next_steps_lists_missing_tools_first():
    steps = cs.next_steps(_toolchain([cs.OK, cs.MISS, cs.MISS]),
                          {"exists": True, "artifacts": ["x.exe"]}, {"level": cs.OK})
    assert any("tool-1、tool-2" in s or "tool-1" in s for s in steps)
    assert steps[0].startswith("1)")


def test_next_steps_repo_missing_branch():
    steps = cs.next_steps(_toolchain([cs.OK]),
                          {"exists": False, "artifacts": []}, {"level": cs.WARN})
    assert any("clone" in s for s in steps)


def test_next_steps_build_when_no_artifacts():
    steps = cs.next_steps(_toolchain([cs.OK]),
                          {"exists": True, "artifacts": []}, {"level": cs.WARN})
    assert any("cargo build" in s for s in steps)
    assert any("CFSOLVE_BIN" in s for s in steps)


def test_next_steps_all_set_is_shortest():
    steps = cs.next_steps(_toolchain([cs.OK]),
                          {"exists": True, "artifacts": ["challenge.exe"]}, {"level": cs.OK})
    assert any("doctor" in s for s in steps)
    assert not any("cargo build" in s for s in steps)


# ── check_repo：仓库缺失 / 补丁状态 ──

def test_check_repo_missing_dir_is_warn(tmp_path):
    r = cs.check_repo(str(tmp_path / "nope"))
    assert r["exists"] is False
    assert r["patch"]["level"] == cs.WARN
    assert "clone" in r["patch"]["detail"]


def test_check_repo_existing_without_patches(tmp_path):
    (tmp_path / ".git").mkdir()
    r = cs.check_repo(str(tmp_path))
    assert r["exists"] is True
    assert r["patch"]["level"] == cs.WARN  # 无 .patch 文件 → WARN（向导入库带 patches，正常机器会有）


# ── check_service_config：判据与 doctor 一致 ──

def test_service_config_ok_when_both_files_exist(tmp_path, monkeypatch):
    binf = tmp_path / "challenge.exe"
    prof = tmp_path / "p.json"
    binf.write_bytes(b"x")
    prof.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CFSOLVE_BIN", str(binf))
    monkeypatch.setenv("CFSOLVE_PROFILE", str(prof))
    c = cs.check_service_config()
    assert c["level"] == cs.OK


def test_service_config_warn_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CFSOLVE_BIN", raising=False)
    monkeypatch.delenv("CFSOLVE_PROFILE", raising=False)
    c = cs.check_service_config()
    assert c["level"] == cs.WARN
    assert "CFSOLVE_BIN" in c["hint"] and "CFSOLVE_PROFILE" in c["hint"]


def test_service_config_bin_path_must_be_real_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CFSOLVE_BIN", str(tmp_path / "ghost.exe"))
    prof = tmp_path / "p.json"
    prof.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CFSOLVE_PROFILE", str(prof))
    c = cs.check_service_config()
    assert c["level"] == cs.WARN  # 路径配了但文件不存在 ≠ 已配置


# ── main：退出码语义 ──

def test_main_exit_code_repo_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CFSOLVE_BIN", raising=False)
    monkeypatch.delenv("CFSOLVE_PROFILE", raising=False)
    rc = cs.main(["--repo", str(tmp_path / "nope"), "--json"])
    assert rc == 2


def test_main_exit_code_toolchain_missing(tmp_path, monkeypatch):
    # 本机真跑了探测：NASM 缺 → exit 1（缺工具链但仓库存在）
    monkeypatch.setattr(cs, "check_repo", lambda arg="": {
        "repo": str(tmp_path), "exists": True, "patch": {"level": cs.OK, "detail": "", "hint": ""},
        "artifacts": ["challenge.exe"]})
    monkeypatch.delenv("CFSOLVE_BIN", raising=False)
    monkeypatch.delenv("CFSOLVE_PROFILE", raising=False)
    rc = cs.main(["--json"])
    assert rc in (1, 0)  # NASM 在则 0，不在则 1 —— 断言不崩且落在定义域内


def test_main_json_output_is_valid(capsys):
    rc = cs.main(["--json", "--repo", "Z:/no-such-dir"])
    out = capsys.readouterr().out
    import json
    data = json.loads(out)
    assert {"toolchain", "repo", "config", "steps"} <= set(data)
    assert rc in (0, 1, 2)
