import errno
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.schemas.security_annotation import RecordingStart
from app.services.security_annotation import recorder, store, workflow


class TimeoutArgumentsTests(unittest.TestCase):
    def setUp(self):
        recorder.rtsp_timeout_args.cache_clear()
        self.addCleanup(recorder.rtsp_timeout_args.cache_clear)

    def options(self, help_text):
        result = subprocess.CompletedProcess([], 0, stdout=help_text, stderr="")
        with patch.object(recorder.subprocess, "run", return_value=result):
            return recorder.rtsp_timeout_args("/example/ffmpeg")

    def test_legacy_ffmpeg_uses_socket_timeout_instead_of_listen_timeout(self):
        self.assertEqual(
            self.options(
                "  -timeout <int> wait for incoming connections, imply flag listen\n"
                "  -stimeout <int> set timeout (in microseconds) of socket TCP I/O operations\n"
            ),
            ("-stimeout", "5000000"),
        )

    def test_modern_ffmpeg_uses_advertised_socket_timeout(self):
        self.assertEqual(
            self.options(
                "  -listen_timeout <int> wait for incoming connections\n"
                "  -timeout <int64> set timeout (in microseconds) of socket I/O operations\n"
            ),
            ("-timeout", "5000000"),
        )

    def test_never_uses_timeout_when_its_only_advertised_meaning_is_listen(self):
        self.assertEqual(
            self.options(
                "  -timeout <int> wait for incoming connections, imply flag listen\n"
            ),
            ("-rw_timeout", "5000000"),
        )

    def test_capability_probe_failure_is_reported_without_starting_a_capture(self):
        with patch.object(
            recorder.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("ffmpeg", 10),
        ):
            with self.assertRaises(HTTPException) as failure:
                recorder.rtsp_timeout_args("/example/ffmpeg")
        self.assertIn("FFmpeg", failure.exception.detail)
        self.assertEqual(failure.exception.status_code, 503)


class RecordingFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dcw-recorder-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(store, "ROOT", self.root / "data"))
        self.enterContext(patch.object(recorder, "sessions", {}))
        self.enterContext(patch.object(
            recorder.media, "executable", side_effect=lambda name: "/example/" + name
        ))
        self.enterContext(patch.object(
            recorder, "rtsp_timeout_args", return_value=("-stimeout", "5000000")
        ))
        self.source = "rtsp://operator:p%40ssword@camera.local/stream?token=private"
        self.enterContext(patch.object(recorder.camera_service, "get", return_value={
            "name": "camera", "transport": "tcp", "url": self.source,
        }))

    def fail_capture(self, errors=b"", output=b"", code=1):
        released = threading.Event()
        process = Mock()
        process.stderr = io.BytesIO(errors)
        process.wait.side_effect = lambda **_kwargs: (released.wait(5), code)[1]

        def spawn(command, **kwargs):
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            if output:
                Path(command[-1]).write_bytes(output)
            return process

        with patch.object(recorder.subprocess, "Popen", side_effect=spawn):
            result = recorder.start(RecordingStart(camera_id="camera"))
            thread = recorder.sessions[result["recording_id"]]["thread"]
            released.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        return store.get(result["recording_id"])

    def test_actual_ffmpeg_failure_is_visible_and_camera_credentials_are_redacted(self):
        meta = self.fail_capture(f"Cannot open {self.source}: 401 Unauthorized\n".encode())
        self.assertEqual(meta["state"], "failed")
        self.assertIn("401 Unauthorized", meta["error"])
        self.assertIn("退出码 1", meta["error"])
        saved = (store.folder(meta["id"]) / "ffmpeg.log").read_text()
        for text in [json.dumps(meta), saved]:
            for secret in [self.source, "operator", "p%40ssword", "token=private"]:
                self.assertNotIn(secret, text)

    def test_disk_write_failure_is_reported_before_starting_ffmpeg(self):
        for error, reason in [
            (errno.EACCES, "没有写入权限"),
            (errno.EROFS, "只读"),
            (errno.ENOSPC, "空间不足"),
            (errno.EDQUOT, "配额不足"),
        ]:
            with (
                self.subTest(error=error),
                patch.object(
                    recorder.tempfile,
                    "TemporaryFile",
                    side_effect=OSError(error, "write failed"),
                ),
                patch.object(recorder.subprocess, "Popen") as spawn,
            ):
                with self.assertRaises(HTTPException) as failure:
                    recorder.start(RecordingStart(camera_id="camera"))
                self.assertIn(reason, failure.exception.detail)
                self.assertIn(str(self.root), failure.exception.detail)
                spawn.assert_not_called()
        for meta in store.listing():
            self.assertEqual(meta["state"], "failed")
        self.assertFalse(recorder.sessions)

    def test_mid_recording_disk_failure_keeps_original_and_reports_storage_error(self):
        with patch.object(workflow, "submit") as prepare:
            meta = self.fail_capture(b"Read-only file system\n", b"partial-original")
            prepare.assert_not_called()
        self.assertEqual(meta["state"], "failed")
        self.assertIn("只读", meta["error"])
        self.assertEqual((store.folder(meta["id"]) / "original.mkv").read_bytes(), b"partial-original")

    def test_diagnostics_keep_a_bounded_tail_during_long_recordings(self):
        errors = "".join(f"error-{index:03d} {self.source}\n" for index in range(100)).encode()
        meta = self.fail_capture(errors)
        saved = (store.folder(meta["id"]) / "ffmpeg.log").read_text()
        self.assertEqual(len(saved.splitlines()), 32)
        self.assertNotIn("error-000 ", saved)
        self.assertIn("error-099 ", saved)
        self.assertNotIn(self.source, saved)

    def test_failure_without_stderr_still_reports_the_process_exit_status(self):
        meta = self.fail_capture(code=7)
        self.assertEqual(meta["state"], "failed")
        self.assertIn("退出码 7", meta["error"])


if __name__ == "__main__":
    unittest.main()
