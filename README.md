# DataCollector Web

面向计算机视觉训练的数据采集、自动预标、人工审核和数据集导出平台。支持 `raw`、`classification`、`detection` 三类数据集。图片目录、视频和 RTSP 采集结果统一进入 JSON 标注与审核流程。

## 功能

- 数据集创建、改名、类别管理、统计和删除；类别 ID 保持稳定，已删除的 ID 不复用，被标注引用的类别不可删除。
- 浏览器整目录上传（单次最多 10000 个文件）和服务器目录递归导入。支持 JPG、JPEG、PNG、BMP、WebP。图片复制到数据集内，不引用外部图片路径。
- 上传 MP4、AVI、MKV 或填写服务器视频路径，按每 N 帧或每 N 秒抽帧，可同时自动预标。
- 摄像头增删改、连接测试、实时预览；FFmpeg、TCP 优先、低缓冲、读流超时和断流重连。
- 四种采集模式：`raw_interval`、`roi_interval`、`detect_raw`、`detect_crop`；pHash 比较最近 128 张已保存图片，支持间隔和相似度配置。
- YOLO-World、自定义 YOLO；缓存模型，跨任务串行设置类别和推理，避免不同数据集类别串用。
- react-konva 原图画布：新增、选择、拖动、缩放、删除框、改类别、撤销；JSON 和带框预览同步保存。
- 图像分类使用类别按钮；raw 不需要标签。三种任务均可确认、拒绝、删除样本和导出。
- 图片导入、预标、视频、导出和连接测试均提供后台任务状态和取消操作。

## 架构与目录

```text
React / TypeScript / Vite
        ↓ /api（Vite 同源代理）
FastAPI → 服务层 → SQLite 索引 + 文件系统
                ↘ 任务线程池
                ↘ 摄像头读流线程 → 模型管理器 → 样本保存

DataCollectorWeb/
├── backend/
│   ├── app/
│   │   ├── api/          数据集、标注、摄像头、采集、模型、视频、任务、导出
│   │   ├── services/     数据持久化、后台任务和采集业务
│   │   ├── inference/    Detector、YOLO-World、自定义 YOLO、模型缓存
│   │   ├── db/           SQLAlchemy / SQLite
│   │   ├── schemas/      请求参数和标注校验
│   │   ├── core/         配置、数据集锁、原子写入
│   │   └── main.py
│   ├── requirements.txt
│   └── run.py
├── frontend/src/         四个页面、Konva 画布、ROI、API 与任务组件
├── datasets/
├── models/               模型权重与 CLIP 缓存
├── config/               index.sqlite3、配置和上传临时目录
├── start.sh              一键启动前后端
└── README.md
```

仅使用一个后端进程（`workers=1`）。摄像头读取与模型推理解耦；同一摄像头的预览和采集复用最新帧。第一版同时运行一项 RTSP 采集，同一数据集的批处理串行，其他数据集可以同时处理。

每个数据集：

```text
datasets/{id}/
├── dataset.json
├── images/               无框训练原图
├── annotations/          每张图片的权威 JSON
├── previews/             独立带框预览
└── exports/              导出目录与 ZIP
```

SQLite 只保存数据集、样本、摄像头、任务与状态索引，不保存图片二进制或完整标注。bbox 为原始图片像素坐标 `[x1,y1,x2,y2]`；裁剪图的框转换到裁剪图坐标，来源记录原图尺寸和裁剪区域。标注记录置信度及 `manual / yolo_world / custom_yolo` 来源。分类保存在 `classification.class_id/class_name`。

## 环境创建和依赖安装

需要 Python 3.11、Node.js 20+、npm。当前服务器环境名称：`data_collector_web`。

```bash
conda create -n data_collector_web python=3.11 pip -y
conda activate data_collector_web
cd /usr/zyy/DataCollectorWeb/backend

# 当前服务器使用 CPU；先安装 CPU 版 PyTorch，避免下载不需要的 CUDA 包。
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

cd ../frontend
npm install
```

