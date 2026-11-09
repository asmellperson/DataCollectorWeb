import shutil
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Request, Response
from email.utils import parsedate_to_datetime
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from app.core.config import STATE_ROOT
from app.core.storage import require
from app.schemas.contracts import ImageImportOptions, AnnotationEdit, ObjectEdit
from app.inference import manager
from app.services import (
    annotation_service as service,
    dataset_service as datasets,
    task_service as tasks,
    label_import_service as labels,
)

router = APIRouter(prefix="/api/datasets/{dataset_id}", tags=["图片与标注"])


class ImportImages(ImageImportOptions):
    path: str


def check_options(dataset_id, options):
    dataset = datasets.get(dataset_id)
    if options.source_classes is not None:
        classes = labels.validate_classes(
            [c.model_dump() for c in options.source_classes]
        )
        labels.resolve_mapping(dataset, classes, options.import_class_mapping)
    require(
        options.auto_label == "off" or dataset["task_type"] == "detection",
        "图片自动标注仅支持 detection 数据集",
    )
    if options.auto_label != "off":
        datasets.label_classes(dataset, options)
        manager.resolve(options.auto_label, options.model_path)


@router.post("/images/upload")
async def upload_images(dataset_id: str, request: Request):
    # 目录上传允许超过框架默认的一千个文件；解码与入库仍在后台任务执行。
    async with request.form(max_files=10000, max_fields=10) as form:
        files = form.getlist("files")
        require(
            files and all(isinstance(f, UploadFile) for f in files), "请选择图片目录"
        )
        try:
            options = ImageImportOptions.model_validate_json(form.get("options", "{}"))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "图片导入参数错误，请检查类别和标注设置") from exc
        label_files = form.getlist("labels")
        require(all(isinstance(f, UploadFile) for f in label_files), "标签文件无效")
        require(not label_files or options.source_classes is not None, "请提供标签类别")
        return await run_in_threadpool(
            stage_upload, dataset_id, files, options, label_files
        )


def stage_upload(
    dataset_id: str,
    files: list[UploadFile],
    options: ImageImportOptions,
    label_files: list[UploadFile] | None = None,
):
    check_options(dataset_id, options)
    datasets.ensure_idle(dataset_id)
    imported_labels = None
    if options.source_classes is not None:
        contents = {}
        total_bytes = 0
        for upload in label_files or []:
            name = upload.filename or ""
            require(name not in contents, f"标签名称冲突：{name}")
            raw = upload.file.read(labels.MAX_LABEL_BYTES + 1)
            total_bytes += len(raw)
            require(len(raw) <= labels.MAX_LABEL_BYTES, f"{name}：标签文件过大")
            require(total_bytes <= labels.MAX_TOTAL_BYTES, "标签总大小不能超过 32 MB")
            try:
                contents[name] = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise HTTPException(400, f"{name}：标签需要使用 UTF-8 编码") from exc
        report, imported_labels = labels.inspect_labels(
            [f.filename or "" for f in files],
            contents,
            [c.model_dump() for c in options.source_classes],
        )
        require(not report["error_count"], "；".join(report["errors"]))
    temp = STATE_ROOT / "uploads" / uuid4().hex
    temp.mkdir(parents=True)
    paths = []
    try:
        for upload in files:
            suffix = Path(upload.filename or "").suffix.lower()
            require(
                suffix in service.IMAGE_EXTS, "图片仅支持 jpg、jpeg、png、bmp、webp"
            )
            dest = temp / f"{uuid4().hex}{suffix}"
            with dest.open("wb") as output:
                shutil.copyfileobj(upload.file, output, 1024 * 1024)
            paths.append((dest, upload.filename))
        return tasks.submit(
            "image_import" if options.auto_label == "off" else "image_import_label",
            dataset_id,
            lambda p: service.import_paths(
                dataset_id, paths, p, options, imported_labels
            ),
            lambda: shutil.rmtree(temp),
        )
    except Exception:
        shutil.rmtree(temp)
        raise
    finally:
        for upload in [*files, *(label_files or [])]:
            upload.file.close()


