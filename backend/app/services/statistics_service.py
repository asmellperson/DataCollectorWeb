"""Read-only statistics over authoritative annotations, with a consistent snapshot."""

import math
from collections import Counter
from pathlib import Path
from statistics import median

from sqlalchemy import select

from app.core.storage import dataset_lock, read_json, require
from app.db.database import Session, Sample, now
from app.services import dataset_service as datasets


ISSUES = {
    "missing_image": "原图文件缺失或路径无效",
    "missing_annotation": "标注文件缺失",
    "invalid_annotation": "标注文件格式错误",
    "metadata_mismatch": "标注与图片索引信息不一致",
    "unknown_class": "未定义或无效的类别 ID",
    "invalid_box": "框坐标非法、越界或面积为零",
    "invalid_classification": "分类标签格式错误或已确认图片缺少分类",
    "unexpected_labels": "标注类型与数据集任务不一致",
    "duplicate_box": "疑似重复框（同类别、相同坐标）",
    "tiny_box": "极小框（宽或高不足 2 像素）",
    "elongated_box": "细长框（长宽比超过 20）",
}
WARNINGS = {"duplicate_box", "tiny_box", "elongated_box"}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 0


def valid_box(box, width, height):
    if not isinstance(box, list) or len(box) != 4:
        return False
    if not all(type(v) is int or (type(v) is float and math.isfinite(v)) for v in box):
        return False
    x1, y1, x2, y2 = box
    return 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


def statistics(dataset_id: str, status: str | None = None):
    require(status in (None, "pending", "approved", "rejected"), "审核状态无效")
    # No decoding, inference or writes inside the lock. JSON and the index must
    # belong to the same snapshot even while a collection worker is saving.
    with dataset_lock(dataset_id):
        root = datasets.folder(dataset_id)
        dataset = datasets.get(dataset_id)
        with Session() as db:
            query = select(Sample).where(Sample.dataset_id == dataset_id)
            if status:
                query = query.where(Sample.status == status)
            rows = db.scalars(query.order_by(Sample.created_at, Sample.id)).all()
        return summarize(root, dataset, rows, status)


