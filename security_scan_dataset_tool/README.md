# 安检扫描数据采集与人工标注工具

一个面向“安检员使用手持扫描仪进行全身扫描”场景的 Windows 桌面工具。界面为中文，包含数据采集和人工标注两个页面。

## 主要能力

- 输入 RTSP 地址并连接实时预览。
- 使用 OpenCV/FFmpeg 的 TCP 模式读取 RTSP；解码在后台线程进行。
- 最新帧队列长度固定为 1，网络积压时丢弃旧画面，避免预览延迟越来越大。
- 在实时画面上拖动鼠标绘制或重画工位 ROI，并将归一化 ROI 持久化。
- 一键开始/停止录制；每次录制生成一个独立短视频样本和一份元数据。
- 默认只保存 ROI 内的视频；也可保存完整画面并把绿色 ROI 框叠加到视频。
- 配置样本 ID、片段名称、录制帧率和数据目录。
- 加载已录视频或任意本地视频，播放、暂停、拖动和逐帧查看。
- 视频级标注左手、右手、腹部、左腿、右腿、背面、左脚、右脚、鞋子 9 个区域的 `scanned / not_scanned`。
- 时间段级标注每个已扫描区域对应的开始帧和结束帧；同一区域允许多个不重叠时间段。
- 标注样本 `valid / invalid`、备注，以及训练用的有效起止帧。
- 两级标签分别保存为 `labels.csv/json` 和 `events.csv/json`；CSV 使用 UTF-8 BOM，方便 Excel 打开。

## 运行环境

- Windows 10/11
- Python 3.10 或更高版本（推荐 3.11/3.12）
- 普通 CPU 即可；不需要 CUDA 或显卡

## 快速启动

首次运行，双击：

```text
run.bat
```

首次启动会自动调用安装程序，在 `%LOCALAPPDATA%\\SecurityScanDatasetTool\\venv` 创建独立环境、安装依赖并启动程序。这样也能避开 Windows 未启用长路径支持时 PySide6 安装失败的问题。

如果启动失败，黑色窗口不会立即消失，并会在项目目录生成 `startup.log`。请打开该日志查看错误，或者将日志内容发给开发人员。

也可以直接双击以下脚本重新安装依赖：

```text
setup_and_run.bat
```

也可在 PowerShell 中手动运行：

```powershell
python -m venv "$env:LOCALAPPDATA\SecurityScanDatasetTool\venv"
& "$env:LOCALAPPDATA\SecurityScanDatasetTool\venv\Scripts\python.exe" -m pip install -r requirements.txt
& "$env:LOCALAPPDATA\SecurityScanDatasetTool\venv\Scripts\python.exe" app.py
```

## 推荐操作流程

### 1. 连接摄像头

在“数据采集”页输入 RTSP 地址，点击“连接预览”。例如：

```text
rtsp://username:password@192.168.1.100:554/Streaming/Channels/101
```

RTSP 用户名或密码会保存在项目目录的 `config.json` 中以便下次恢复。若电脑多人共用，建议摄像头使用专门的低权限账号，并妥善控制项目目录访问权限。

每段样本元数据中的 `source_url` 会自动隐藏 RTSP 用户名和密码，只保留摄像头地址用于数据追溯。

### 2. 绘制工位 ROI

在预览画面上按住鼠标左键拖动。再次拖动会直接重画；“清除 / 重画”可清空。点击“保存 ROI”后，程序保存像素坐标和归一化坐标。归一化坐标能适应摄像头分辨率变化。

录制开始后 ROI 会被锁定，停止录制后恢复编辑，防止单个样本中途改变范围。

### 3. 录制短视频样本

填写“样本 ID”和“片段名称”，选择数据目录，然后点击“开始录制”。建议每段包含一次完整扫描流程，并额外保留约 1 秒前后缓冲。

推荐的采集组合：

- 完整且合规扫描：每种身高、体型、服装、站姿都要覆盖。
- 明确漏扫单个区域：九个区域分别采集足够的漏扫样本。
- 同时漏扫多个部位。
- 无效样本：人员中途离开、遮挡严重、多人闯入、扫描仪不可见、流程被打断。
- 不同扫描方向、速度、摄像机角度和光照。

停止后会保存视频和元数据。若编码速度暂时跟不上，程序只丢弃待编码帧而不阻塞 UI，丢帧数会写入元数据。

### 4. 两级人工标注

切换到“人工标注”页，双击左侧样本。也可以点击“打开其他视频”。

1. 播放、暂停或拖动时间轴检查整段动作。
2. 默认标注整段；需要排除片头片尾时，用当前帧设置有效起点和终点。
3. 选择 `valid / invalid`，并为九个扫描区域选择 `scanned / not_scanned`。
4. 对每个 `scanned` 区域：在播放器定位动作开始，点击“当前帧设为开始”；再定位动作结束，点击“当前帧设为结束”。
5. 选择对应区域，点击“添加时间段”。同一区域被重复扫描时可以添加多个不重叠时间段。
6. 双击时间段表格可跳回开始帧；标错后可选中并删除。
7. 填写可选备注并点击“保存两级标注”。