GPU 主机需先安装与驱动相适配的 PyTorch。将 `config/example.env` 复制为 `config/.env`，设置：

```dotenv
DEVICE=cuda:0
```

CPU 使用 `DEVICE=cpu`，也是默认值。环境变量优先于 `config/.env`。支持 `WORLD_MODEL` 指定 YOLO-World 权重，`DATA_ROOT` 和 `STATE_ROOT` 指定新项目数据和索引路径。不要把它们设置到旧项目目录。

## 模型权重

默认 YOLO-World：`models/yolov8s-worldv2.pt`。当前服务器已下载该权重和 CLIP ViT-B/32 文本编码权重；另提供 models/yolov8n.pt 作为自定义 YOLO 选择示例。

在其他服务器安装时：

```bash
cd /usr/zyy/DataCollectorWeb
curl -fL -o models/yolov8s-worldv2.pt \
  https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8s-worldv2.pt
```

YOLO-World 使用 Ultralytics 官方接口，类别来自目标数据集，建议使用清晰的英文目标名称，例如 `person`、`helmet`、`forklift`。首次设置类别需要 CLIP，依赖已包含在 requirements 中；首次运行可能需要联网下载文本权重。模型每个 provider/路径仅加载一次，类别变化通过 `set_classes()` 更新。

