"""独立 FFmpeg 原码流录制，预览和模型任务不会抽掉录制帧。"""

from collections import deque
import errno
from functools import lru_cache
import hashlib
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
from uuid import uuid4

from app.core.storage import atomic_bytes, require
from app.services import camera_service
from . import media, store, workflow

guard = threading.RLock()
sessions = {}
log = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def rtsp_timeout_args(binary):
    try:
        result = subprocess.run(
            [binary, "-hide_banner", "-h", "demuxer=rtsp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        require(False, "无法读取 FFmpeg 的 RTSP 参数，请检查 FFmpeg 安装", 503)
    require(result.returncode == 0, "FFmpeg 无法读取 RTSP 输入参数", 503)
    help_text = result.stdout + result.stderr
    # 老版的 -timeout 会开启监听模式；拉取摄像头应使用 -stimeout。
    if re.search(r"(?m)^\s*-stimeout\s", help_text):
        return ("-stimeout", "5000000")
    if re.search(r"(?m)^\s*-timeout\s.*microseconds.*socket", help_text):
        return ("-timeout", "5000000")
    return ("-rw_timeout", "5000000")


def storage_error(error, path):
    reason = {
        errno.EACCES: "没有写入权限",
        errno.EPERM: "没有写入权限",
        errno.EROFS: "文件系统为只读",
        errno.ENOSPC: "磁盘空间不足",
        errno.EDQUOT: "磁盘配额不足",
    }.get(error.errno, error.strerror or "写入失败")
    return f"录像目录无法写入：{path}（{reason}）"


def verify_storage(path):
    with tempfile.TemporaryFile(dir=path) as probe:
        probe.write(b"recording-write-check")
        probe.flush()
        os.fsync(probe.fileno())


def sanitize_error(text, source_url):
    text = text.replace(source_url, "[摄像头地址]")
    return re.sub(r"(?i)rtsps?://[^\s<>\"']+", "[摄像头地址]", text).strip()


def recording_failure(code, details):
    hint = ""
    if "Permission denied" in details:
        hint = "，FFmpeg 访问被拒绝，请检查录像目录权限及摄像头访问权限"
    elif "Read-only file system" in details:
        hint = "，录像目录所在文件系统为只读"
    elif "No space left on device" in details or "Disk quota exceeded" in details:
        hint = "，录像目录空间或配额不足"
    message = f"FFmpeg 录制退出（退出码 {code}）{hint}"
    return message + ("：\n" + details[-1500:] if details else "")


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
    timeout_args = rtsp_timeout_args(binary)
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
        root = store.folder(rid)
        try:
            verify_storage(root)
        except OSError as exc:
            message = storage_error(exc, root)
            store.update(rid, state="failed", error=message)
            require(False, message, 503)
        try:
            process = subprocess.Popen(
                [
                    binary,
                    "-v",
                    "error",
                    "-nostdin",
                    "-rtsp_transport",
                    camera["transport"],
                    *timeout_args,
                    "-i",
                    camera["url"],
                    "-map",
                    "0:v:0",
                    "-an",
                    "-c:v",
                    "copy",
                    "-f",
                    "matroska",
                    str(root / "original.mkv"),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            message = f"无法启动 FFmpeg 原码流录制：{exc.strerror or '进程启动失败'}"
            store.update(rid, state="failed", error=message)
            require(False, message, 503)
        stderr_tail = deque(maxlen=32)

        def read_errors():
            with process.stderr:
                for line in process.stderr:
                    text = sanitize_error(
                        line.decode("utf-8", errors="replace"), camera["url"]
                    )
                    if text:
                        stderr_tail.append(text[-2000:])

        errors_thread = threading.Thread(
            target=read_errors, name="inspection-errors-" + rid[:8], daemon=True
        )
        errors_thread.start()
        item = {"camera_id": body.camera_id, "process": process, "stopping": False}
        sessions[rid] = item

        def watch():
            code = process.wait()
            errors_thread.join(timeout=2)
            details = "\n".join(stderr_tail)
            try:
                if details:
                    atomic_bytes(root / "ffmpeg.log", details.encode("utf-8"))
                path = root / "original.mkv"
                require(
                    path.is_file() and path.stat().st_size > 0,
                    "原码流未写入；" + recording_failure(code, details),
                )
                stopped = item["stopping"]
                # 断流不伪装成正常完成，也不把断流前后的人员动作拼接起来。
                if not stopped:
                    store.update(
                        rid,
                        state="failed",
                        error=(
                            "录像连接中断，已保留原文件；"
                            + recording_failure(code, details)
                        )[:2000],
                    )
                    return
                require(
                    code in (0, 255),
                    "原码流录制未正常结束，已保留原文件；"
                    + recording_failure(code, details),
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
