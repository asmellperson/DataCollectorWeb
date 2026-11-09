# SCRFD 人脸框与五点标注

本功能仅覆盖采集、预标注、人工审核和数据导出，不包含模型训练。继续使用现有 `detection` 任务、样本库、任务队列和审核流程；不新增独立任务类型。

## 部署配置

实际模型位于**部署服务器**，开发环境没有该文件是正常情况，不需要复制或重新下载权重。

在部署服务器的项目 `config/.env` 增加（或设置同名进程环境变量；进程环境变量优先）：

```dotenv
SCRFD_MODEL_PATH=/srv/models/insightface/buffalo_l/det_10g.onnx
```

在项目所用 Python 环境安装 ONNX Runtime：

```bash
conda activate data_collector_web
python -m pip install 'onnxruntime>=1.20,<2'
```

`backend/requirements.txt` 已声明该依赖。SCRFD 使用 CPUExecutionProvider，不依赖 YOLO 的 `DEVICE`，也不需要安装 InsightFace Python 包。权重及 ONNX Runtime 均延迟到首次预标请求时加载，不在启动时加载，不执行权重下载。已有 YOLO-World、自定义 YOLO、图片导入、视频抽帧、RTSP、审核和 YOLO 导出路径保持可用。

路径不存在时，`GET /api/models` 的 `scrfd.available` 为 `false`，前端显示部署验证提示。请求 SCRFD 预标会返回 503，说明当前服务器未配置模型；仍可关闭自动标注后导入图片、手工标注和导出。不能用此开发环境的模型缺失判定功能开发失败。

## 使用方法

1. 新建数据集，任务类型选择“目标检测”，标注模式选择“人脸框 + 五点（SCRFD）”。类别固定为 `0 face`。模式创建后保持不变，原数据集仍默认使用目标框模式。
2. 导入图片时选择 SCRFD，或先关闭预标导入，再在审核页“预标当前图片 / 批量预标待审核”。视频抽帧与 RTSP 保存后自动标注同样可选择 SCRFD。
3. RTSP 触发检测仍使用现有 YOLO-World / 自定义 YOLO，保存后的 SCRFD 标注独立运行。固定间隔和 ROI 采集可直接使用 SCRFD。ROI 裁剪会平移框和关键点，裁剪区域外的点改为缺失。
4. 审核页用 `N` 新增人脸框，拖动或缩放框时点同步变换；选择框后，在右侧为某个点点击“放置 / 重置”，再点击画布。已有点可以直接拖动；“删除点”将该点标为缺失，不会编造默认点。`Esc` 退出放置。
5. 五点顺序为**画面左侧眼、画面右侧眼、鼻尖、画面左侧嘴角、画面右侧嘴角**，与 SCRFD 输出顺序一致（不是人物自身左右）。画布及右侧控件显示点编号与颜色，缩略图和带标注预览也使用对应颜色。
6. 删除框同步删除其五点。对后脑勺、空场景等非人脸图，删除最后一个框或点击“清空人脸（负样本）”，然后“确认并下一张”。空标注是有效负样本，可正常通过审核。人工保存过的空标注不会被默认批量预标重新填框；确需重标时明确勾选覆盖。
7. 每次修改沿用已有自动保存、修订冲突保护、失败重试和撤销。导出默认仅含已确认样本，默认保留零目标图片；可选择包含待审核或取消保留空目标图片。

## 数据兼容与接口

`dataset.json` 增加 `annotation_mode: "bbox" | "face_5pt"`；旧 JSON 未包含该字段时按 `bbox` 读取。数据库表无需迁移，`task_type` 仍为 `detection`。face_5pt 的类别只能是 `[{"id": 0, "name": "face"}]`。

在原对象上增加可选 `keypoints`，坐标始终是**原图像素坐标**。缺失点用 `null`；face_5pt 的旧式 bbox 对象未提供关键点时按五个缺失点处理。普通 bbox 对象不强制增加该字段。

```json
{
  "id": "obj_face_1",
  "class_id": 0,
  "class_name": "face",
  "bbox": [10, 20, 90, 120],
  "confidence": 0.95,
  "source": "scrfd",
  "keypoints": [[30, 50], [70, 50], [50, 75], null, [65, 100]]
}
```

`bbox` 为 `[x1,y1,x2,y2]`。每个点为 `[x,y]` 或 `null`，固定五个槽位；拒绝非有限值、越界点和错误长度。手工修改将该对象来源改为 `manual` 并清除过期置信度。负样本的 `objects` 为 `[]`，无独立“背景”类别和虚构人脸。

预标接口沿用 `POST /api/datasets/{id}/auto-label`，设置 `provider: "scrfd"`，`sample_ids` 为单图 ID 列表或 `null`（批量），`overwrite` 默认为 `false`。模型路径只取服务器 `SCRFD_MODEL_PATH`，不需要前端填写。SCRFD 与 face_5pt 必须匹配，不能将普通 YOLO 误用于人脸五点预标。

导出接口为 `POST /api/datasets/{id}/export/scrfd`；通用 `/export` 对 face_5pt 也会选择 SCRFD 格式。沿用原有导出选项、任务进度、取消和 ZIP 下载接口。

## labelv2.txt 格式

