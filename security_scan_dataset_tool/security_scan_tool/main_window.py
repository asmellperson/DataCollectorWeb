from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from .annotation_page import AnnotationPage
from .capture_page import CapturePage

STYLE = """
QMainWindow, QWidget { background: #f8fafc; color: #172033; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QGroupBox { background: white; border: 1px solid #dbe3ef; border-radius: 8px; margin-top: 12px; padding-top: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background: white; border: 1px solid #cbd5e1; border-radius: 5px; padding: 6px; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #2563eb; }
QPushButton { background: #e8eef7; border: 0; border-radius: 6px; padding: 8px 13px; }
QPushButton:hover { background: #dbe6f5; }
QPushButton:disabled { color: #94a3b8; background: #eef2f7; }
QPushButton#primaryButton { color: white; background: #2563eb; font-weight: 600; }
QPushButton#primaryButton:hover { background: #1d4ed8; }
QPushButton#dangerButton { color: white; background: #dc2626; font-weight: 600; }
QPushButton#dangerButton:hover { background: #b91c1c; }
QLabel#pageTitle { font-size: 20px; font-weight: 700; color: #0f172a; }
QTabBar::tab { background: #e8eef7; padding: 10px 24px; margin-right: 2px; }
QTabBar::tab:selected { background: white; color: #1d4ed8; font-weight: 700; }
QListWidget { background: white; border: 1px solid #dbe3ef; border-radius: 6px; padding: 4px; }
QListWidget::item { padding: 7px; border-radius: 4px; }
QListWidget::item:selected { color: white; background: #2563eb; }
QSlider::groove:horizontal { height: 5px; background: #dbe3ef; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; margin: -6px 0; background: #2563eb; border-radius: 8px; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._load_chinese_font()
        self.app_dir = Path(__file__).resolve().parent.parent
        self.setWindowTitle("安检扫描数据采集与人工标注工具")
        self.resize(1500, 900)
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(STYLE)

        self.tabs = QTabWidget()
        self.capture_page = CapturePage(self.app_dir)
        self.annotation_page = AnnotationPage(self.capture_page.dataset_dir)
        self.tabs.addTab(self.capture_page, "① 数据采集")
        self.tabs.addTab(self.annotation_page, "② 人工标注")
        self.setCentralWidget(self.tabs)
        self.capture_page.datasetChanged.connect(self.annotation_page.set_dataset_dir)
        self.statusBar().showMessage("就绪")

    @staticmethod
    def _load_chinese_font() -> None:
        """某些独立 Qt 环境不会自动枚举 Windows 字体，显式加载中文字体。"""
        if sys.platform != "win32":
            return
        for path in (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simsun.ttc"),
        ):
            if not path.exists():
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                app = QApplication.instance()
                if app is not None:
                    app.setFont(QFont(families[0], 10))
                return

    def closeEvent(self, event: QCloseEvent) -> None:
        self.capture_page.shutdown()
        self.annotation_page.shutdown()
        event.accept()
