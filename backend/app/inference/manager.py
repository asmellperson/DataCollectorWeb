import logging
import threading
from pathlib import Path
from fastapi import HTTPException
from app.core.config import WORLD_MODEL, MODEL_ROOT, SCRFD_MODEL_PATH
from app.core.storage import require
from app.inference.yolo_world import WorldDetector
from app.inference.custom_yolo import CustomDetector

log = logging.getLogger(__name__)
# 类别设置和推理持同一把锁，防止不同数据集并发时类别串用。
lock = threading.RLock()
cache = {}


def resolve(provider, path=None):
    if provider == "scrfd":
        path = Path(SCRFD_MODEL_PATH).expanduser()
        require(
            path.is_file() and path.suffix.lower() == ".onnx",
            f"SCRFD 模型在当前服务器不可用：{path}。请在实际部署服务器配置 SCRFD_MODEL_PATH 并验证；不会自动下载模型。",
            503,
        )
        return str(path.resolve())
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
                if provider == "scrfd":
                    from app.inference.scrfd import SCRFDDetector

                    detector = SCRFDDetector(path)
                else:
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
                hint = (
                    "SCRFD 模型加载失败，请在实际部署服务器检查权重与 onnxruntime 依赖"
                    if provider == "scrfd"
                    else "模型加载失败，请检查权重、依赖和 DEVICE 配置"
                )
                raise HTTPException(400, f"{hint}：{exc}") from exc
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
            hint = (
                "SCRFD 推理失败，请在实际部署服务器检查模型输出与运行环境"
                if provider == "scrfd"
                else "推理失败，请检查类别配置、模型依赖或 CUDA 状态"
            )
            raise HTTPException(400, f"{hint}：{exc}") from exc
