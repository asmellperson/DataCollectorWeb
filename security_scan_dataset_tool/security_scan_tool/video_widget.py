from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel


class VideoCanvas(QLabel):
    roiChanged = Signal(object)

    def __init__(self, allow_roi: bool = False) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(640, 360)
        self.setStyleSheet("background:#111827; color:#94a3b8; border-radius:8px;")
        self.setText("等待视频画面")
        self._frame: np.ndarray | None = None
        self._qimage: QImage | None = None
        self._image_rect = QRect()
        self._roi: tuple[int, int, int, int] | None = None
        self._drawing = False
        self._start = QPoint()
        self._current = QPoint()
        self.allow_roi = allow_roi
        self.interaction_enabled = allow_roi

    @property
    def frame_size(self) -> tuple[int, int]:
        if self._frame is None:
            return (0, 0)
        height, width = self._frame.shape[:2]
        return (width, height)

    def set_frame(self, frame: np.ndarray) -> None:
        self._frame = frame
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        self._qimage = QImage(
            rgb.data, width, height, channels * width, QImage.Format.Format_RGB888
        ).copy()
        self.update()

    def set_roi(self, roi: tuple[int, int, int, int] | None) -> None:
        self._roi = roi
        self.roiChanged.emit(roi)
        self.update()

    def roi(self) -> tuple[int, int, int, int] | None:
        return self._roi

    def clear_roi(self) -> None:
        self.set_roi(None)

    def set_interaction_enabled(self, enabled: bool) -> None:
        self.interaction_enabled = enabled
        self.setCursor(
            Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor
        )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._qimage is None:
            return
        painter = QPainter(self)
        scaled = QPixmap.fromImage(self._qimage).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        self._image_rect = QRect(x, y, scaled.width(), scaled.height())
        painter.drawPixmap(self._image_rect, scaled)

        if self._roi:
            display_rect = self._source_to_display(self._roi)
            painter.setPen(QPen(QColor("#22c55e"), 3))
            painter.setBrush(QColor(34, 197, 94, 35))
            painter.drawRect(display_rect)
            painter.setPen(QColor("white"))
            painter.fillRect(
                display_rect.x(),
                display_rect.y() - 24,
                92,
                24,
                QColor(22, 163, 74, 220),
            )
            painter.drawText(display_rect.x() + 8, display_rect.y() - 7, "工位 ROI")

        if self._drawing:
            rect = (
                QRect(self._start, self._current)
                .normalized()
                .intersected(self._image_rect)
            )
            painter.setPen(QPen(QColor("#f59e0b"), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(245, 158, 11, 30))
            painter.drawRect(rect)

    def _source_to_display(self, roi: tuple[int, int, int, int]) -> QRect:
        frame_w, frame_h = self.frame_size
        if not frame_w or not frame_h or self._image_rect.isEmpty():
            return QRect()
        sx = self._image_rect.width() / frame_w
        sy = self._image_rect.height() / frame_h
        x, y, w, h = roi
        return QRect(
            round(self._image_rect.x() + x * sx),
            round(self._image_rect.y() + y * sy),
            max(1, round(w * sx)),
            max(1, round(h * sy)),
        )

    def _display_to_source(self, rect: QRect) -> tuple[int, int, int, int]:
        frame_w, frame_h = self.frame_size
        sx = frame_w / self._image_rect.width()
        sy = frame_h / self._image_rect.height()
        x = round((rect.x() - self._image_rect.x()) * sx)
        y = round((rect.y() - self._image_rect.y()) * sy)
        w = round(rect.width() * sx)
        h = round(rect.height() * sy)
        x = min(max(x, 0), frame_w - 1)
        y = min(max(y, 0), frame_h - 1)
        w = min(max(w, 1), frame_w - x)
        h = min(max(h, 1), frame_h - y)
        return (x, y, w, h)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            self.allow_roi
            and self.interaction_enabled
            and self._qimage is not None
            and event.button() == Qt.MouseButton.LeftButton
            and self._image_rect.contains(event.position().toPoint())
        ):
            self._drawing = True
            self._start = event.position().toPoint()
            self._current = self._start
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drawing:
            self._current = event.position().toPoint()
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drawing and event.button() == Qt.MouseButton.LeftButton:
            self._drawing = False
            rect = (
                QRect(self._start, event.position().toPoint())
                .normalized()
                .intersected(self._image_rect)
            )
            if rect.width() >= 20 and rect.height() >= 20:
                self.set_roi(self._display_to_source(rect))
            self.update()
            return
        super().mouseReleaseEvent(event)
