import copy
import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from uuid import uuid4
import cv2
import numpy as np
from sqlalchemy import select
from app.db.database import Session, Sample
from app.core.storage import require
from app.inference import manager
from app.inference.targets import trigger_classes
from app.services import (
    camera_service as cameras,
    annotation_service as annotations,
    dataset_service as datasets,
    task_service as tasks,
)

log = logging.getLogger(__name__)
lock = threading.RLock()
MAX_CAMERAS = 9
sessions = {}


def new_state(options):
    return {
        "active": True,
        "phase": "starting",
        "stopping": False,
        "saved": 0,
        "labeled": 0,
        "duplicates": 0,
        "task_id": None,
        "camera_id": options.camera_id,
        "dataset_id": options.dataset_id,
        "mode": options.mode,
        "error": "",
        "detecting": options.mode.startswith("detect_") or options.auto_label != "off",
        "config": options.model_dump(),
        "preview": None,
        "preview_at": 0.0,
    }


def public_session(current):
    return {
        k: copy.deepcopy(v)
        for k, v in current.items()
        if k not in ("preview", "preview_at", "preview_view")
    }


def set_phase(current, phase):
    with lock:
        if not current["stopping"]:
            current["phase"] = phase


@contextmanager
def inference_lock(progress):
    # A camera queued behind another model can stop without waiting for its turn.
    while not manager.lock.acquire(timeout=0.1):
        progress.check()
    try:
        progress.check()
        yield
    finally:
        manager.lock.release()


