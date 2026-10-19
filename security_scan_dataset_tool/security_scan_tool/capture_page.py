from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .capture import AsyncVideoRecorder, LatestFrameReader
from .video_widget import VideoCanvas


def safe_name(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", text.strip())
    return cleaned.strip("_")[:80] or fallback


class CapturePage(QWidget):
    datasetChanged = Signal(str)

    def __init__(self, app_dir: Path) -> None:
        super().__init__()
        self.app_dir = app_dir
        self.config_path = app_dir / "config.json"
        self.config = self._load_config()
        self.reader = LatestFrameReader()
        self.recorder = AsyncVideoRecorder()
        self.current_frame: np.ndarray | None = None
        self.last_frame_id = 0
        self.record_roi: tuple[int, int, int, int] | None = None
        self.record_crop = True
        self.record_started_at: datetime | None = None
        self.record_started_mono = 0.0
        self.record_next_frame_mono = 0.0
        self.record_path: Path | None = None
        self.record_meta: dict[str, Any] = {}
        self._pending_normalized_roi = self.config.get("roi_normalized")
        self._last_reader_status = ""

        self._build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._poll_frame)
        self.timer.start()

    @property
    def dataset_dir(self) -> Path:
        return Path(self.output_edit.text()).expanduser().resolve()

    def _load_config(self) -> dict[str, Any]:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_config(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        controls = QVBoxLayout()
        controls.setSpacing(12)
        source_box = QGroupBox("视频源")
        source_form = QFormLayout(source_box)
        self.url_edit = QLineEdit(self.config.get("last_rtsp", ""))
        self.url_edit.setPlaceholderText("rtsp://用户名:密码@摄像头IP:554/stream")
        self.url_edit.setClearButtonEnabled(True)
        source_form.addRow("RTSP 地址", self.url_edit)
        connect_row = QHBoxLayout()
        self.connect_btn = QPushButton("连接预览")
        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.setEnabled(False)
        connect_row.addWidget(self.connect_btn)
        connect_row.addWidget(self.disconnect_btn)
        source_form.addRow(connect_row)
        self.connection_label = QLabel("● 未连接")
        source_form.addRow("状态", self.connection_label)
        controls.addWidget(source_box)

        roi_box = QGroupBox("工位 ROI")
        roi_layout = QVBoxLayout(roi_box)
        roi_layout.addWidget(
            QLabel("在右侧画面中按住鼠标左键拖动即可绘制；再次拖动可重画。")
        )
        roi_row = QHBoxLayout()
        self.save_roi_btn = QPushButton("保存 ROI")
        self.clear_roi_btn = QPushButton("清除 / 重画")
        roi_row.addWidget(self.save_roi_btn)
        roi_row.addWidget(self.clear_roi_btn)
        roi_layout.addLayout(roi_row)
        self.roi_label = QLabel("尚未绘制")
        roi_layout.addWidget(self.roi_label)
        controls.addWidget(roi_box)

        record_box = QGroupBox("样本录制")
        record_form = QFormLayout(record_box)
        self.sample_edit = QLineEdit(
            datetime.now().astimezone().strftime("S%Y%m%d_%H%M%S")
        )
        self.name_edit = QLineEdit("正常扫描")
        self.output_edit = QLineEdit(
            self.config.get("dataset_dir", str(self.app_dir / "data"))
        )
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        self.output_btn = QPushButton("选择…")
        output_row.addWidget(self.output_btn)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 30)
        self.fps_spin.setValue(int(self.config.get("record_fps", 25)))
        self.crop_check = QCheckBox("只保存 ROI 内画面（推荐）")
        self.crop_check.setChecked(bool(self.config.get("crop_to_roi", True)))
        record_form.addRow("样本 ID", self.sample_edit)
        record_form.addRow("片段名称", self.name_edit)
        record_form.addRow("数据目录", output_row)
        record_form.addRow("录制帧率", self.fps_spin)
        record_form.addRow("保存方式", self.crop_check)
        record_buttons = QHBoxLayout()
        self.start_btn = QPushButton("● 开始录制")
        self.start_btn.setObjectName("dangerButton")
        self.stop_btn = QPushButton("■ 停止并保存")
        self.stop_btn.setEnabled(False)
        record_buttons.addWidget(self.start_btn)
        record_buttons.addWidget(self.stop_btn)
        record_form.addRow(record_buttons)
        self.record_label = QLabel("等待录制")
        record_form.addRow("录制状态", self.record_label)
        controls.addWidget(record_box)
        controls.addStretch(1)

        left = QWidget()
        left.setLayout(controls)
        left.setMaximumWidth(430)
        root.addWidget(left)

        preview_layout = QVBoxLayout()
        title = QLabel("实时预览")
        title.setObjectName("pageTitle")
        preview_layout.addWidget(title)
        self.canvas = VideoCanvas(allow_roi=True)
        preview_layout.addWidget(self.canvas, 1)
        self.frame_info = QLabel("分辨率：-    来源帧率：-")
        preview_layout.addWidget(self.frame_info)
        preview = QWidget()
        preview.setLayout(preview_layout)
        root.addWidget(preview, 1)

        self.connect_btn.clicked.connect(self.connect_stream)
        self.disconnect_btn.clicked.connect(self.disconnect_stream)
        self.save_roi_btn.clicked.connect(self.save_roi)
        self.clear_roi_btn.clicked.connect(self.canvas.clear_roi)
        self.canvas.roiChanged.connect(self._roi_changed)
        self.output_btn.clicked.connect(self.choose_output)
        self.output_edit.editingFinished.connect(self._dataset_edited)
        self.start_btn.clicked.connect(self.start_recording)
        self.stop_btn.clicked.connect(self.stop_recording)

    def connect_stream(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "缺少地址", "请输入 RTSP 地址。")
            return
        self.config["last_rtsp"] = url
        self._save_config()
        self.reader.start(url)
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.connection_label.setText("● 正在连接…")

    def disconnect_stream(self) -> None:
        if self.recorder.recording:
            self.stop_recording()
        self.reader.stop(wait=False)
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.connection_label.setText("● 已断开")

    def _poll_frame(self) -> None:
        item = self.reader.latest()
        if item is not None:
            frame_id, _, frame = item
            self.last_frame_id = frame_id
            self.current_frame = frame
            self.canvas.set_frame(frame)
            height, width = frame.shape[:2]
            self.frame_info.setText(
                f"分辨率：{width} × {height}    来源帧率：{self.reader.source_fps:.1f} FPS"
            )
            if self._pending_normalized_roi and not self.canvas.roi():
                try:
                    nx, ny, nw, nh = map(float, self._pending_normalized_roi)
                    self.canvas.set_roi(
                        (
                            round(nx * width),
                            round(ny * height),
                            round(nw * width),
                            round(nh * height),
                        )
                    )
                except (TypeError, ValueError):
                    pass
                self._pending_normalized_roi = None
            if self.recorder.recording:
                now = time.monotonic()
                if now >= self.record_next_frame_mono:
                    processed = self._prepare_record_frame(frame)
                    if processed is not None:
                        self.recorder.submit(processed)
                    interval = 1.0 / self.fps_spin.value()
                    self.record_next_frame_mono = max(
                        self.record_next_frame_mono + interval, now + interval * 0.25
                    )
                elapsed = now - self.record_started_mono
                self.record_label.setText(
                    f"● 录制中  {elapsed:0.1f} 秒 / {self.recorder.result.frames_written} 帧"
                )

        if self.reader.status != self._last_reader_status:
            self._last_reader_status = self.reader.status
            self.connection_label.setText(f"● {self.reader.status}")
            if self.reader.status in {"连接失败", "连接中断", "已断开"}:
                self.connect_btn.setEnabled(True)
                self.disconnect_btn.setEnabled(False)

    def _prepare_record_frame(self, frame: np.ndarray) -> np.ndarray | None:
        if not self.record_roi:
            return None
        height, width = frame.shape[:2]
        x, y, w, h = self.record_roi
        x, y = max(0, x), max(0, y)
        w, h = min(w, width - x), min(h, height - y)
        w -= w % 2
        h -= h % 2
        if w <= 1 or h <= 1:
            return None
        if self.record_crop:
            result = frame[y : y + h, x : x + w].copy()
            cv2.rectangle(result, (1, 1), (w - 2, h - 2), (0, 220, 0), 2)
            return result
        result = frame.copy()
        cv2.rectangle(result, (x, y), (x + w, y + h), (0, 220, 0), 3)
        cv2.putText(
            result,
            "WORKSTATION ROI",
            (x + 8, max(28, y + 28)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (0, 220, 0),
            2,
            cv2.LINE_AA,
        )
        return result[: height - height % 2, : width - width % 2]

    def _roi_changed(self, roi: object) -> None:
        if roi:
            x, y, w, h = roi
            self.roi_label.setText(f"x={x}, y={y}, w={w}, h={h}")
        else:
            self.roi_label.setText("尚未绘制")

    def save_roi(self) -> None:
        roi = self.canvas.roi()
        frame_w, frame_h = self.canvas.frame_size
        if not roi or not frame_w or not frame_h:
            QMessageBox.warning(self, "无法保存", "请先连接视频并绘制 ROI。")
            return
        x, y, w, h = roi
        self.config["roi_pixels"] = list(roi)
        self.config["roi_normalized"] = [
            x / frame_w,
            y / frame_h,
            w / frame_w,
            h / frame_h,
        ]
        self._save_config()
        QMessageBox.information(self, "已保存", "工位 ROI 已保存，下次启动会自动恢复。")

    def choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择数据保存目录", self.output_edit.text()
        )
        if selected:
            self.output_edit.setText(selected)
            self._dataset_edited()

    def _dataset_edited(self) -> None:
        self.config["dataset_dir"] = self.output_edit.text().strip()
        self._save_config()
        self.datasetChanged.emit(str(self.dataset_dir))

    def start_recording(self) -> None:
        if self.current_frame is None or self.reader.status != "已连接":
            QMessageBox.warning(self, "无法录制", "请先连接视频流并等待画面出现。")
            return
        roi = self.canvas.roi()
        if not roi:
            QMessageBox.warning(self, "缺少 ROI", "请先在画面上绘制工位 ROI。")
            return

        dataset = self.dataset_dir
        (dataset / "videos").mkdir(parents=True, exist_ok=True)
        (dataset / "metadata").mkdir(parents=True, exist_ok=True)
        sample_id = safe_name(self.sample_edit.text(), "sample")
        clip_name = safe_name(self.name_edit.text(), "clip")
        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        clip_id = f"{sample_id}_{clip_name}_{timestamp}"
        self.record_path = dataset / "videos" / f"{clip_id}.mp4"
        self.record_roi = roi
        self.record_crop = self.crop_check.isChecked()
        self.record_started_at = datetime.now().astimezone()
        self.record_started_mono = time.monotonic()
        self.record_next_frame_mono = self.record_started_mono
        source_h, source_w = self.current_frame.shape[:2]
        x, y, w, h = roi
        self.record_meta = {
            "metadata_version": "1.0",
            "clip_id": clip_id,
            "sample_id": self.sample_edit.text().strip() or sample_id,
            "clip_name": self.name_edit.text().strip() or clip_name,
            "video_path": str(Path("videos") / self.record_path.name),
            "source_url": re.sub(
                r"^(rtsp://)[^/@]+@",
                r"\1***@",
                self.url_edit.text().strip(),
                flags=re.IGNORECASE,
            ),
            "recorded_at": self.record_started_at.isoformat(timespec="milliseconds"),
            "source_width": source_w,
            "source_height": source_h,
            "source_fps": self.reader.source_fps,
            "record_fps": self.fps_spin.value(),
            "roi_pixels": {"x": x, "y": y, "w": w, "h": h},
            "roi_normalized": [x / source_w, y / source_h, w / source_w, h / source_h],
            "crop_to_roi": self.record_crop,
        }
        self.config.update(
            {
                "dataset_dir": str(dataset),
                "record_fps": self.fps_spin.value(),
                "crop_to_roi": self.record_crop,
            }
        )
        self._save_config()
        self.recorder.start(self.record_path, self.fps_spin.value())
        self.canvas.set_interaction_enabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.record_label.setText("● 录制中")

    def stop_recording(self) -> None:
        if not self.recorder.recording:
            return
        result = self.recorder.stop()
        elapsed = max(0.0, time.monotonic() - self.record_started_mono)
        self.canvas.set_interaction_enabled(True)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.record_path is None:
            return
        self.record_meta.update(
            {
                "stopped_at": datetime.now()
                .astimezone()
                .isoformat(timespec="milliseconds"),
                "duration_wall_sec": round(elapsed, 3),
                "frames_written": result.frames_written,
                "frames_dropped_by_writer": result.frames_dropped,
                "saved_width": result.width,
                "saved_height": result.height,
                "encoding": "mp4v",
                "writer_error": result.error,
            }
        )
        metadata_path = self.dataset_dir / "metadata" / f"{self.record_path.stem}.json"
        metadata_path.write_text(
            json.dumps(self.record_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.datasetChanged.emit(str(self.dataset_dir))
        if result.error or result.frames_written == 0:
            self.record_label.setText("保存失败")
            QMessageBox.critical(
                self,
                "录制失败",
                result.error or "未写入任何视频帧，请检查视频流和 ROI。",
            )
        else:
            self.record_label.setText(
                f"已保存：{self.record_path.name}（{result.frames_written} 帧）"
            )
            QMessageBox.information(
                self,
                "样本已保存",
                f"视频：{self.record_path}\n元数据：{metadata_path}",
            )
        self.record_roi = None

    def shutdown(self) -> None:
        if self.recorder.recording:
            self.stop_recording()
        self.reader.stop(wait=True)
