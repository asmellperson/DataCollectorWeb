"""Validate YOLO imports before images are saved; all paths are relative metadata."""

import math
import re
from pathlib import PurePosixPath
from uuid import uuid4

import yaml
from fastapi import HTTPException

from app.core.storage import require
from app.schemas.contracts import ClassItem
from app.services import dataset_service as datasets

MAX_LABEL_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        require(
            isinstance(key, (str, int)) and not isinstance(key, bool), "YAML 键无效"
        )
        require(key not in result, f"YAML 中存在重复键：{key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def validate_classes(classes):
    require(classes, "请填写类别或选择 YAML")
    try:
        result = [ClassItem.model_validate(c).model_dump() for c in classes]
    except ValueError as exc:
        raise HTTPException(400, "类别编号应为 0–9999，名称应为 1–100 个字符") from exc
    for item in result:
        item["name"] = item["name"].strip()
    datasets.validate_classes(result, "detection")
    return result


def parse_classes(classes_text="", yaml_text=""):
    require(not (classes_text.strip() and yaml_text.strip()), "请选择一种类别来源")
    if yaml_text.strip():
        try:
            data = yaml.load(yaml_text, Loader=UniqueKeyLoader)
        except (yaml.YAMLError, ValueError, RecursionError) as exc:
            raise HTTPException(400, "YAML 格式错误") from exc
        require(isinstance(data, dict), "YAML 需要包含 names")
        names = data.get("names")
        require(isinstance(names, (dict, list)) and names, "YAML 需要包含 names")
        pairs = enumerate(names) if isinstance(names, list) else names.items()
        classes = []
        for key, name in pairs:
            require(
                not isinstance(key, bool) and re.fullmatch(r"[0-9]+", str(key)),
                "YAML 类别编号必须是非负整数",
            )
            require(isinstance(name, str), "YAML 类别名称必须是文字")
            classes.append({"id": int(key), "name": name})
    else:
        classes = []
        for number, line in enumerate(classes_text.splitlines(), 1):
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*([0-9]+)\s+(.+?)\s*", line)
            require(match, f"类别第 {number} 行格式应为：0 person")
            classes.append({"id": int(match[1]), "name": match[2]})
    return validate_classes(classes)


def relative_name(name):
    require(isinstance(name, str) and name and "\\" not in name, "文件路径无效")
    path = PurePosixPath(name)
    require(
        not path.is_absolute()
        and all(part not in ("", ".", "..") for part in name.split("/")),
        "文件路径必须是目录内的相对路径",
    )
    return path


def label_name(image_name):
    return str(relative_name(image_name).with_suffix(".txt"))


def parse_label(text, class_ids, filename):
    require(len(text.encode("utf-8")) <= MAX_LABEL_BYTES, f"{filename}：标签文件过大")
    boxes = []
    for number, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        if not line.strip():
            continue
        prefix = f"{filename}:{number}"
        parts = line.split()
        require(len(parts) == 5, f"{prefix}：每行应为类别编号和 4 个框坐标")
        require(re.fullmatch(r"[0-9]+", parts[0]), f"{prefix}：类别编号无效")
        cid = int(parts[0])
        require(cid in class_ids, f"{prefix}：类别 {cid} 未定义")
        try:
            x, y, width, height = map(float, parts[1:])
        except ValueError as exc:
            raise HTTPException(400, f"{prefix}：框坐标必须是数字") from exc
        require(
            all(math.isfinite(v) for v in (x, y, width, height))
            and 0 <= x <= 1
            and 0 <= y <= 1
            and 0 < width <= 1
            and 0 < height <= 1,
            f"{prefix}：需要 0–1 归一化坐标，宽高必须大于 0",
        )
        corners = [x - width / 2, y - height / 2, x + width / 2, y + height / 2]
        require(
            all(-1e-6 <= v <= 1 + 1e-6 for v in corners),
            f"{prefix}：标注框超出图片范围",
        )
        boxes.append((cid, [max(0.0, min(1.0, v)) for v in corners]))
    return boxes


def inspect_labels(image_names, labels, classes):
    require(image_names, "请选择图片目录")
    require(
        sum(len(v.encode("utf-8")) for v in labels.values()) <= MAX_TOTAL_BYTES,
        "标签总大小不能超过 32 MB，请分批导入",
    )
    ids = {c["id"] for c in classes}
    expected = set()
    errors = []
    error_count = 0
    matched = empty = 0
    parsed = {}

    def fail(message):
        nonlocal error_count
        error_count += 1
        if len(errors) < 20:
            errors.append(message)

    for name in image_names:
        key = label_name(name)
        if key in expected:
            fail(f"图片名称冲突：{name}")
        expected.add(key)
    normalized = {}
    for name, text in labels.items():
        path = relative_name(name)
        require(path.suffix.lower() == ".txt", "标签文件必须为 TXT")
        key = str(path.with_suffix(".txt"))
        if key in normalized:
            fail(f"标签名称冲突：{name}")
        normalized[key] = text
        try:
            parsed[key] = parse_label(text, ids, name)
        except HTTPException as exc:
            fail(exc.detail)
    for key in expected:
        if key in normalized:
            matched += 1
            empty += int(key in parsed and not parsed[key])
    report = {
        "classes": classes,
        "images": len(image_names),
        "matched": matched,
        "missing": len(expected - normalized.keys()),
        "empty": empty,
        "unmatched": len(normalized.keys() - expected),
        "errors": errors,
        "error_count": error_count,
    }
    return report, parsed


def resolve_mapping(dataset, classes, mapping):
    require(dataset["task_type"] == "detection", "已有标签仅支持目标检测数据集")
    source_ids = {c["id"] for c in classes}
    targets = {c["id"]: c for c in dataset["classes"]}
    by_name = {c["name"]: c["id"] for c in dataset["classes"]}
    require(set(mapping) <= source_ids, "类别对应包含未定义的来源编号")
    result = {}
    for source in classes:
        target = mapping.get(source["id"], by_name.get(source["name"]))
        require(target in targets, f"请为 {source['id']} {source['name']} 选择对应类别")
        result[source["id"]] = targets[target]
    return result


def to_objects(boxes, mapping, width, height):
    return [
        {
            "id": "obj_" + uuid4().hex,
            "class_id": mapping[cid]["id"],
            "class_name": mapping[cid]["name"],
            "bbox": [
                v * (width if index % 2 == 0 else height)
                for index, v in enumerate(corners)
            ],
            "confidence": None,
            "source": "manual",
        }
        for cid, corners in boxes
    ]