def phash(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    low = cv2.dct(cv2.resize(gray, (32, 32)).astype(np.float32))[:8, :8].flatten()
    bits = low > np.median(low[1:])
    return sum(int(bit) << i for i, bit in enumerate(bits))


def similarity(a, b):
    return 1 - (a ^ b).bit_count() / 64


def status(camera_id=None):
    with lock:
        selected = [
            s for cid, s in sessions.items() if camera_id is None or cid == camera_id
        ]
        results = [public_session(s) for s in selected]
    # 不在注册表锁内读取摄像头，避免与摄像头释放路径反向加锁。
    for result in results:
        with cameras.readers_lock:
            reader = cameras.readers.get(result["camera_id"])
        info = (
            reader.status()
            if reader and reader.thread.is_alive()
            else {
                "connected": False,
                "fps": 0,
                "width": 0,
                "height": 0,
                "error": "",
            }
        )
        info["error"] = result["error"] or info["error"]
        result.update(info)
    if camera_id is not None:
        require(results, "该摄像头没有采集记录", 404)
        return results[0]
    active = [s for s in results if s["active"]]
    # 保留旧单路客户端读取的顶层字段；新客户端使用 sessions。
    primary = (active or results or [{}])[0]
    return {
        **primary,
        "active": bool(active),
        "active_count": len(active),
        "max_cameras": MAX_CAMERAS,
        "sessions": results,
        **{
            key: sum(s[key] for s in results)
            for key in ("saved", "labeled", "duplicates")
        },
    }


def preview_image(camera_id=None, with_view=False):
    with lock:
        if camera_id is None:
            active = [s for s in sessions.values() if s["active"]]
            require(len(active) <= 1, "多路采集请指定 camera_id", 400)
            current = active[0] if active else None
        else:
            current = sessions.get(camera_id)
        require(
            current
            and current["preview"] is not None
            and current["active"]
            and time.monotonic() - current["preview_at"] < 10,
            "正在等待检测画面",
            503,
        )
        cid = current["camera_id"]
        jpeg = current["preview"]
        preview_view = current.get("preview_view")
    # A changed view must never show detection boxes from an older crop.
    capture = cameras.reader(cid)
    if preview_view is not None and preview_view != capture.camera.get("view", cameras.DEFAULT_VIEW):
        return capture.image(with_view=with_view)
    return (jpeg, preview_view or cameras.DEFAULT_VIEW.copy()) if with_view else jpeg


def start(options):
    with lock:
        require(
            not sessions.get(options.camera_id, {}).get("active"),
            "该摄像头正在采集，请先停止",
            409,
        )
        require(
            sum(s["active"] for s in sessions.values()) < MAX_CAMERAS,
            "最多同时采集 9 个摄像头，请先停止其他摄像头",
            409,
        )
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
            path = (
                (options.trigger_model_path or options.model_path)
                if options.mode.startswith("detect_")
                else options.model_path
            )
            manager.resolve(provider, path)
            if options.auto_label != "off":
                manager.resolve(options.auto_label, options.model_path)
                datasets.label_classes(dataset, options)
            if options.mode.startswith("detect_") and options.detector in (
                "yolo_world",
                "scrfd",
            ):
                trigger_classes(options.detector, options.target_classes)
        require(
            options.mode != "roi_interval" or options.roi is not None,
            "ROI采集模式需要先绘制ROI",
        )
        if options.roi:
            x1, y1, x2, y2 = options.roi
            require(0 <= x1 < x2 and 0 <= y1 < y2, "ROI坐标无效")
        # 与相机编辑/删除共用读流锁，防止刚建立的读流被并发释放。
        with cameras.readers_lock:
            capture = cameras.reader(options.camera_id)
            capture.pinned = True
        current = new_state(options)
        previous = sessions.get(options.camera_id)
        sessions[options.camera_id] = current
        try:
            result = tasks.submit(
                "collection",
                options.dataset_id,
                lambda p: collect(options, capture, dataset, p, current),
                lambda: finish(capture, current),
            )
            current["task_id"] = result["task_id"]
            # No camera status or model initialization is needed to acknowledge
            # the request. The client can show the actual background session now.
            return {**result, "session": public_session(current)}
        except Exception:
            if previous is None:
                sessions.pop(options.camera_id, None)
            else:
                sessions[options.camera_id] = previous
            capture.pinned = False
            raise


def stop(camera_id=None, dataset_id=None):
    with lock:
        for cid, current in sessions.items():
            if camera_id is not None and camera_id != cid:
                continue
            if dataset_id is not None and current["dataset_id"] != dataset_id:
                continue
            if current["task_id"] and current["active"]:
                current["stopping"] = True
                current["phase"] = "stopping"
                tasks.cancel(current["task_id"])
    return status()


def finish(capture, current):
    # 使用本次会话对象；旧任务的回调不能清除新任务的状态。
    with lock:
        if sessions.get(current["camera_id"]) is current:
            capture.pinned = False
            current.update(
                active=False,
                stopping=False,
                preview=None,
                preview_at=0.0,
                phase="failed" if current["error"] else "stopped",
            )


def crop_objects(objects, area):
    x1, y1, x2, y2 = area
    result = []
    for obj in objects:
        a, b, c, d = obj["bbox"]
        a, b, c, d = max(a, x1), max(b, y1), min(c, x2), min(d, y2)
        if c > a and d > b:
            item = copy.deepcopy(obj)
            item.update(id="obj_" + uuid4().hex, bbox=[a - x1, b - y1, c - x1, d - y1])
            if item.get("keypoints") is not None:
                item["keypoints"] = [
                    [p[0] - x1, p[1] - y1]
                    if p is not None and x1 <= p[0] <= x2 and y1 <= p[1] <= y2
                    else None
                    for p in item["keypoints"]
                ]
            result.append(item)
    return result


def collect(options, capture, dataset, progress, current=None):
    if current is None:
        current = new_state(options)
    last_check = 0.0
    last_infer = 0.0
    last_id = -1
    recent = deque(maxlen=128)
    try:
        trigger = options.mode.startswith("detect_")
        infer = trigger or options.auto_label != "off"
        provider = options.detector if trigger else options.auto_label
        trigger_path = options.trigger_model_path or options.model_path
        path = trigger_path if trigger else options.model_path
        label_classes = (
            datasets.label_classes(dataset, options)
            if options.auto_label != "off"
            else []
        )
        classes = label_classes
        if infer:
            set_phase(current, "loading_model")
            with inference_lock(progress):
                detector = manager.get(provider, path)
                progress.check()
                if trigger:
                    classes = trigger_classes(
                        provider,
                        options.target_classes,
                        detector.model.names if provider == "custom_yolo" else None,
                    )
                detector.set_classes(classes)
                if options.auto_label != "off":
                    manager.get(options.auto_label, options.model_path).set_classes(
                        label_classes
                    )
                progress.check()
        set_phase(current, "preparing")
        with Session() as db:
            paths = db.scalars(
                select(Sample.id)
                .where(Sample.dataset_id == options.dataset_id)
                .order_by(Sample.created_at.desc())
                .limit(128)
            ).all()
        for sample_id in paths:
            progress.check()
            ann = annotations.get(options.dataset_id, sample_id)
            if ann.get("source", {}).get("camera_id") == options.camera_id:
                recent.append(
                    phash(
                        annotations.read_image(
                            datasets.folder(options.dataset_id) / ann["image"]
                        )
                    )
                )
        set_phase(current, "waiting_frame")
        while True:
            progress.check()
            now = time.monotonic()
            delay = (
                (options.infer_interval - (now - last_infer))
                if infer
                else (options.interval - (now - last_check))
            )
            if delay > 0:
                time.sleep(min(0.1, delay))
                continue
            frame_id, frame, view = cameras.collection_snapshot(capture)
            if frame is None or frame_id == last_id:
                time.sleep(0.03)
                continue
            last_id = frame_id
            objects = []
            if infer:
                with inference_lock(progress):
                    objects = manager.predict(
                        provider, path, classes, frame, options.confidence
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
                # 预览缩放与编码不占用注册表锁，训练原图仍保存原始分辨率。
                jpeg = cameras.encode_preview(rendered)
                with lock:
                    current["preview"] = jpeg
                    current["preview_at"] = time.monotonic()
                    current["preview_view"] = {key: view[key] for key in cameras.DEFAULT_VIEW}
            set_phase(current, "collecting")
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
                    "view": view,
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
                        current["duplicates"] += 1
                    continue
                labels = []
                if options.auto_label != "off":
                    if not trigger or (
                        provider == "scrfd" and options.auto_label == "scrfd"
                    ):
                        labels = (
                            crop_objects(objects, area)
                            if area
                            else copy.deepcopy(objects)
                        )
                    else:
                        with inference_lock(progress):
                            labels = manager.predict(
                                options.auto_label,
                                options.model_path,
                                label_classes,
                                saved_frame,
                                options.confidence,
                            )
                progress.check()
                annotations.save_sample(
                    options.dataset_id, saved_frame, source, objects=labels
                )
                recent.append(digest)
                with lock:
                    current["saved"] += 1
                    current["labeled"] += int(options.auto_label != "off")
                    saved_count = current["saved"]
                progress.update(total=saved_count)
                progress.advance()
    except tasks.Cancelled:
        raise
    except Exception as exc:
        with lock:
            current["error"] = f"采集失败：{getattr(exc, 'detail', str(exc))}"
        raise
