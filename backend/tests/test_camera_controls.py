"""Synthetic camera frames, real sample persistence, and mocked ONVIF transport."""

import copy
import os
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET


class CameraControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="dcw-zoom-")))
        cls.stack.enter_context(patch.dict(os.environ, {
            "DATA_ROOT": str(root / "data"), "STATE_ROOT": str(root / "state"),
        }))
        import cv2
        import numpy as np
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import database
        from app.schemas import contracts
        from app.services import camera_service, collection_service, annotation_service, dataset_service
        from app.services import task_service, lens_service, camera_view

        cls.cv2, cls.np = cv2, np
        cls.database, cls.contracts = database, contracts
        cls.cameras, cls.collection, cls.annotations, cls.datasets = camera_service, collection_service, annotation_service, dataset_service
        cls.tasks, cls.lens, cls.camera_view = task_service, lens_service, camera_view
        engine = create_engine(f"sqlite:///{root / 'test.sqlite3'}")
        cls.stack.callback(engine.dispose)
        database.Base.metadata.create_all(engine)
        session = sessionmaker(engine, expire_on_commit=False)
        for module in (database, camera_service, collection_service, annotation_service, dataset_service, task_service):
            cls.stack.enter_context(patch.object(module, "Session", session))
        (root / "data").mkdir(exist_ok=True)
        cls.stack.enter_context(patch.object(dataset_service, "DATA_ROOT", root / "data"))

    def setUp(self):
        self.enterContext(patch.object(self.cameras, "readers", {}))
        self.enterContext(patch.object(self.lens, "clients", {}))
        self.image = self.np.zeros((80, 100, 3), dtype=self.np.uint8)
        self.image[:40, 50:] = (30, 100, 230)
        self.view = {"zoom": 2.0, "center_x": .75, "center_y": .25}

    def test_view_crop_clamps_at_edges_and_preserves_output_size(self):
        viewed, area = self.camera_view.apply_view(self.image, self.view)
        self.assertEqual(area, [50, 0, 100, 40])
        self.assertEqual(viewed.shape, self.image.shape)
        self.assertTrue(self.np.all(viewed == (30, 100, 230)))
        self.assertEqual(self.camera_view.crop_bounds(101, 79, {"zoom": 2, "center_x": 1, "center_y": 0}), [50, 0, 101, 40])
        original, bounds = self.camera_view.apply_view(self.image, self.camera_view.DEFAULT_VIEW)
        self.assertIs(original, self.image)
        self.assertEqual(bounds, [0, 0, 100, 80])

    def test_invalid_zoom_and_lens_speed_are_rejected(self):
        from pydantic import ValidationError
        for value in (0, 8.1, float("nan"), float("inf")):
            with self.assertRaises(ValidationError):
                self.contracts.CameraView(zoom=value)
        for value in (-1.01, 1.01, float("nan")):
            with self.assertRaises(ValidationError):
                self.contracts.LensMove(speed=value)

    def test_existing_cameras_get_defaults_and_view_persists_without_releasing_active_reader(self):
        with self.database.Session() as db:
            db.add(self.database.Camera(id="legacy", name="Legacy", url="rtsp://camera/stream", transport="tcp"))
            db.commit()
        self.assertEqual(self.cameras.get("legacy")["view"], self.camera_view.DEFAULT_VIEW)
        capture = self.cameras.CameraReader.__new__(self.cameras.CameraReader)
        capture.camera = self.cameras.get("legacy")
        capture.lock = threading.RLock()
        capture.pinned = True
        capture.frame = self.image.copy()
        capture.frame_id, capture.jpeg_id = 1, -1
        capture.last_frame = time.monotonic()
        capture.error = ""
        self.cameras.readers["legacy"] = capture
        original, meta = capture.image(with_view=True)
        self.assertEqual(meta, self.camera_view.DEFAULT_VIEW)
        self.cameras.set_view("legacy", self.contracts.CameraView(**self.view))
        zoomed, meta = capture.image(with_view=True)
        self.assertNotEqual(original, zoomed)
        self.assertEqual(meta, self.view)
        self.assertEqual(capture.image(with_view=True), (zoomed, self.view))
        raw, raw_view = capture.image(with_view=True, original=True)
        self.assertEqual(raw, original)
        self.assertEqual(raw_view, self.camera_view.DEFAULT_VIEW)
        _, snapshot, source = self.cameras.collection_snapshot(capture)
        self.assertTrue(self.np.all(snapshot == (30, 100, 230)))
        self.assertEqual(source["crop"], [50, 0, 100, 40])
        self.assertTrue(capture.pinned)
        self.assertEqual(self.cameras.get("legacy")["view"], self.view)
        # Recreated readers obtain the persisted view via get().
        self.assertEqual(self.cameras.get("legacy")["view"], meta)

    def test_camera_api_validates_settings_and_attaches_matching_frame_view(self):
        import json
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import cameras as camera_api, collection as collection_api

        app = FastAPI()
        app.include_router(camera_api.router)
        app.include_router(collection_api.router)
        with TestClient(app) as client:
            response = client.post("/api/cameras", json={"name": "API Zoom", "url": "rtsp://camera/live"})
            self.assertEqual(response.status_code, 200)
            camera = response.json()
            cid = camera["id"]
            base = f"/api/cameras/{cid}"
            capture = self.cameras.CameraReader.__new__(self.cameras.CameraReader)
            capture.camera, capture.lock = camera, threading.RLock()
            capture.thread = Mock()
            capture.thread.is_alive.return_value = True
            capture.frame = self.image.copy()
            capture.frame_id, capture.jpeg_id = 1, -1
            capture.last_frame, capture.error = time.monotonic(), ""
            self.cameras.readers[cid] = capture
            self.assertEqual(client.put(base + "/view", json={"zoom": 9}).status_code, 422)
            self.assertEqual(client.put(base + "/view", json=self.view).json(), self.view)
            self.assertEqual(client.get(base + "/view").json(), self.view)
            frame = client.get(base + "/frame")
            self.assertEqual(frame.status_code, 200)
            self.assertEqual(json.loads(frame.headers["X-Camera-View"]), self.view)
            original = client.get(base + "/frame?original=true")
            self.assertEqual(json.loads(original.headers["X-Camera-View"]), self.camera_view.DEFAULT_VIEW)
            self.assertNotEqual(original.content, frame.content)
            with patch.object(self.lens, "move", return_value={"ok": True}) as move:
                self.assertEqual(client.post(base + "/lens", json={"speed": 1.1}).status_code, 422)
                self.assertEqual(client.post(base + "/lens", json={"speed": 0}).status_code, 200)
                move.assert_called_once_with(cid, 0)
            options = self.contracts.CollectionStart(dataset_id="unused", camera_id=cid)
            current = self.collection.new_state(options)
            current.update(preview=frame.content, preview_at=time.monotonic(), preview_view=self.view)
            with patch.object(self.collection, "sessions", {cid: current}):
                reset = self.contracts.CameraView()
                self.cameras.set_view(cid, reset)
                refreshed = client.get(f"/api/collection/frame?camera_id={cid}")
                self.assertEqual(refreshed.content, original.content)
                self.assertEqual(json.loads(refreshed.headers["X-Camera-View"]), reset.model_dump())

    def test_onvif_credentials_support_rtsp_reuse_and_separate_overrides(self):
        item = self.lens.LensClient({"url": "rtsp://u%40ser:p%26ss@camera/live"})
        self.assertEqual((item.username, item.password), ("u@ser", "p&ss"))
        item = self.lens.LensClient({"url": "rtsp://user:old@camera/live", "onvif_password": "new"})
        self.assertEqual((item.username, item.password), ("user", "new"))
        item = self.lens.LensClient({"url": "rtsp://user:old@camera/live", "onvif_username": "operator"})
        self.assertEqual((item.username, item.password), ("operator", ""))

    def test_camera_edit_roundtrips_onvif_settings_and_preserves_view(self):
        body = self.contracts.CameraEdit(name="Zoom", url="rtsp://user:secret@host/video",
                                         onvif_url="http://host:8080/onvif/device_service",
                                         onvif_username="operator", onvif_password="private", onvif_profile="main")
        camera = self.cameras.create(body)
        cid = camera["id"]
        self.cameras.set_view(cid, self.contracts.CameraView(**self.view))
        # Legacy API edits must not wipe optional lens configuration.
        camera = self.cameras.update(cid, self.contracts.CameraEdit(name="Renamed", url=body.url))
        self.assertEqual(camera["onvif_url"], body.onvif_url)
        self.assertEqual(camera["onvif_profile"], "main")
        self.assertEqual(camera["view"], self.view)

    def test_all_collection_modes_infer_and_save_zoomed_pixels_with_correct_annotations(self):
        face = {"id": "face", "class_id": 0, "class_name": "face", "bbox": [4, 6, 30, 24],
                "source": "scrfd", "confidence": .9, "keypoints": [[10, 10], None, None, None, None]}
        for mode in ("raw_interval", "roi_interval", "detect_raw", "detect_crop"):
            with self.subTest(mode=mode):
                dataset = self.datasets.create(self.contracts.DatasetCreate(name=mode, task_type="detection", annotation_mode="face_5pt",
                    classes=[{"id": 0, "name": "face"}]))
                capture = Mock(camera={"id": "camera", "name": "test", "view": self.view})
                capture.snapshot.side_effect = [(1, self.image.copy()), self.tasks.Cancelled()]
                options = self.contracts.CollectionStart(dataset_id=dataset["id"], camera_id="camera", mode=mode,
                    roi=[0, 0, 24, 16] if mode == "roi_interval" else None, detector="scrfd", auto_label="scrfd")
                with (
                    patch.object(self.collection.manager, "get"),
                    patch.object(self.collection.manager, "predict", return_value=[copy.deepcopy(face)]) as predict,
                    patch.object(self.collection.time, "monotonic", side_effect=count(100)),
                    patch.object(self.collection.time, "sleep"),
                    patch.object(self.annotations, "save_sample", wraps=self.annotations.save_sample) as save,
                ):
                    with self.assertRaises(self.tasks.Cancelled):
                        self.collection.collect(options, capture, dataset, Mock())
                self.assertTrue(self.np.all(predict.call_args.args[3] == (30, 100, 230)))
                self.assertTrue(self.np.all(save.call_args.args[1] == (30, 100, 230)))
                sample = self.annotations.list_samples(dataset["id"])["items"][0]
                ann = self.annotations.get(dataset["id"], sample["id"])
                self.assertEqual(ann["source"]["view"], {**self.view, "crop": [50, 0, 100, 40], "original_size": [100, 80]})
                expected = {"raw_interval": (100, 80, [10, 10]), "detect_raw": (100, 80, [10, 10]),
                            "roi_interval": (24, 16, [10, 10]), "detect_crop": (26, 18, [6, 4])}[mode]
                self.assertEqual((ann["width"], ann["height"]), expected[:2])
                self.assertEqual(ann["objects"][0]["keypoints"][0], expected[2])

    def lens_client(self, profiles=None):
        lens = self.lens
        item = lens.LensClient({"url": "rtsp://user:p%26ss@camera/live", "onvif_profile": ""})
        profile_xml = profiles or '<trt:Profiles token="main&amp;1"><tt:PTZConfiguration token="ptz"/></trt:Profiles>'
        replies = [
            '<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>http://camera/media</tt:XAddr></tt:Media>'
            '<tt:PTZ><tt:XAddr>http://camera/ptz</tt:XAddr></tt:PTZ></tds:Capabilities></tds:GetCapabilitiesResponse>',
            '<trt:GetProfilesResponse>' + profile_xml + '</trt:GetProfilesResponse>',
            '<tptz:GetConfigurationOptionsResponse><tptz:PTZConfigurationOptions><tt:Spaces><tt:ContinuousZoomVelocitySpace>'
            '<tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace</tt:URI>'
            '<tt:XRange><tt:Min>-0.5</tt:Min><tt:Max>1</tt:Max></tt:XRange>'
            '</tt:ContinuousZoomVelocitySpace></tt:Spaces></tptz:PTZConfigurationOptions></tptz:GetConfigurationOptionsResponse>',
        ]
        calls = []
        def response(request, timeout):
            calls.append((request, timeout))
            payload = replies.pop(0) if replies else '<tptz:Response/>'
            namespaces = " ".join(f'xmlns:{key}="{value}"' for key, value in lens.NS.items())
            data = f'<s:Envelope {namespaces}><s:Body>{payload}</s:Body></s:Envelope>'.encode()
            return MockResponse(data)
        item.opener.open = response
        return item, calls

    def test_onvif_commands_use_zoom_only_short_timeout_digest_and_explicit_stop(self):
        item, calls = self.lens_client()
        item.move(0)
        self.assertFalse(calls)
        item.move(-.6)
        item.move(0)
        self.assertEqual(len(calls), 5)
        move = ET.fromstring(calls[-2][0].data)
        ns = self.lens.NS
        self.assertEqual(move.findtext(".//tptz:Timeout", namespaces=ns), "PT1S")
        self.assertEqual(move.find(".//tt:Zoom", ns).get("x"), "-0.3")
        self.assertEqual(move.findtext(".//tptz:ProfileToken", namespaces=ns), "main&1")
        self.assertIsNotNone(move.find(f'.//{{{self.lens.WSSE}}}Password'))
        self.assertNotIn(b"p&ss", calls[-2][0].data)
        stop = ET.fromstring(calls[-1][0].data)
        self.assertEqual(stop.findtext(".//tptz:PanTilt", namespaces=ns), "false")
        self.assertEqual(stop.findtext(".//tptz:Zoom", namespaces=ns), "true")
        self.assertTrue(all(timeout == 3 for _, timeout in calls))

    def test_unsupported_zoom_does_not_send_a_movement(self):
        from fastapi import HTTPException
        item, calls = self.lens_client()
        original = item.opener.open
        def unsupported(request, timeout):
            result = original(request, timeout)
            if b"GetConfigurationOptions" in request.data:
                result.data = b'<Response/>'
            return result
        item.opener.open = unsupported
        with self.assertRaises(HTTPException) as error:
            item.move(.5)
        self.assertIn("持续变焦", error.exception.detail)
        self.assertEqual(len(calls), 3)


class MockResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.data[:limit]


if __name__ == "__main__":
    unittest.main()
