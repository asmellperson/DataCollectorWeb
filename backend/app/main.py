import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api import (
    datasets,
    annotations,
    tasks,
    models,
    cameras,
    collection,
    videos,
    exports,
)
from app.services.task_service import recover
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    recover()
    yield
    from app.services import camera_service, collection_service

    collection_service.stop()
    camera_service.shutdown()
    from app.services.task_service import shutdown

    shutdown()


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("collector")
app = FastAPI(title="数据采集与标注平台", version="1.0.0", lifespan=lifespan)
app.include_router(datasets.router)
app.include_router(annotations.router)
app.include_router(tasks.router)
app.include_router(models.router)
app.include_router(cameras.router)
app.include_router(collection.router)
app.include_router(videos.router)
app.include_router(exports.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    fields = [".".join(map(str, e["loc"])) for e in exc.errors()]
    return JSONResponse(
        status_code=422, content={"detail": "参数格式或范围错误：" + "、".join(fields)}
    )


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("请求处理失败：%s", request.url.path)
    message = (
        "磁盘或文件操作失败，请检查路径、权限与磁盘空间"
        if isinstance(exc, OSError)
        else "服务器处理失败，请查看后端日志"
    )
    return JSONResponse(status_code=500, content={"detail": message})
