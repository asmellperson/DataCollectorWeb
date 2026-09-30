from fastapi import APIRouter
from app.schemas.contracts import DatasetCreate, DatasetUpdate
from app.services import dataset_service as service

router = APIRouter(prefix="/api/datasets", tags=["数据集"])
router.get("")(service.list_all)
router.get("/{dataset_id}")(service.get)
router.delete("/{dataset_id}")(service.delete)


@router.post("")
def create(body: DatasetCreate):
    return service.create(body)


@router.put("/{dataset_id}")
def update(dataset_id: str, body: DatasetUpdate):
    return service.update(dataset_id, body)
