"""原文件入库、源帧时间索引及浏览器播放副本；不从预览队列录制。"""

from bisect import bisect_left
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import time
from uuid import uuid4

import cv2

from app.core.storage import read_json, require, write_json
from . import store

VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".ts", ".m4v"}


def executable(name):
    value = shutil.which(os.getenv(name.upper() + "_BIN", name))
    require(value, f"需要安装 {name}，用于原视频时间索引和播放副本", 503)
    return value


def run_process(args, progress, timeout=1800):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        while True:
            progress.check()
            require(
                time.monotonic() - started < timeout, "视频准备超时，请缩短单个录像文件"
            )
            try:
                output, _ = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        # FFmpeg 错误可能带输入地址，不向 API 或持久化日志透传。
        require(process.returncode == 0, "视频处理失败，请检查文件完整性与编码")
        return output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def validate_path(path):
    path = Path(path).expanduser().resolve()
    require(path.is_file(), "服务器视频文件不存在")
    require(path.suffix.lower() in VIDEO_EXTS, "支持 MP4、AVI、MKV、MOV、TS、M4V")
    return path


def copy_source(recording_id, path, progress):
    destination = store.folder(recording_id) / ("original" + path.suffix.lower())
    require(not destination.exists(), "原视频已经存在，不能覆盖", 409)
    before = path.stat()
    digest = hashlib.sha256()
    temp = destination.with_suffix(destination.suffix + ".part")
    try:
        with path.open("rb") as source, temp.open("xb") as target:
            while block := source.read(4 * 1024 * 1024):
                progress.check()
                target.write(block)
                digest.update(block)
        after = path.stat()
        require(
            (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            "源文件仍在写入，请在录像停止后导入",
        )
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    store.update(
        recording_id,
        original_file=destination.name,
        sha256=digest.hexdigest(),
        original_bytes=before.st_size,
    )


def normalize_timestamps(raw_times, fps, *, allow_repairs=True):
    origin = raw_times[0]
    origin_us = round(origin * 1_000_000)
    source_us = [round(value * 1_000_000) - origin_us for value in raw_times]
    steps = [b - a for a, b in zip(source_us, source_us[1:]) if b > a]
    frame_us = 1_000_000 / fps if fps > 0 else median(steps) if steps else 40_000
    limit_us = max(500_000, frame_us * 5)
    times_us = []
    corrected = []
    last_good = 0
    for index, value in enumerate(source_us):
        if times_us and value <= times_us[-1]:
            require(allow_repairs, "播放副本时间轴异常，未发布，请重新准备原文件")
            require(
                times_us[-1] - value <= limit_us
                and (index - last_good) * frame_us <= limit_us,
                f"原视频第 {index + 1} 帧时间戳持续重复或大幅倒退，"
                f"超出轻微抖动修复范围（当前 {value / 1_000_000:.6f} 秒，"
                f"上一帧 {times_us[-1] / 1_000_000:.6f} 秒）；原文件已保留",
            )
            # 仅消除排序冲突，保留其他源 PTS；不能按固定帧率重写整段。
            value = times_us[-1] + 1
            corrected.append(index)
        else:
            last_good = index
        times_us.append(value)
    return (
        [value / 1_000_000 for value in times_us],
        [value / 1_000_000 for value in source_us],
        corrected,
    )


def index_video(path, progress, *, allow_repairs=True):
    output = run_process(
        [
            executable("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,r_frame_rate:frame=best_effort_timestamp_time,pkt_duration_time,duration_time",
            "-of",
            "json",
            str(path),
        ],
        progress,
    )
    data = json.loads(output)
    frames = data.get("frames", [])
    require(frames and data.get("streams"), "原视频没有可解码的视频帧")
    require(
        all("best_effort_timestamp_time" in frame for frame in frames),
        "原视频缺少帧时间戳，不能可靠标注快速动作",
    )
    raw_times = [float(frame["best_effort_timestamp_time"]) for frame in frames]
    require(all(math.isfinite(t) for t in raw_times), "视频帧时间戳无效")
    origin = raw_times[0]
    stream = data["streams"][0]
    fps = 0
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            numerator, denominator = stream.get(key, "0/1").split("/")
            value = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(value) and value > 0:
            fps = value
            break
    times, source_times, corrected = normalize_timestamps(
        raw_times, fps, allow_repairs=allow_repairs
    )
    last_duration = float(
        frames[-1].get("duration_time", frames[-1].get("pkt_duration_time", 0)) or 0
    )
    if not math.isfinite(last_duration) or last_duration <= 0:
        last_duration = times[-1] - times[-2] if len(times) > 1 else 1 / (fps or 25)
    duration = round(times[-1] + last_duration, 6)
    repairs = []
    for index in corrected:
        issue = {
            "start_frame": index - 1,
            "end_frame": index,
            "start_sec": times[index - 1],
            "end_sec": times[index + 1] if index + 1 < len(times) else duration,
        }
        if repairs and issue["start_sec"] <= repairs[-1]["end_sec"]:
            repairs[-1]["end_frame"] = index
            repairs[-1]["end_sec"] = issue["end_sec"]
        else:
            repairs.append(issue)
    gaps = [
        {"start_sec": a, "end_sec": b}
        for a, b in zip(times, times[1:])
        if b - a > max(0.5, 5 / (fps or 25))
    ]
    return {
        "times_sec": times,
        **({"source_times_sec": source_times} if corrected else {}),
        "timestamp_repair_count": len(corrected),
        "timestamp_repairs": repairs,
        "time_basis": "source_pts_with_local_monotonic_repairs"
        if corrected else "source_pts_seconds_from_first_frame",
        "origin_sec": origin,
        "duration_sec": duration,
        "gaps": gaps,
        "frame_count": len(times),
        "fps_hint": round(fps, 6),
        "width": stream["width"],
        "height": stream["height"],
    }


def verify_preview(index, preview):
    require(
        preview["frame_count"] == index["frame_count"],
        "播放副本帧数与原视频不一致，未发布；原文件已保留",
    )
    require(
        all(abs(a - b) <= 0.000002 for a, b in zip(index["times_sec"], preview["times_sec"])),
        "播放副本与原视频时间轴不一致，未发布；原文件已保留",
    )


def prepare(recording_id, progress):
    meta = store.get(recording_id)
    root = store.folder(recording_id)
    path = root / meta["original_file"]
    require(path.is_file(), "已保存的原视频不存在，请重新导入")
    if not meta.get("sha256"):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(4 * 1024 * 1024):
                progress.check()
                digest.update(block)
        store.update(
            recording_id, sha256=digest.hexdigest(), original_bytes=path.stat().st_size
        )
    progress.update(total=2)
    index = index_video(path, progress)
    write_json(root / "timeline.json", index)
    progress.advance()
    temporary = root / f"preview_{uuid4().hex}.mp4"
    try:
        run_process(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-copyts",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                "settb=1/1000000,setpts='if(eq(N,0),0,max(PTS-STARTPTS,PREV_OUTPTS+1))',"
                "scale=trunc(min(1280\\,iw)/2)*2:-2",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-bf",
                "0",
                "-pix_fmt",
                "yuv420p",
                "-enc_time_base",
                "1:1000000",
                "-video_track_timescale",
                "1000000",
                "-vsync",
                "0",
                "-movflags",
                "+faststart",
                str(temporary),
            ],
            progress,
        )
        preview = index_video(temporary, progress, allow_repairs=False)
        verify_preview(index, preview)
        progress.check()
        temporary.replace(root / "preview.mp4")
    finally:
        temporary.unlink(missing_ok=True)
    values = {
        key: value for key, value in index.items()
        if key not in ("times_sec", "source_times_sec")
    }
    store.update(recording_id, **values, state="ready", error="")
    progress.advance()
    return {"recording_id": recording_id}


def timeline(recording_id):
    return read_json(store.folder(recording_id) / "timeline.json")


def encode_frame(frame, max_side=1920):
    h, w = frame.shape[:2]
    scale = min(1, max_side / max(h, w))
    if scale < 1:
        frame = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))))
    ok, value = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    require(ok, "原视频帧编码失败")
    return value.tobytes()


