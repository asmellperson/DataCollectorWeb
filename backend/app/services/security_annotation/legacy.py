"""直接复用桌面工具的纯 Python 标签存储与区域配置，不加载 Qt。"""

from functools import lru_cache
import hashlib
import json
from importlib import import_module, util
from pathlib import Path
import sys
import threading
from uuid import uuid4

from app.core.config import ROOT
from app.core.storage import require
from app.schemas.security_annotation import InspectionEdit
from . import store

_import_lock = threading.Lock()


@lru_cache(maxsize=1)
def modules():
    # 桌面工具保持可独立运行；这里只加载它已有的无界面模块。
    with _import_lock:
        name = "_collector_security_scan_tool"
        directory = ROOT / "security_scan_dataset_tool" / "security_scan_tool"
        spec = util.spec_from_file_location(
            name, directory / "__init__.py", submodule_search_locations=[str(directory)]
        )
        require(spec and spec.loader, "缺少现有 security_scan_dataset_tool 源码")
        package = util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
        return (
            import_module(name + ".label_store"),
            import_module(name + ".event_store"),
            import_module(name + ".label_schema"),
        )


def presets():
    return [
        {"key": key, "name": title.split()[0]}
        for key, title in modules()[2].SCAN_REGIONS
    ]


def import_labels(recording_id, body):
    directory = Path(body.dataset_path).expanduser().resolve()
    labels_module, events_module, _ = modules()
    require((directory / "labels.json").is_file(), "目录中没有 labels.json")
    rows = labels_module.LabelStore(directory).load_all()
    matching = [
        r
        for r in rows
        if str(r.get("clip_id", r.get("annotation_id", ""))) == body.clip_id
    ]
    require(len(matching) == 1, "旧标签中找不到唯一的片段 ID")
    old = matching[0]
    video = Path(old["video_path"])
    video = video if video.is_absolute() else directory / video
    require(video.is_file(), "旧标签对应的视频不存在，无法核对原文件")
    digest = hashlib.sha256()
    with video.open("rb") as source:
        while chunk := source.read(4 * 1024 * 1024):
            digest.update(chunk)
    meta = store.get(recording_id)
    require(
        meta.get("sha256") == digest.hexdigest(), "旧标注对应的视频与当前原视频不一致"
    )
    times = json.loads((store.folder(recording_id) / "timeline.json").read_text())[
        "times_sec"
    ]
    count = meta["frame_count"]
    start = int(old.get("segment_start_frame", 0))
    end = int(old.get("segment_end_frame", count - 1))
    require(0 <= start <= end < count, "旧标注有效帧范围超出原视频")

    def right(frame):
        return times[frame + 1] if frame + 1 < count else meta["duration_sec"]

    events = []
    for event in events_module.EventStore(directory).load_for_clip(body.clip_id):
        a, b = int(event["start_frame"]), int(event["end_frame"])
        require(start <= a <= b <= end, "旧标注动作超出有效区间")
        events.append(
            {
                "id": uuid4().hex,
                "action": "扫描",
                "region": event["region"],
                "start_sec": times[a],
                "end_sec": right(b),
                "evidence_frames": [a, b],
            }
        )
    coverage = {key: old[key] for key in modules()[2].SCAN_REGION_KEYS if key in old}
    content = InspectionEdit(
        revision=body.revision,
        persons=[],
        rounds=[
            {
                "id": uuid4().hex,
                "start_sec": times[start],
                "end_sec": right(end),
                "inspection": "present" if events else "uncertain",
                "quality": "unusable"
                if old.get("validity") == "invalid"
                else "uncertain",
                "coverage": coverage,
                "events": events,
                "notes": "从原工具导入，人物角色待复核。" + str(old.get("notes", "")),
            }
        ],
    )
    return store.save(recording_id, content, origin="legacy")
