import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException

# 单进程锁覆盖文件和数据库的协同修改；后台任务复用同一把锁。
locks: dict[str, threading.RLock] = {}
guard = threading.Lock()


@contextmanager
def dataset_lock(dataset_id: str):
    with guard:
        lock = locks.setdefault(dataset_id, threading.RLock())
    with lock:
        yield


def atomic_bytes(path: Path, content: bytes):
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path: Path, content: dict):
    atomic_bytes(
        path,
        json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False).encode(
            "utf-8"
        ),
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(value, message: str, code=400):
    if not value:
        raise HTTPException(code, message)
