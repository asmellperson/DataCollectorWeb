import logging
import os
import threading
import time
from collections import deque
from uuid import uuid4
import cv2
from sqlalchemy import select
from app.db.database import Session, Camera, CameraControl
from app.core.storage import require
from app.services.camera_view import DEFAULT_VIEW, apply_view

ONVIF_FIELDS = ("onvif_url", "onvif_username", "onvif_password", "onvif_profile")

log = logging.getLogger(__name__)
open_lock = threading.Lock()
readers_lock = threading.RLock()
readers = {}


def encode_preview(frame, max_width=960):
    """浏览器预览使用小尺寸 JPEG，采集快照和训练图片不缩放。"""
    height, width = frame.shape[:2]
    if width > max_width:
        frame = cv2.resize(
            frame, (max_width, max(1, round(height * max_width / width)))
        )
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    require(ok, "预览编码失败", 503)
    return data.tobytes()


class CameraReader:
    def __init__(self, camera):
        self.camera = camera
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.frame = None
        self.frame_id = 0
        self.last_frame = 0.0
        self.last_access = time.monotonic()
        self.times = deque(maxlen=60)
        self.error = "正在连接"
        self.pinned = False
        self.jpeg_id = -1
        self.jpeg = None
        self.thread = threading.Thread(
            target=self.run, daemon=True, name=f"camera-{camera['id']}"
        )
        self.thread.start()

    def run(self):
        # 保留旧版独立读流、最新帧快照和重连思路，增加超时与空闲释放。
        while not self.stop_event.is_set():
            if not self.pinned and time.monotonic() - self.last_access > 90:
                break
            cap = None
            try:
                with open_lock:
                    if self.stop_event.is_set():
                        return
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                        f"rtsp_transport;{self.camera['transport']}"
                    )
                    cap = cv2.VideoCapture(
                        self.camera["url"],
                        cv2.CAP_FFMPEG,
                        [
                            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                            5000,
                            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                            5000,
                        ],
                    )
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not cap.isOpened():
                    raise RuntimeError("RTSP连接失败，正在重连")
                while not self.stop_event.is_set():
                    if not self.pinned and time.monotonic() - self.last_access > 90:
                        return
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise RuntimeError("RTSP断流，正在重连")
                    with self.lock:
                        self.frame = frame
                        self.frame_id += 1
                        self.last_frame = time.monotonic()
                        self.times.append(self.last_frame)
                        self.error = ""
            except Exception as exc:
                with self.lock:
                    self.error = (
                        str(exc)
                        if isinstance(exc, RuntimeError)
                        else "RTSP读取失败，正在重连"
                    )
                    self.frame = None
                    self.times.clear()
                    self.jpeg = None
                log.warning("摄像头 %s：%s", self.camera["name"], self.error)
            finally:
                if cap is not None:
                    cap.release()
            self.stop_event.wait(2)
        with self.lock:
            self.frame = None
            self.error = "已停止"

    def snapshot(self):
        self.last_access = time.monotonic()
        with self.lock:
            fresh = time.monotonic() - self.last_frame < 6 and not self.error
            return (
                self.frame_id,
                self.frame.copy() if fresh and self.frame is not None else None,
            )

    def status(self):
        self.last_access = time.monotonic()
        with self.lock:
            connected = (
                self.frame is not None
                and not self.error
                and time.monotonic() - self.last_frame < 6
            )
            fps = (
                (len(self.times) - 1) / (self.times[-1] - self.times[0])
                if connected and len(self.times) > 1 and self.times[-1] > self.times[0]
                else 0
            )
            height, width = self.frame.shape[:2] if self.frame is not None else (0, 0)
            return {
                "connected": connected,
                "error": self.error,
                "fps": round(fps, 1),
                "width": width,
                "height": height,
                "frame_id": self.frame_id,
                "view": self.camera.get("view", DEFAULT_VIEW).copy(),
            }

    def image(self, with_view=False, original=False):
        if original:
            _, frame = self.snapshot()
            require(frame is not None, "暂无实时图像", 503)
            jpeg = encode_preview(frame)
            return (jpeg, DEFAULT_VIEW.copy()) if with_view else jpeg
        self.last_access = time.monotonic()
        with self.lock:
            require(
                self.frame is not None
                and not self.error
                and time.monotonic() - self.last_frame < 6,
                self.error or "暂无实时图像",
                503,
            )
            if self.jpeg_id == self.frame_id:
                return (self.jpeg, self.jpeg_view.copy()) if with_view else self.jpeg
            frame, frame_id = self.frame, self.frame_id
            view = self.camera.get("view", DEFAULT_VIEW).copy()
        # 编码时允许读流线程继续接收新帧，不把预览负载传回采集链路。
        viewed, _ = apply_view(frame, view)
        jpeg = encode_preview(viewed)
        with self.lock:
            if (frame_id >= self.jpeg_id and self.frame is not None and not self.error
                    and view == self.camera.get("view", DEFAULT_VIEW)):
                self.jpeg, self.jpeg_id = jpeg, frame_id
                self.jpeg_view = view
        return (jpeg, view) if with_view else jpeg

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=6)


