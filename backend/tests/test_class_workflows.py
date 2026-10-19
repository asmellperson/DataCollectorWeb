"""Exercise class selection using real temporary datasets, without cameras or weights."""

import copy
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class ClassWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        root = Path(
            cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="dcw-classes-"))
        )
        data_root = root / "datasets"
        data_root.mkdir()
        cls.stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "DATA_ROOT": str(data_root),
                    "STATE_ROOT": str(root / "config"),
                },
            )
        )
        global \
            np, \
            schemas, \
            datasets, \
            annotations, \
            collection, \
            exports, \
            videos, \
            manager, \
            tasks
        global HTTPException, trigger_classes, CustomDetector
        import numpy as np
        from fastapi import HTTPException
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import database
        from app.schemas import contracts as schemas
        from app.inference import manager
        from app.inference.custom_yolo import CustomDetector
        from app.inference.targets import trigger_classes
        from app.services import dataset_service as datasets
        from app.services import annotation_service as annotations
        from app.services import collection_service as collection
        from app.services import export_service as exports
        from app.services import video_service as videos
        from app.services import task_service as tasks

        # Also safe when another test module has already imported the application.
        engine = create_engine(f"sqlite:///{root / 'test.sqlite3'}")
        cls.stack.callback(engine.dispose)
        database.Base.metadata.create_all(engine)
        session = sessionmaker(engine, expire_on_commit=False)
        for module in (database, datasets, annotations, collection, exports, tasks):
            cls.stack.enter_context(patch.object(module, "Session", session))
        cls.stack.enter_context(patch.object(datasets, "DATA_ROOT", data_root))

    def setUp(self):
        self.classes = [
            {"id": 4, "name": "person"},
            {"id": 9, "name": "play_phone"},
            {"id": 12, "name": "car"},
        ]
        self.dataset = datasets.create(
            schemas.DatasetCreate(
                name="车辆采集",
                task_type="detection",
                classes=self.classes,
            )
        )
        self.did = self.dataset["id"]
        self.image = np.zeros((32, 48, 3), dtype=np.uint8)
        self.progress = Mock()
        self.loader = self.enterContext(patch.object(manager, "get"))
        self.loader.return_value.model.names = {0: "person", 2: "car", 7: "truck"}

    def box(self, index, name=None):
        cls = self.classes[index]
        return {
            "id": name or cls["name"],
            "class_id": cls["id"],
            "class_name": cls["name"],
            "bbox": [1.0, 2.0, 20.0, 25.0],
            "confidence": 0.8,
            "source": "yolo_world",
        }

    def sample(self, objects=None):
        return annotations.save_sample(
            self.did, self.image, {"type": "image"}, objects=objects
        )

    def run_collection(self, options, predictions, frames=1, dataset=None):
        capture = Mock(camera={"id": "camera", "name": "test"})
        capture.snapshot.side_effect = [
            (i, self.image.copy()) for i in range(frames)
        ] + [tasks.Cancelled()]
        with (
            patch.object(manager, "predict", side_effect=predictions) as predict,
            patch.object(collection.time, "monotonic", side_effect=count(100)),
            patch.object(collection.time, "sleep"),
        ):
            with self.assertRaises(tasks.Cancelled):
                collection.collect(
                    options, capture, dataset or self.dataset, self.progress
                )
        return predict

    def test_car_trigger_then_all_three_labels_use_separate_models_and_ids(self):
        trigger = {**self.box(2), "class_id": 0}
        labels = [self.box(i) for i in range(3)]
        options = schemas.CollectionStart(
            dataset_id=self.did,
            camera_id="camera",
            mode="detect_raw",
            detector="custom_yolo",
            trigger_model_path="coco.pt",
            target_classes=["2"],
            auto_label="custom_yolo",
            model_path="business.pt",
            label_class_ids=[4, 9, 12],
        )
        # First frame contains no selected trigger; only the second frame is labeled/saved.
        predict = self.run_collection(options, [[], [trigger], labels], frames=2)
        self.assertEqual(len(predict.call_args_list), 3)
        self.assertEqual(
            predict.call_args_list[0].args[:3],
            ("custom_yolo", "coco.pt", [{"id": 0, "name": "car"}]),
        )
        self.assertEqual(
            predict.call_args_list[2].args[:3],
            ("custom_yolo", "business.pt", self.classes),
        )
        samples = annotations.list_samples(self.did)["items"]
        self.assertEqual(len(samples), 1)
        saved = annotations.get(self.did, samples[0]["id"])
        self.assertEqual(saved["objects"], labels)
        self.assertEqual(saved["source"]["frame_id"], 1)
        self.assertEqual((saved["width"], saved["height"]), (48, 32))
        self.assertTrue(
            np.array_equal(
                annotations.read_image(datasets.folder(self.did) / saved["image"]),
                self.image,
            )
        )

    def test_world_trigger_not_reused_as_labels_even_with_same_provider(self):
        options = schemas.CollectionStart(
            dataset_id=self.did,
            camera_id="camera",
            mode="detect_raw",
            target_classes=["2"],
            auto_label="yolo_world",
            label_class_ids=[9],
        )
        predict = self.run_collection(
            options, [[{**self.box(2), "class_id": 0}], [self.box(1)]]
        )
        self.assertEqual(predict.call_args_list[1].args[2], [self.classes[1]])
        saved = annotations.get(
            self.did, annotations.list_samples(self.did)["items"][0]["id"]
        )
        self.assertEqual([o["class_id"] for o in saved["objects"]], [9])

    def test_trigger_crop_uses_trigger_box_but_labels_cropped_image(self):
        options = schemas.CollectionStart(
            dataset_id=self.did,
            camera_id="camera",
            mode="detect_crop",
            target_classes=["car"],
            auto_label="yolo_world",
            label_class_ids=[4],
        )
        trigger = {**self.box(2), "bbox": [2, 3, 30, 31]}
        predict = self.run_collection(options, [[trigger], [self.box(0)]])
        self.assertEqual(predict.call_args_list[1].args[3].shape, (28, 28, 3))
        saved = annotations.get(
            self.did, annotations.list_samples(self.did)["items"][0]["id"]
        )
        self.assertEqual(saved["source"]["roi"], [2, 3, 30, 31])
        self.assertEqual(saved["objects"], [self.box(0)])

    def test_raw_collection_trigger_does_not_write_labels(self):
        raw = datasets.create(schemas.DatasetCreate(name="raw", task_type="raw"))
        options = schemas.CollectionStart(
            dataset_id=raw["id"],
            camera_id="camera",
            mode="detect_raw",
            target_classes=["car"],
        )
        predict = self.run_collection(options, [[self.box(2)]], dataset=raw)
        self.assertEqual(predict.call_count, 1)
        sid = annotations.list_samples(raw["id"])["items"][0]["id"]
        self.assertEqual(annotations.get(raw["id"], sid)["objects"], [])

    def test_trigger_names_numbers_and_invalid_ids(self):
        expected = [{"id": 0, "name": "car"}, {"id": 1, "name": "truck"}]
        self.assertEqual(trigger_classes("yolo_world", ["2", "car", "7"]), expected)
        self.assertEqual(
            trigger_classes("custom_yolo", ["8", "truck"], {8: "car", 4: "truck"}),
            expected,
        )
        for provider, target, names in [
            ("yolo_world", "80", None),
            ("custom_yolo", "2", {0: "car"}),
            ("custom_yolo", "unknown", {0: "car"}),
        ]:
            with self.assertRaises(HTTPException):
                trigger_classes(provider, [target], names)

    def test_custom_model_rejects_partially_unsupported_selection(self):
        detector = CustomDetector("unused.pt")
        detector.model = SimpleNamespace(names={0: "person", 2: "car"})
        with self.assertRaisesRegex(HTTPException, "play_phone"):
            detector.set_classes(self.classes)
        detector.set_classes([self.classes[2]])
        self.assertEqual(detector.mapping, {2: self.classes[2]})
        self.assertEqual(detector.model.names, {0: "person", 2: "car"})

    def test_label_selection_validates_unknown_duplicate_and_empty_ids(self):
        for ids in ([123], [4, 4], []):
            with self.assertRaises(HTTPException):
                datasets.selected_classes(self.dataset, ids)
        from pydantic import ValidationError

        for contract in (
            schemas.LabelOptions,
            schemas.ImageImportOptions,
            schemas.VideoOptions,
        ):
            for ids in ([], [-1], [True], [1.5]):
                with self.assertRaises(ValidationError):
                    contract(label_class_ids=ids)

    def test_overwrite_subset_preserves_other_classes_and_clears_selected_no_detection(
        self,
    ):
        sample = self.sample([self.box(i) for i in range(3)])
        options = schemas.LabelOptions(label_class_ids=[12], overwrite=True)
        with patch.object(manager, "predict", return_value=[]) as predict:
            annotations.auto_label(self.did, options, self.progress)
        self.assertEqual(predict.call_args.args[2], [self.classes[2]])
        self.assertEqual(
            annotations.get(self.did, sample["id"])["objects"],
            [self.box(0), self.box(1)],
        )

    def test_can_add_missing_class_without_overwriting_other_class(self):
        sample = self.sample([self.box(0)])
        with patch.object(manager, "predict", return_value=[self.box(2)]):
            annotations.auto_label(
                self.did, schemas.LabelOptions(label_class_ids=[12]), self.progress
            )
        self.assertEqual(
            annotations.get(self.did, sample["id"])["objects"],
            [self.box(0), self.box(2)],
        )

    def test_existing_selected_class_is_skipped_without_overwrite(self):
        sample = self.sample([self.box(2)])
        with patch.object(manager, "predict") as predict:
            annotations.auto_label(
                self.did, schemas.LabelOptions(label_class_ids=[12]), self.progress
            )
        predict.assert_not_called()
        self.progress.advance.assert_called_once_with(skipped=True)
        self.assertEqual(
            annotations.get(self.did, sample["id"])["objects"], [self.box(2)]
        )

    def test_import_images_and_video_pass_only_selected_classes(self):
        import cv2

        path = datasets.folder(self.did) / "test.png"
        cv2.imwrite(str(path), self.image)
        with patch.object(manager, "predict", return_value=[self.box(2)]) as predict:
            annotations.import_paths(
                self.did,
                [(path, "test.png")],
                self.progress,
                schemas.ImageImportOptions(
                    auto_label="yolo_world", label_class_ids=[12]
                ),
            )
            self.assertEqual(predict.call_args.args[2], [self.classes[2]])
        cap = Mock()
        cap.isOpened.return_value = True
        cap.get.side_effect = lambda key: {
            cv2.CAP_PROP_FPS: 25,
            cv2.CAP_PROP_FRAME_COUNT: 1,
            cv2.CAP_PROP_POS_MSEC: 0,
        }[key]
        cap.read.side_effect = [(True, self.image), (False, None)]
        with (
            patch.object(videos.cv2, "VideoCapture", return_value=cap),
            patch.object(manager, "predict", return_value=[self.box(1)]) as predict,
        ):
            videos.extract(
                self.did,
                Path("video.mp4"),
                schemas.VideoOptions(auto_label="yolo_world", label_class_ids=[9]),
                self.progress,
            )
            self.assertEqual(predict.call_args.args[2], [self.classes[1]])
        cap.release.assert_called_once()

    def test_single_class_export_filters_remaps_and_keeps_source_unchanged(self):
        import yaml

        selected = self.sample([self.box(0), self.box(2)])
        self.sample([self.box(0)])
        self.sample([])
        before = copy.deepcopy(annotations.get(self.did, selected["id"]))
        options = schemas.ExportOptions(
            split_mode="none", include_pending=True, class_ids=[12], include_empty=False
        )
        self.assertEqual(exports.preview(self.did, options)["total"], 1)
        result = exports.export(self.did, options, self.progress, yolo=True)
        root = Path(result["path"])
        self.assertEqual(
            yaml.safe_load((root / "data.yaml").read_text())["names"], {0: "car"}
        )
        self.assertTrue(
            (root / "labels" / f"{selected['id']}.txt").read_text().startswith("0 ")
        )
        ann = json.loads((root / "annotations" / f"{selected['id']}.json").read_text())
        self.assertEqual([o["class_id"] for o in ann["objects"]], [0])
        self.assertTrue((root / ann["image"]).is_file())
        self.assertEqual(
            json.loads((root / "dataset.json").read_text())["classes"],
            [{"id": 0, "name": "car"}],
        )
        self.assertEqual(
            json.loads((root / "manifest.json").read_text())["class_id_mapping"],
            {"12": 0},
        )
        self.assertEqual(annotations.get(self.did, selected["id"]), before)
        self.assertEqual(datasets.get(self.did)["classes"], self.classes)

    def test_multiclass_export_keeps_optional_backgrounds_and_split_counts_match(self):
        import yaml

        self.sample([self.box(i) for i in range(3)])
        other = self.sample([self.box(1)])
        empty = self.sample([])
        options = schemas.ExportOptions(include_pending=True, class_ids=[4, 12])
        plan = exports.preview(self.did, options)
        result = exports.export(self.did, options, self.progress, yolo=True)
        self.assertEqual(plan["counts"], result["counts"])
        self.assertEqual(result["total"], 3)
        root = Path(result["path"])
        self.assertEqual(
            yaml.safe_load((root / "data.yaml").read_text())["names"],
            {0: "person", 1: "car"},
        )
        for sample in (other, empty):
            self.assertEqual(
                next((root / "labels").rglob(f"{sample['id']}.txt")).read_text(), ""
            )

    def test_legacy_export_keeps_original_ids_and_empty_selection_is_rejected(self):
        import yaml

        self.sample([self.box(2)])
        options = schemas.ExportOptions(split_mode="none", include_pending=True)
        result = exports.export(self.did, options, self.progress, yolo=True)
        names = yaml.safe_load((Path(result["path"]) / "data.yaml").read_text())[
            "names"
        ]
        self.assertEqual(names[12], "car")
        self.assertEqual(names[0], "__unused_class_0")
        with self.assertRaises(HTTPException):
            exports.preview(
                self.did, schemas.ExportOptions(class_ids=[999], split_mode="none")
            )

    def test_coco_phone_mapping_saved_under_play_phone_and_exported(self):
        detector = CustomDetector("coco.pt")
        detector.model = Mock(names={0: "person", 2: "car", 67: "cell phone"})
        detector.model.predict.return_value = [
            SimpleNamespace(
                boxes=[
                    SimpleNamespace(
                        cls=np.array(67),
                        conf=np.array(0.8),
                        xyxy=np.array([[1.0, 2.0, 20.0, 25.0]]),
                    )
                ]
            )
        ]
        self.loader.return_value = detector
        sample = self.sample([self.box(0), self.box(1), self.box(2)])
        options = schemas.LabelOptions(
            provider="custom_yolo",
            model_path="coco.pt",
            label_class_ids=[9],
            label_class_mapping={9: 67},
            overwrite=True,
        )
        annotations.auto_label(self.did, options, self.progress)
        saved = annotations.get(self.did, sample["id"])
        phone = next(obj for obj in saved["objects"] if obj["class_id"] == 9)
        self.assertEqual(phone["class_name"], "play_phone")
        self.assertEqual(phone["bbox"], [1.0, 2.0, 20.0, 25.0])
        self.assertEqual(phone["source"], "custom_yolo")
        self.assertEqual(
            [o for o in saved["objects"] if o["class_id"] != 9],
            [self.box(0), self.box(2)],
        )
        self.assertEqual(detector.model.predict.call_args.kwargs["classes"], [67])
        self.assertEqual(detector.model.names[67], "cell phone")
        result = exports.export(
            self.did,
            schemas.ExportOptions(
                class_ids=[9],
                split_mode="none",
                include_pending=True,
            ),
            self.progress,
            yolo=True,
        )
        import yaml

        self.assertEqual(
            yaml.safe_load((Path(result["path"]) / "data.yaml").read_text())["names"],
            {0: "play_phone"},
        )

    def test_world_coco_mapping_changes_prompt_but_preserves_saved_name(self):
        from app.inference.yolo_world import WorldDetector

        detector = WorldDetector("world.pt")
        detector.model = Mock()
        detector.model.predict.return_value = [
            SimpleNamespace(
                boxes=[
                    SimpleNamespace(
                        cls=np.array(1),
                        conf=np.array(0.8),
                        xyxy=np.array([[1.0, 2.0, 20.0, 25.0]]),
                    )
                ]
            )
        ]
        classes = datasets.label_classes(
            self.dataset, schemas.LabelOptions(label_class_mapping={4: 0, 9: 67, 12: 2})
        )
        detector.set_classes(classes)
        detector.model.set_classes.assert_called_once_with(
            ["person", "cell phone", "car"]
        )
        objects = detector.predict(self.image)
        self.assertEqual(
            (objects[0]["class_id"], objects[0]["class_name"]), (9, "play_phone")
        )
        detector.set_classes(self.classes)
        detector.model.set_classes.assert_called_with(["person", "play_phone", "car"])
        with self.assertRaises(HTTPException):
            detector.set_classes([{**self.classes[1], "model_class_id": 80}])

    def test_invalid_mapping_cannot_silently_change_output_class(self):
        from pydantic import ValidationError

        for contract in (
            schemas.LabelOptions,
            schemas.ImageImportOptions,
            schemas.VideoOptions,
            schemas.CollectionStart,
        ):
            params = (
                {"dataset_id": self.did, "camera_id": "camera"}
                if contract is schemas.CollectionStart
                else {}
            )
            for mapping in (
                {9: -1},
                {9: 1.5},
                {9: True},
                {-1: 67},
                {12: []},
                {12: [2, True]},
                {12: [2, -3]},
                {12: [2, 1.5]},
                {12: [2, "3"]},
                {12: [10000]},
            ):
                with self.assertRaises(ValidationError):
                    contract(**params, label_class_mapping=mapping)
            parsed = contract(
                **params, label_class_mapping={4: 0, 9: 67, 12: [2, 3, 2]}
            )
            self.assertEqual(parsed.label_class_mapping, {4: 0, 9: 67, 12: [2, 3]})
        parsed = schemas.LabelOptions.model_validate_json(
            '{"label_class_mapping":{"9":67}}'
        )
        self.assertEqual(
            datasets.label_classes(self.dataset, parsed)[1],
            {**self.classes[1], "model_class_id": 67},
        )
        with self.assertRaises(HTTPException):
            datasets.label_classes(
                self.dataset, schemas.LabelOptions(label_class_mapping={999: 67})
            )
        detector = CustomDetector("coco.pt")
        detector.model = Mock(names={0: "person", 2: "car", 67: "cell phone"})
        for classes in (
            [{**self.classes[1], "model_class_id": 999}],
            [self.classes[0], {**self.classes[1], "model_class_id": 0}],
        ):
            with self.assertRaises(HTTPException):
                detector.set_classes(classes)
        detector.model.predict.assert_not_called()

    def test_mapping_reaches_collection_image_import_and_video(self):
        mapping = {4: 0, 9: 67, 12: [2, 3]}
        options = schemas.CollectionStart(
            dataset_id=self.did,
            camera_id="camera",
            mode="detect_raw",
            target_classes=["2"],
            auto_label="yolo_world",
            label_class_mapping=mapping,
        )
        predict = self.run_collection(options, [[self.box(2)], [self.box(1)]])
        expected = [
            {**self.classes[0], "model_class_id": 0},
            {**self.classes[1], "model_class_id": 67},
            {**self.classes[2], "model_class_ids": [2, 3]},
        ]
        self.assertEqual(predict.call_args_list[0].args[2], [{"id": 0, "name": "car"}])
        self.assertEqual(predict.call_args_list[1].args[2], expected)
        import cv2

        path = datasets.folder(self.did) / "mapped.png"
        cv2.imwrite(str(path), self.image)
        with patch.object(manager, "predict", return_value=[self.box(1)]) as predict:
            annotations.import_paths(
                self.did,
                [(path, "mapped.png")],
                self.progress,
                schemas.ImageImportOptions(
                    auto_label="yolo_world", label_class_mapping=mapping
                ),
            )
            self.assertEqual(predict.call_args.args[2], expected)
        cap = Mock()
        cap.isOpened.return_value = True
        cap.get.side_effect = lambda key: {
            cv2.CAP_PROP_FPS: 25,
            cv2.CAP_PROP_FRAME_COUNT: 1,
            cv2.CAP_PROP_POS_MSEC: 0,
        }[key]
        cap.read.side_effect = [(True, self.image), (False, None)]
        with (
            patch.object(videos.cv2, "VideoCapture", return_value=cap),
            patch.object(manager, "predict", return_value=[self.box(1)]) as predict,
        ):
            videos.extract(
                self.did,
                Path("video.mp4"),
                schemas.VideoOptions(
                    auto_label="yolo_world", label_class_mapping=mapping
                ),
                self.progress,
            )
            self.assertEqual(predict.call_args.args[2], expected)

    def test_multiple_detector_classes_saved_and_exported_as_one_car_class(self):
        detector = CustomDetector("coco.pt")
        detector.model = Mock(names={2: "car", 3: "motorcycle"})
        boxes = [
            SimpleNamespace(
                cls=np.array(source),
                conf=np.array(0.8),
                xyxy=np.array([[1.0 + index, 2.0, 20.0, 25.0]]),
            )
            for index, source in enumerate([2, 3])
        ]
        detector.model.predict.return_value = [SimpleNamespace(boxes=boxes)]
        self.loader.return_value = detector
        sample = self.sample([self.box(0)])
        annotations.auto_label(
            self.did,
            schemas.LabelOptions(
                provider="custom_yolo",
                model_path="coco.pt",
                label_class_ids=[12],
                label_class_mapping={12: [2, 3]},
            ),
            self.progress,
        )
        objects = annotations.get(self.did, sample["id"])["objects"]
        self.assertEqual(objects[0], self.box(0))
        merged = objects[1:]
        self.assertEqual(
            [(obj["class_id"], obj["class_name"]) for obj in merged], [(12, "car")] * 2
        )
        self.assertEqual(
            [obj["bbox"] for obj in merged], [box.xyxy[0].tolist() for box in boxes]
        )
        self.assertEqual(len({obj["id"] for obj in merged}), 2)
        self.assertEqual(detector.model.predict.call_args.kwargs["classes"], [2, 3])
        result = exports.export(
            self.did,
            schemas.ExportOptions(
                class_ids=[12],
                split_mode="none",
                include_pending=True,
            ),
            self.progress,
            yolo=True,
        )
        root = Path(result["path"])
        lines = (root / "labels" / f"{sample['id']}.txt").read_text().splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["0", "0"])
        import yaml

        self.assertEqual(
            yaml.safe_load((root / "data.yaml").read_text())["names"], {0: "car"}
        )

    def test_world_multiple_prompts_keep_following_class_ids_and_cache_correct(self):
        from app.inference.yolo_world import WorldDetector

        detector = WorldDetector("world.pt")
        detector.model = Mock()
        classes = [
            {**self.classes[2], "model_class_ids": [2, 3, 2]},
            {**self.classes[1], "model_class_id": 67},
        ]
        detector.set_classes(classes)
        detector.set_classes(classes)
        detector.model.set_classes.assert_called_once_with(
            ["car", "motorcycle", "cell phone"]
        )
        detector.model.predict.return_value = [
            SimpleNamespace(
                boxes=[
                    SimpleNamespace(
                        cls=np.array(i),
                        conf=np.array(0.8),
                        xyxy=np.array([[1.0, 2.0, 20.0, 25.0]]),
                    )
                    for i in range(3)
                ]
            )
        ]
        self.assertEqual(
            [
                (obj["class_id"], obj["class_name"])
                for obj in detector.predict(self.image)
            ],
            [(12, "car"), (12, "car"), (9, "play_phone")],
        )
        classes[0]["model_class_ids"].append(1)
        detector.set_classes(classes)
        detector.model.set_classes.assert_called_with(
            ["car", "motorcycle", "bicycle", "cell phone"]
        )

    def test_overlapping_sources_cannot_map_to_different_saved_classes(self):
        from app.inference.yolo_world import WorldDetector

        custom = CustomDetector("coco.pt")
        custom.model = Mock(names={2: "car", 3: "motorcycle", 67: "cell phone"})
        world = WorldDetector("world.pt")
        world.model = Mock()
        for detector in [custom, world]:
            for classes in [
                [
                    {**self.classes[2], "model_class_ids": [2, 3]},
                    {**self.classes[1], "model_class_ids": [67, 3]},
                ],
                [{**self.classes[2], "model_class_ids": [2, 80]}],
            ]:
                with self.assertRaises(HTTPException):
                    detector.set_classes(classes)
            detector.model.predict.assert_not_called()

    def test_review_anchor_tracks_image_188_after_earlier_images_are_deleted(self):
        from app.db.database import Sample
        from sqlalchemy import delete

        ids = [f"{self.did}_{index:04}" for index in range(205)]
        with annotations.Session() as db:
            db.add_all(
                [
                    Sample(
                        id=sid,
                        dataset_id=self.did,
                        image=f"images/{sid}.jpg",
                        width=48,
                        height=32,
                        created_at="2026-10-09T00:00:00",
                    )
                    for sid in ids
                ]
            )
            db.commit()
        page = annotations.list_samples(self.did, offset=100, anchor_id=ids[187])
        self.assertEqual(
            (page["offset"], page["anchor_index"], page["total"]), (100, 187, 205)
        )
        self.assertEqual(page["items"][87]["id"], ids[187])
        with annotations.Session() as db:
            db.execute(delete(Sample).where(Sample.id.in_(ids[:100])))
            db.commit()
        page = annotations.list_samples(self.did, offset=100, anchor_id=ids[187])
        self.assertEqual((page["offset"], page["anchor_index"]), (0, 87))
        self.assertEqual(page["items"][87]["id"], ids[187])
        with annotations.Session() as db:
            db.get(Sample, ids[187]).status = "approved"
            db.commit()
        filtered = annotations.list_samples(
            self.did, status="pending", offset=100, anchor_id=ids[187]
        )
        self.assertIsNone(filtered["anchor_index"])
        self.assertNotIn(ids[187], [s["id"] for s in filtered["items"]])
        approved = annotations.list_samples(
            self.did, status="approved", anchor_id=ids[187]
        )
        self.assertEqual((approved["offset"], approved["anchor_index"]), (0, 0))

    def test_review_anchor_missing_or_from_another_dataset_falls_back_safely(self):
        sample = self.sample()
        page = annotations.list_samples(self.did, offset=900, anchor_id="deleted")
        self.assertEqual((page["offset"], page["anchor_index"]), (0, None))
        self.assertEqual(page["items"][0]["id"], sample["id"])
        empty = datasets.create(schemas.DatasetCreate(name="empty", task_type="raw"))
        page = annotations.list_samples(empty["id"], offset=100, anchor_id=sample["id"])
        self.assertEqual(
            (page["items"], page["offset"], page["anchor_index"]), ([], 0, None)
        )

    def test_classification_export_subset(self):
        classification = datasets.create(
            schemas.DatasetCreate(
                name="分类", task_type="classification", classes=self.classes
            )
        )
        for index in (0, 2):
            ann = annotations.save_sample(classification["id"], self.image, {})
            ann["classification"] = {
                "class_id": self.classes[index]["id"],
                "class_name": self.classes[index]["name"],
            }
            annotations.persist(classification["id"], ann)
        options = schemas.ExportOptions(
            split_mode="none", include_pending=True, class_ids=[12]
        )
        result = exports.export(classification["id"], options, self.progress)
        self.assertEqual(result["total"], 1)
        self.assertEqual(exports.preview(classification["id"], options)["total"], 1)
        self.assertEqual(
            len(list((Path(result["path"]) / "images" / "0_car").glob("*.jpg"))), 1
        )


if __name__ == "__main__":
    unittest.main()
