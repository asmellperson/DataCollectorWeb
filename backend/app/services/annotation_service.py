import logging
from pathlib import Path
from uuid import uuid4
import cv2
import numpy as np
from sqlalchemy import select, func
from app.core.storage import atomic_bytes, read_json, write_json, dataset_lock, require
from app.db.database import Session, Sample, now
from app.services import dataset_service as datasets

log = logging.getLogger(__name__)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def read_image(path):
    image = cv2.imdecode(
        np.frombuffer(Path(path).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    require(image is not None and image.size > 0, f"图片读取失败：{Path(path).name}")
    return image


def encode(image):
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    require(ok, "图片编码失败")
    return data.tobytes()


def get(dataset_id, sample_id):
    root = datasets.folder(dataset_id)
    with Session() as db:
        row = db.get(Sample, sample_id)
        require(row and row.dataset_id == dataset_id, "样本不存在", 404)
    data = read_json(root / "annotations" / f"{sample_id}.json")
    names = {c["id"]: c["name"] for c in read_json(root / "dataset.json")["classes"]}
    for obj in data["objects"]:
        obj["class_name"] = names.get(obj["class_id"], obj["class_name"])
    if data.get("classification"):
        data["classification"]["class_name"] = names[data["classification"]["class_id"]]
    return data


def urls(dataset_id, data):
    sid = data["id"]
    return {
        **data,
        "image_url": f"/api/datasets/{dataset_id}/samples/{sid}/image",
        "preview_url": f"/api/datasets/{dataset_id}/samples/{sid}/preview?v={data.get('revision', 0)}",
        "thumbnail_url": f"/api/datasets/{dataset_id}/samples/{sid}/thumbnail?v={data.get('revision', 0)}",
    }


def list_samples(dataset_id, status=None, offset=0, limit=100):
    datasets.folder(dataset_id)
    with Session() as db:
        where = [Sample.dataset_id == dataset_id]
        if status:
            where.append(Sample.status == status)
        total = db.scalar(select(func.count()).select_from(Sample).where(*where))
        rows = db.scalars(
            select(Sample)
            .where(*where)
            .order_by(Sample.created_at, Sample.id)
            .offset(offset)
            .limit(limit)
        ).all()
        items = [
            urls(
                dataset_id,
                {c.name: getattr(s, c.name) for c in Sample.__table__.columns},
            )
            for s in rows
        ]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def save_sample(
    dataset_id,
    image,
    source,
    original: bytes | None = None,
    suffix=".jpg",
    objects=None,
):
    with dataset_lock(dataset_id), Session() as db:
        root = datasets.folder(dataset_id)
        sid = uuid4().hex
        height, width = image.shape[:2]
        ann = {
            "id": sid,
            "image": f"images/{sid}{suffix}",
            "width": width,
            "height": height,
            "source": {"timestamp": now(), **source},
            "status": "pending",
            "objects": objects or [],
            "classification": None,
            "revision": 0,
        }
        paths = [
            root / ann["image"],
            root / "annotations" / f"{sid}.json",
            root / "previews" / f"{sid}.jpg",
        ]
        try:
            atomic_bytes(paths[0], original if original is not None else encode(image))
            write_json(paths[1], ann)
            validate_annotation(dataset_id, ann)
            atomic_bytes(paths[2], encode(render_preview(image, ann)))
            db.add(
                Sample(
                    id=sid,
                    dataset_id=dataset_id,
                    image=ann["image"],
                    width=width,
                    height=height,
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            for path in paths:
                path.unlink(missing_ok=True)
            raise
        return ann


def import_paths(dataset_id, paths, progress, options=None):
    from app.inference import manager
    from app.services.task_service import Cancelled

    progress.update(total=len(paths))
    provider = options.auto_label if options else "off"
    dataset = datasets.get(dataset_id)
    if provider != "off":
        # 模型只加载一次；初始化失败及时结束，避免每张图重复加载。
        with manager.lock:
            manager.get(provider, options.model_path).set_classes(dataset["classes"])
    labeled = 0
    last_sample_id = None
    for path, source_name in paths:
        progress.check()
        try:
            data = path.read_bytes()
            image = read_image(path)
            objects = None
            if provider != "off":
                objects = manager.predict(
                    provider,
                    options.model_path,
                    dataset["classes"],
                    image,
                    options.confidence,
                )
            progress.check()
            ann = save_sample(
                dataset_id,
                image,
                {"type": "image", "filename": source_name},
                data,
                path.suffix.lower(),
                objects=objects,
            )
            last_sample_id = ann["id"]
            labeled += int(provider != "off")
            progress.advance()
        except Cancelled:
            raise
        except Exception as exc:
            log.exception("图片导入失败：%s", path.name)
            progress.advance(
                f"图片导入失败：{path.name}，{getattr(exc, 'detail', str(exc))}"
            )
    return {
        "message": "图片导入及自动标注结束" if provider != "off" else "图片导入结束",
        "labeled": labeled,
        "last_sample_id": last_sample_id,
    }


def thumbnail(dataset_id, ann):
    # 缩略图单独缓存，原图和全尺寸带框预览不受影响。
    with dataset_lock(dataset_id):
        root = datasets.folder(dataset_id)
        source = root / "previews" / f"{ann['id']}.jpg"
        target = root / "previews" / f"{ann['id']}.thumb.jpg"
        if not target.exists() or target.stat().st_mtime_ns < source.stat().st_mtime_ns:
            from PIL import Image
            from io import BytesIO

            with Image.open(source) as image:
                image.thumbnail((320, 240))
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=80)
            atomic_bytes(target, output.getvalue())
        return target


def render_preview(image, ann):
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(canvas)
    size = max(14, round(min(ann["width"], ann["height"]) / 45))
    fonts = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font_path = next((p for p in fonts if Path(p).is_file()), None)
    font = (
        ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    )
    for obj in ann["objects"]:
        x1, y1, x2, y2 = obj["bbox"]
        label = obj["class_name"] + (
            f" {obj['confidence']:.2f}" if obj.get("confidence") is not None else ""
        )
        color = ["#58c7ff", "#f7c95f", "#a08bff", "#65ddb0"][obj["class_id"] % 4]
        draw.rectangle((x1, y1, x2, y2), outline=color, width=max(2, size // 7))
        draw.text(
            (x1 + 3, max(0, y1 - size - 4)),
            label,
            fill=color,
            font=font,
            stroke_width=1,
            stroke_fill="#152030",
        )
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def persist(dataset_id, ann):
    with dataset_lock(dataset_id), Session() as db:
        root = datasets.folder(dataset_id)
        row = db.get(Sample, ann["id"])
        require(row and row.dataset_id == dataset_id, "样本不存在", 404)
        image = read_image(root / ann["image"])
        ann["revision"] = ann.get("revision", 0) + 1
        preview = encode(render_preview(image, ann))
        old = read_json(root / "annotations" / f"{ann['id']}.json")
        try:
            write_json(root / "annotations" / f"{ann['id']}.json", ann)
            atomic_bytes(root / "previews" / f"{ann['id']}.jpg", preview)
            row.status = ann["status"]
            db.commit()
        except Exception:
            db.rollback()
            write_json(root / "annotations" / f"{ann['id']}.json", old)
            atomic_bytes(
                root / "previews" / f"{ann['id']}.jpg",
                encode(render_preview(image, old)),
            )
            raise
        return urls(dataset_id, ann)


def auto_label(dataset_id, options, progress):
    from app.inference import manager

    dataset = datasets.get(dataset_id)
    require(dataset["task_type"] == "detection", "自动预标仅适用于 detection 数据集")
    with Session() as db:
        query = select(Sample.id).where(
            Sample.dataset_id == dataset_id, Sample.status == "pending"
        )
        if options.sample_ids is not None:
            query = query.where(Sample.id.in_(options.sample_ids))
        ids = list(db.scalars(query).all())
    progress.update(total=len(ids))
    require(ids, "没有可预标的待审核样本")
    # 提前加载失败会让任务明确失败，避免数千张逐张重复加载失败。
    with manager.lock:
        manager.get(options.provider, options.model_path).set_classes(
            dataset["classes"]
        )
    for sid in ids:
        progress.check()
        try:
            with dataset_lock(dataset_id):
                ann = get(dataset_id, sid)
                if ann["status"] != "pending" or (
                    ann["objects"] and not options.overwrite
                ):
                    progress.advance("已跳过：样本含已有标注，默认保留")
                    continue
                revision = ann["revision"]
                image = read_image(datasets.folder(dataset_id) / ann["image"])
            objects = manager.predict(
                options.provider,
                options.model_path,
                dataset["classes"],
                image,
                options.confidence,
            )
            progress.check()
            with dataset_lock(dataset_id):
                current = get(dataset_id, sid)
                require(
                    current["revision"] == revision,
                    "样本在推理期间被修改，已保留人工修改",
                    409,
                )
                current.update(objects=objects, status="pending")
                persist(dataset_id, current)
            progress.advance()
        except Exception as exc:
            log.exception("样本预标失败：%s", sid)
            progress.advance(f"样本预标失败：{getattr(exc, 'detail', str(exc))}")
    return {"message": "自动预标结束"}


def validate_annotation(dataset_id, ann):
    dataset = datasets.get(dataset_id)
    names = {c["id"]: c["name"] for c in dataset["classes"]}
    require(
        dataset["task_type"] == "detection" or not ann["objects"], "当前任务不支持 bbox"
    )
    require(
        len({o["id"] for o in ann["objects"]}) == len(ann["objects"]),
        "标注对象 ID 不能重复",
    )
    for obj in ann["objects"]:
        require(obj["class_id"] in names, "类别配置错误：类别 ID 不存在")
        x1, y1, x2, y2 = obj["bbox"]
        require(
            0 <= x1 < x2 <= ann["width"] and 0 <= y1 < y2 <= ann["height"],
            "bbox 必须是图像范围内的有效像素坐标",
        )
        obj["class_name"] = names[obj["class_id"]]
    if ann.get("classification"):
        require(dataset["task_type"] == "classification", "当前任务不支持图像分类标签")
        cid = ann["classification"]["class_id"]
        require(cid in names, "分类类别不存在")
        ann["classification"]["class_name"] = names[cid]
    return ann


def edit(dataset_id, sample_id, body):
    with dataset_lock(dataset_id):
        ann = get(dataset_id, sample_id)
        require(
            ann["revision"] == body.revision,
            "样本已被其他操作更新，请重新打开后编辑",
            409,
        )
        ann["objects"] = [o.model_dump() for o in body.objects]
        ann["classification"] = (
            {"class_id": body.classification.id, "class_name": body.classification.name}
            if body.classification
            else None
        )
        ann["status"] = "pending"
        return persist(dataset_id, validate_annotation(dataset_id, ann))


def edit_object(dataset_id, sample_id, body, object_id=None):
    with dataset_lock(dataset_id):
        ann = get(dataset_id, sample_id)
        obj = {
            "id": object_id or "obj_" + uuid4().hex,
            "class_id": body.class_id,
            "class_name": "",
            "bbox": list(body.bbox),
            "confidence": None,
            "source": "manual",
        }
        if object_id:
            require(
                any(o["id"] == object_id for o in ann["objects"]), "标注框不存在", 404
            )
            ann["objects"] = [
                obj if o["id"] == object_id else o for o in ann["objects"]
            ]
        else:
            ann["objects"].append(obj)
        ann["status"] = "pending"
        return persist(dataset_id, validate_annotation(dataset_id, ann))


def delete_object(dataset_id, sample_id, object_id):
    with dataset_lock(dataset_id):
        ann = get(dataset_id, sample_id)
        require(any(o["id"] == object_id for o in ann["objects"]), "标注框不存在", 404)
        ann["objects"] = [o for o in ann["objects"] if o["id"] != object_id]
        ann["status"] = "pending"
        return persist(dataset_id, ann)


def set_status(dataset_id, sample_id, status):
    with dataset_lock(dataset_id):
        ann = get(dataset_id, sample_id)
        validate_annotation(dataset_id, ann)
        if (
            status == "approved"
            and datasets.get(dataset_id)["task_type"] == "classification"
        ):
            require(ann.get("classification"), "请先选择图像类别")
        ann["status"] = status
        return persist(dataset_id, ann)


def delete_sample(dataset_id, sample_id):
    import shutil

    with dataset_lock(dataset_id), Session() as db:
        datasets.ensure_idle(dataset_id)
        ann = get(dataset_id, sample_id)
        root = datasets.folder(dataset_id)
        tomb = root / f".delete-{uuid4().hex}"
        tomb.mkdir()
        moved = []
        try:
            for path in (
                root / ann["image"],
                root / "annotations" / f"{sample_id}.json",
                root / "previews" / f"{sample_id}.jpg",
                root / "previews" / f"{sample_id}.thumb.jpg",
                root / "exports",
            ):
                if path.exists():
                    target = tomb / path.name
                    path.rename(target)
                    moved.append((path, target))
            (root / "exports").mkdir(exist_ok=True)
            db.delete(db.get(Sample, sample_id))
            db.commit()
        except Exception:
            db.rollback()
            for path, target in reversed(moved):
                if path.is_dir():
                    path.rmdir()
                target.rename(path)
            raise
        finally:
            if not any(target.exists() for _, target in moved):
                tomb.rmdir()
        shutil.rmtree(tomb)
    return {"ok": True}
