import shutil
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Request
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
)

router = APIRouter(prefix="/api/datasets/{dataset_id}", tags=["图片与标注"])


class ImportImages(ImageImportOptions):
    path: str


def check_options(dataset_id, options):
    dataset = datasets.get(dataset_id)
    require(
        options.auto_label == "off" or dataset["task_type"] == "detection",
        "图片自动标注仅支持 detection 数据集",
    )
    if options.auto_label != "off":
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
            raise HTTPException(
                422, "图片导入参数错误，请检查自动标注和置信度设置"
            ) from exc
        return await run_in_threadpool(stage_upload, dataset_id, files, options)


def stage_upload(dataset_id: str, files: list[UploadFile], options: ImageImportOptions):
    check_options(dataset_id, options)
    datasets.ensure_idle(dataset_id)
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
            lambda p: service.import_paths(dataset_id, paths, p, options),
            lambda: shutil.rmtree(temp),
        )
    except Exception:
        shutil.rmtree(temp)
        raise
    finally:
        for upload in files:
            upload.file.close()


@router.post("/images/import")
def import_images(dataset_id: str, body: ImportImages):
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
):
    return service.list_samples(dataset_id, status, offset, limit)


@router.get("/samples/{sample_id}")
def sample(dataset_id: str, sample_id: str):
    return service.urls(dataset_id, service.get(dataset_id, sample_id))


@router.get("/samples/{sample_id}/{media_kind}")
def media(
    dataset_id: str,
    sample_id: str,
    media_kind: Literal["image", "preview", "thumbnail"],
):
    ann = service.get(dataset_id, sample_id)
    root = datasets.folder(dataset_id)
    if media_kind == "thumbnail":
        return FileResponse(
            service.thumbnail(dataset_id, ann), headers={"Cache-Control": "no-cache"}
        )
    path = (
        root / ann["image"]
        if media_kind == "image"
        else root / "previews" / f"{sample_id}.jpg"
    )
    require(path.is_file(), "图片文件不存在", 404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


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
