"""删除录像只清理本次入库副本，并与录制、后台任务和列表刷新互斥。"""

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.security_annotation import router
from app.core.storage import write_json
from app.services.security_annotation import recorder, store


class RecordingDeletionTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.enterContext(patch.object(store, "ROOT", self.root / "data"))
        self.enterContext(patch.object(recorder, "sessions", {}))
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def create(self, state="ready", **values):
        meta = store.create(
            "安检录像", source_type="file", state=state,
            original_file="original.mp4", **values,
        )
        (store.folder(meta["id"]) / "original.mp4").write_bytes(b"copied video")
        return meta["id"]

    def delete(self, recording_id):
        return self.client.delete(f"/api/security-annotations/recordings/{recording_id}")

    def test_delete_cleans_video_annotations_and_drafts_but_preserves_other_sources(self):
        recording_id = self.create()
        other_id = self.create()
        external_source = self.root / "source.mp4"
        external_source.write_bytes(b"external original")
        folder = store.folder(recording_id)
        (folder / "preview.mp4").write_bytes(b"preview")
        for name in ("drafts", "revisions", "frames"):
            (folder / name).mkdir()
            write_json(folder / name / "entry.json", {"value": name})
        # 目录中的符号链接不能导致服务器其他文件被一并删除。
        (folder / "external").symlink_to(external_source)

        result = self.delete(recording_id)

        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {"deleted": recording_id})
        self.assertFalse(folder.exists())
        self.assertEqual(external_source.read_bytes(), b"external original")
        self.assertEqual((store.folder(other_id) / "original.mp4").read_bytes(), b"copied video")
        self.assertEqual([item["id"] for item in store.listing()], [other_id])
        self.assertEqual(self.client.get(f"/api/security-annotations/recordings/{recording_id}").status_code, 404)
        self.assertEqual(self.delete(recording_id).status_code, 404)

    def test_failed_recording_can_be_deleted_without_a_video(self):
        recording_id = self.create("failed")
        folder = store.folder(recording_id)
        (folder / "original.mp4").unlink()
        self.assertEqual(self.delete(recording_id).status_code, 200)
        self.assertEqual(store.listing(), [])

    def test_recording_preparation_and_active_tasks_block_deletion(self):
        for state, task_id in (("recording", ""), ("preparing", ""), ("ready", "task"), ("failed", "task")):
            with self.subTest(state=state, task=task_id):
                recording_id = self.create(state, active_task_id=task_id)
                result = self.delete(recording_id)
                self.assertEqual(result.status_code, 409)
                self.assertEqual((store.folder(recording_id) / "original.mp4").read_bytes(), b"copied video")

    def test_ffmpeg_watcher_must_finish_before_failed_recording_can_be_deleted(self):
        recording_id = self.create("failed")
        recorder.sessions[recording_id] = {"stopping": True}
        self.assertEqual(self.delete(recording_id).status_code, 409)
        recorder.sessions.pop(recording_id)
        self.assertEqual(self.delete(recording_id).status_code, 200)

    def test_invalid_ids_and_symlinked_recording_directories_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        recording_id = self.create()
        linked_id = "a" * 32
        (store.ROOT / "recordings" / linked_id).symlink_to(outside, target_is_directory=True)
        for invalid in ("not-an-id", linked_id, "b" * 32):
            self.assertEqual(self.delete(invalid).status_code, 404)
        with self.assertRaises(HTTPException) as failure:
            recorder.delete("../outside")
        self.assertEqual(failure.exception.status_code, 404)
        self.assertEqual((outside / "keep.txt").read_text(), "keep")
        self.assertTrue(store.folder(recording_id).is_dir())

    def test_list_skips_recording_deleted_while_waiting_for_its_lock(self):
        recording_id = self.create()
        original_lock = store.lock
        waiting = True

        @contextmanager
        def delete_before_acquiring_lock(value):
            nonlocal waiting
            if waiting:
                waiting = False
                recorder.delete(value)
            with original_lock(value):
                yield

        with patch.object(store, "lock", delete_before_acquiring_lock):
            self.assertEqual(store.listing(), [])
        self.assertEqual(self.delete(recording_id).status_code, 404)


if __name__ == "__main__":
    unittest.main()
