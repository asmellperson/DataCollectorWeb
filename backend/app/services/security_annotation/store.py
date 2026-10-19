"""复用平台原子 JSON 与锁；模型草稿、人工修订和原视频分开保存。"""

from datetime import datetime, timezone
from uuid import uuid4
import re
import logging

from app.core.config import DATA_ROOT
from app.core.storage import dataset_lock, read_json, require, write_json
from app.schemas.security_annotation import InspectionContent, InspectionEdit

ROOT = DATA_ROOT / "_security_inspection"
log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def folder(recording_id: str):
    require(re.fullmatch(r"[a-f0-9]{32}", recording_id), "录像 ID 无效", 404)
    path = ROOT / "recordings" / recording_id
    require(path.is_dir() and not path.is_symlink(), "录像不存在", 404)
    return path


def lock(recording_id: str):
    return dataset_lock("inspection:" + recording_id)


def create(name, source_group="", **source):
    recording_id = uuid4().hex
    path = ROOT / "recordings" / recording_id
    path.mkdir(parents=True)
    meta = {
        "id": recording_id,
        "name": name,
        "source_group": source_group or recording_id,
        "created_at": now(),
        "state": "preparing",
        "error": "",
        "active_task_id": "",
        "duration_sec": 0,
        "frame_count": 0,
        "fps_hint": 0,
        "width": 0,
        "height": 0,
        **source,
    }
    write_json(path / "recording.json", meta)
    write_json(
        path / "annotation.json",
        {
            "schema_version": "security-inspection/1",
            "revision": 0,
            "persons": [],
            "rounds": [],
            "review_seconds": 0,
            "updated_at": now(),
        },
    )
    return meta


def get(recording_id):
    return read_json(folder(recording_id) / "recording.json")


def update(recording_id, **values):
    with lock(recording_id):
        meta = get(recording_id)
        meta.update(values)
        write_json(folder(recording_id) / "recording.json", meta)
        return meta


def annotation(recording_id):
    return read_json(folder(recording_id) / "annotation.json")


def listing():
    parent = ROOT / "recordings"
    if not parent.exists():
        return []
    result = []
    for path in parent.iterdir():
        if path.is_dir() and re.fullmatch(r"[a-f0-9]{32}", path.name):
            with lock(path.name):
                if not path.is_dir() or path.is_symlink():
                    continue
                try:
                    meta = get(path.name)
                    doc = annotation(path.name)
                except (OSError, ValueError):
                    log.exception("录像索引或标注无法读取，原文件保留：%s", path.name)
                    continue
                meta["round_count"] = len(doc["rounds"])
                meta["approved_count"] = sum(
                    r["review_status"] == "approved" for r in doc["rounds"]
                )
                result.append(meta)
    return sorted(result, key=lambda m: m["created_at"], reverse=True)


def validate_content(meta, content: InspectionContent):
    duration = meta["duration_sec"]
    count = meta["frame_count"]
    for person in content.persons:
        for evidence in person.evidence:
            require(evidence.frame_index < count, "人物证据帧超出原视频")
    for item in content.rounds:
        require(item.end_sec <= duration + 0.00001, "轮次超出原视频时间范围")
        for event in item.events:
            require(
                all(frame < count for frame in event.evidence_frames),
                "动作证据帧超出原视频",
            )


def save(recording_id, edit: InspectionEdit, *, origin="manual"):
    with lock(recording_id):
        meta = get(recording_id)
        require(meta["state"] == "ready", "原视频尚未准备完成", 409)
        old = annotation(recording_id)
        require(
            edit.revision == old["revision"],
            "标注已更新，请保留当前编辑并重新读取版本",
            409,
        )
        validate_content(meta, edit)
        doc = {
            "schema_version": "security-inspection/1",
            "revision": old["revision"] + 1,
            **edit.model_dump(exclude={"revision", "review_seconds"}),
            "review_seconds": round(old["review_seconds"] + edit.review_seconds, 3),
            "updated_at": now(),
            "origin": origin,
        }
        # 修订历史先落盘，当前指针只在完整版本写入后更新。
        history = folder(recording_id) / "revisions"
        history.mkdir(exist_ok=True)
        write_json(history / f"{doc['revision']}_{uuid4().hex}.json", doc)
        write_json(folder(recording_id) / "annotation.json", doc)
        return doc


def recover():
    for meta in listing():
        if meta["state"] in ("recording", "preparing") or meta["active_task_id"]:
            state = "failed" if meta["state"] != "ready" else "ready"
            update(
                meta["id"],
                state=state,
                active_task_id="",
                error="服务曾中断；已保存的原文件、模型草稿和人工修订仍保留，可重新准备或预标",
            )