def get(camera_id):
    with Session() as db:
        row = db.get(Camera, camera_id)
        require(row, "摄像头不存在", 404)
        control = db.get(CameraControl, camera_id)
        return {
            **{c.name: getattr(row, c.name) for c in Camera.__table__.columns},
            **{key: getattr(control, key) if control else "" for key in ONVIF_FIELDS},
            "view": {key: getattr(control, key) if control else value
                     for key, value in DEFAULT_VIEW.items()},
        }


def list_all():
    with Session() as db:
        ids = db.scalars(select(Camera.id)).all()
    return [get(i) for i in ids]


def reader(camera_id):
    with readers_lock:
        if camera_id not in readers or not readers[camera_id].thread.is_alive():
            readers[camera_id] = CameraReader(get(camera_id))
        return readers[camera_id]


def create(body):
    with Session() as db:
        cid = uuid4().hex
        values = body.model_dump()
        db.add(Camera(id=cid, **{k: values[k] for k in ("name", "url", "transport")}))
        db.flush()
        db.add(CameraControl(camera_id=cid, **{k: values[k] for k in ONVIF_FIELDS}))
        db.commit()
    return get(cid)


def release(camera_id):
    with readers_lock:
        item = readers.get(camera_id)
        if item:
            require(not item.pinned, "摄像头正在采集，请先停止采集", 409)
            item.stop()
            del readers[camera_id]


def update(camera_id, body):
    get(camera_id)
    release(camera_id)
    from app.services import lens_service
    lens_service.invalidate(camera_id)
    with Session() as db:
        row = db.get(Camera, camera_id)
        for k, v in body.model_dump(include={"name", "url", "transport"}).items():
            setattr(row, k, v)
        control = db.get(CameraControl, camera_id)
        if control is None:
            control = CameraControl(camera_id=camera_id)
            db.add(control)
        for key in set(ONVIF_FIELDS) & body.model_fields_set:
            setattr(control, key, getattr(body, key))
        db.commit()
    return get(camera_id)


def delete(camera_id):
    get(camera_id)
    release(camera_id)
    from app.services import lens_service
    lens_service.invalidate(camera_id)
    with Session() as db:
        db.delete(db.get(Camera, camera_id))
        db.commit()
    return {"ok": True}


def set_view(camera_id, body):
    # Readers and collection workers see one immutable view value at a time.
    with readers_lock:
        get(camera_id)
        view = body.model_dump()
        with Session() as db:
            control = db.get(CameraControl, camera_id)
            if control is None:
                control = CameraControl(camera_id=camera_id)
                db.add(control)
            for key, value in view.items():
                setattr(control, key, value)
            db.commit()
        capture = readers.get(camera_id)
        if capture:
            with capture.lock:
                capture.camera = {**capture.camera, "view": view}
                capture.jpeg, capture.jpeg_id = None, -1
        return view


def collection_snapshot(capture):
    frame_id, frame = capture.snapshot()
    view = capture.camera.get("view", DEFAULT_VIEW).copy()
    if frame is None:
        return frame_id, None, None
    viewed, area = apply_view(frame, view)
    return frame_id, viewed, {
        **view, "crop": area, "original_size": [frame.shape[1], frame.shape[0]]
    }


def test(camera_id, progress):
    capture = reader(camera_id)
    progress.update(total=1)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        progress.check()
        status = capture.status()
        if status["connected"]:
            progress.advance()
            return status
        time.sleep(0.1)
    raise RuntimeError("RTSP连接失败：请检查地址、账号、网络及摄像头在线状态")


def shutdown():
    with readers_lock:
        for capture in readers.values():
            capture.stop_event.set()
        for capture in readers.values():
            capture.stop()
        readers.clear()