@router.post("/images/import")
def import_images(dataset_id: str, body: ImportImages):
    require(body.source_classes is None, "已有标签请通过本地目录上传")
    check_options(dataset_id, body)
    path = Path(body.path).expanduser().resolve()
    require(path.is_dir(), "服务器目录不存在")
    root = datasets.folder(dataset_id)
    require(
        not path.is_relative_to(root) and not root.is_relative_to(path),
        "不能从当前数据集自身或其父目录导入",
    )

    def work(p):
        paths = [
            (f, str(f.relative_to(path)))
            for f in path.rglob("*")
            if f.is_file() and f.suffix.lower() in service.IMAGE_EXTS
        ]
        require(paths, "服务器目录中没有支持的图片")
        return service.import_paths(dataset_id, paths, p, body)

    return tasks.submit(
        "image_import" if body.auto_label == "off" else "image_import_label",
        dataset_id,
        work,
    )


@router.get("/samples")
def samples(
    dataset_id: str,
    status: Literal["pending", "approved", "rejected"] | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    anchor_id: str | None = Query(None, max_length=100),
):
    return service.list_samples(dataset_id, status, offset, limit, anchor_id)


@router.get("/samples/{sample_id}")
def sample(dataset_id: str, sample_id: str):
    return service.urls(dataset_id, service.get(dataset_id, sample_id))


@router.get("/samples/{sample_id}/{media_kind}")
def media(
    request: Request,
    dataset_id: str,
    sample_id: str,
    media_kind: Literal["image", "preview", "thumbnail"],
):
    ann = service.get(dataset_id, sample_id)
    root = datasets.folder(dataset_id)
    path = (
        service.thumbnail(dataset_id, ann)
        if media_kind == "thumbnail"
        else (
            root / ann["image"]
            if media_kind == "image"
            else service.preview_file(dataset_id, ann)
        )
    )
    require(path.is_file(), "图片文件不存在", 404)
    # 原图以唯一样本 ID 寻址且不会覆写；预览需重新校验，防止缓存旧标注。
    cache_control = (
        "private, max-age=86400, immutable" if media_kind == "image" else "no-cache"
    )
    response = FileResponse(
        path, stat_result=path.stat(), headers={"Cache-Control": cache_control}
    )
    etag = request.headers.get("if-none-match")
    unchanged = False
    if etag is not None:
        unchanged = any(
            tag.strip().removeprefix("W/") in ("*", response.headers["etag"])
            for tag in etag.split(",")
        )
    elif modified := request.headers.get("if-modified-since"):
        try:
            unchanged = parsedate_to_datetime(modified) >= parsedate_to_datetime(
                response.headers["last-modified"]
            )
        except (TypeError, ValueError, OverflowError):
            pass
    if unchanged:
        return Response(
            status_code=304,
            headers={
                key: response.headers[key]
                for key in ("etag", "last-modified", "cache-control")
            },
        )
    return response


@router.put("/samples/{sample_id}/annotation")
def edit_annotation(dataset_id: str, sample_id: str, body: AnnotationEdit):
    return service.edit(dataset_id, sample_id, body)


@router.post("/samples/{sample_id}/objects")
def create_object(dataset_id: str, sample_id: str, body: ObjectEdit):
    return service.edit_object(dataset_id, sample_id, body)


@router.put("/samples/{sample_id}/objects/{object_id}")
def update_object(dataset_id: str, sample_id: str, object_id: str, body: ObjectEdit):
    return service.edit_object(dataset_id, sample_id, body, object_id)


@router.delete("/samples/{sample_id}/objects/{object_id}")
def delete_object(dataset_id: str, sample_id: str, object_id: str):
    return service.delete_object(dataset_id, sample_id, object_id)


@router.post("/samples/{sample_id}/approve")
def approve(dataset_id: str, sample_id: str):
    return service.set_status(dataset_id, sample_id, "approved")


@router.post("/samples/{sample_id}/reject")
def reject(dataset_id: str, sample_id: str):
    return service.set_status(dataset_id, sample_id, "rejected")


@router.delete("/samples/{sample_id}")
def delete_sample(dataset_id: str, sample_id: str):
    return service.delete_sample(dataset_id, sample_id)
