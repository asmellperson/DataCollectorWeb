# 从内网仓库部署 DataCollectorWeb

仓库地址：`http://172.16.8.79:3000/zhouyaoyao/DataCollectorWeb.git`，部署分支为 `main`。

以下步骤适用于 Linux 服务器，使用 Conda 管理 Python。服务器需要能够访问内网 Git 服务和依赖下载源；代码可以放在任意有写权限的目录。下面以 `~/DataCollectorWeb` 为例。

## 1. 准备系统依赖并拉取代码

Ubuntu / Debian 可执行以下安装命令；使用 root 登录时省略 `sudo`。其他发行版通过对应包管理器安装 Git、curl、FFmpeg 和 util-linux。

```bash
sudo apt-get update
sudo apt-get install -y git curl ffmpeg util-linux libgl1 libglib2.0-0

cd ~
git clone --branch main http://172.16.8.79:3000/zhouyaoyao/DataCollectorWeb.git
cd DataCollectorWeb
```

如果 Git 提示认证，使用内网代码平台的账号和密码或访问令牌。

## 2. 创建环境并安装依赖

先安装并初始化 Conda，再执行：

```bash
conda create -n data_collector_web python=3.11 pip -y
conda activate data_collector_web

# 已安装 Node.js 20+ 和 npm 时可以跳过这一行。
conda install -c conda-forge nodejs=22 -y

# CPU 运行方案，可先用它启动平台。
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r backend/requirements.txt

cd frontend
npm ci
cd ..
```

GPU 服务器需要安装与 NVIDIA 驱动匹配的 GPU 版 PyTorch，再安装 `backend/requirements.txt`，并在下一步设置 `DEVICE=cuda:0`。如果已有包含 GPU 版 PyTorch 的环境，可使用该环境安装项目依赖，通过 `DCW_PYTHON` 指定解释器；无需执行上面的 CPU 版安装命令。

## 3. 配置数据位置和千问服务

首次部署时复制配置模板：

```bash
cp config/example.env config/.env
```

编辑 `config/.env`，CPU 使用 `DEVICE=cpu`。默认数据集位于项目的 `datasets/`，状态索引位于 `config/`。需要把数据保存在 `/data` 时，创建当前运行用户有写权限的目录，并在配置中加入：

```dotenv
DEVICE=cpu
DATA_ROOT=/data/DataCollectorWeb/datasets
STATE_ROOT=/data/DataCollectorWeb/state
```

安检视频的原文件、标注、修订和训练清单位于 `${DATA_ROOT}/_security_inspection/`。启动时会自动创建所需目录。

使用“大模型自动初标”还需要一个独立运行的千问服务。在 `config/.env` 中取消相应示例项的注释，并设置：

```dotenv
INSPECTION_VLM_BASE_URL=http://千问服务IP:18100/v1
INSPECTION_VLM_MODEL=fuda-qwen38-27b
INSPECTION_VLM_TIMEOUT_SEC=120
INSPECTION_VLM_MAX_TOKENS=8192
```

把 `千问服务IP` 替换为新服务器能够访问的实际地址。只有模型服务也运行在新服务器本机时，才使用默认值 `http://127.0.0.1:18100/v1`；拉取项目不会启动或复制千问服务。需要认证时，通过 `config/.env` 或服务器环境变量设置 `INSPECTION_VLM_API_KEY`。

## 4. 按需准备检测模型

使用 YOLO-World 自动标注图片或检测采集时，下载默认权重：

```bash
mkdir -p models
curl -fL -o models/yolov8s-worldv2.pt \
  https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8s-worldv2.pt
```

首次使用 YOLO-World 还可能下载 CLIP 文本编码权重；也可以从原服务器单独迁移模型权重和缓存。仅使用安检视频工作区时，其大模型初标连接上一步配置的千问服务。

## 5. 检查并后台启动

```bash
bash start.sh --check
bash start.sh
bash start.sh status
curl --fail http://127.0.0.1:5180/api/health
```

浏览器访问 `http://新服务器IP:5180/`。前端通过同源 `/api` 代理连接后端；后端监听 `8000`，API 文档位于 `http://新服务器IP:8000/docs`。确保客户端能访问新服务器的 `5180` 端口。

脚本会构建前端并在后台启动服务，关闭 SSH 后继续运行。服务器重启后需要重新启动。管理命令：

```bash
bash start.sh logs
bash start.sh restart
bash start.sh stop
```

`logs` 中的 Ctrl+C 只退出日志查看。如果没有使用名为 `data_collector_web` 的 Conda 环境，启动和重启时指定实际 Python 解释器，例如：

```bash
DCW_PYTHON=/实际环境路径/bin/python bash start.sh
```

## 后续更新

在新服务器的项目目录中执行：

```bash
conda activate data_collector_web
git pull --ff-only origin main
python -m pip install -r backend/requirements.txt
cd frontend
npm ci
cd ..
bash start.sh restart
```

新服务器克隆后，`origin` 就是这个内网仓库。原开发服务器上的内网远程名为 `intranet`，提交新代码后使用 `git push intranet main` 同步。

## 代码与已有数据

Git 同步源代码、配置模板和文档。现有数据集、原视频、标注、SQLite 索引、模型权重和 `config/.env` 需要单独迁移或配置。复制已有数据前按 README 的备份说明停止相关写入，并同时保存数据目录与状态索引。

安检训练清单包含视频与时间索引的绝对路径；迁移后应保持这些路径可用，或在目标服务器重新生成清单。
