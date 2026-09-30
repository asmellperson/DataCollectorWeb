import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# 配置文件仅使用 KEY=VALUE，不执行任何内容。
config = ROOT / "config" / ".env"
if config.exists():
    for line in config.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
DATA_ROOT = Path(os.getenv("DATA_ROOT", str(ROOT / "datasets"))).resolve()
STATE_ROOT = Path(os.getenv("STATE_ROOT", str(ROOT / "config"))).resolve()
MODEL_ROOT = ROOT / "models"
DEVICE = os.getenv("DEVICE", "cpu")
WORLD_MODEL = os.getenv("WORLD_MODEL", str(MODEL_ROOT / "yolov8s-worldv2.pt"))
for path in (DATA_ROOT, STATE_ROOT, MODEL_ROOT):
    path.mkdir(parents=True, exist_ok=True)
(ROOT / "config" / "ultralytics").mkdir(exist_ok=True)
os.environ["YOLO_CONFIG_DIR"] = str(ROOT / "config" / "ultralytics")
os.environ.setdefault("YOLO_AUTOINSTALL", "false")

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
