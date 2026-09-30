import shutil
from uuid import uuid4
from sqlalchemy import select, func
from app.db.database import Session, Dataset, Sample, Task
from app.core.config import DATA_ROOT
from app.core.storage import dataset_lock, read_json, write_json, require


def folder(dataset_id):
    with Session() as db:
        require(db.get(Dataset, dataset_id), "数据集不存在", 404)
    return DATA_ROOT / dataset_id


def validate_classes(classes, task_type):
    require(task_type == "raw" or classes, "类别配置错误：请至少添加一个类别")
    require(task_type != "raw" or not classes, "raw 数据集不需要类别")
    require(len({c["id"] for c in classes}) == len(classes), "类别 ID 不得重复")
    require(
        len({c["name"].strip() for c in classes}) == len(classes), "类别名称不得重复"
    )
    require(all(c["name"].strip() for c in classes), "类别名称不得为空")


def get(dataset_id):
    root = folder(dataset_id)
    data = read_json(root / "dataset.json")
    with Session() as db:
        counts = dict(
            db.execute(
                select(Sample.status, func.count())
                .where(Sample.dataset_id == dataset_id)
                .group_by(Sample.status)
            ).all()
        )
    return {
        **data,
        "total": sum(counts.values()),
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
    }


def list_all():
    with Session() as db:
        ids = db.scalars(select(Dataset.id).order_by(Dataset.created_at.desc())).all()
    return [get(i) for i in ids]


def create(body):
    data = body.model_dump()
    data["id"] = data["id"] or uuid4().hex[:16]
    validate_classes(data["classes"], data["task_type"])
    with dataset_lock(data["id"]), Session() as db:
        root = DATA_ROOT / data["id"]
        require(
            not root.exists() and not db.get(Dataset, data["id"]),
            "数据集 ID 已存在",
            409,
        )
        root.mkdir()
        try:
            for name in ("images", "annotations", "previews", "exports"):
                (root / name).mkdir()
            data["retired_class_ids"] = []
            write_json(root / "dataset.json", data)
            db.add(
                Dataset(id=data["id"], name=data["name"], task_type=data["task_type"])
            )
            db.commit()
        except Exception:
            shutil.rmtree(root)
            raise
    return get(data["id"])


def ensure_idle(dataset_id):
    with Session() as db:
        active = db.scalar(
            select(Task.id).where(
                Task.dataset_id == dataset_id, Task.status.in_(["queued", "running"])
            )
        )
    require(not active, "该数据集有运行中的任务，请先停止或等待完成", 409)


def update(dataset_id, body):
    with dataset_lock(dataset_id), Session() as db:
        ensure_idle(dataset_id)
        data = get(dataset_id)
        classes = [c.model_dump() for c in body.classes]
        validate_classes(classes, data["task_type"])
        old_ids = {c["id"] for c in data["classes"]}
        new_ids = {c["id"] for c in classes}
        retired = set(data.get("retired_class_ids", []))
        require(not ((new_ids - old_ids) & retired), "已删除的类别 ID 不可重新使用")
        # 已有标注引用的类别不能删除；名称变更将在读取和保存标注时同步。
        removed = old_ids - new_ids
        root = folder(dataset_id)
        if removed:
            for path in (root / "annotations").glob("*.json"):
                ann = read_json(path)
                refs = {o["class_id"] for o in ann["objects"]}
                if ann.get("classification"):
                    refs.add(ann["classification"]["class_id"])
                require(not refs & removed, "类别仍被样本引用，不能删除")
        meta = {
            k: v
            for k, v in data.items()
            if k not in ("total", "pending", "approved", "rejected")
        }
        meta.update(
            name=body.name, classes=classes, retired_class_ids=list(retired | removed)
        )
        # 类别改名同步到权威 JSON 与预览，类别 ID 和列表顺序保持原样。
        renamed = {c["id"]: c["name"] for c in classes}
        old_names = {c["id"]: c["name"] for c in data["classes"]}
        if any(
            old_names.get(i) != name for i, name in renamed.items() if i in old_names
        ):
            from app.services.annotation_service import (
                read_image,
                render_preview,
                encode,
            )
            from app.core.storage import atomic_bytes

            for path in (root / "annotations").glob("*.json"):
                ann = read_json(path)
                for obj in ann["objects"]:
                    obj["class_name"] = renamed[obj["class_id"]]
                if ann.get("classification"):
                    ann["classification"]["class_name"] = renamed[
                        ann["classification"]["class_id"]
                    ]
                ann["revision"] = ann.get("revision", 0) + 1
                write_json(path, ann)
                atomic_bytes(
                    root / "previews" / f"{ann['id']}.jpg",
                    encode(render_preview(read_image(root / ann["image"]), ann)),
                )
        write_json(root / "dataset.json", meta)
        db.get(Dataset, dataset_id).name = body.name
        db.commit()
    return get(dataset_id)


def delete(dataset_id):
    with dataset_lock(dataset_id), Session() as db:
        ensure_idle(dataset_id)
        root = folder(dataset_id)
        # 先原子移出正式目录，数据库失败时可恢复。
        trash = root.with_name(f".deleted-{uuid4().hex}")
        root.rename(trash)
        try:
            db.delete(db.get(Dataset, dataset_id))
            db.commit()
        except Exception:
            trash.rename(root)
            raise
        shutil.rmtree(trash)
    return {"ok": True}