格式依据 [InsightFace 的 RetinaFaceDataset 读取实现](https://github.com/deepinsight/insightface/blob/master/detection/scrfd/mmdet/datasets/retinaface.py)。每张图片头行为 `# 相对图片路径 宽 高`，之后每个人脸一行，共 19 个数：四个 `x1 y1 x2 y2`，接五组 `x y flag`。有效点的 flag 写 `1`，缺失点整组三元组写 `-1 -1 -1`。不写人脸数量行、不写置信度、不把 bbox 转为 xywh。

```text
# positive.jpg 640 480
10 20 90 120 30 50 1 70 50 1 50 75 1 -1 -1 -1 65 100 1
# negative.jpg 640 480
```

零目标图片只有头行，下一行直接是下一张图片的头行或文件结束。不写空白对象行或占位框。对应原图总会包含在 ZIP 内。原始置信度和完整标注保留在 `annotations/*.json`。

未划分：

```text
export/
  images/<sample-id>.jpg
  labelv2.txt
  annotations/<sample-id>.json
  dataset.json
  manifest.json
  SCRFD_README.txt
```

这时 `img_prefix` 为 `export/images/`。若选择 train/val 或 train/val/test，布局为 `<split>/images/` 和 `<split>/labelv2.txt`，各分组 labelv2 中的图片名相对该分组 `images/`。根目录另有汇总 `labelv2.txt`，其中路径为 `train/images/<id>.jpg` 等，相对导出根目录；图片不重复复制。划分、随机种子和图片清单仍在 manifest 中。

**后续训练的衔接注意事项**：官方 `RetinaFaceDataset.load_annotations` 在训练模式下会过滤零目标图片。此导出会如实保留负样本图片和图片头；未来训练阶段若需使用负样本，应修改或选择保留空目标图片的训练读取器。该训练适配不在本阶段实现。

## 验证

开发环境测试不依赖真实权重、GPU 或 ONNX Runtime；使用模拟 ONNX 输出验证 RGB 归一化、head 解码、缩放还原、阈值和 NMS，并用临时数据库覆盖预标、导入、ROI、修订保护、人工修正、负样本审核及 ZIP 导出。

```bash
cd backend
python -m unittest discover -s tests -p 'test_face_5pt.py' -v
cd ../frontend
npm test
npm run build
```

实际部署服务器验收：

1. 设置环境变量、安装依赖后重启应用：在项目根目录执行 `bash start.sh restart`。检查运行服务的用户可读取 `/srv/models/insightface/buffalo_l/det_10g.onnx`，检查 `/api/models` 返回配置路径和 `available: true`。
2. 新建 face_5pt 数据集，导入正脸、侧脸、遮挡、多人、小人脸、后脑勺和无人场景图片。运行“预标当前图片”和“批量预标待审核”，检查任务统计及框、置信度和五点；特别核对横图、竖图的缩放和点顺序。这一步才是**真实 det_10g.onnx 推理验收**。
3. 新增一个框，逐一放置五点，拖动点和框、缩放框，删除一个点、撤销并重新打开图片，确认保存一致。删除最后一个框后确认通过审核，重新打开检查 `objects: []`。
4. 分别导入短视频和固定间隔 RTSP 帧，选择 SCRFD；再测试 ROI 裁剪，确认点坐标相对裁剪后的图片，范围外为缺失。需要负样本时使用固定间隔采集；触发采集本身会过滤无触发目标的帧。
5. 分别整份导出和划分导出，解压 ZIP，检查上述 labelv2 路径可定位图片、尺寸正确、人脸每行 19 列、缺失点为完整 `-1 -1 -1`、负样本只有头行。用“保留空目标图片”选项对比导出图片数。
6. 使用现有普通 detection 数据集回归 YOLO-World、自定义 YOLO、图片与 YOLO 标签导入、视频抽帧、RTSP、人工审核和 YOLO 导出。

推理预处理与三层 head 解码参考 [InsightFace SCRFD 官方实现](https://github.com/deepinsight/insightface/blob/v0.7/python-package/insightface/model_zoo/scrfd.py)。本地推理没有触发模型下载的代码。

## 修改文件

| 范围 | 文件（相对项目根目录） |
| --- | --- |
| 模型与配置 | `backend/app/inference/scrfd.py`（新增）、`backend/app/inference/manager.py`、`backend/app/core/config.py`、`backend/requirements.txt`、`config/example.env` |
| 数据契约与存储 | `backend/app/schemas/contracts.py`、`backend/app/services/dataset_service.py`、`backend/app/services/annotation_service.py` |
| 采集与导出 | `backend/app/services/collection_service.py`、`backend/app/services/export_service.py`、`backend/app/api/models.py`、`backend/app/api/exports.py` |
| 前端类型与保存 | `frontend/src/types/index.ts`、`frontend/src/hooks/useImageImport.ts`、`frontend/src/services/annotationSave.ts`、`frontend/src/services/face5pt.ts`（新增） |
| 画布与界面 | `frontend/src/components/AnnotationCanvas.tsx`、`AutoLabel.tsx`、`ExportDialog.tsx`、`ImageImport.tsx`、`ScrfdInfo.tsx`（新增），后五者同在 `frontend/src/components/` |
| 页面与样式 | `frontend/src/pages/Datasets.tsx`、`Review.tsx`、`Collection.tsx`（同在 pages 目录）、`frontend/src/styles.css` |
| 测试与说明 | `backend/tests/test_face_5pt.py`（新增）、`frontend/tests/face-5pt.test.mjs`（新增）、`frontend/tests/annotation-save.test.mjs`、本文档（新增） |

开发环境验证结果：后端全部模块回归及新增检查共 125 项通过，前端 43 项测试通过，TypeScript 与 Vite 生产构建通过，Ruff 与 `git diff --check` 通过。另使用临时数据库、模拟检测器和无头 Chromium 实际验证新建 face_5pt、预标、拖动/放置/删除点、移动/缩放框、新增/删除框、零目标确认和导出，页面无运行错误。真实权重未在当前开发环境加载，须按上面的部署步骤验收。
