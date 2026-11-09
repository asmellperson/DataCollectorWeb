from app.core.storage import require

# Ultralytics COCO contiguous class indices (not the original COCO category IDs).
COCO_NAMES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)


def source_class_ids(cls):
    """Normalize explicit mappings while preserving the legacy scalar setting."""
    if "model_class_ids" in cls:
        require(cls["model_class_ids"], "检测类别 ID 列表不能为空")
        return list(dict.fromkeys(cls["model_class_ids"]))
    source_id = cls.get("model_class_id")
    return [source_id] if source_id is not None else None


def trigger_classes(provider, targets, model_names=None):
    """Resolve trigger IDs in their own namespace, never as dataset label IDs."""
    if provider == "scrfd":
        require(
            targets and all(target.strip() in ("face", "0") for target in targets),
            "SCRFD 触发目标固定为 face（0）",
        )
        return [{"id": 0, "name": "face"}]
    names = dict(enumerate(COCO_NAMES)) if provider == "yolo_world" else model_names
    require(names is not None, "无法读取触发模型类别")
    names = {int(key): value for key, value in names.items()}
    resolved = []
    for target in targets:
        target = target.strip()
        require(target, "触发目标不能为空")
        if target.isdecimal():
            index = int(target)
            require(index in names, f"触发类别 ID {index} 不存在，请读取模型类别确认")
            name = names[index]
        else:
            name = target
            require(
                provider == "yolo_world" or name in names.values(),
                f"触发模型不支持类别 {name}，请读取模型类别确认",
            )
        if name not in resolved:
            resolved.append(name)
    require(resolved, "请至少填写一个触发目标")
    return [{"id": i, "name": name} for i, name in enumerate(resolved)]
