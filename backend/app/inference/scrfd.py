"""Local SCRFD-10G-KPS ONNX inference, without InsightFace's model downloader.

Output layout and preprocessing follow InsightFace's SCRFD model_zoo decoder:
three score, three distance-box and three distance-landmark heads (8/16/32).
"""

from uuid import uuid4

import cv2
import numpy as np

from app.core.storage import require
from app.inference.base import Detector


class SCRFDDetector(Detector):
    provider = "scrfd"

    def __init__(self, path):
        self.path = path
        self.session = None

    def load(self):
        if self.session is not None:
            return
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("请在部署服务器安装 onnxruntime 后验证 SCRFD") from exc
        session = ort.InferenceSession(self.path, providers=["CPUExecutionProvider"])
        shape = session.get_inputs()[0].shape
        require(len(shape) == 4 and shape[1] == 3, "SCRFD 需要 NCHW 三通道输入")
        require(
            len(session.get_outputs()) == 9,
            "需要包含五点关键点的 SCRFD-10G-KPS（9 个输出）",
        )
        self.input_name = session.get_inputs()[0].name
        self.input_size = (
            shape[3] if isinstance(shape[3], int) else 640,
            shape[2] if isinstance(shape[2], int) else 640,
        )
        require(
            all(v > 0 and v % 32 == 0 for v in self.input_size),
            "SCRFD 输入尺寸必须为 32 的正整数倍",
        )
        self.session = session

    def set_classes(self, classes):
        require(
            len(classes) == 1
            and classes[0]["id"] == 0
            and classes[0]["name"] == "face",
            "SCRFD 类别固定为 0 face",
        )

    @staticmethod
    def nms(boxes, scores, threshold=0.4):
        order = scores.argsort()[::-1]
        keep = []
        areas = (boxes[:, 2] - boxes[:, 0] + 1) * (boxes[:, 3] - boxes[:, 1] + 1)
        while order.size:
            index = int(order[0])
            keep.append(index)
            rest = order[1:]
            lt = np.maximum(boxes[index, :2], boxes[rest, :2])
            rb = np.minimum(boxes[index, 2:], boxes[rest, 2:])
            wh = np.maximum(0, rb - lt + 1)
            intersection = wh[:, 0] * wh[:, 1]
            iou = intersection / np.maximum(
                areas[index] + areas[rest] - intersection, 1e-8
            )
            order = rest[iou <= threshold]
        return keep

    def predict(self, image, confidence=0.25):
        self.load()
        height, width = image.shape[:2]
        input_w, input_h = self.input_size
        ratio = min(input_w / width, input_h / height)
        resized_w, resized_h = max(1, int(width * ratio)), max(1, int(height * ratio))
        padded = np.zeros((input_h, input_w, 3), dtype=np.uint8)
        padded[:resized_h, :resized_w] = cv2.resize(image, (resized_w, resized_h))
        blob = cv2.dnn.blobFromImage(
            padded, 1 / 128.0, self.input_size, (127.5,) * 3, swapRB=True
        )
        outputs = self.session.run(None, {self.input_name: blob})
        boxes, scores, landmarks = [], [], []
        for level, stride in enumerate((8, 16, 32)):
            score = np.asarray(outputs[level]).reshape(-1)
            distances = np.asarray(outputs[level + 3]).reshape(-1, 4) * stride
            offsets = np.asarray(outputs[level + 6]).reshape(-1, 5, 2) * stride
            grid_h, grid_w = input_h // stride, input_w // stride
            centers = (
                np.stack(np.mgrid[:grid_h, :grid_w][::-1], axis=-1).reshape(-1, 2)
                * stride
            )
            centers = np.repeat(centers, 2, axis=0)
            require(
                len(score) == len(distances) == len(offsets) == len(centers),
                "SCRFD 输出形状与 10G-KPS 模型不匹配",
            )
            indices = np.flatnonzero(
                np.isfinite(score) & (score >= confidence) & (score <= 1)
            )
            center, distance = centers[indices], distances[indices]
            boxes.append(
                np.concatenate(
                    (center - distance[:, :2], center + distance[:, 2:]), axis=1
                )
            )
            scores.append(score[indices])
            landmarks.append(center[:, None, :] + offsets[indices])
        boxes, scores, landmarks = (
            np.concatenate(boxes),
            np.concatenate(scores),
            np.concatenate(landmarks),
        )
        if not len(scores):
            return []
        # Undo top-left letterboxing in original-image pixel coordinates.
        scale = np.array([width / resized_w, height / resized_h])
        boxes *= np.tile(scale, 2)
        landmarks *= scale
        boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width)
        boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height)
        valid = (
            np.isfinite(boxes).all(axis=1)
            & (boxes[:, 2] > boxes[:, 0])
            & (boxes[:, 3] > boxes[:, 1])
        )
        boxes, scores, landmarks = boxes[valid], scores[valid], landmarks[valid]
        objects = []
        for index in self.nms(boxes, scores):
            points = [
                [float(x), float(y)]
                if np.isfinite([x, y]).all() and 0 <= x <= width and 0 <= y <= height
                else None
                for x, y in landmarks[index]
            ]
            objects.append(
                {
                    "id": "obj_" + uuid4().hex,
                    "class_id": 0,
                    "class_name": "face",
                    "bbox": boxes[index].astype(float).tolist(),
                    "keypoints": points,
                    "confidence": float(scores[index]),
                    "source": self.provider,
                }
            )
        return objects
