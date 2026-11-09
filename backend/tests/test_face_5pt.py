"""SCRFD data workflow tests: no weights, downloads, GPU or ONNX Runtime needed."""

import copy
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class FaceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.root = Path(
            cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="dcw-face-"))
        )
        data = cls.root / "datasets"
        data.mkdir()
        cls.stack.enter_context(
            patch.dict(
                os.environ,
                {"DATA_ROOT": str(data), "STATE_ROOT": str(cls.root / "config")},
            )
        )
        global \
            np, \
            schemas, \
            datasets, \
            annotations, \
            exports, \
            collection, \
            videos, \
            manager, \
            tasks, \
            SCRFDDetector, \
            HTTPException
        import numpy as np
        from fastapi import HTTPException
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import database
        from app.schemas import contracts as schemas
        from app.inference import manager
        from app.inference.scrfd import SCRFDDetector
        from app.services import (
            dataset_service as datasets,
            annotation_service as annotations,
        )
        from app.services import (
            export_service as exports,
            collection_service as collection,
        )
        from app.services import video_service as videos, task_service as tasks

        engine = create_engine(
            f"sqlite:///{cls.root / 'test.sqlite3'}",
            connect_args={"check_same_thread": False},
        )
        cls.stack.callback(engine.dispose)
        database.Base.metadata.create_all(engine)
        session = sessionmaker(engine, expire_on_commit=False)
        for module in (database, datasets, annotations, exports, collection, tasks):
            cls.stack.enter_context(patch.object(module, "Session", session))
        cls.stack.enter_context(patch.object(datasets, "DATA_ROOT", data))

    def setUp(self):
        self.dataset = datasets.create(
            schemas.DatasetCreate(
                name="人脸", task_type="detection", annotation_mode="face_5pt"
            )
        )
        self.did = self.dataset["id"]
        self.image = np.zeros((32, 64, 3), dtype=np.uint8)
        self.face = {
            "id": "face_1",
            "class_id": 0,
            "class_name": "face",
            "bbox": [8.0, 4.0, 40.0, 28.0],
            "confidence": 0.9,
            "source": "scrfd",
            "keypoints": [
                [16.0, 10.0],
                [32.0, 10.0],
                [24.0, 16.0],
                [18.0, 22.0],
                [30.0, 22.0],
            ],
        }
        self.progress = Mock()

    def sample(self, objects=None):
        return annotations.save_sample(
            self.did, self.image, {"type": "image"}, objects=copy.deepcopy(objects)
        )

    def label(self, **kwargs):
        return annotations.auto_label(
            self.did, schemas.LabelOptions(provider="scrfd", **kwargs), self.progress
        )

    def test_mode_classes_and_legacy_defaults(self):
        self.assertEqual(self.dataset["task_type"], "detection")
        self.assertEqual(self.dataset["classes"], [{"id": 0, "name": "face"}])
        for kwargs in (
            {"task_type": "raw"},
            {"classes": [{"id": 1, "name": "face"}]},
            {"classes": [{"id": 0, "name": "person"}]},
        ):
            with self.assertRaises(ValueError):
                schemas.DatasetCreate(
                    **(
                        {
                            "name": "x",
                            "task_type": "detection",
                            "annotation_mode": "face_5pt",
                        }
                        | kwargs
                    )
                )
        with self.assertRaises(HTTPException):
            datasets.update(
                self.did,
                schemas.DatasetUpdate(name="x", classes=[{"id": 0, "name": "person"}]),
            )
        old = datasets.create(
            schemas.DatasetCreate(
                name="old", task_type="detection", classes=[{"id": 3, "name": "person"}]
            )
        )
        path = datasets.folder(old["id"]) / "dataset.json"
        data = json.loads(path.read_text())
        data.pop("annotation_mode")
        path.write_text(json.dumps(data))
        self.assertEqual(datasets.get(old["id"])["annotation_mode"], "bbox")
        obj = {k: v for k, v in self.face.items() if k != "keypoints"}
        obj.update(class_id=3, class_name="person", source="custom_yolo")
        sample = annotations.save_sample(old["id"], self.image, {}, objects=[obj])
        self.assertEqual(annotations.get(old["id"], sample["id"])["objects"], [obj])

    def test_provider_guard_and_schema_extensions(self):
        with self.assertRaises(HTTPException):
            datasets.label_classes(self.dataset, schemas.LabelOptions())
        with self.assertRaises(HTTPException):
            datasets.label_classes(
                self.dataset,
                schemas.LabelOptions(provider="scrfd", label_class_mapping={0: 0}),
            )
        for points in ([[1, 2]] * 4, [[float("nan"), 1]] * 5):
            with self.assertRaises(ValueError):
                schemas.ObjectItem(**(self.face | {"keypoints": points}))
        self.assertEqual(schemas.ObjectItem(**self.face).source, "scrfd")
        regular = {**self.dataset, "annotation_mode": "bbox"}
        with self.assertRaises(HTTPException):
            datasets.label_classes(regular, schemas.LabelOptions(provider="scrfd"))

    def test_save_edit_reload_missing_points_and_revision_conflict(self):
        sample = self.sample([self.face])
        obj = {
            **self.face,
            "keypoints": [None, [32, 10], None, [18, 22], None],
            "source": "manual",
            "confidence": None,
        }
        body = schemas.AnnotationEdit(revision=0, objects=[obj])
        saved = annotations.edit(self.did, sample["id"], body)
        self.assertEqual(
            saved["objects"][0]["keypoints"],
            [None, (32.0, 10.0), None, (18.0, 22.0), None],
        )
        self.assertEqual(
            annotations.get(self.did, sample["id"])["objects"][0]["keypoints"],
            [None, [32.0, 10.0], None, [18.0, 22.0], None],
        )
        with self.assertRaises(HTTPException) as conflict:
            annotations.edit(self.did, sample["id"], body)
        self.assertEqual(conflict.exception.status_code, 409)
        for points in ([[-1, 10]] * 5, [[65, 10]] * 5, [[1, 33]] * 5):
            with self.assertRaises(HTTPException):
                annotations.edit(
                    self.did,
                    sample["id"],
                    schemas.AnnotationEdit(
                        revision=saved["revision"],
                        objects=[{**obj, "keypoints": points}],
                    ),
                )

    def test_bbox_only_face_normalizes_missing_points_on_disk(self):
        face = {k: v for k, v in self.face.items() if k != "keypoints"}
        sample = self.sample([face])
        self.assertEqual(
            annotations.get(self.did, sample["id"])["objects"][0]["keypoints"],
            [None] * 5,
        )

    def test_object_api_moves_points_and_delete_last_face_approves_negative(self):
        sample = self.sample([self.face])
        moved = annotations.edit_object(
            self.did,
            sample["id"],
            schemas.ObjectEdit(class_id=0, bbox=[16, 8, 48, 32]),
            "face_1",
        )
        self.assertEqual(moved["objects"][0]["keypoints"][0], [24, 14])
        deleted = annotations.delete_object(self.did, sample["id"], "face_1")
        self.assertEqual(deleted["objects"], [])
        self.assertEqual(deleted["source"]["label_status"], "empty")
        self.assertEqual(
            annotations.set_status(self.did, sample["id"], "approved")["status"],
            "approved",
        )

    def test_explicit_empty_edit_protected_from_default_prelabel(self):
        sample = self.sample([self.face])
        annotations.edit(
            self.did, sample["id"], schemas.AnnotationEdit(revision=0, objects=[])
        )
        with patch.object(manager, "get"), patch.object(manager, "predict") as predict:
            self.label()
        predict.assert_not_called()
        self.assertEqual(annotations.get(self.did, sample["id"])["objects"], [])

    def test_single_batch_prelabel_and_empty_prediction_overwrite(self):
        first, second = self.sample(), self.sample()
        with (
            patch.object(manager, "get"),
            patch.object(
                manager, "predict", return_value=[copy.deepcopy(self.face)]
            ) as predict,
        ):
            self.label(sample_ids=[first["id"]])
            predict.assert_called_once()
            self.assertEqual(annotations.get(self.did, second["id"])["objects"], [])
            self.label()
            self.assertEqual(predict.call_count, 2)
        annotations.set_status(self.did, first["id"], "approved")
        with (
            patch.object(manager, "get"),
            patch.object(manager, "predict", return_value=[]) as predict,
        ):
            self.label(overwrite=True)
            predict.assert_called_once()
        self.assertEqual(
            annotations.get(self.did, second["id"])["source"]["label_status"], "empty"
        )
        self.assertEqual(len(annotations.get(self.did, first["id"])["objects"]), 1)

    def test_inference_cannot_overwrite_concurrent_point_edit(self):
        sample = self.sample()

        def concurrent(*args):
            annotations.edit(
                self.did,
                sample["id"],
                schemas.AnnotationEdit(revision=0, objects=[self.face]),
            )
            return []

        with (
            patch.object(manager, "get"),
            patch.object(manager, "predict", side_effect=concurrent),
            self.assertLogs(annotations.log, level="ERROR"),
        ):
            self.label(overwrite=True)
        self.assertEqual(
            annotations.get(self.did, sample["id"])["objects"][0]["keypoints"],
            self.face["keypoints"],
        )
        self.assertIn("已保留人工修改", self.progress.advance.call_args.args[0])

    def test_image_and_video_prelabel_reuse_scrfd(self):
        path = self.root / "import.jpg"
        path.write_bytes(annotations.encode(self.image))
        with (
            patch.object(manager, "get"),
            patch.object(
                manager, "predict", return_value=[copy.deepcopy(self.face)]
            ) as predict,
        ):
            result = annotations.import_paths(
                self.did,
                [(path, "face.jpg")],
                self.progress,
                schemas.ImageImportOptions(auto_label="scrfd"),
            )
            self.assertEqual(result["labeled"], 1)
            predict.assert_called_once()
            capture = Mock()
            capture.get.side_effect = lambda prop: {
                videos.cv2.CAP_PROP_FPS: 25,
                videos.cv2.CAP_PROP_FRAME_COUNT: 1,
            }.get(prop, 0)
            capture.read.side_effect = [(True, self.image), (False, None)]
            with patch.object(videos.cv2, "VideoCapture", return_value=capture):
                result = videos.extract(
                    self.did,
                    path,
                    schemas.VideoOptions(auto_label="scrfd", every=1),
                    self.progress,
                )
            self.assertEqual(result["saved"], 1)
            self.assertEqual(predict.call_count, 2)

    def test_roi_crop_translates_points_and_marks_outside_missing(self):
        face = copy.deepcopy(self.face)
        result = collection.crop_objects([face], [16, 8, 40, 24])[0]
        self.assertEqual(result["bbox"], [0, 0, 24, 16])
        self.assertEqual(
            result["keypoints"], [[0, 2], [16, 2], [8, 8], [2, 14], [14, 14]]
        )
        result = collection.crop_objects([face], [20, 8, 40, 24])[0]
        self.assertIsNone(result["keypoints"][0])
        self.assertIsNone(result["keypoints"][3])
        self.assertEqual(face, self.face)

    def test_rtsp_roi_saves_faces_with_translated_points(self):
        capture = Mock(camera={"id": "camera", "name": "test"})
        capture.snapshot.side_effect = [(1, self.image), tasks.Cancelled()]
        options = schemas.CollectionStart(
            dataset_id=self.did,
            camera_id="camera",
            mode="roi_interval",
            roi=[16, 8, 40, 24],
            auto_label="scrfd",
        )
        with (
            patch.object(manager, "get"),
            patch.object(manager, "predict", return_value=[copy.deepcopy(self.face)]),
            patch.object(collection.time, "monotonic", side_effect=count(100)),
            patch.object(collection.time, "sleep"),
        ):
            with self.assertRaises(tasks.Cancelled):
                collection.collect(options, capture, self.dataset, self.progress)
        saved = annotations.list_samples(self.did)["items"][0]
        ann = annotations.get(self.did, saved["id"])
        self.assertEqual((ann["width"], ann["height"]), (24, 16))
        self.assertEqual(ann["objects"][0]["keypoints"][0], [0, 2])

    def test_labelv2_unsplit_missing_points_and_negative_zip(self):
        face = copy.deepcopy(self.face)
        face["keypoints"][1] = None
        positive, negative, rejected = (
            self.sample([face]),
            self.sample(),
            self.sample([self.face]),
        )
        for sample in (positive, negative):
            annotations.set_status(self.did, sample["id"], "approved")
        annotations.set_status(self.did, rejected["id"], "rejected")
        options = schemas.ExportOptions(split_mode="none")
        result = exports.export(self.did, options, self.progress, scrfd=True)
        root = Path(result["path"])
        lines = (root / "labelv2.txt").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(sum(line.startswith("#") for line in lines), 2)
        values = next(line.split() for line in lines if not line.startswith("#"))
        self.assertEqual(len(values), 19)
        self.assertEqual(
            [float(v) for v in values[:4]], self.face["bbox"]
        )  # xyxy, not xywh
        self.assertEqual(values[7:10], ["-1", "-1", "-1"])
        for line in lines:
            if line.startswith("#"):
                _, filename, width, height = line.split()
                self.assertTrue((root / "images" / filename).is_file())
                self.assertEqual((int(width), int(height)), (64, 32))
        with zipfile.ZipFile(root.with_suffix(".zip")) as archive:
            self.assertIn(f"{root.name}/labelv2.txt", archive.namelist())
        self.assertEqual(
            exports.preview(
                self.did, schemas.ExportOptions(split_mode="none", include_empty=False)
            )["total"],
            1,
        )
        self.assertEqual(exports.preview(self.did, options)["total"], 2)

    def test_split_export_each_label_path_resolves_and_no_dummy_faces(self):
        for objects in ([self.face], [], []):
            sample = self.sample(objects)
            annotations.set_status(self.did, sample["id"], "approved")
        options = schemas.ExportOptions(
            split_mode="train_val_test",
            train_ratio=0.34,
            val_ratio=0.33,
            test_ratio=0.33,
        )
        result = exports.export(self.did, options, self.progress, scrfd=True)
        root = Path(result["path"])
        for part in ("train", "val", "test"):
            lines = (root / part / "labelv2.txt").read_text().splitlines()
            self.assertTrue((root / part / "images" / lines[0].split()[1]).is_file())
        for line in (root / "labelv2.txt").read_text().splitlines():
            if line.startswith("#"):
                self.assertTrue((root / line.split()[1]).is_file())

    def test_api_manual_empty_approve_export_and_missing_model_message(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import (
            datasets as dataset_api,
            annotations as annotation_api,
            exports as export_api,
            models as model_api,
        )

        app = FastAPI()
        for module in (dataset_api, annotation_api, export_api, model_api):
            app.include_router(module.router)
        sample = self.sample([self.face])
        base = f"/api/datasets/{self.did}"
        with TestClient(app) as client:
            response = client.put(
                f"{base}/samples/{sample['id']}/annotation",
                json={"revision": 0, "objects": []},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                client.post(f"{base}/samples/{sample['id']}/approve").status_code, 200
            )
            with patch.object(
                manager, "SCRFD_MODEL_PATH", "/not-on-this-server/det_10g.onnx"
            ):
                response = client.post(f"{base}/auto-label", json={"provider": "scrfd"})
            self.assertEqual(response.status_code, 503)
            self.assertIn("部署服务器", response.json()["detail"])
            with patch.object(
                tasks, "submit", return_value={"task_id": "test"}
            ) as submit:
                self.assertEqual(
                    client.post(
                        f"{base}/export/scrfd", json={"split_mode": "none"}
                    ).status_code,
                    200,
                )
                result = submit.call_args.args[2](self.progress)
                self.assertTrue((Path(result["path"]) / "labelv2.txt").is_file())

    def outputs(self, batched=False):
        outputs = []
        for features in (1, 4, 10):
            for stride in (8, 16, 32):
                outputs.append(
                    np.zeros(((32 // stride) ** 2 * 2, features), dtype=np.float32)
                )
        # Two duplicate anchors; lower score must disappear in NMS.
        outputs[0][10:12] = [[0.95], [0.8]]
        outputs[3][10:12] = [1, 1, 1, 1]
        outputs[6][10:12] = [-0.25, -0.25, 0.25, -0.25, 0, 0, -0.25, 0.25, 0.25, 0.25]
        return [value[None] for value in outputs] if batched else outputs

    def detector(self, outputs, shape=None):
        session = Mock()
        session.get_inputs.return_value = [
            SimpleNamespace(name="input", shape=shape or [1, 3, 32, 32])
        ]
        session.get_outputs.return_value = [Mock() for _ in range(9)]
        session.run.return_value = outputs
        runtime = SimpleNamespace(InferenceSession=Mock(return_value=session))
        with patch.dict(sys.modules, {"onnxruntime": runtime}):
            detector = SCRFDDetector("/simulated/det_10g.onnx")
            detector.load()
            detector.load()
        runtime.InferenceSession.assert_called_once()
        detector.set_classes(self.dataset["classes"])
        return detector, session

    def test_decoder_original_pixels_nms_confidence_rgb_and_empty(self):
        for batched in (False, True):
            detector, session = self.detector(self.outputs(batched))
            image = self.image.copy()
            image[:] = [10, 20, 30]
            objects = detector.predict(image, 0.5)
            self.assertEqual(len(objects), 1)
            self.assertEqual(objects[0]["bbox"], [0, 0, 32, 32])
            self.assertEqual(
                objects[0]["keypoints"],
                [[12, 12], [20, 12], [16, 16], [12, 20], [20, 20]],
            )
            self.assertAlmostEqual(objects[0]["confidence"], 0.95, places=6)
            blob = session.run.call_args.args[1]["input"]
            self.assertEqual(blob.shape, (1, 3, 32, 32))
            np.testing.assert_allclose(
                blob[0, :, 0, 0], (np.array([30, 20, 10]) - 127.5) / 128
            )
            self.assertEqual(detector.predict(image, 0.99), [])

    def test_decoder_dynamic_input_and_invalid_head(self):
        detector, session = self.detector(self.outputs(), [1, 3, "height", "width"])
        self.assertEqual(detector.input_size, (640, 640))
        with self.assertRaises(HTTPException):
            detector.predict(self.image)
        detector, session = self.detector(self.outputs())
        session.run.return_value[6][10] = [100] * 10
        self.assertEqual(detector.predict(self.image)[0]["keypoints"], [None] * 5)

    def test_decoder_portrait_and_rounded_letterbox_scaling(self):
        detector, _ = self.detector(self.outputs())
        portrait = np.zeros((64, 32, 3), dtype=np.uint8)
        self.assertEqual(detector.predict(portrait)[0]["keypoints"][0], [12, 12])
        odd_size = np.zeros((31, 65, 3), dtype=np.uint8)
        obj = detector.predict(odd_size)[0]
        self.assertEqual(obj["bbox"], [0, 0, 32.5, 31])
        np.testing.assert_allclose(obj["keypoints"][0], [6 * 65 / 32, 6 * 31 / 15])

    def test_missing_runtime_reports_deployment_guidance_without_caching_failure(self):
        with (
            patch.object(manager, "resolve", return_value="/simulated/det_10g.onnx"),
            patch.object(manager, "cache", {}),
            patch.dict(sys.modules, {"onnxruntime": None}),
            self.assertLogs(manager.log, level="ERROR"),
        ):
            with self.assertRaises(HTTPException) as failure:
                manager.get("scrfd")
            self.assertIn("实际部署服务器", failure.exception.detail)
            self.assertIn("onnxruntime", failure.exception.detail)
            self.assertEqual(manager.cache, {})

    def run_face_trigger(self, predictions, mode="detect_raw", auto_label="scrfd"):
        capture = Mock(camera={"id": "camera", "name": "test"})
        capture.snapshot.side_effect = [
            (i, self.image.copy()) for i in range(len(predictions))
        ] + [tasks.Cancelled()]
        options = schemas.CollectionStart(
            dataset_id=self.did,
            camera_id="camera",
            mode=mode,
            detector="scrfd",
            auto_label=auto_label,
        )
        with (
            patch.object(manager, "get"),
            patch.object(
                manager, "predict", side_effect=copy.deepcopy(predictions)
            ) as predict,
            patch.object(collection.time, "monotonic", side_effect=count(100)),
            patch.object(collection.time, "sleep"),
        ):
            with self.assertRaises(tasks.Cancelled):
                collection.collect(options, capture, self.dataset, self.progress)
        return predict

    def test_scrfd_trigger_defaults_to_face_and_rejects_other_targets(self):
        from app.inference.targets import trigger_classes

        options = schemas.CollectionStart(
            dataset_id=self.did, camera_id="camera", detector="scrfd"
        )
        self.assertEqual(options.target_classes, ["face"])
        self.assertEqual(
            trigger_classes("scrfd", ["face", "0"]), [{"id": 0, "name": "face"}]
        )
        for targets in ([], ["person"], ["1"], ["face", "person"]):
            with self.assertRaises(HTTPException):
                trigger_classes("scrfd", targets)

    def test_detect_raw_scrfd_skips_no_face_and_reuses_predictions_with_landmarks(self):
        predict = self.run_face_trigger([[], [self.face]])
        self.assertEqual(
            predict.call_count, 2
        )  # once per frame, no second labelling pass
        self.assertTrue(all(call.args[0] == "scrfd" for call in predict.call_args_list))
        self.assertEqual(predict.call_args.args[2], [{"id": 0, "name": "face"}])
        samples = annotations.list_samples(self.did)["items"]
        self.assertEqual(len(samples), 1)
        ann = annotations.get(self.did, samples[0]["id"])
        self.assertEqual((ann["width"], ann["height"]), (64, 32))
        self.assertEqual(ann["objects"], [self.face])
        self.assertEqual(ann["source"]["mode"], "detect_raw")

    def test_detect_crop_scrfd_translates_points_and_does_not_infer_again(self):
        predict = self.run_face_trigger([[self.face]], mode="detect_crop")
        predict.assert_called_once()
        sample = annotations.list_samples(self.did)["items"][0]
        ann = annotations.get(self.did, sample["id"])
        self.assertEqual((ann["width"], ann["height"]), (32, 24))
        self.assertEqual(ann["objects"][0]["bbox"], [0, 0, 32, 24])
        self.assertEqual(ann["objects"][0]["keypoints"][0], [8, 6])
        self.assertEqual(ann["source"]["original_size"], [64, 32])
        self.assertEqual(self.face["keypoints"][0], [16, 10])

    def test_scrfd_trigger_can_save_unlabelled_images_when_auto_label_is_off(self):
        predict = self.run_face_trigger([[self.face]], auto_label="off")
        predict.assert_called_once()
        sample = annotations.list_samples(self.did)["items"][0]
        self.assertEqual(annotations.get(self.did, sample["id"])["objects"], [])

    def test_small_face_preview_uses_small_landmark_dots(self):
        face = copy.deepcopy(self.face)
        face["bbox"] = [50, 50, 70, 74]
        face["keypoints"] = [[56, 58], [64, 58], [60, 62], [57, 69], [63, 69]]
        preview = annotations.render_preview(
            np.zeros((100, 100, 3), dtype=np.uint8),
            {"width": 100, "height": 100, "objects": [face]},
        )
        red = np.all(preview == [102, 102, 255], axis=2)
        self.assertTrue(red[58, 56])
        self.assertLessEqual(int(red.sum()), 4)
        self.assertFalse(red[58, 58])

    def test_existing_face_preview_refreshes_once_without_editing_annotations(self):
        sample = self.sample([self.face])
        before = copy.deepcopy(annotations.get(self.did, sample["id"]))
        preview = datasets.folder(self.did) / "previews" / f"{sample['id']}.jpg"
        old_time = preview.stat().st_mtime_ns - 10_000_000_000
        os.utime(preview, ns=(old_time, old_time))
        with patch.object(
            annotations, "render_preview", wraps=annotations.render_preview
        ) as render:
            self.assertEqual(annotations.preview_file(self.did, before), preview)
            refreshed = preview.stat().st_mtime_ns
            self.assertGreater(refreshed, old_time)
            annotations.preview_file(self.did, before)
            annotations.thumbnail(self.did, before)
            render.assert_called_once()
        self.assertEqual(annotations.get(self.did, sample["id"]), before)


if __name__ == "__main__":
    unittest.main()
