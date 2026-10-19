import os
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    socket_path = Path(
        os.getenv(
            "DCW_BACKEND_SOCKET",
            str(Path(__file__).resolve().parents[1] / "config/run/backend.sock"),
        )
    )
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    # 模型缓存、采集线程和文件锁由单进程统一管理，不能开启多 worker。
    uvicorn.run("app.main:app", uds=str(socket_path), workers=1)
