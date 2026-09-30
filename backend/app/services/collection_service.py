import copy
import logging
import threading
import time
from collections import deque
from uuid import uuid4
import cv2
import numpy as np
from sqlalchemy import select
from app.db.database import Session, Sample
from app.core.storage import require
from app.inference import manager
from app.services import (
    camera_service as cameras,
    annotation_service as annotations,
    dataset_service as datasets,
    task_service as tasks,
)

log = logging.getLogger(__name__)
lock = threading.RLock()
state = {
    "active": False,
    "saved": 0,
    "labeled": 0,
    "duplicates": 0,
    "task_id": None,
    "camera_id": None,
    "error": "",
    "detecting": False,
}
preview = None
preview_at = 0.0


def phash(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    low = cv2.dct(cv2.resize(gray, (32, 32)).astype(np.float32))[:8, :8].flatten()
    bits = low > np.median(low[1:])
    return sum(int(bit) << i for i, bit in enumerate(bits))


def similarity(a, b):
    return 1 - (a ^ b).bit_count() / 64


def status():
    with lock:
        result = dict(state)
    if result["camera_id"]:
        with cameras.readers_lock:
            reader = cameras.readers.get(result["camera_id"])
        if reader and reader.thread.is_alive():
            info = reader.status()
            info["error"] = result["error"] or info["error"]
            result.update(info)
        else:
            result.update(connected=False, fps=0, width=0, height=0)
    else:
        result.update(connected=False, fps=0, width=0, height=0)
    return result


def preview_image():
    with lock:
        require(
            preview is not None
            and state["active"]
            and time.monotonic() - preview_at < 10,
            "正在等待检测画面",
            503,
        )
        return preview


def start(options):
    global preview
    with lock:
        require(not state["active"], "已有采集任务，请先停止", 409)
        dataset = datasets.get(options.dataset_id)
        needs_detection = (
            options.mode.startswith("detect_") or options.auto_label != "off"
        )
        require(
            options.auto_label == "off" or dataset["task_type"] == "detection",
            "自动预标仅适用于 detection 数据集",
        )
        if needs_detection:
            provider = (
                options.detector
                if options.mode.startswith("detect_")
                else options.auto_label
            )
            manager.resolve(provider, options.model_path)
            if options.auto_label != "off":
                manager.resolve(options.auto_label, options.model_path)
        require(
            options.mode != "roi_interval" or options.roi is not None,
            "ROI采集模式需要先绘制ROI",
        )
        if options.roi:
            x1, y1, x2, y2 = options.roi
            require(0 <= x1 < x2 and 0 <= y1 < y2, "ROI坐标无效")
        capture = cameras.reader(options.camera_id)
        capture.pinned = True
        preview = None
        state.update(
            active=True,
            saved=0,
            labeled=0,
            duplicates=0,
            error="",
            camera_id=options.camera_id,
            dataset_id=options.dataset_id,
            mode=options.mode,
            detecting=needs_detection,
            config=options.model_dump(),
        )
        try:
            result = tasks.submit(
                "collection",
                options.dataset_id,
                lambda p: collect(options, capture, dataset, p),
                lambda: finish(capture),
            )
            state["task_id"] = result["task_id"]
            return result
        except Exception:
            state["active"] = False
            capture.pinned = False
            raise


def stop():
    with lock:
        if state["task_id"] and state["active"]:
            tasks.cancel(state["task_id"])
    return status()


def finish(capture):
    # 排队期间取消也必须释放摄像头占用和采集状态。
    capture.pinned = False
    with lock:
        state["active"] = False


def crop_objects(objects, area):
    x1, y1, x2, y2 = area
    result = []
    for obj in objects:
        a, b, c, d = obj["bbox"]
        a, b, c, d = max(a, x1), max(b, y1), min(c, x2), min(d, y2)
        if c > a and d > b:
            item = copy.deepcopy(obj)
            item.update(id="obj_" + uuid4().hex, bbox=[a - x1, b - y1, c - x1, d - y1])
            result.append(item)
    return result


def collect(options, capture, dataset, progress):
    global preview, preview_at
    last_check = 0.0
    last_infer = 0.0
    last_id = -1
    recent = deque(maxlen=128)
    try:
        classes = (
            dataset["classes"]
            if dataset["task_type"] == "detection"
            else [
                {"id": i, "name": name} for i, name in enumerate(options.target_classes)
            ]
        )
        trigger = options.mode.startswith("detect_")
        infer = trigger or options.auto_label != "off"
        provider = options.detector if trigger else options.auto_label
        if infer:
            with manager.lock:
                manager.get(provider, options.model_path).set_classes(classes)
        with Session() as db:
            paths = db.scalars(
                select(Sample.image)
                .where(Sample.dataset_id == options.dataset_id)
                .order_by(Sample.created_at.desc())
                .limit(128)
            ).all()
        for path in paths:
            progress.check()
            recent.append(
                phash(
                    annotations.read_image(datasets.folder(options.dataset_id) / path)
                )
            )
        while True:
            progress.check()
            frame_id, frame = capture.snapshot()
            now = time.monotonic()
            if (
                frame is None
                or frame_id == last_id
                or (infer and now - last_infer < options.infer_interval)
            ):
                time.sleep(0.03)
                continue
            last_id = frame_id
            objects = []
            if infer:
                objects = manager.predict(
                    provider, options.model_path, classes, frame, options.confidence
                )
                last_infer = time.monotonic()
                progress.check()
                rendered = annotations.render_preview(
                    frame,
                    {
                        "width": frame.shape[1],
                        "height": frame.shape[0],
                        "objects": objects,
                    },
                )
                with lock:
                    preview = annotations.encode(rendered)
                    preview_at = time.monotonic()
            if now - last_check < options.interval:
                time.sleep(0.03)
                continue
            last_check = now
            if trigger and not objects:
                continue
            h, w = frame.shape[:2]
            areas = [None]
            if options.mode == "roi_interval":
                x1, y1, x2, y2 = map(int, options.roi)
                x2, y2 = min(w, x2), min(h, y2)
                require(x1 < x2 and y1 < y2, "ROI超出当前摄像头分辨率，请重新绘制")
                areas = [(x1, y1, x2, y2)]
            elif options.mode == "detect_crop":
                areas = [tuple(map(int, o["bbox"])) for o in objects]
            for area in areas:
                progress.check()
                saved_frame = frame
                source = {
                    "type": "rtsp",
                    "camera": capture.camera["name"],
                    "camera_id": capture.camera["id"],
                    "frame_id": frame_id,
                    "mode": options.mode,
                }
                if area:
                    x1, y1, x2, y2 = area
                    if x2 <= x1 or y2 <= y1:
                        continue
                    saved_frame = frame[y1:y2, x1:x2].copy()
                    source.update(roi=list(area), original_size=[w, h])
                digest = phash(saved_frame)
                if any(similarity(digest, old) >= options.similarity for old in recent):
                    with lock:
                        state["duplicates"] += 1
                    continue
                labels = []
                if options.auto_label != "off":
                    if provider == options.auto_label:
                        labels = (
                            crop_objects(objects, area)
                            if area
                            else copy.deepcopy(objects)
                        )
                    else:
                        labels = manager.predict(
                            options.auto_label,
                            options.model_path,
                            dataset["classes"],
                            saved_frame,
                            options.confidence,
                        )
                progress.check()
                annotations.save_sample(
                    options.dataset_id, saved_frame, source, objects=labels
                )
                recent.append(digest)
                with lock:
                    state["saved"] += 1
                    state["labeled"] += int(options.auto_label != "off")
                    progress.update(total=state["saved"])
                progress.advance()
    except tasks.Cancelled:
        raise
    except Exception as exc:
        with lock:
            state["error"] = f"采集失败：{getattr(exc, 'detail', str(exc))}"
        raise
