from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .event_store import EventStore
from .label_schema import REGION_TITLES, SCAN_REGION_KEYS, SCAN_REGIONS
from .label_store import LabelStore
from .video_widget import VideoCanvas


class AnnotationPage(QWidget):
    def __init__(self, dataset_dir: Path) -> None:
        super().__init__()
        self.dataset_dir = dataset_dir.resolve()
        self.store = LabelStore(self.dataset_dir)
        self.event_store = EventStore(self.dataset_dir)
        self.cap: cv2.VideoCapture | None = None
        self.video_path: Path | None = None
        self.metadata: dict[str, Any] = {}
        self.total_frames = 0
        self.current_frame = 0
        self.fps = 25.0
        self.segment_start = 0
        self.segment_end = 0
        self.event_start: int | None = None
        self.event_end: int | None = None
        self.scan_events: list[dict[str, int | str]] = []
        self.playing = False

        self._build_ui()
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._next_frame)
        self.refresh_list()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        header = QHBoxLayout()
        title = QLabel("两级人工标注")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.dataset_label = QLabel(str(self.dataset_dir))
        self.dataset_label.setToolTip(str(self.dataset_dir))
        header.addWidget(QLabel("数据目录："))
        header.addWidget(self.dataset_label)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        source_box = QGroupBox("已录样本")
        source_layout = QVBoxLayout(source_box)
        self.video_list = QListWidget()
        self.video_list.setMinimumWidth(250)
        source_layout.addWidget(self.video_list, 1)
        source_buttons = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.open_btn = QPushButton("打开其他视频…")
        source_buttons.addWidget(self.refresh_btn)
        source_buttons.addWidget(self.open_btn)
        source_layout.addLayout(source_buttons)
        splitter.addWidget(source_box)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = VideoCanvas(allow_roi=False)
        center_layout.addWidget(self.canvas, 1)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        center_layout.addWidget(self.timeline)
        player_row = QHBoxLayout()
        self.play_btn = QPushButton("▶ 播放")
        self.prev_btn = QPushButton("上一帧")
        self.next_btn = QPushButton("下一帧")
        self.time_label = QLabel("00:00.000 / 00:00.000    帧 0 / 0")
        player_row.addWidget(self.play_btn)
        player_row.addWidget(self.prev_btn)
        player_row.addWidget(self.next_btn)
        player_row.addStretch(1)
        player_row.addWidget(self.time_label)
        center_layout.addLayout(player_row)
        splitter.addWidget(center)

        form_box = QGroupBox("样本标签")
        form_layout = QVBoxLayout(form_box)
        self.clip_info = QLabel("请先加载视频")
        self.clip_info.setWordWrap(True)
        form_layout.addWidget(self.clip_info)

        segment_box = QGroupBox("有效时序区间")
        segment_layout = QVBoxLayout(segment_box)
        self.segment_label = QLabel("起点 0 — 终点 0")
        segment_layout.addWidget(self.segment_label)
        segment_buttons = QHBoxLayout()
        self.start_mark_btn = QPushButton("当前帧设为起点")
        self.end_mark_btn = QPushButton("当前帧设为终点")
        self.full_mark_btn = QPushButton("使用整段")
        segment_buttons.addWidget(self.start_mark_btn)
        segment_buttons.addWidget(self.end_mark_btn)
        segment_buttons.addWidget(self.full_mark_btn)
        segment_layout.addLayout(segment_buttons)
        form_layout.addWidget(segment_box)

        event_box = QGroupBox("逐部位扫描时间段")
        event_layout = QVBoxLayout(event_box)
        self.event_region_combo = QComboBox()
        self.event_region_combo.addItem("请选择扫描区域", "")
        for key, display in SCAN_REGIONS:
            self.event_region_combo.addItem(display, key)
        event_layout.addWidget(self.event_region_combo)
        self.event_range_label = QLabel("开始帧：未设置    结束帧：未设置")
        event_layout.addWidget(self.event_range_label)
        event_mark_row = QHBoxLayout()
        self.event_start_btn = QPushButton("当前帧设为开始")
        self.event_end_btn = QPushButton("当前帧设为结束")
        self.add_event_btn = QPushButton("＋ 添加时间段")
        event_mark_row.addWidget(self.event_start_btn)
        event_mark_row.addWidget(self.event_end_btn)
        event_mark_row.addWidget(self.add_event_btn)
        event_layout.addLayout(event_mark_row)
        self.event_table = QTableWidget(0, 5)
        self.event_table.setHorizontalHeaderLabels(
            ["区域", "开始帧", "结束帧", "开始时间", "结束时间"]
        )
        self.event_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.event_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.verticalHeader().setVisible(False)
        self.event_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.event_table.horizontalHeader().setStretchLastSection(True)
        self.event_table.setMinimumHeight(190)
        event_layout.addWidget(self.event_table)
        event_action_row = QHBoxLayout()
        self.jump_event_btn = QPushButton("跳到所选时间段")
        self.delete_event_btn = QPushButton("删除所选时间段")
        event_action_row.addWidget(self.jump_event_btn)
        event_action_row.addWidget(self.delete_event_btn)
        event_layout.addLayout(event_action_row)
        self.event_count_label = QLabel("已添加 0 个扫描时间段")
        event_layout.addWidget(self.event_count_label)
        form_layout.addWidget(event_box)

        label_form = QFormLayout()
        self.validity_combo = QComboBox()
        self.validity_combo.addItems(["请选择", "valid", "invalid"])
        label_form.addRow("样本有效性", self.validity_combo)
        self.part_combos: dict[str, QComboBox] = {}
        for key, title_text in SCAN_REGIONS:
            combo = QComboBox()
            combo.addItems(["请选择", "scanned", "not_scanned"])
            self.part_combos[key] = combo
            label_form.addRow(title_text, combo)
        form_layout.addLayout(label_form)
        form_layout.addWidget(QLabel("备注"))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("可记录遮挡、多人干扰、漏扫原因等")
        self.notes_edit.setMaximumHeight(100)
        form_layout.addWidget(self.notes_edit)
        self.save_btn = QPushButton("保存两级标注")
        self.save_btn.setObjectName("primaryButton")
        form_layout.addWidget(self.save_btn)
        self.label_path = QLabel("保存到：labels.* / events.*")
        self.label_path.setToolTip(
            f"{self.store.csv_path}\n{self.store.json_path}\n"
            f"{self.event_store.csv_path}\n{self.event_store.json_path}"
        )
        self.label_path.setWordWrap(True)
        form_layout.addWidget(self.label_path)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        form_scroll.setMinimumWidth(390)
        form_scroll.setWidget(form_box)
        splitter.addWidget(form_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([270, 760, 390])
        root.addWidget(splitter, 1)

        self.refresh_btn.clicked.connect(self.refresh_list)
        self.open_btn.clicked.connect(self.open_other)
        self.video_list.itemDoubleClicked.connect(
            lambda item: self.load_video(Path(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.play_btn.clicked.connect(self.toggle_play)
        self.prev_btn.clicked.connect(lambda: self.show_frame(self.current_frame - 1))
        self.next_btn.clicked.connect(lambda: self.show_frame(self.current_frame + 1))
        self.timeline.sliderPressed.connect(self.pause)
        self.timeline.sliderReleased.connect(
            lambda: self.show_frame(self.timeline.value())
        )
        self.start_mark_btn.clicked.connect(self.mark_start)
        self.end_mark_btn.clicked.connect(self.mark_end)
        self.full_mark_btn.clicked.connect(self.mark_full)
        self.event_start_btn.clicked.connect(self.mark_event_start)
        self.event_end_btn.clicked.connect(self.mark_event_end)
        self.add_event_btn.clicked.connect(self.add_scan_event)
        self.delete_event_btn.clicked.connect(self.delete_selected_event)
        self.jump_event_btn.clicked.connect(self.jump_to_selected_event)
        self.event_table.itemDoubleClicked.connect(
            lambda _item: self.jump_to_selected_event()
        )
        self.save_btn.clicked.connect(self.save_label)

    def set_dataset_dir(self, path: str) -> None:
        self.dataset_dir = Path(path).expanduser().resolve()
        self.store = LabelStore(self.dataset_dir)
        self.event_store = EventStore(self.dataset_dir)
        self.dataset_label.setText(str(self.dataset_dir))
        self.dataset_label.setToolTip(str(self.dataset_dir))
        self.label_path.setText("保存到：labels.* / events.*")
        self.label_path.setToolTip(
            f"{self.store.csv_path}\n{self.store.json_path}\n"
            f"{self.event_store.csv_path}\n{self.event_store.json_path}"
        )
        self.refresh_list()

    def refresh_list(self) -> None:
        selected = str(self.video_path) if self.video_path else ""
        self.video_list.clear()
        videos_dir = self.dataset_dir / "videos"
        if not videos_dir.exists():
            return
        for path in sorted(
            videos_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            self.video_list.addItem(path.name)
            item = self.video_list.item(self.video_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            if str(path) == selected:
                item.setSelected(True)

    def open_other(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "加载视频",
            str(self.dataset_dir / "videos"),
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)",
        )
        if selected:
            self.load_video(Path(selected))

    def _find_metadata(self, video_path: Path) -> tuple[dict[str, Any], Path | None]:
        candidates = [
            self.dataset_dir / "metadata" / f"{video_path.stem}.json",
            video_path.with_suffix(".json"),
            video_path.with_suffix(".meta.json"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8")), path
                except (OSError, json.JSONDecodeError):
                    continue
        return {}, None

    def load_video(self, path: Path) -> None:
        self.pause()
        if self.cap is not None:
            self.cap.release()
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            QMessageBox.critical(self, "无法加载", f"OpenCV 无法解码该视频：\n{path}")
            return
        self.cap = cap
        self.video_path = path.resolve()
        self.total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        self.fps = fps if 1 <= fps <= 120 else 25.0
        self.segment_start = 0
        self.segment_end = self.total_frames - 1
        self.timeline.setRange(0, self.total_frames - 1)
        self.metadata, metadata_path = self._find_metadata(self.video_path)
        self.metadata["_metadata_path"] = str(metadata_path) if metadata_path else ""
        clip_id = self.metadata.get("clip_id", self.video_path.stem)
        sample_id = self.metadata.get("sample_id", clip_id)
        clip_name = self.metadata.get("clip_name", self.video_path.stem)
        self.clip_info.setText(
            f"片段：{clip_id}\n样本 ID：{sample_id}\n名称：{clip_name}\n"
            f"{self.total_frames} 帧 · {self.fps:.2f} FPS"
        )
        self.show_frame(0)
        self._load_existing_label()
        self._load_existing_events()

    def _load_existing_label(self) -> None:
        for combo in [self.validity_combo, *self.part_combos.values()]:
            combo.setCurrentIndex(0)
        self.notes_edit.clear()
        if not self.video_path:
            return
        existing = self.store.find_for_video(self.video_path)
        if not existing:
            self._update_segment_label()
            return
        self.validity_combo.setCurrentText(str(existing.get("validity", "请选择")))
        for key, _ in SCAN_REGIONS:
            self.part_combos[key].setCurrentText(str(existing.get(key, "请选择")))
        self.notes_edit.setPlainText(str(existing.get("notes", "")))
        self.segment_start = int(existing.get("segment_start_frame", 0) or 0)
        self.segment_end = int(
            existing.get("segment_end_frame", self.total_frames - 1) or 0
        )
        self.segment_start = min(max(self.segment_start, 0), self.total_frames - 1)
        self.segment_end = min(
            max(self.segment_end, self.segment_start), self.total_frames - 1
        )
        self._update_segment_label()

    def _clip_id(self) -> str:
        if not self.video_path:
            return ""
        return str(self.metadata.get("clip_id", self.video_path.stem))

    def _load_existing_events(self) -> None:
        self.scan_events = []
        self.event_start = None
        self.event_end = None
        self._update_event_range_label()
        clip_id = self._clip_id()
        if not clip_id:
            self._refresh_event_table()
            return
        for record in self.event_store.load_for_clip(clip_id):
            region = str(record.get("region", ""))
            if region not in SCAN_REGION_KEYS:
                continue
            try:
                start = int(record.get("start_frame", 0))
                end = int(record.get("end_frame", start))
            except (TypeError, ValueError):
                continue
            start = min(max(start, 0), self.total_frames - 1)
            end = min(max(end, start), self.total_frames - 1)
            self.scan_events.append(
                {"region": region, "start_frame": start, "end_frame": end}
            )
        self.scan_events.sort(key=lambda event: int(event["start_frame"]))
        for event in self.scan_events:
            region = str(event["region"])
            if self.part_combos[region].currentIndex() == 0:
                self.part_combos[region].setCurrentText("scanned")
        self._refresh_event_table()

    def show_frame(self, index: int) -> None:
        if self.cap is None:
            return
        index = min(max(int(index), 0), self.total_frames - 1)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.cap.read()
        if not ok:
            self.pause()
            return
        self.current_frame = index
        self.canvas.set_frame(frame)
        with QSignalBlocker(self.timeline):
            self.timeline.setValue(index)
        self._update_time_label()

    def _next_frame(self) -> None:
        if self.cap is None or self.current_frame >= self.total_frames - 1:
            self.pause()
            return
        ok, frame = self.cap.read()
        if not ok:
            self.pause()
            return
        self.current_frame += 1
        self.canvas.set_frame(frame)
        with QSignalBlocker(self.timeline):
            self.timeline.setValue(self.current_frame)
        self._update_time_label()

    def toggle_play(self) -> None:
        if self.cap is None:
            return
        if self.playing:
            self.pause()
        else:
            if self.current_frame >= self.total_frames - 1:
                self.show_frame(0)
            self.playing = True
            self.play_btn.setText("⏸ 暂停")
            self.play_timer.start(max(10, round(1000 / self.fps)))

    def pause(self) -> None:
        self.playing = False
        self.play_timer.stop()
        self.play_btn.setText("▶ 播放")

    def _format_time(self, seconds: float) -> str:
        minutes = int(seconds // 60)
        remain = seconds - minutes * 60
        return f"{minutes:02d}:{remain:06.3f}"

    def _update_time_label(self) -> None:
        current_sec = self.current_frame / self.fps
        total_sec = self.total_frames / self.fps
        self.time_label.setText(
            f"{self._format_time(current_sec)} / {self._format_time(total_sec)}    "
            f"帧 {self.current_frame + 1} / {self.total_frames}"
        )

    def mark_start(self) -> None:
        self.segment_start = self.current_frame
        self.segment_end = max(self.segment_end, self.segment_start)
        self._update_segment_label()

    def mark_end(self) -> None:
        self.segment_end = self.current_frame
        self.segment_start = min(self.segment_start, self.segment_end)
        self._update_segment_label()

    def mark_full(self) -> None:
        self.segment_start = 0
        self.segment_end = max(0, self.total_frames - 1)
        self._update_segment_label()

    def _update_segment_label(self) -> None:
        self.segment_label.setText(
            f"起点 {self.segment_start}（{self.segment_start / self.fps:.3f}s） — "
            f"终点 {self.segment_end}（{self.segment_end / self.fps:.3f}s）"
        )

    def mark_event_start(self) -> None:
        if not self.video_path:
            return
        self.event_start = self.current_frame
        if self.event_end is not None and self.event_end < self.event_start:
            self.event_end = self.event_start
        self._update_event_range_label()

    def mark_event_end(self) -> None:
        if not self.video_path:
            return
        self.event_end = self.current_frame
        if self.event_start is not None and self.event_start > self.event_end:
            self.event_start = self.event_end
        self._update_event_range_label()

    def _update_event_range_label(self) -> None:
        start = "未设置" if self.event_start is None else str(self.event_start)
        end = "未设置" if self.event_end is None else str(self.event_end)
        self.event_range_label.setText(f"开始帧：{start}    结束帧：{end}")

    def add_scan_event(self) -> None:
        if not self.video_path:
            QMessageBox.warning(self, "无法添加", "请先加载视频。")
            return
        region = str(self.event_region_combo.currentData() or "")
        if region not in SCAN_REGION_KEYS:
            QMessageBox.warning(self, "缺少区域", "请选择这个时间段对应的扫描区域。")
            return
        if self.event_start is None or self.event_end is None:
            QMessageBox.warning(
                self, "缺少时间", "请分别用当前帧设置扫描开始帧和结束帧。"
            )
            return
        start, end = self.event_start, self.event_end
        if start < self.segment_start or end > self.segment_end:
            QMessageBox.warning(
                self,
                "超出有效区间",
                "扫描时间段必须位于样本的有效时序区间内。",
            )
            return
        for event in self.scan_events:
            if str(event["region"]) != region:
                continue
            old_start = int(event["start_frame"])
            old_end = int(event["end_frame"])
            if not (end < old_start or start > old_end):
                QMessageBox.warning(
                    self,
                    "时间段重叠",
                    f"{REGION_TITLES[region]}已经存在重叠时间段，请调整后再添加。",
                )
                return
        self.scan_events.append(
            {"region": region, "start_frame": start, "end_frame": end}
        )
        self.scan_events.sort(key=lambda event: int(event["start_frame"]))
        self.part_combos[region].setCurrentText("scanned")
        self.event_start = None
        self.event_end = None
        self._update_event_range_label()
        self._refresh_event_table()

    def _refresh_event_table(self) -> None:
        self.event_table.setRowCount(len(self.scan_events))
        for row, event in enumerate(self.scan_events):
            region = str(event["region"])
            start = int(event["start_frame"])
            end = int(event["end_frame"])
            values = [
                REGION_TITLES.get(region, region),
                str(start),
                str(end),
                f"{start / self.fps:.3f}s",
                f"{(end + 1) / self.fps:.3f}s",
            ]
            for column, value in enumerate(values):
                self.event_table.setItem(row, column, QTableWidgetItem(value))
        region_count = len({str(event["region"]) for event in self.scan_events})
        self.event_count_label.setText(
            f"已添加 {len(self.scan_events)} 个时间段，覆盖 {region_count} 个区域"
        )

    def delete_selected_event(self) -> None:
        row = self.event_table.currentRow()
        if 0 <= row < len(self.scan_events):
            del self.scan_events[row]
            self._refresh_event_table()

    def jump_to_selected_event(self) -> None:
        row = self.event_table.currentRow()
        if 0 <= row < len(self.scan_events):
            self.show_frame(int(self.scan_events[row]["start_frame"]))

    def _relative_or_absolute(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.dataset_dir))
        except ValueError:
            return str(path.resolve())

    def save_label(self) -> None:
        if not self.video_path:
            QMessageBox.warning(self, "无法保存", "请先加载一个视频样本。")
            return
        if self.validity_combo.currentIndex() == 0:
            QMessageBox.warning(
                self, "标签不完整", "请选择样本有效性 valid / invalid。"
            )
            return
        missing = [
            title
            for key, title in SCAN_REGIONS
            if self.part_combos[key].currentIndex() == 0
        ]
        if missing:
            QMessageBox.warning(
                self, "标签不完整", "以下部位尚未标注：\n" + "、".join(missing)
            )
            return

        event_regions = {str(event["region"]) for event in self.scan_events}
        conflicting = [
            REGION_TITLES[key]
            for key in event_regions
            if self.part_combos[key].currentText() == "not_scanned"
        ]
        if conflicting:
            QMessageBox.warning(
                self,
                "两级标签冲突",
                "以下区域标为未扫描，但存在扫描时间段：\n" + "、".join(conflicting),
            )
            return
        out_of_range = [
            event
            for event in self.scan_events
            if int(event["start_frame"]) < self.segment_start
            or int(event["end_frame"]) > self.segment_end
        ]
        if out_of_range:
            QMessageBox.warning(
                self,
                "时间段超界",
                "存在超出样本有效时序区间的扫描时间段，请修改或删除。",
            )
            return
        if self.validity_combo.currentText() == "valid":
            missing_events = [
                REGION_TITLES[key]
                for key in SCAN_REGION_KEYS
                if self.part_combos[key].currentText() == "scanned"
                and key not in event_regions
            ]
            if missing_events:
                QMessageBox.warning(
                    self,
                    "缺少时间段",
                    "有效样本中，以下已扫描区域还没有标注扫描时间段：\n"
                    + "、".join(missing_events),
                )
                return

        meta = self.metadata
        roi = (
            meta.get("roi_pixels", {})
            if isinstance(meta.get("roi_pixels"), dict)
            else {}
        )
        clip_id = str(meta.get("clip_id", self.video_path.stem))
        metadata_path_text = str(meta.get("_metadata_path", ""))
        metadata_path = Path(metadata_path_text) if metadata_path_text else None
        duration = self.total_frames / self.fps
        annotated_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        video_path_value = self._relative_or_absolute(self.video_path)
        record: dict[str, Any] = {
            "annotation_version": "2.0",
            "annotation_id": clip_id,
            "clip_id": clip_id,
            "sample_id": meta.get("sample_id", clip_id),
            "clip_name": meta.get("clip_name", self.video_path.stem),
            "video_path": video_path_value,
            "metadata_path": self._relative_or_absolute(metadata_path)
            if metadata_path
            else "",
            "source_url": meta.get("source_url", ""),
            "recorded_at": meta.get("recorded_at", ""),
            "annotated_at": annotated_at,
            "segment_start_frame": self.segment_start,
            "segment_end_frame": self.segment_end,
            "segment_start_sec": round(self.segment_start / self.fps, 6),
            "segment_end_sec": round((self.segment_end + 1) / self.fps, 6),
            "fps": round(self.fps, 6),
            "frame_count": self.total_frames,
            "duration_sec": round(duration, 6),
            "source_width": meta.get("source_width", ""),
            "source_height": meta.get("source_height", ""),
            "saved_width": meta.get("saved_width", ""),
            "saved_height": meta.get("saved_height", ""),
            "roi_x": roi.get("x", ""),
            "roi_y": roi.get("y", ""),
            "roi_w": roi.get("w", ""),
            "roi_h": roi.get("h", ""),
            "roi_normalized": meta.get("roi_normalized", []),
            "crop_to_roi": meta.get("crop_to_roi", ""),
            "validity": self.validity_combo.currentText(),
            "notes": self.notes_edit.toPlainText().strip(),
        }
        for key, _ in SCAN_REGIONS:
            record[key] = self.part_combos[key].currentText()
        event_records: list[dict[str, Any]] = []
        for event in self.scan_events:
            region = str(event["region"])
            start = int(event["start_frame"])
            end = int(event["end_frame"])
            duration_frames = end - start + 1
            event_records.append(
                {
                    "event_version": "1.0",
                    "event_id": f"{clip_id}:{region}:{start}:{end}",
                    "clip_id": clip_id,
                    "sample_id": meta.get("sample_id", clip_id),
                    "video_path": video_path_value,
                    "region": region,
                    "start_frame": start,
                    "end_frame": end,
                    "start_sec": round(start / self.fps, 6),
                    "end_sec": round((end + 1) / self.fps, 6),
                    "duration_frames": duration_frames,
                    "duration_sec": round(duration_frames / self.fps, 6),
                    "fps": round(self.fps, 6),
                    "annotated_at": annotated_at,
                }
            )
        try:
            self.event_store.replace_for_clip(clip_id, event_records)
            self.store.upsert(record)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"无法写入两级标签文件：\n{exc}")
            return
        QMessageBox.information(
            self,
            "两级标注已保存",
            "已同步写入：\n"
            f"{self.store.csv_path}\n{self.store.json_path}\n"
            f"{self.event_store.csv_path}\n{self.event_store.json_path}",
        )

    def shutdown(self) -> None:
        self.pause()
        if self.cap is not None:
            self.cap.release()
