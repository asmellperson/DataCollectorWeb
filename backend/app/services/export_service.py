import random
import re
import shutil
import zipfile
from datetime import datetime
from uuid import uuid4
import yaml
from sqlalchemy import func, select
from app.core.storage import write_json, dataset_lock, require
from app.db.database import Session, Sample
from app.services import dataset_service as datasets, annotation_service as annotations


def split_plan(total, options):
    require(total > 0, "没有符合导出条件的图片，请先确认样本或选择包含待审核")
    if options.split_mode == "none":
        return {
            "split_mode": "none",
            "total": total,
            "ratios": {},
            "counts": {"all": total},
        }
    ratios = {"train": options.train_ratio, "val": options.val_ratio}
    if options.split_mode == "train_val_test":
        ratios["test"] = options.test_ratio
    require(
        total >= len(ratios), f"所选划分方式至少需要 {len(ratios)} 张符合条件的图片"
    )
    expected = {part: total * ratio for part, ratio in ratios.items()}
    counts = {part: int(value) for part, value in expected.items()}
    # 先按最大余数补齐取整差额，再保证每个启用的分组至少有一张。
    remainder_order = sorted(
        ratios, key=lambda part: expected[part] - counts[part], reverse=True
    )
    for part in remainder_order[: total - sum(counts.values())]:
        counts[part] += 1
    for part in ratios:
        if counts[part] == 0:
            donor = max(
                (p for p in ratios if counts[p] > 1),
                key=lambda p: counts[p] - expected[p],
            )
            counts[donor] -= 1
            counts[part] = 1
    return {
        "split_mode": options.split_mode,
        "total": total,
        "ratios": ratios,
        "counts": counts,
    }


def preview(dataset_id, options):
    with dataset_lock(dataset_id), Session() as db:
        datasets.folder(dataset_id)
        statuses = ["approved", "pending"] if options.include_pending else ["approved"]
        total = db.scalar(
            select(func.count())
            .select_from(Sample)
            .where(Sample.dataset_id == dataset_id, Sample.status.in_(statuses))
        )
    return split_plan(total, options)


def export(dataset_id, options, progress, yolo=False):
    with dataset_lock(dataset_id), Session() as db:
        dataset = datasets.get(dataset_id)
        require(
            not yolo or dataset["task_type"] == "detection",
            "YOLO Detect 导出只支持 detection 数据集",
        )
        statuses = ["approved", "pending"] if options.include_pending else ["approved"]
        ids = list(
            db.scalars(
                select(Sample.id)
                .where(Sample.dataset_id == dataset_id, Sample.status.in_(statuses))
                .order_by(Sample.id)
            ).all()
        )
        plan = split_plan(len(ids), options)
        snapshot = [annotations.get(dataset_id, i) for i in ids]
    if dataset["task_type"] == "classification":
        require(
            all(a.get("classification") for a in snapshot),
            "分类导出包含尚未选择类别的图片，请完成分类",
        )
    rng = random.Random(options.seed)
    rng.shuffle(snapshot)
    parts = [part for part, count in plan["counts"].items() for _ in range(count)]
    root = datasets.folder(dataset_id)
    name = f"{'yolo' if yolo else dataset['task_type']}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    temp = root / "exports" / ("." + name)
    dest = root / "exports" / name
    archive = root / "exports" / (name + ".zip")
    archive_temp = root / "exports" / ("." + name + ".zip")
    temp.mkdir()
    try:
        progress.update(total=len(snapshot))
        (temp / "annotations").mkdir()
        for part in plan["counts"]:
            folder = "" if part == "all" else part
            if dataset["task_type"] == "classification":
                (temp / (folder or "images")).mkdir(parents=True)
            else:
                (temp / "images" / folder).mkdir(parents=True)
            if yolo:
                (temp / "labels" / folder).mkdir(parents=True)
        manifest = {
            "dataset": dataset,
            "format": "yolo_detect" if yolo else dataset["task_type"],
            "seed": options.seed,
            "statuses": statuses,
            **plan,
            "samples": [],
        }
        for index, ann in enumerate(snapshot):
            progress.check()
            part = parts[index]
            folder = "" if part == "all" else part
            image_path = root / ann["image"]
            output = temp / "images" / folder / image_path.name
            if dataset["task_type"] == "classification":
                cls = ann["classification"]
                safe = re.sub(r"[^\w.-]+", "_", cls["class_name"]).strip(".") or "class"
                output = (
                    temp
                    / (folder or "images")
                    / f"{cls['class_id']}_{safe}"
                    / image_path.name
                )
                output.parent.mkdir(exist_ok=True)
            shutil.copyfile(image_path, output)
            write_json(temp / "annotations" / f"{ann['id']}.json", ann)
            if yolo:
                lines = []
                for obj in ann["objects"]:
                    x1, y1, x2, y2 = obj["bbox"]
                    w, h = ann["width"], ann["height"]
                    require(
                        0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h,
                        "标注框超出图片范围，无法导出",
                    )
                    values = (
                        (x1 + x2) / 2 / w,
                        (y1 + y2) / 2 / h,
                        (x2 - x1) / w,
                        (y2 - y1) / h,
                    )
                    lines.append(
                        str(obj["class_id"])
                        + " "
                        + " ".join(f"{v:.8f}" for v in values)
                    )
                (temp / "labels" / folder / f"{ann['id']}.txt").write_text(
                    "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
                )
            manifest["samples"].append(
                {"id": ann["id"], "split": part, "image": str(output.relative_to(temp))}
            )
            progress.advance()
        write_json(temp / "manifest.json", manifest)
        write_json(
            temp / "dataset.json",
            {k: dataset[k] for k in ("id", "name", "task_type", "classes")},
        )
        if yolo:
            classes = {c["id"]: c["name"] for c in dataset["classes"]}
            names = {
                i: classes.get(i, f"__unused_class_{i}")
                for i in range(max(classes) + 1)
            }
            paths = (
                {"train": "images", "val": None}
                if options.split_mode == "none"
                else {part: f"images/{part}" for part in plan["counts"]}
            )
            note = (
                "# 未划分数据集；训练前请配置独立的验证集路径。\n"
                if options.split_mode == "none"
                else ""
            )
            (temp / "data.yaml").write_text(
                note
                + yaml.safe_dump(
                    {**paths, "names": names},
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        with zipfile.ZipFile(archive_temp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for path in sorted(temp.rglob("*")):
                progress.check()
                z.write(path, f"{name}/{path.relative_to(temp)}")
        progress.check()
        temp.rename(dest)
        archive_temp.rename(archive)
        return {
            "name": name,
            **plan,
            "train": plan["counts"].get("train", 0),
            "val": plan["counts"].get("val", 0),
            "test": plan["counts"].get("test", 0),
            "download_url": f"/api/datasets/{dataset_id}/exports/{archive.name}",
            "path": str(dest),
        }
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        if dest.exists():
            shutil.rmtree(dest)
        archive_temp.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        raise