def frame(recording_id, frame_index):
    meta = store.get(recording_id)
    require(meta["state"] == "ready", "录像尚未准备完成", 409)
    require(0 <= frame_index < meta["frame_count"], "帧号超出原视频")
    cap = cv2.VideoCapture(str(store.folder(recording_id) / meta["original_file"]))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, image = cap.read()
        require(ok, "原视频帧读取失败")
        return encode_frame(image)
    finally:
        cap.release()


def window_frames(recording_id, times, start, end, options, progress):
    indices = set()
    sample_number = 0
    while (
        target := round(start + sample_number / options.sample_fps, 6)
    ) < end - 0.000001:
        # 源 PTS 已按微秒归一化；避免 0.24 的浮点累积把短动作所在帧跳过去。
        index = bisect_left(times, target - 0.0000001)
        if index < len(times) and times[index] < end:
            indices.add(index)
        sample_number += 1
    last = bisect_left(times, end) - 1
    if last >= 0 and times[last] >= start:
        indices.add(last)
    require(indices, "窗口内没有可读取的视频帧")
    meta = store.get(recording_id)
    cap = cv2.VideoCapture(str(store.folder(recording_id) / meta["original_file"]))
    result = []
    try:
        first = min(indices)
        cap.set(cv2.CAP_PROP_POS_FRAMES, first)
        for index in range(first, max(indices) + 1):
            progress.check()
            ok, image = cap.read()
            require(ok, "预标期间原视频解码中断")
            if index in indices:
                result.append(
                    {
                        "frame_index": index,
                        "time_sec": times[index],
                        "jpeg": encode_frame(image, options.image_max_side),
                    }
                )
    finally:
        cap.release()
    return result
