"""独立 FFmpeg 原码流录制，预览和模型任务不会抽掉录制帧。"""

import hashlib
import logging
import signal
import subprocess
import threading
from uuid import uuid4

from app.core.storage import require
from app.services import camera_service
from . import media, store, workflow

guard = threading.RLock()
sessions = {}
log = logging.getLogger(__name__)


def status():
    with guard:
        return [
            {
                "recording_id": rid,
                "camera_id": item["camera_id"],
                "stopping": item["stopping"],
            }
            for rid, item in sessions.items()
        ]


def start(body):
    binary = media.executable("ffmpeg")
    media.executable("ffprobe")
    camera = camera_service.get(body.camera_id)
    with guard:
        require(
            not any(s["camera_id"] == body.camera_id for s in sessions.values()),
            "此摄像头已有连续原视频录制",
            409,
        )
        group = body.source_group or f"{body.camera_id}:{store.now()[:10]}"
        meta = store.create(
            body.name or camera["name"],
            group,
            source_type="rtsp",
            camera_id=body.camera_id,
            state="recording",
            original_file="original.mkv",
        )
        rid = meta["id"]
        try:
            process = subprocess.Popen(
                [
                    binary,
                    "-v",
                    "error",
                    "-nostdin",
                    "-rtsp_transport",
                    camera["transport"],
                    "-timeout",
                    "5000000",
                    "-i",
                    camera["url"],
                    "-map",
                    "0:v:0",
                    "-an",
                    "-c:v",
                    "copy",
                    "-f",
                    "matroska",
                    str(store.folder(rid) / "original.mkv"),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            store.update(rid, state="failed", error="无法启动 FFmpeg 原码流录制")
            raise
        item = {"camera_id": body.camera_id, "process": process, "stopping": False}
        sessions[rid] = item

        def watch():
            code = process.wait()
            try:
                path = store.folder(rid) / "original.mkv"
                require(
                    path.is_file() and path.stat().st_size > 0,
                    "原码流未写入，请检查摄像头连接",
                )
                stopped = item["stopping"]
                # 断流不伪装成正常完成，也不把断流前后的人员动作拼接起来。
                if not stopped:
                    store.update(
                        rid,
                        state="failed",
                        error=f"录像连接中断（退出码 {code}）；已保留原文件，可重新准备查看",
                    )
                    return
                require(
                    code in (0, 255),
                    "原码流录制未正常结束；已保留原文件，可重新准备检查",
                )
                store.update(rid, state="preparing")

                def prepare(progress):
                    digest = hashlib.sha256()
                    with path.open("rb") as source:
                        while chunk := source.read(4 * 1024 * 1024):
                            progress.check()
                            digest.update(chunk)
                    store.update(
                        rid,
                        sha256=digest.hexdigest(),
                        original_bytes=path.stat().st_size,
                    )
                    return media.prepare(rid, progress)

                workflow.submit(rid, "inspection_prepare", prepare)
            except Exception as exc:
                store.update(
                    rid, state="failed", error=str(getattr(exc, "detail", exc))[:2000]
                )
            finally:
                with guard:
                    sessions.pop(rid, None)

        thread = threading.Thread(
            target=watch, name="inspection-record-" + uuid4().hex[:8], daemon=True
        )
        item["thread"] = thread
        thread.start()
        return {"recording_id": rid}


def stop(recording_id):
    with guard:
        item = sessions.get(recording_id)
        require(item, "该录像没有运行中的录制", 409)
        if not item["stopping"]:
            item["stopping"] = True
            if item["process"].poll() is None:
                try:
                    item["process"].send_signal(signal.SIGINT)
                except ProcessLookupError:
                    log.info("录像进程已退出：%s", recording_id)

                def finish():
                    try:
                        item["process"].wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        item["process"].kill()
                        log.warning(
                            "录像进程停止超时，原文件保留待检查：%s", recording_id
                        )

                threading.Thread(
                    target=finish, name="inspection-capture-stop", daemon=True
                ).start()
        return {"recording_id": recording_id, "stopping": True}


def shutdown():
    with guard:
        items = list(sessions.items())
    for rid, item in items:
        try:
            stop(rid)
        except Exception:
            log.exception("关闭录像时状态已改变：%s", rid)
            continue
        try:
            item["process"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            item["process"].kill()
        item["thread"].join(timeout=15)
