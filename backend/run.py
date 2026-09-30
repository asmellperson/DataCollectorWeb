import uvicorn

if __name__ == "__main__":
    # 模型缓存、采集线程和文件锁由单进程统一管理，不能开启多 worker。
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1)
