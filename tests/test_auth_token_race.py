"""多进程首启 token 竞态回归（P1-06）。

为什么需要真实进程：旧实现的缺陷只在"`.env` 已存在但没有 token 行"时暴露——
每个进程走 `FileExistsError` 分支，各自追加自己的 token 并返回自己那份。
单进程测试永远绿。这里用 4 个真实子进程同时启动，断言最终只有一个 token。

跑法：python -m pytest tests/test_auth_token_race.py -q
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_concurrent_first_start_converges_on_one_token(tmp_path):
    env_file = tmp_path / ".env"
    # 关键前置：文件存在、但没有 CF_SOLVE_TOKEN 行（用户自己维护其它 env 的形态）。
    env_file.write_text("# 用户自己的 env 内容\nOTHER=1\n", encoding="utf-8")

    solver_dir = str(ROOT / "solver")
    start = time.time() + 2.0
    code = (
        "import sys,time;"
        f"sys.path.insert(0,{solver_dir!r});"
        "from auth import load_or_create_token;"
        f"time.sleep(max(0.0,{start!r}-time.time()));"
        f"print(load_or_create_token({str(env_file)!r})[0])"
    )
    env = os.environ.copy()
    env.pop("CF_SOLVE_TOKEN", None)

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(4)
    ]
    tokens = []
    for proc in procs:
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0, err
        tokens.append(out.strip())

    assert len(set(tokens)) == 1, f"多进程拿到了不同 token: {tokens}"
    lines = [
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("CF_SOLVE_TOKEN=")
    ]
    assert len(lines) == 1, f"token 文件应只有一行，实际: {lines}"
    assert lines[0].split("=", 1)[1] == tokens[0]
    # 用户其它 env 内容必须保留。
    assert "OTHER=1" in env_file.read_text(encoding="utf-8")
    # 锁文件可以保留（OS 锁跟随 fd），但必须已释放。
    assert (tmp_path / ".env.lock").exists()

def test_empty_placeholder_line_does_not_hide_real_token(tmp_path):
    """回归：`CF_SOLVE_TOKEN=`（空占位）不能让后面那行真 token 永远读不到。"""
    sys.path.insert(0, str(ROOT / "solver"))
    import auth  # noqa: E402

    path = tmp_path / ".env"
    path.write_text("CF_SOLVE_TOKEN=\nCF_SOLVE_TOKEN=real-token\nOTHER=1\n", encoding="utf-8")
    assert auth._read_token_file(str(path)) == "real-token"
    token, source = auth.load_or_create_token(str(path))
    assert token == "real-token" and source == "file"
    # 第二次调用不得再追加第三行。
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.startswith("CF_SOLVE_TOKEN=")]
    assert len(lines) == 2, lines


def test_token_lock_acquire_and_release(tmp_path):
    sys.path.insert(0, str(ROOT / "solver"))
    import auth  # noqa: E402

    lock = auth._TokenLock(str(tmp_path / "token.lock"))
    assert lock.acquire(1.0) is True
    assert lock.fd is not None
    lock.release()
    assert lock.fd is None


def test_append_token_adds_newline_when_file_lacks_one(tmp_path):
    sys.path.insert(0, str(ROOT / "solver"))
    import auth  # noqa: E402

    path = tmp_path / ".env"
    path.write_bytes(b"OTHER=1")          # 末尾没有换行
    token, source = auth.load_or_create_token(str(path))
    assert token and source == "generated"
    text = path.read_text(encoding="utf-8")
    assert "OTHER=1\nCF_SOLVE_TOKEN=" in text, repr(text)
