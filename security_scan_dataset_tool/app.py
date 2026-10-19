from __future__ import annotations

import os
import sys

# 必须在任何 OpenCV 导入之前设置，确保 FFmpeg 使用 RTSP/TCP。
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;5000000|max_delay;500000",
)

from PySide6.QtWidgets import QApplication

from security_scan_tool.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("安检扫描数据采集与标注工具")
    app.setOrganizationName("SecurityScan")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
