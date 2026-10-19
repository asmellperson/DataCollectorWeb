from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# OpenCV/FFmpeg 读取 RTSP 时强制 TCP，并设置较短超时。
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|max_delay;500000",
)

import cv2
import numpy as np


class LatestFrameReader:
    """后台读取视频流；队列永远只保留最新帧。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[int, float, np.ndarray]] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture: cv2.VideoCapture | None = None
        self._lock = threading.Lock()
        self.status = "未连接"
        self.last_error = ""
        self.source_fps = 25.0
        self.frames_read = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, url: str) -> None:
        self.stop(wait=True)
        self._stop.clear()
        self.status = "正在连接…"
        self.last_error = ""
        self.frames_read = 0
        self._thread = threading.Thread(
            target=self._run, args=(url,), name="rtsp-reader", daemon=True
        )
        self._thread.start()

    def _open_capture(self, url: str) -> cv2.VideoCapture:
        params = []
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            params += [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000]
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            params += [cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000]
        try:
            return cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
        except (TypeError, cv2.error):
            return cv2.VideoCapture(url, cv2.CAP_FFMPEG)

    def _run(self, url: str) -> None:
        cap = self._open_capture(url)
        with self._lock:
            self._capture = cap
        if not cap.isOpened():
            self.last_error = "无法打开视频流，请检查地址、网络和摄像头权限。"
            self.status = "连接失败"
            cap.release()
            return

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        if 1.0 <= fps <= 120.0:
            self.source_fps = fps
        self.status = "已连接"

        consecutive_failures = 0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    self.last_error = "视频流连续读取失败，连接已断开。"
                    self.status = "连接中断"
                    break
                time.sleep(0.03)
                continue
            consecutive_failures = 0
            self.frames_read += 1
            item = (self.frames_read, time.monotonic(), frame)
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    pass

        cap.release()
        with self._lock:
            self._capture = None
        if self.status == "已连接":
            self.status = "已断开"

    def latest(self) -> tuple[int, float, np.ndarray] | None:
        item = None
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return item

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        if wait and self._thread and self._thread.is_alive():
            # 依靠读取超时退出，避免在 UI 线程中 release 正在读取的 capture。
            self._thread.join(timeout=6.0)
        if not self.running:
            self.status = "已断开"


@dataclass
class RecorderResult:
    frames_written: int = 0
    frames_dropped: int = 0
    width: int = 0
    height: int = 0
    error: str = ""


class AsyncVideoRecorder:
    """后台编码视频，避免磁盘和编码开销阻塞界面。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=12)
        self._thread: threading.Thread | None = None
        self._path: Path | None = None
        self._fps = 25.0
        self._active = False
        self.result = RecorderResult()

    @property
    def recording(self) -> bool:
        return self._active

    def start(self, path: Path, fps: float) -> None:
        if self.recording:
            raise RuntimeError("正在录制中")
        self._path = path
        self._fps = min(max(float(fps), 5.0), 60.0)
        self.result = RecorderResult()
        self._queue = queue.Queue(maxsize=12)
        self._active = True
        self._thread = threading.Thread(
            target=self._run, name="video-writer", daemon=True
        )
        self._thread.start()

    def submit(self, frame: np.ndarray) -> None:
        if not self.recording:
            return
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            self.result.frames_dropped += 1

    def _run(self) -> None:
        writer: cv2.VideoWriter | None = None
        try:
            while True:
                frame = self._queue.get()
                if frame is None:
                    break
                if writer is None:
                    height, width = frame.shape[:2]
                    self.result.width = width
                    self.result.height = height
                    assert self._path is not None
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(
                        str(self._path), fourcc, self._fps, (width, height)
                    )
                    if not writer.isOpened():
                        raise RuntimeError("无法创建 MP4 文件（mp4v 编码器不可用）")
                writer.write(frame)
                self.result.frames_written += 1
        except Exception as exc:  # noqa: BLE001 - 后台线程错误必须通过 result 交给 UI
            self.result.error = str(exc)
        finally:
            if writer is not None:
                writer.release()

    def stop(self, timeout: float = 8.0) -> RecorderResult:
        if self._thread and self._thread.is_alive():
            while True:
                try:
                    self._queue.put(None, timeout=0.2)
                    break
                except queue.Full:
                    continue
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self.result.error = "视频编码线程未能及时结束"
        self._active = False
        return self.result
