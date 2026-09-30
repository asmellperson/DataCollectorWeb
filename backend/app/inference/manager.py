import logging
import threading
from pathlib import Path
from fastapi import HTTPException
from app.core.config import WORLD_MODEL, MODEL_ROOT
from app.core.storage import require
from app.inference.yolo_world import WorldDetector
from app.inference.custom_yolo import CustomDetector

log = logging.getLogger(__name__)
# 类别设置和推理持同一把锁，防止不同数据集并发时类别串用。
lock = threading.RLock()
cache = {}


def resolve(provider, path=None):
    if provider == "yolo_world":
        path = WORLD_MODEL
    require(provider in ("yolo_world", "custom_yolo"), "检测器类型不正确")
    require(path, "请选择自定义 YOLO 模型路径")
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = MODEL_ROOT / path
    require(
        path.is_file() and path.suffix.lower() == ".pt", "模型文件不存在或不是 .pt 文件"
    )
    return str(path.resolve())


def get(provider, path=None):
    path = resolve(provider, path)
    key = (provider, path)
    with lock:
        if key not in cache:
            try:
                from ultralytics import settings

                settings.update({"weights_dir": str(MODEL_ROOT)})
                detector = (
                    WorldDetector(path)
                    if provider == "yolo_world"
                    else CustomDetector(path)
                )
                detector.load()
                cache[key] = detector
            except Exception as exc:
                log.exception("模型加载失败")
                raise HTTPException(
                    400, f"模型加载失败，请检查权重、依赖和 DEVICE 配置：{exc}"
                ) from exc
        return cache[key]


def predict(provider, path, classes, image, confidence):
    with lock:
        try:
            detector = get(provider, path)
            detector.set_classes(classes)
            return detector.predict(image, confidence)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("推理失败")
            raise HTTPException(
                400, f"推理失败，请检查类别配置、模型依赖或 CUDA 状态：{exc}"
            ) from exc
