import math
from pathlib import Path
import cv2
from app.core.storage import require
from app.inference import manager
from app.services import annotation_service as annotations, dataset_service as datasets

VIDEO_EXTS = {".mp4", ".avi", ".mkv"}


def validate(path):
    path = Path(path).expanduser().resolve()
    require(path.is_file(), "服务器视频文件不存在")
    require(path.suffix.lower() in VIDEO_EXTS, "视频仅支持 MP4、AVI、MKV")
    return path


def extract(dataset_id, path, options, progress, filename=None):
    dataset = datasets.get(dataset_id)
    require(
        options.auto_label == "off" or dataset["task_type"] == "detection",
        "视频自动预标仅支持 detection 数据集",
    )
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    try:
        require(cap.isOpened(), "视频读取失败，请检查格式和编码")
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        require(
            options.sampling != "seconds" or (math.isfinite(fps) and fps > 0),
            "视频帧率无效，无法按秒采样，请改用每 N 帧",
        )
        step = options.every if options.sampling == "frames" else options.every * fps
        estimate = max(1, math.ceil(count / max(1, step))) if count > 0 else 0
        progress.update(total=estimate)
        if options.auto_label != "off":
            with manager.lock:
                manager.get(options.auto_label, options.model_path).set_classes(
                    dataset["classes"]
                )
        index = 0
        saved = 0
        next_time = 0.0
        while True:
            progress.check()
            ok, image = cap.read()
            if not ok:
                break
            source_seconds = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if source_seconds <= 0 and fps > 0:
                source_seconds = index / fps
            selected = (
                index % int(options.every) == 0
                if options.sampling == "frames"
                else source_seconds + 1e-8 >= next_time
            )
            if selected:
                if options.sampling == "seconds":
                    next_time = source_seconds + options.every
                objects = []
                if options.auto_label != "off":
                    objects = manager.predict(
                        options.auto_label,
                        options.model_path,
                        dataset["classes"],
                        image,
                        options.confidence,
                    )
                progress.check()
                annotations.save_sample(
                    dataset_id,
                    image,
                    {
                        "type": "video",
                        "filename": filename or path.name,
                        "frame_index": index,
                        "video_seconds": source_seconds,
                    },
                    objects=objects,
                )
                saved += 1
                if saved > estimate:
                    progress.update(total=saved)
                progress.advance()
            index += 1
        require(index > 0, "视频读取失败，未解码出任何图像")
        require(
            not count or index >= count - 2,
            "视频在结尾前解码中断，已保留成功采样的图片",
        )
        progress.update(total=saved)
        return {"decoded_frames": index, "saved": saved, "fps": fps}
    finally:
        cap.release()
