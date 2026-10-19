from typing import Literal
from fastapi import APIRouter, Response
from app.schemas.contracts import DatasetCreate, DatasetUpdate, LabelImportPreview
from app.services import dataset_service as service
from app.services import statistics_service

router = APIRouter(prefix="/api/datasets", tags=["数据集"])
router.get("")(service.list_all)
router.get("/{dataset_id}")(service.get)
router.delete("/{dataset_id}")(service.delete)


@router.post("/import-labels/preview")
def preview_labels(body: LabelImportPreview):
    from app.services import label_import_service as labels

    classes = labels.parse_classes(body.classes_text, body.yaml_text)
    report, _ = labels.inspect_labels(body.image_names, body.labels, classes)
    return report


@router.get("/{dataset_id}/statistics")
def statistics(
    dataset_id: str,
    response: Response,
    status: Literal["pending", "approved", "rejected"] | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    return statistics_service.statistics(dataset_id, status)


@router.post("")
def create(body: DatasetCreate):
    return service.create(body)


@router.put("/{dataset_id}")
def update(dataset_id: str, body: DatasetUpdate):
    return service.update(dataset_id, body)