同一视频再次保存会更新原记录，不会重复追加。有效样本中，每个 `scanned` 区域必须至少有一个时间段，每个 `not_scanned` 区域不能存在时间段。

旧版 `labels.json` 可以继续读取。旧标注没有逐区域时间段，因此再次保存 `valid` 样本前，需要为所有 `scanned` 区域补充时间段；`invalid` 表示视频数据本身不适合可靠训练，不等同于安检不合规。

## 数据目录结构

```text
data/
├─ videos/
│  └─ S001_正常扫描_20260812_170000_123.mp4
├─ metadata/
│  └─ S001_正常扫描_20260812_170000_123.json
├─ labels.csv
├─ labels.json
├─ events.csv
└─ events.json
```

视频文件名中的 `clip_id` 是唯一片段标识；人工输入的 `sample_id` 允许按人员、批次或实验设计复用。

## 标签字段说明

核心字段如下：

| 字段 | 含义 |
|---|---|
| `annotation_id` / `clip_id` | 唯一片段标识，重新保存时用于更新 |
| `sample_id` / `clip_name` | 人工配置的样本 ID 和片段名称 |
| `video_path` / `metadata_path` | 相对数据目录的文件路径；外部视频使用绝对路径 |
| `segment_start_frame` / `segment_end_frame` | 进入时序模型的有效帧区间（包含两端） |
| `segment_start_sec` / `segment_end_sec` | 同一区间的秒数表示，结束秒采用右开区间 |
| `fps` / `frame_count` / `duration_sec` | 视频时序信息 |
| `roi_x/y/w/h` / `roi_normalized` | 原始画面中的 ROI |
| `crop_to_roi` | 视频是否已裁剪为 ROI |
| `validity` | `valid` 或 `invalid` |
| 九个区域字段 | `left_hand`、`right_hand`、`abdomen`、`left_leg`、`right_leg`、`back`、`left_foot`、`right_foot`、`shoes`，值为 `scanned` 或 `not_scanned` |
| `notes` | 遮挡、干扰、异常原因等备注 |

`labels.csv/json` 用于训练整段九区域覆盖分类模型。

`events.csv/json` 每行代表一个扫描时间段，核心字段包括：

| 字段 | 含义 |
|---|---|
| `event_id` | 时间段唯一标识 |
| `clip_id` / `video_path` | 所属视频片段 |
| `region` | 九个扫描区域之一 |
| `start_frame` / `end_frame` | 扫描动作起止帧，包含两端 |
| `start_sec` / `end_sec` | 起止秒数，结束秒采用右开区间 |
| `duration_frames` / `duration_sec` | 扫描时间段长度 |

这些事件可用于把视频切成小窗口，训练逐帧或短片段区域分类模型；整段标签则用于最终累计判断九个区域是否完成覆盖。

## 工程说明

### RTSP 与延迟

- 程序在导入 OpenCV 前设置 FFmpeg 选项，强制 `rtsp_transport=tcp`。
- 打开超时约 5 秒、读取超时约 3 秒；具体行为取决于 OpenCV 所带 FFmpeg 版本。
- 读取线程和界面线程分离，最新帧队列只保留 1 帧。
- 连接中断后会停止读取并在界面显示状态；当前版本不会无限自动重连，避免摄像头离线时后台持续占用资源，点击“连接预览”即可重连。

### CPU 与存储建议

- ROI 裁剪能显著减少视频体积和后续训练解码开销，建议保持默认开启。
- 录制使用兼容性较好的 `mp4v`。若需要更高压缩率，可在部署环境中改用 FFmpeg/H.264，但需额外处理编码器许可和安装。
- 录制帧率建议 15–25 FPS。动作轨迹通常不需要 30 FPS 以上。
- 若摄像头主码流分辨率很高，可改用摄像头子码流采集，前提是手持扫描仪仍清晰可见。
- 真实人员视频属于敏感数据；部署时应取得必要授权，并设置访问控制、留存期限和删除流程。

## 项目结构

```text
security_scan_dataset_tool/
├─ app.py
├─ requirements.txt
├─ run.bat
├─ setup_and_run.bat
├─ README.md
└─ security_scan_tool/
   ├─ __init__.py
   ├─ main_window.py
   ├─ capture.py
   ├─ capture_page.py
   ├─ video_widget.py
   ├─ annotation_page.py
   ├─ label_schema.py
   ├─ label_store.py
   └─ event_store.py
```

## 常见问题

**连接失败或几秒后断开**

先用 VLC 验证同一 RTSP 地址。检查摄像头是否允许 TCP、账号权限、防火墙和通道路径。某些摄像头有最大并发连接数限制。

**录制生成 0 帧**

等待预览稳定后再开始录制；确认 ROI 在画面范围内。错误原因也会写入对应元数据的 `writer_error`。

**视频无法播放**

本工具的标注播放器使用 OpenCV 解码。若第三方播放器不支持 `mp4v`，可使用 VLC，或用 FFmpeg 转码为 H.264；不要直接改文件扩展名。

**CSV 中文乱码**

程序使用 UTF-8 BOM 写入，现代 Excel 可直接打开。训练代码建议显式使用 `encoding="utf-8-sig"`。
