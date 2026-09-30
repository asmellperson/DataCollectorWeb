import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy import select
from fastapi import HTTPException
from app.db.database import Session, Task
from app.core.storage import dataset_lock, require
from app.services.dataset_service import ensure_idle, folder

log = logging.getLogger(__name__)
pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")
events: dict[str, threading.Event] = {}


class Cancelled(Exception):
    pass


class Progress:
    def __init__(self, task_id):
        self.id = task_id

    def check(self):
        if events[self.id].is_set():
            raise Cancelled("任务已取消")

    def update(self, **values):
        with Session() as db:
            row = db.get(Task, self.id)
            for k, v in values.items():
                setattr(row, k, v)
            db.commit()

    def advance(self, error=None):
        with Session() as db:
            row = db.get(Task, self.id)
            row.processed += 1
            row.failed += int(error is not None)
            row.success += int(error is None)
            if error is not None:
                row.error = str(error)[:2000]
            db.commit()


def submit(kind, dataset_id, fn, cleanup=None):
    with dataset_lock(dataset_id or "_global"), Session() as db:
        if dataset_id:
            folder(dataset_id)
            ensure_idle(dataset_id)
        task_id = uuid4().hex
        db.add(Task(id=task_id, kind=kind, dataset_id=dataset_id))
        db.commit()
        events[task_id] = threading.Event()

    def run():
        progress = Progress(task_id)
        try:
            progress.check()
            progress.update(status="running")
            result = fn(progress)
            progress.check()
            progress.update(
                status="completed", result=json.dumps(result or {}, ensure_ascii=False)
            )
        except Cancelled:
            progress.update(status="cancelled")
        except Exception as exc:
            log.exception("后台任务失败：%s", kind)
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            progress.update(status="failed", error=f"任务失败：{detail}"[:2000])
        finally:
            if cleanup:
                try:
                    cleanup()
                except Exception:
                    log.exception("任务临时文件清理失败")
            events.pop(task_id, None)

    pool.submit(run)
    return {"task_id": task_id}


def get(task_id):
    with Session() as db:
        row = db.get(Task, task_id)
        require(row, "任务不存在", 404)
        data = {c.name: getattr(row, c.name) for c in Task.__table__.columns}
    data["result"] = json.loads(data["result"])
    return data


def cancel(task_id):
    data = get(task_id)
    event = events.get(task_id)
    if event:
        event.set()
    return data


def list_tasks(dataset_id=None):
    with Session() as db:
        q = select(Task.id).order_by(Task.created_at.desc()).limit(30)
        if dataset_id:
            q = q.where(Task.dataset_id == dataset_id)
        ids = db.scalars(q).all()
    return [get(i) for i in ids]


def recover():
    with Session() as db:
        for row in db.scalars(
            select(Task).where(Task.status.in_(["queued", "running"]))
        ):
            row.status = "failed"
            row.error = "服务已重启，任务中断；已成功保存的样本仍保留"
        db.commit()


def shutdown():
    for event in list(events.values()):
        event.set()
    pool.shutdown(wait=True)