参考：[YOLO-World 官方文档](https://docs.ultralytics.com/models/yolo-world/)。

## 启动

一条命令在后台启动前后端，无需手动激活 conda 环境：

```bash
bash /usr/zyy/DataCollectorWeb/start.sh
```

脚本自动查找 `data_collector_web` 环境，检查依赖和端口，等待前后端就绪后返回命令行。**可以关闭终端、断开 SSH 或关闭 VS Code，服务继续运行。** 后台进程使用独立会话，日志写入文件。

管理命令：

```bash
bash /usr/zyy/DataCollectorWeb/start.sh status   # 查看进程和健康状态
bash /usr/zyy/DataCollectorWeb/start.sh stop     # 停止前后端
bash /usr/zyy/DataCollectorWeb/start.sh restart  # 重启并加载代码更新
bash /usr/zyy/DataCollectorWeb/start.sh logs     # 持续查看日志，Ctrl+C 只退出日志查看
```

直接执行 `start.sh` 等同于 `start.sh start`。重复启动只显示现有实例；启停命令通过文件锁串行执行，并核验进程身份，避免过期 PID 导致误停其他进程。停止会等待当前任务退出，最多等待 45 秒后提示检查状态，不强制打断文件写入。如果某个服务异常退出，后台守护进程会停止另一个服务，查看日志后使用 `restart` 恢复。依赖首次安装请参照上面的安装步骤；脚本不会自动安装依赖。

日志保存在 `config/logs/backend.log`、`config/logs/frontend.log` 和 `config/logs/supervisor.log`，按启动次数追加。也可以直接查看：

```bash
tail -f /usr/zyy/DataCollectorWeb/config/logs/backend.log /usr/zyy/DataCollectorWeb/config/logs/frontend.log
```

只检查环境、不启动服务：`bash /usr/zyy/DataCollectorWeb/start.sh --check`。若 conda 环境安装在自定义路径，可通过 `DCW_PYTHON=/绝对路径/bin/python bash /usr/zyy/DataCollectorWeb/start.sh` 指定解释器。若端口被其他方式启动的进程占用，脚本会报错退出，不会自动结束其他进程。升级前已运行的旧版前台脚本需先在原终端按 Ctrl+C 停止，再使用新脚本启动。

后端监听 `0.0.0.0:8000`。健康检查：`http://服务器IP:8000/api/health`；API 文档：`http://服务器IP:8000/docs`。

前端配置端口为 `5180`，避免占用服务器其他应用的 5173/5174。当前服务器访问地址：**http://172.16.19.215:5180/**。浏览器请求通过 Vite `/api` 代理转发到后端 8000，无需配置跨域。

构建检查：

```bash
cd /usr/zyy/DataCollectorWeb/frontend
npm run build
```

平台当前面向可信内网使用，未实现登录、多用户权限或公网部署。后台启动不包含开机自启；服务器重启后，再次执行 `start.sh`。

## 创建数据集

在“数据集”页点“新建数据集”，选择任务类型。detection 和 classification 至少提供一个类别，每行 `ID 名称`：

```text
0 person
1 helmet
2 forklift
```

raw 不填类别。ID 不会按名称重排；新增类别请使用未用过的 ID。设置中可改名、增加类别、删除未被引用的类别。数据集删除有二次确认，不能删除正在执行任务的数据集。

新建弹窗提供 **“导入本地图片目录（可选）”**：

- 不选择目录，点击“创建数据集”，得到空数据集。
- 点击“选择本地图片目录”，显示目录名、图片数和大小；点击 **“创建并导入”**，先创建数据集，再自动上传和保存图片，无需跳转到采集页。
- 同一个弹窗持续显示 **“已导入 X / N 张”**、处理进度条、上传进度以及成功、失败、剩余张数。完成后可关闭或直接进入标注审核。停止导入会保留已创建的数据集和已保存图片。
- detection 数据集选好目录后，还可选择导入时使用 YOLO-World 或自定义 YOLO 自动标注。

## 图片目录导入

图片目录导入统一放在 **“数据集”** 页面。新数据集在创建时选择本地目录；已有数据集点击对应行的 **“导入图片”** 补充图片。“数据采集”页面只提供视频抽帧和 RTSP 采集。

- 本地目录：点击“选择图片目录”，先显示图片数、总大小和自适应预览；选好设置后再点击“开始导入”。保留相对路径作为来源信息，大目录按最多 50 张或约 32 MB 分批上传（超出上限的单张图片单独一批）。
- 服务器目录：点击“服务器图片目录”，填写如 `/data/images/test`，递归扫描在后台执行，不经过浏览器传输。
- detection 数据集：在 **“导入时自动标注”** 选择 **“YOLO-World 自动标注”** 或自定义 YOLO。每张图会完成推理、原图保存和 JSON 标注后更新处理进度，结果进入待审核。
- 页面分别显示当前批次的上传字节进度和全部图片的处理进度、成功与失败数。首次使用检测器需等待模型加载；进度不会模拟增长。保持导入页面打开，点击“停止导入”可取消后续上传和当前后台任务，已保存图片保留。
- 预览自适应窗口，原始训练图片不压缩、不画框。审核列表按需生成最大 320 × 240 的缩略图，编辑画布仍加载原图。

文件传输完成后返回任务 ID，解码和入库不阻塞 HTTP。损坏图片单独计入失败，其他图片继续处理。`GET /api/tasks/{task_id}` 返回状态、总量、已处理数、成功数和失败数。取消保留已成功导入的样本。服务重启后，未完成任务标记为失败，可以重新提交；当前不自动断点续传。

## 视频导入

“数据采集”中选择“视频抽帧”，上传文件或填写服务器视频文件路径。选择“每 N 帧”或“每 N 秒”，点击“开始抽帧导入”。从首帧开始采样，原视频不会修改。

detection 可选预标检测器。浏览器不支持的 AVI/MKV 编码可能无法直接预览，但后端仍可抽帧。按秒抽样需要有效视频帧率/时间戳；时间参数保存在每个样本的来源字段中。

## RTSP 采集

1. 在“摄像头”页添加名称、RTSP 地址、TCP/UDP，执行“连接测试”。
2. 在“数据采集”中选择目标数据集和 RTSP 摄像头，等实时画面连接。
3. 选择四种采集模式之一。ROI 模式先在画面上拖动绘制，可重画或清除。
4. 设置保存间隔、预标检测器、置信度和去重阈值，点击“开始采集”。
5. 点击“停止”后，任务在当前推理或文件写入结束时停止。切换页面不终止后台采集。

去重条件：达到保存检查间隔、满足采集条件、pHash 与近期样本相似度低于阈值。相似度取 `1 - 汉明距离 / 64`，默认 `0.92`，越小越严格。重复计数只统计实际检查后跳过的候选图片。近期窗口跨启动从当前数据集最近 128 张图片恢复。

实时检测只用于预览和采集判断；训练原图始终不画框。预标关闭时，目标触发仍可以采集无标签图片。raw/classification 的目标触发可单独填写目标名称，例如 `person`；不会把检测框写成分类标签。

底部显示实际连接状态、摄像头读取 FPS、本次保存数、预标处理数和重复跳过数。浏览器预览有独立刷新频率，不等同于摄像头 FPS。读流断开后会清空有效帧并重连。

## 自动预标与人工审核

“标注审核”右侧展开“自动预标”，选择 YOLO-World 或自定义 YOLO，可处理当前图片或全部待审核图片。默认只处理 `pending`，保留已有标注。需要重做预标时可先删除原有框；API 支持显式 `overwrite=true`。不会覆盖推理期间发生的人工修改。

画布使用原图。绘框、拖动、缩放、改类别和删除框都会立即保存 JSON、重新生成 preview，并将样本置为 pending。手工修改的框来源为 manual、置信度为空。最多撤销当前图片最近 50 次修改；切换图片清空撤销历史。

| 按键 | 操作 |
|---|---|
| A / ← | 上一张 |
| D / → | 下一张 |
| Enter | 确认并下一张 |
| Delete | 删除选中的框，保留图片 |
| N | 新增框 |
| 1–9 | 按类别按钮顺序切换类别，不改变类别 ID |
| Ctrl+Z / Cmd+Z | 撤销 |
| Esc | 取消绘制或选择 |

输入框、下拉框、文本框获得焦点时不触发这些快捷键。

classification 显示大图与类别按钮，选择类别后确认；未选择类别不能确认。raw 只浏览、管理、审核、删除和导出，无需框或分类标签。

“删除框”只修改标注。“删除样本”需二次确认，同步删除原图、JSON、preview、SQLite 记录并清理该数据集的已有导出快照，避免已删除的样本继续留在导出副本中。正在执行导入、预标、采集或导出时先停止任务再删除样本。

## 自定义 YOLO

在检测器中选择“自定义 YOLO”，填写服务器 `.pt` 路径，或选择 models 目录的候选文件。点击“读取模型类别”查看原始类别名称。只支持 Ultralytics 目标检测模型。

模型类别按**名称精确匹配**到数据集类别 ID，不覆盖模型原始 names，也不把模型的类别序号直接当作数据集 ID。例如模型 `0 person` 可映射到数据集 `9 person`。没有同名类别时返回中文错误；不在数据集中的模型类别不会保存。请先添加你需要的模型类别名称。

模型文件只读取，不改写，可用于目标触发、目标裁剪、视频预标、图片预标和实时预标。前端无需设置 GPU 编号。

## 数据导出

数据集页点“导出”。默认只导出 approved，不包含 rejected；可明确勾选“包含待审核”。检测、分类和 raw 数据集均支持三种方式：

- **不划分（整份导出）**：全部图片导出到同一份目录，至少需要 1 张图片。
- **训练集 + 验证集**：默认 80% / 20%，至少需要 2 张图片。
- **训练集 + 验证集 + 测试集**：默认 70% / 20% / 10%，至少需要 3 张图片。

各组比例可独立修改，支持小数，合计必须为 100%，每组大于 0%。弹窗按当前审核状态显示预计导出总数和每组张数；数量不足时会提示。生成后下载 ZIP，也可直接使用 exports 下的目录。

目标检测导出：

```text
exports/yolo_时间_编号/
├── images/train/、images/val/、images/test/   test 仅在三份划分时生成
├── labels/train/、labels/val/、labels/test/
├── data.yaml
├── annotations/           JSON 快照
├── dataset.json
└── manifest.json          样本、划分、类别及来源配置
```

标签为 `class_id x_center y_center width height`，坐标归一化到 0–1。空目标图片生成空 txt。按图片随机划分，使用固定种子 42，API 可修改 seed；样本不变、比例和种子相同时划分可重复。按最大余数分配取整差额，并确保启用的分组至少有一张图片，各组不重叠、不遗漏。manifest 记录划分方式、请求比例、实际张数及每张图片归属。

类别 ID 有空缺时，data.yaml 用 `__unused_class_ID` 保留空位，现有 ID 不重编号。划分导出的 data.yaml 包含 train、val 和可选 test 路径，可由 Ultralytics 直接读取：

```bash
yolo detect train model=yolov8n.pt data=/绝对路径/exports/yolo_xxx/data.yaml
```

不划分时，检测导出到 `images/`、`labels/`，data.yaml 的 train 指向 `images`，val 留空；用于训练前需另行配置独立验证集。分类整份导出到 `images/类别ID_类别名/`，划分时使用 `train/类别ID_类别名/`、`val/类别ID_类别名/` 及可选 `test/类别ID_类别名/`。raw 导出到 `images/` 或 `images/train/`、`images/val/`、`images/test/`。所有方式均附统一 JSON 快照，ZIP 与解压目录一致。

API：`POST /api/datasets/{id}/export/preview` 预览张数，`POST /api/datasets/{id}/export/yolo`（检测）或 `/export` 生成后台任务。参数 `split_mode` 为 `none`、`train_val`、`train_val_test`；划分比例用 0–1 数值。例如三份划分：`{"split_mode":"train_val_test","train_ratio":0.7,"val_ratio":0.2,"test_ratio":0.1}`。不传 split_mode 时沿用默认训练/验证划分；none 忽略比例。

## 常见问题

- **页面显示服务未连接**：确认后端 8000 在运行，前端通过 5180 访问；健康接口应返回 `status=ok`。修改端口时同时修改 `frontend/vite.config.ts` 中的代理。
- **RTSP 无法连接或断流**：检查地址、账号、服务器到摄像头的网络和 RTSP 端口；默认 TCP；连接/读取超时约 5 秒，失败后约 2 秒重连。预览停止访问 90 秒后自动释放空闲读流。
- **CUDA 异常**：检查 `nvidia-smi` 和 PyTorch CUDA 支持；当前服务器驱动不可用时使用 `DEVICE=cpu`。切换设备后重启后端。
- **YOLO-World 加载失败**：检查权重存在、CLIP 依赖安装和首次下载网络；查看后台任务错误与后端日志。离线服务器需预先准备两个权重。
- **模型检测结果不符合预期**：预标是待审核建议；调整英文提示词与置信度，再人工确认。业务专用类别可使用自定义检测模型。
- **类别配置错误**：ID 与名称必须唯一；对象类别必须存在；raw 不允许类别，classification 不允许 bbox。
- **图片/视频读取失败**：检查文件内容、扩展名及编码；失败原因记录在任务中。视频中途解码中断会使任务失败，已保存样本保留。
- **图片或预览保存失败**：检查 datasets、config 的权限和剩余空间；文件通过临时文件 + 原子替换保存，异常会写后端日志。
- **并发修改冲突**：重新打开图片获取最新标注版本。不要启用多个 Uvicorn worker 或同时运行两个后端操作同一数据目录。
- **数据库/文件备份**：停止后端后一起备份 datasets 和 config/index.sqlite3；运行中备份应使用 SQLite backup API 或完整快照。
- **任务完成但有失败计数**：检查任务最后一条错误。损坏图片不影响其他图片处理；已有标注默认跳过并在任务里给出原因。
