from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ClassItem(Contract):
    id: int = Field(ge=0, le=9999)
    name: str = Field(min_length=1, max_length=100)


class DatasetCreate(Contract):
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    task_type: Literal["raw", "classification", "detection"]
    classes: list[ClassItem] = Field(default_factory=list)


class DatasetUpdate(Contract):
    name: str = Field(min_length=1, max_length=120)
    classes: list[ClassItem]


class LabelOptions(Contract):
    provider: Literal["yolo_world", "custom_yolo"] = "yolo_world"
    model_path: str | None = None
    confidence: float = Field(default=0.25, gt=0, le=1)
    sample_ids: list[str] | None = None
    overwrite: bool = False


class ObjectItem(Contract):
    id: str = Field(min_length=1, max_length=100)
    class_id: int = Field(ge=0)
    class_name: str = ""
    bbox: tuple[float, float, float, float]
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: Literal["manual", "yolo_world", "custom_yolo"] = "manual"


class ObjectEdit(Contract):
    class_id: int = Field(ge=0)
    bbox: tuple[float, float, float, float]


class AnnotationEdit(Contract):
    revision: int = Field(ge=0)
    objects: list[ObjectItem] = Field(default_factory=list)
    classification: ClassItem | None = None


class ClassificationEdit(Contract):
    class_id: int = Field(ge=0)


class CameraEdit(Contract):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(pattern=r"^rtsp[s]?://\S+$")
    transport: Literal["tcp", "udp"] = "tcp"


class CollectionStart(Contract):
    dataset_id: str
    camera_id: str
    mode: Literal["raw_interval", "roi_interval", "detect_raw", "detect_crop"] = (
        "raw_interval"
    )
    interval: float = Field(default=2, ge=0.1, le=86400)
    roi: tuple[float, float, float, float] | None = None
    auto_label: Literal["off", "yolo_world", "custom_yolo"] = "off"
    detector: Literal["yolo_world", "custom_yolo"] = "yolo_world"
    model_path: str | None = None
    confidence: float = Field(default=0.25, gt=0, le=1)
    similarity: float = Field(default=0.92, ge=0, le=1)
    target_classes: list[str] = Field(default_factory=lambda: ["person"], min_length=1)
    infer_interval: float = Field(default=0.5, ge=0.1, le=60)


class VideoOptions(Contract):
    path: str = ""
    sampling: Literal["frames", "seconds"] = "frames"
    every: float = Field(default=30, gt=0, le=1000000)
    auto_label: Literal["off", "yolo_world", "custom_yolo"] = "off"
    model_path: str | None = None
    confidence: float = Field(default=0.25, gt=0, le=1)

    @model_validator(mode="after")
    def check_frames(self):
        if self.sampling == "frames" and (
            not float(self.every).is_integer() or self.every < 1
        ):
            raise ValueError("按帧采样必须输入正整数")
        return self


class ImageImportOptions(Contract):
    auto_label: Literal["off", "yolo_world", "custom_yolo"] = "off"
    model_path: str | None = None
    confidence: float = Field(default=0.25, gt=0, le=1)


class ExportOptions(Contract):
    split_mode: Literal["none", "train_val", "train_val_test"] = "train_val"
    train_ratio: float = Field(default=0.8, ge=0, le=1)
    val_ratio: float = Field(default=0.2, ge=0, le=1)
    test_ratio: float = Field(default=0, ge=0, le=1)
    include_pending: bool = False
    seed: int = 42

    @model_validator(mode="after")
    def check_split(self):
        if self.split_mode == "none":
            # 整份导出忽略划分比例，避免把未划分图片误当成训练集。
            self.train_ratio = self.val_ratio = self.test_ratio = 0
            return self
        if self.train_ratio <= 0 or self.val_ratio <= 0:
            raise ValueError("训练集和验证集比例必须大于 0")
        if self.split_mode == "train_val_test" and self.test_ratio <= 0:
            raise ValueError("三份划分时测试集比例必须大于 0")
        if self.split_mode == "train_val" and self.test_ratio != 0:
            raise ValueError("两份划分时测试集比例必须为 0")
        if abs(self.train_ratio + self.val_ratio + self.test_ratio - 1) > 1e-8:
            raise ValueError("所选分组的比例之和必须为 1（100%）")
        return self