def summarize(root: Path, dataset: dict, rows, status):
    task_type = dataset["task_type"]
    classes = {
        c["id"]: {
            **c,
            "box_count": 0,
            "image_count": 0,
            "max_boxes": 0,
            "approved": 0,
            "pending": 0,
            "rejected": 0,
            "small": 0,
            "medium": 0,
            "large": 0,
        }
        for c in dataset["classes"]
    }
    overview = {
        "images": len(rows),
        "annotations_readable": 0,
        "images_with_labels": 0,
        "empty_images": 0,
        "confirmed_negative": 0,
        "pending_empty": 0,
        "boxes": 0,
        "invalid_records": 0,
        "missing_images": 0,
        "issue_images": 0,
        "approved": 0,
        "pending": 0,
        "rejected": 0,
    }
    samples = []
    issue_records, issue_images = Counter(), Counter()
    resolutions, orientations, formats, sources, box_sources = (
        Counter() for _ in range(5)
    )
    histogram = dict.fromkeys(["0", "1", "2–5", "6–10", ">10"], 0)
    box_counts = []
    for row in rows:
        issues = Counter()
        per_class = Counter()
        count = 0
        annotation_ok = False
        reviewable = True
        is_empty = False
        source_type = "unknown"
        filename = Path(row.image).name
        overview[row.status] = overview.get(row.status, 0) + 1
        resolutions[f"{row.width} × {row.height}"] += 1
        orientations[
            "横图"
            if row.width > row.height
            else "竖图"
            if row.width < row.height
            else "方图"
        ] += 1
        formats[Path(row.image).suffix.lower() or "未知"] += 1
        image_path = (root / row.image).resolve()
        image_exists = (
            image_path.is_relative_to(root.resolve()) and image_path.is_file()
        )
        if not image_exists:
            issues["missing_image"] += 1
            overview["missing_images"] += 1
            reviewable = False
        path = root / "annotations" / f"{row.id}.json"
        ann = None
        try:
            ann = read_json(path)
            if not isinstance(ann, dict) or not isinstance(ann.get("objects"), list):
                raise ValueError("Invalid annotation structure")
            annotation_ok = True
        except FileNotFoundError:
            issues["missing_annotation"] += 1
        except (OSError, ValueError, TypeError):
            issues["invalid_annotation"] += 1
        if not annotation_ok:
            reviewable = False
        else:
            overview["annotations_readable"] += 1
            if any(
                ann.get(key) != getattr(row, key)
                for key in ("id", "image", "width", "height", "status")
            ):
                issues["metadata_mismatch"] += 1
                reviewable = False
            source = ann.get("source")
            if isinstance(source, dict):
                source_type = str(source.get("type") or "unknown")
                if isinstance(source.get("filename"), str):
                    filename = source["filename"]
            if task_type == "detection":
                if ann.get("classification") is not None:
                    issues["unexpected_labels"] += 1
                    reviewable = False
                is_empty = (
                    len(ann["objects"]) == 0 and ann.get("classification") is None
                )
                seen = set()
                for obj in ann["objects"]:
                    if not isinstance(obj, dict):
                        issues["invalid_annotation"] += 1
                        overview["invalid_records"] += 1
                        reviewable = False
                        continue
                    cid = obj.get("class_id")
                    known = type(cid) is int and cid in classes
                    box = obj.get("bbox")
                    geometry_ok = valid_box(box, row.width, row.height)
                    if not known:
                        issues["unknown_class"] += 1
                    if not geometry_ok:
                        issues["invalid_box"] += 1
                    if not known or not geometry_ok:
                        overview["invalid_records"] += 1
                        reviewable = False
                        continue
                    count += 1
                    per_class[cid] += 1
                    cls = classes[cid]
                    cls["box_count"] += 1
                    width, height = box[2] - box[0], box[3] - box[1]
                    area = width * height / (row.width * row.height)
                    cls[
                        "small" if area < 0.01 else "medium" if area < 0.1 else "large"
                    ] += 1
                    box_source = obj.get("source")
                    box_sources[
                        box_source
                        if box_source in ("manual", "yolo_world", "custom_yolo")
                        else "unknown"
                    ] += 1
                    signature = (cid, *box)
                    if signature in seen:
                        issues["duplicate_box"] += 1
                    seen.add(signature)
                    if min(width, height) < 2:
                        issues["tiny_box"] += 1
                    if max(width, height) / min(width, height) > 20:
                        issues["elongated_box"] += 1
                    # The review editor requires these fields even when a box
                    # is otherwise countable; report malformed records safely.
                    if not isinstance(obj.get("id"), str) or not isinstance(
                        obj.get("class_name"), str
                    ):
                        issues["invalid_annotation"] += 1
                        reviewable = False
                box_counts.append(count)
                histogram[
                    "0"
                    if count == 0
                    else "1"
                    if count == 1
                    else "2–5"
                    if count <= 5
                    else "6–10"
                    if count <= 10
                    else ">10"
                ] += 1
            elif task_type == "classification":
                classification = ann.get("classification")
                is_empty = classification is None and not ann["objects"]
                if classification is None:
                    if row.status == "approved":
                        issues["invalid_classification"] += 1
                elif not isinstance(classification, dict):
                    issues["invalid_classification"] += 1
                    overview["invalid_records"] += 1
                    reviewable = False
                else:
                    cid = classification.get("class_id")
                    if type(cid) is int and cid in classes:
                        per_class[cid] = 1
                    else:
                        issues["unknown_class"] += 1
                        overview["invalid_records"] += 1
                        reviewable = False
                if ann["objects"]:
                    issues["unexpected_labels"] += 1
                    reviewable = False
            elif ann["objects"] or ann.get("classification") is not None:
                issues["unexpected_labels"] += 1
                reviewable = False
            # An inconsistent record cannot establish that an image has been
            # reviewed as empty, even if the index happens to say approved.
            if issues["metadata_mismatch"]:
                is_empty = False
            for cid, amount in per_class.items():
                cls = classes[cid]
                cls["image_count"] += 1
                cls[row.status] = cls.get(row.status, 0) + 1
                cls["max_boxes"] = max(
                    cls["max_boxes"], amount if task_type == "detection" else 0
                )
            overview["boxes"] += count
            overview["images_with_labels"] += bool(per_class)
            overview["empty_images"] += is_empty
            overview["confirmed_negative"] += (
                task_type == "detection" and is_empty and row.status == "approved"
            )
            overview["pending_empty"] += is_empty and row.status == "pending"
        sources[source_type] += 1
        if issues:
            overview["issue_images"] += 1
        issue_records.update(issues)
        issue_images.update(issues.keys())
        samples.append(
            {
                "id": row.id,
                "image": row.image,
                "filename": filename,
                "width": row.width,
                "height": row.height,
                "status": row.status,
                "box_count": count,
                "class_ids": sorted(per_class),
                "annotation_ok": annotation_ok,
                "empty": is_empty,
                "image_exists": image_exists,
                "reviewable": reviewable,
                "issues": dict(issues),
            }
        )
    overview.update(
        average_boxes=ratio(sum(box_counts), len(box_counts)),
        median_boxes=median(box_counts) if box_counts else 0,
        max_boxes=max(box_counts, default=0),
        configured_classes=len(classes),
        present_classes=sum(c["image_count"] > 0 for c in classes.values()),
    )
    for cls in classes.values():
        cls.update(
            box_share=ratio(cls["box_count"], overview["boxes"]),
            image_coverage=ratio(cls["image_count"], len(rows)),
            average_boxes=ratio(cls["box_count"], cls["image_count"]),
        )

    def distribution(counter):
        return [
            {"name": name, "count": amount} for name, amount in counter.most_common()
        ]

    return {
        "dataset_id": dataset["id"],
        "dataset_name": dataset["name"],
        "task_type": task_type,
        "generated_at": now(),
        "scope": {"status": status, "dataset_total": dataset["total"]},
        "overview": overview,
        "classes": list(classes.values()),
        "distributions": {
            "boxes_per_image": [
                {"name": name, "count": amount} for name, amount in histogram.items()
            ],
            "resolutions": distribution(resolutions),
            "orientations": distribution(orientations),
            "formats": distribution(formats),
            "sources": distribution(sources),
            "box_sources": distribution(box_sources),
        },
        "quality": [
            {
                "code": code,
                "name": name,
                "severity": "warning" if code in WARNINGS else "error",
                "count": issue_records[code],
                "image_count": issue_images[code],
            }
            for code, name in ISSUES.items()
        ],
        "samples": samples,
    }
