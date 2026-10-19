"""Real task scheduling and temporary datasets; no RTSP devices or model weights."""

import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch


class MultiCameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        root = Path(
            cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="dcw-multi-"))
        )
        cls.stack.enter_context(
            patch.dict(
                os.environ,
                {"DATA_ROOT": str(root / "data"), "STATE_ROOT": str(root / "state")},
            )
        )
        import numpy as np
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from fastapi import HTTPException
        from app.db import database
        from app.services import (
            collection_service,
            annotation_service,
            dataset_service,
            task_service,
            camera_service,
        )
        from app.schemas.contracts import DatasetCreate, CollectionStart

        cls.np, cls.HTTPException = np, HTTPException
        cls.collection, cls.annotations, cls.datasets, cls.tasks, cls.cameras = (
            collection_service,
            annotation_service,
            dataset_service,
            task_service,
            camera_service,
        )
        cls.DatasetCreate, cls.CollectionStart = DatasetCreate, CollectionStart
        engine = create_engine(
            f"sqlite:///{root / 'test.sqlite3'}",
            connect_args={"check_same_thread": False},
        )
        cls.stack.callback(engine.dispose)
        database.Base.metadata.create_all(engine)
        session = sessionmaker(engine, expire_on_commit=False)
        for module in (
            database,
            collection_service,
            annotation_service,
            dataset_service,
            task_service,
        ):
            cls.stack.enter_context(patch.object(module, "Session", session))
        (root / "data").mkdir(exist_ok=True)
        cls.stack.enter_context(
            patch.object(dataset_service, "DATA_ROOT", root / "data")
        )
        pool = cls.stack.enter_context(ThreadPoolExecutor(max_workers=9))
        cls.stack.enter_context(patch.object(task_service, "collection_pool", pool))

    def setUp(self):
        self.enterContext(patch.object(self.collection, "sessions", {}))
        self.enterContext(patch.object(self.cameras, "readers", {}))
        self.dataset = self.datasets.create(
            self.DatasetCreate(name="九路", task_type="raw")
        )
        self.captures = {}
        self.enterContext(
            patch.object(self.cameras, "reader", side_effect=self.capture)
        )
        self.addCleanup(self.stop_all)

    def capture(self, camera_id):
        if camera_id not in self.captures:
            capture = Mock(camera={"id": camera_id, "name": camera_id}, pinned=False)
            capture.thread.is_alive.return_value = True
            capture.status.return_value = {
                "connected": True,
                "fps": 25,
                "width": 48,
                "height": 32,
                "error": "",
            }
            capture.snapshot.return_value = (
                1,
                self.np.zeros((32, 48, 3), dtype=self.np.uint8),
            )
            self.captures[camera_id] = capture
            self.cameras.readers[camera_id] = capture
        return self.captures[camera_id]

    def options(self, cid, **kwargs):
        return self.CollectionStart(
            dataset_id=self.dataset["id"], camera_id=cid, **kwargs
        )

    def wait_for(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for collection state")

    def stop_all(self):
        self.collection.stop()
        self.wait_for(lambda: not self.collection.status()["active"])

    def test_nine_cameras_save_same_dataset_and_stop_independently(self):
        ids = [f"camera-{i}" for i in range(9)]
        for cid in ids:
            self.collection.start(self.options(cid))
        self.wait_for(lambda: self.collection.status()["saved"] == 9)
        state = self.collection.status()
        self.assertEqual(state["active_count"], 9)
        self.assertTrue(all(s["saved"] == 1 for s in state["sessions"]))
        samples = self.annotations.list_samples(self.dataset["id"])["items"]
        self.assertEqual(
            {
                self.annotations.get(self.dataset["id"], s["id"])["source"]["camera_id"]
                for s in samples
            },
            set(ids),
        )
        for cid in (ids[0], "tenth"):
            with self.assertRaises(self.HTTPException) as failure:
                self.collection.start(self.options(cid))
            self.assertEqual(failure.exception.status_code, 409)
        with self.assertRaises(self.HTTPException):
            self.tasks.submit("export", self.dataset["id"], lambda p: {})
        # Long-running collection workers must not starve ordinary jobs.
        job = self.tasks.submit("camera_test", None, lambda p: {"ok": True})
        self.wait_for(lambda: self.tasks.get(job["task_id"])["status"] == "completed")
        self.collection.stop(camera_id=ids[0])
        self.wait_for(lambda: not self.collection.status(ids[0])["active"])
        self.assertFalse(self.captures[ids[0]].pinned)
        self.assertEqual(self.collection.status()["active_count"], 8)
        self.assertTrue(all(self.captures[cid].pinned for cid in ids[1:]))
        self.collection.start(self.options("replacement"))
        self.wait_for(lambda: self.collection.status("replacement")["saved"] == 1)
        self.assertEqual(self.collection.status()["active_count"], 9)

    def test_simultaneous_starts_cannot_exceed_limit(self):
        def start(i):
            try:
                self.collection.start(self.options(f"race-{i}"))
                return True
            except self.HTTPException as exc:
                self.assertEqual(exc.status_code, 409)
                return False

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(start, range(10)))
        self.assertEqual(sum(results), 9)
        self.assertEqual(self.collection.status()["active_count"], 9)

    def test_dataset_scoped_stop_and_preview_isolation(self):
        first = self.collection.new_state(self.options("a"))
        second = self.collection.new_state(self.options("b"))
        first.update(task_id="one", preview=b"one", preview_at=time.monotonic())
        second.update(
            task_id="two",
            dataset_id="other",
            preview=b"two",
            preview_at=time.monotonic(),
        )
        self.collection.sessions.update(a=first, b=second)
        with patch.object(self.tasks, "cancel") as cancel:
            self.collection.stop(dataset_id=self.dataset["id"])
            cancel.assert_called_once_with("one")
        self.assertEqual(self.collection.preview_image("a"), b"one")
        self.assertEqual(self.collection.preview_image("b"), b"two")
        with self.assertRaises(self.HTTPException):
            self.collection.preview_image()
        first["active"] = second["active"] = False
        with self.assertRaises(self.HTTPException):
            self.collection.preview_image("a")

    def test_failed_start_restores_slot_and_unpins(self):
        with patch.object(
            self.tasks, "submit", side_effect=RuntimeError("submit failed")
        ):
            with self.assertRaises(RuntimeError):
                self.collection.start(self.options("broken"))
        self.assertFalse(self.capture("broken").pinned)
        self.assertEqual(self.collection.status()["active_count"], 0)
        self.assertNotIn("broken", self.collection.sessions)

    def test_failure_in_one_camera_does_not_stop_others(self):
        self.capture("broken").snapshot.side_effect = RuntimeError("camera failed")
        result = self.collection.start(self.options("broken"))
        self.collection.start(self.options("healthy"))
        self.wait_for(lambda: not self.collection.status("broken")["active"])
        self.wait_for(lambda: self.collection.status("healthy")["saved"] == 1)
        self.assertEqual(self.tasks.get(result["task_id"])["status"], "failed")
        self.assertIn("camera failed", self.collection.status("broken")["error"])
        self.assertTrue(self.collection.status("healthy")["active"])

    def test_camera_specific_roi_and_original_pixels_are_preserved(self):
        self.collection.start(self.options("full"))
        self.collection.start(
            self.options("roi", mode="roi_interval", roi=(3, 4, 23, 19))
        )
        self.wait_for(lambda: self.collection.status()["saved"] == 2)
        samples = self.annotations.list_samples(self.dataset["id"])["items"]
        sizes = {
            self.annotations.get(self.dataset["id"], s["id"])["source"]["camera_id"]: (
                s["width"],
                s["height"],
            )
            for s in samples
        }
        self.assertEqual(sizes, {"full": (48, 32), "roi": (20, 15)})

    def test_existing_non_collection_task_blocks_start(self):
        event = threading.Event()

        def work(p):
            while not event.wait(0.01):
                p.check()

        task = self.tasks.submit("export", self.dataset["id"], work)
        try:
            with self.assertRaises(self.HTTPException):
                self.collection.start(self.options("blocked"))
            self.assertFalse(self.capture("blocked").pinned)
        finally:
            event.set()
            self.wait_for(
                lambda: self.tasks.get(task["task_id"])["status"] == "completed"
            )

    def test_late_cleanup_cannot_clear_new_session(self):
        capture = self.capture("a")
        old = self.collection.new_state(self.options("a"))
        new = self.collection.new_state(self.options("a"))
        self.collection.sessions["a"] = new
        capture.pinned = True
        self.collection.finish(capture, old)
        self.assertTrue(new["active"])
        self.assertTrue(capture.pinned)
        self.collection.finish(capture, new)

    def test_status_change_does_not_decode_or_rewrite_preview(self):
        ann = self.annotations.save_sample(
            self.dataset["id"],
            self.np.zeros((32, 48, 3), dtype=self.np.uint8),
            {"type": "image"},
        )
        path = (
            self.datasets.folder(self.dataset["id"]) / "previews" / f"{ann['id']}.jpg"
        )
        before = path.stat().st_mtime_ns
        with patch.object(
            self.annotations,
            "read_image",
            side_effect=AssertionError("unnecessary image read"),
        ):
            result = self.annotations.set_status(
                self.dataset["id"], ann["id"], "approved"
            )
        self.assertEqual(result["status"], "approved")
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(result["revision"], ann["revision"] + 1)

    def test_image_http_cache_returns_304_and_preview_invalidates_after_edit(self):
        from starlette.requests import Request
        from app.api.annotations import media
        from app.schemas.contracts import AnnotationEdit

        ann = self.annotations.save_sample(
            self.dataset["id"],
            self.np.zeros((32, 48, 3), dtype=self.np.uint8),
            {"type": "image"},
        )

        def request(**headers):
            return Request(
                {
                    "type": "http",
                    "headers": [
                        (k.replace("_", "-").encode(), v.encode())
                        for k, v in headers.items()
                    ],
                }
            )

        response = media(request(), self.dataset["id"], ann["id"], "image")
        self.assertIn("immutable", response.headers["cache-control"])
        cached = media(
            request(if_none_match=response.headers["etag"]),
            self.dataset["id"],
            ann["id"],
            "image",
        )
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.body, b"")
        preview = media(request(), self.dataset["id"], ann["id"], "preview")
        self.annotations.edit(
            self.dataset["id"], ann["id"], AnnotationEdit(revision=0, objects=[])
        )
        changed = media(
            request(if_none_match=preview.headers["etag"]),
            self.dataset["id"],
            ann["id"],
            "preview",
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.headers["cache-control"], "no-cache")
        cached = media(
            request(if_modified_since=changed.headers["last-modified"]),
            self.dataset["id"],
            ann["id"],
            "preview",
        )
        self.assertEqual(cached.status_code, 304)

    def test_preview_is_resized_but_capture_frame_is_not_modified(self):
        frame = self.np.zeros((1080, 1920, 3), dtype=self.np.uint8)
        jpeg = self.cameras.encode_preview(frame)
        import cv2

        image = cv2.imdecode(
            self.np.frombuffer(jpeg, dtype=self.np.uint8), cv2.IMREAD_COLOR
        )
        self.assertEqual(image.shape, (540, 960, 3))
        self.assertEqual(frame.shape, (1080, 1920, 3))


if __name__ == "__main__":
    unittest.main()
