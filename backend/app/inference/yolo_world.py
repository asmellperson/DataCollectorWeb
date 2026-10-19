from uuid import uuid4
import copy
from app.inference.base import Detector
from app.core.config import DEVICE
from app.core.storage import require
from app.inference.targets import COCO_NAMES, source_class_ids


class WorldDetector(Detector):
    provider = "yolo_world"

    def __init__(self, path):
        self.path = path
        self.model = None
        self.classes = []
        self.prediction_classes = []

    def load(self):
        if self.model is None:
            from ultralytics import YOLOWorld

            self.model = YOLOWorld(self.path)
            self.model.to(DEVICE)

    def set_classes(self, classes):
        self.load()
        if classes != self.classes:
            mapping = {}
            for cls in classes:
                sources = source_class_ids(cls)
                prompts = [cls["name"]]
                if sources is not None:
                    prompts = []
                    for source_id in sources:
                        require(
                            0 <= source_id < len(COCO_NAMES),
                            f"COCO 类别 ID {source_id} 不存在（应为 0–79）",
                        )
                        prompts.append(COCO_NAMES[source_id])
                for prompt in prompts:
                    require(
                        prompt not in mapping or mapping[prompt]["id"] == cls["id"],
                        "同一检测目标不能同时映射到多个标注类别",
                    )
                    mapping[prompt] = dict(cls)
            self.model.set_classes(list(mapping))
            self.classes = copy.deepcopy(classes)
            self.prediction_classes = list(mapping.values())

    def predict(self, image, confidence=0.25):
        result = self.model.predict(
            image, conf=confidence, device=DEVICE, verbose=False
        )[0]
        objects = []
        for box in result.boxes:
            cls = self.prediction_classes[int(box.cls.item())]
            objects.append(
                {
                    "id": "obj_" + uuid4().hex,
                    "class_id": cls["id"],
                    "class_name": cls["name"],
                    "bbox": [float(v) for v in box.xyxy[0].tolist()],
                    "confidence": float(box.conf.item()),
                    "source": self.provider,
                }
            )
        return objects
