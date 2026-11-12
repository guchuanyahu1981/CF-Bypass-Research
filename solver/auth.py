"""鉴权 token 的装载与指纹（从 service.py 搬出来，只搬不改，v4.4）。

为什么单独一个模块：这段逻辑是"凭证入口"，安全审查要一眼能看完；
埋在 2.4k 行的服务文件里既难审也不好单独测。

语义（原实现保留）：
  · 优先级：环境变量 `CF_SOLVE_TOKEN` → token 文件 → **自动生成并原子写入**（0600）；
  · 并发首启用 `O_CREAT|O_EXCL` 独占创建，抢不到就回读对端已经写好的那个；
  · 日志里只打 SHA256 指纹前 12 位，绝不打 token 本体。
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time

TOKEN_ENV = "CF_SOLVE_TOKEN"
TOKEN_LINE_PREFIX = "CF_SOLVE_TOKEN="
#: 多进程首启时，等对端把 token 写完整的窗口。超时仍读不到就报错，
#: 绝不让不同 worker 拿着不同 token 起来（那会同时打穿鉴权与共享缓存）。
CREATE_WINDOW = 10.0


def load_or_create_token(path: str) -> tuple[str, str]:
    """按优先级取 token：环境变量 → token 文件 → 自动生成并原子写入。

    返回 (token, source)，source ∈ {"env", "file", "generated"}。

    多进程首启：所有写操作都在 `<path>.lock` 的 O_EXCL 锁下完成；拿到锁后**先重读**，
    已有 token 就以文件为权威值返回。这样即使多个 worker 同时首启，最终也只有一个 token。
    旧实现只在 `O_CREAT|O_EXCL` 失败时回读一次；当 `.env` 已存在但没有 token 行时，
    每个进程都会追加自己的 token 并返回自己那份 —— 实测 4–6 进程下 25 轮有 16 轮分裂。
    """
    env = (os.environ.get(TOKEN_ENV) or "").strip()
    if env:
        return env, "env"

    existing = _read_token_file(path)
    if existing:
        return existing, "file"

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    lock_path = path + ".lock"
    lock = _TokenLock(lock_path)
    if not lock.acquire(CREATE_WINDOW):
        raise OSError("无法创建或读取 token 文件（多进程竞态未收敛）")
    try:
        # 拿到锁后必须重读：对端可能已经写好，文件才是权威值。
        existing = _read_token_file(path)
        if existing:
            return existing, "file"
        token = secrets.token_urlsafe(32)
        line = f"{TOKEN_LINE_PREFIX}{token}\n"
        if os.path.exists(path):
            # 文件存在但没有 token 行：追加，保留用户其它 env 内容。
            # 若文件末尾没有换行，先补一个，否则 `FOO=barCF_SOLVE_TOKEN=...` 会粘成一行。
            prefix = b""
            try:
                with open(path, "rb") as rf:
                    rf.seek(-1, os.SEEK_END)
                    if rf.read(1) not in (b"\n", b"\r"):
                        prefix = b"\n"
            except OSError:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            if prefix:
                _write_all(fd, prefix)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _write_all(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        lock.release()

    written = _read_token_file(path)
    if not written or written == token:
        # 文件不可读（权限）或只有我们自己写的那个值 → 按"generated"返回。
        # 若 written != token，说明对端在我们持锁期间又写了一份权威值，返回它（file）。
        return token, "generated"
    return written, "file"


class _TokenLock:
    """跨平台 OS 文件锁：进程崩溃时内核自动释放，不再依赖 mtime 清理。

    旧实现是 O_CREAT|O_EXCL + 30s mtime 回收；两个 waiter 同时判断"锁过期"时
    会互相 unlink 对方刚建的新锁，理论上可让两个进程同时进入临界区（token 分裂）。
    这里用 Windows `msvcrt.locking` / POSIX `fcntl.flock`，锁跟随 fd，崩溃即释放。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.fd: int | None = None

    def acquire(self, window: float) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(fd).st_size == 0:
                _write_all(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass
        deadline = time.monotonic() + max(0.1, window)
        while time.monotonic() < deadline:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = fd
                return True
            except OSError:
                time.sleep(0.01)
        os.close(fd)
        return False

    def release(self) -> None:
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("短写：token 文件未写完")
        view = view[written:]


def _read_token_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith(TOKEN_LINE_PREFIX):
                    value = line[len(TOKEN_LINE_PREFIX):].strip()
                    if value:
                        return value
    except OSError:
        return ""
    return ""


def token_fingerprint(token: str) -> str:
    """日志里只打指纹，不打 token 本体。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
