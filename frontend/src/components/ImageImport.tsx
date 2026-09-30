import { useEffect, useRef, useState } from "react";
import type { Dataset } from "../types";
import { api, errorText, formatBytes } from "../services/api";
import ModelPicker from "./ModelPicker";
import {
  useImageImport,
  imageFiles,
  type ImportOptions,
} from "../hooks/useImageImport";

export default function ImageImport({
  dataset,
  reload,
  notify,
  onReview,
  onBusyChange,
}: {
  dataset: Dataset;
  reload: () => Promise<void>;
  notify: (message: string) => void;
  onReview: () => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [location, setLocation] = useState("local");
  const [files, setFiles] = useState<File[]>([]);
  const [skipped, setSkipped] = useState(0);
  const [path, setPath] = useState("");
  const [provider, setProvider] = useState<ImportOptions["auto_label"]>("off");
  const [modelPath, setModelPath] = useState("");
  const [confidence, setConfidence] = useState(0.25);
  const [preview, setPreview] = useState("");
  const [previewName, setPreviewName] = useState("");
  const importer = useImageImport();
  const { busy, counts } = importer;
  const input = useRef<HTMLInputElement>(null);
  const autoLabel = dataset.task_type === "detection" && provider !== "off";
  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  useEffect(
    () => () => {
      if (preview.startsWith("blob:")) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  function selectFiles(list: FileList | null) {
    const selected = imageFiles(list);
    setFiles(selected);
    setSkipped((list?.length || 0) - selected.length);
    importer.reset();
    setPreview(selected.length ? URL.createObjectURL(selected[0]) : "");
    setPreviewName(selected[0]?.webkitRelativePath || selected[0]?.name || "");
    if (list?.length && !selected.length) notify("目录中没有支持的图片");
  }

  async function start() {
    const result = await importer.runImport(
      dataset,
      location === "local" ? { files } : { path },
      {
        auto_label: autoLabel ? provider : "off",
        model_path: modelPath || null,
        confidence,
      },
    );
    try {
      await reload();
      if (result.lastSample) {
        const sample = await api(
          `/datasets/${dataset.id}/samples/${result.lastSample}`,
        );
        setPreview(sample.preview_url);
        setPreviewName(
          autoLabel ? "自动标注结果预览 · 等待人工审核" : "导入结果预览",
        );
      }
    } catch (error) {
      notify(errorText(error));
    }
  }

  return (
    <div className="collection-layout image-import-layout">
      <section className="panel import-main">
        <h2>图片目录导入与自动标注</h2>
        <p>选择目录 → 设置自动标注 → 开始导入 → 人工审核</p>
        <fieldset disabled={busy}>
          <div className="source-tabs" role="group" aria-label="图片目录位置">
            <button
              className={location === "local" ? "primary" : ""}
              onClick={() => setLocation("local")}
            >
              本地图片目录
            </button>
            <button
              className={location === "server" ? "primary" : ""}
              onClick={() => setLocation("server")}
            >
              服务器图片目录
            </button>
          </div>
          {location === "local" ? (
            <div className="directory-picker">
              <input
                ref={input}
                className="file-input-hidden"
                type="file"
                multiple
                {...{ webkitdirectory: "" }}
                aria-label="选择图片目录"
                onChange={(event) => selectFiles(event.target.files)}
              />
              <button
                className="directory-button"
                onClick={() => input.current?.click()}
              >
                ＋ {files.length ? "重新选择图片目录" : "选择图片目录"}
              </button>
              <div>
                <strong>
                  {files.length
                    ? `${files.length} 张图片 · ${formatBytes(files.reduce((sum, file) => sum + file.size, 0))}`
                    : "选择电脑中的整个图片文件夹"}
                </strong>
                <p>
                  {files[0]?.webkitRelativePath.split("/")[0] ||
                    "支持 JPG、JPEG、PNG、BMP、WebP，包含子目录"}
                  {skipped > 0 && ` · 已忽略 ${skipped} 个非图片文件`}
                </p>
              </div>
            </div>
          ) : (
            <label>
              服务器图片目录
              <input
                placeholder="/data/images/test"
                value={path}
                onChange={(event) => setPath(event.target.value)}
              />
              <small>直接从服务器复制，无需经过浏览器上传。</small>
            </label>
          )}
        </fieldset>
        <div className="import-actions actions">
          <button
            className="primary"
            disabled={
              busy ||
              (location === "local" ? !files.length : !path.trim()) ||
              (autoLabel && provider === "custom_yolo" && !modelPath.trim())
            }
            onClick={() => void start()}
          >
            {busy
              ? "正在处理…"
              : autoLabel
                ? "开始导入并自动标注"
                : "开始导入图片"}
          </button>
          {busy && <button onClick={importer.cancel}>停止导入</button>}
          {!busy && counts.success > 0 && (
            <button onClick={onReview}>查看图片 / 标注审核 →</button>
          )}
        </div>
        <p className="import-hint">
          大目录分批上传，每批最多 50 张或约 32 MB；原图按原始分辨率保存。
        </p>
        <ImportProgress importer={importer} />
        <div className="import-preview">
          {preview ? (
            <>
              <div className="preview-stage">
                <img src={preview} alt="目录图片预览" />
              </div>
              <p>{previewName} · 自适应窗口</p>
            </>
          ) : (
            <div className="preview-stage">
              <span>选择目录后在这里预览图片</span>
            </div>
          )}
        </div>
      </section>
      <section className="panel controls import-settings">
        <h3>导入设置</h3>
        <p>目标数据集</p>
        <strong>{dataset.name}</strong>
        <fieldset disabled={busy}>
          {dataset.task_type === "detection" ? (
            <>
              <label>
                导入时自动标注
                <select
                  aria-label="导入时自动标注"
                  value={provider}
                  onChange={(event) =>
                    setProvider(
                      event.target.value as ImportOptions["auto_label"],
                    )
                  }
                >
                  <option value="off">关闭 · 仅导入图片</option>
                  <option value="yolo_world">YOLO-World 自动标注</option>
                  <option value="custom_yolo">自定义 YOLO 自动标注</option>
                </select>
              </label>
              {provider === "custom_yolo" && (
                <ModelPicker
                  value={modelPath}
                  onChange={setModelPath}
                  notify={notify}
                />
              )}
              {autoLabel && (
                <label>
                  置信度阈值
                  <input
                    type="number"
                    min="0.01"
                    max="1"
                    step="0.05"
                    value={confidence}
                    onChange={(event) =>
                      setConfidence(Number(event.target.value))
                    }
                  />
                </label>
              )}
              <p>
                {autoLabel
                  ? "导入过程中逐张自动标框，结果进入待审核。"
                  : "选择上方 YOLO-World，即可在导入时自动标框。"}
              </p>
              <h4>当前数据集类别</h4>
              <div className="class-tags">
                {dataset.classes.map((item) => (
                  <span className="tag" key={item.id}>
                    {item.id} · {item.name}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <p>
              {dataset.task_type === "raw"
                ? "raw 数据集保存无标签原图。"
                : "分类数据集导入后，请在标注审核页选择类别。"}
            </p>
          )}
        </fieldset>
        <div className="import-tip">
          <strong>图片已经在服务器上？</strong>
          <p>选择“服务器图片目录”，填写目录路径即可导入，省去浏览器上传。</p>
        </div>
        <button className="review-link" disabled={busy} onClick={onReview}>
          进入标注审核
        </button>
      </section>
    </div>
  );
}

export function ImportProgress({
  importer,
}: {
  importer: ReturnType<typeof useImageImport>;
}) {
  const { phase, counts, batchLabel, transfer, autoLabel, busy, issue } =
    importer;
  if (!phase) return null;
  return (
    <div className="task import-progress" role="status" aria-live="polite">
      <div className="progress-heading">
        <strong>{phase}</strong>
        <span>{batchLabel}</span>
      </div>
      <div className="import-count">
        <strong>
          已导入 {counts.success} / {counts.total || "扫描中"} 张
        </strong>
      </div>
      <div className="progress-item">
        <div>
          {autoLabel ? "导入与自动标注进度" : "图片导入进度"}
          <span>
            已处理 {counts.processed} / {counts.total || "扫描中"} 张
            {counts.total > 0 &&
              ` · ${Math.floor((counts.processed / counts.total) * 100)}%`}
          </span>
        </div>
        <progress
          aria-label="图片导入进度"
          value={counts.total ? counts.processed : undefined}
          max={counts.total || 1}
        />
        <span>
          成功 {counts.success} 张 · 失败 {counts.failed} 张 · 剩余{" "}
          {Math.max(0, counts.total - counts.processed)} 张
        </span>
      </div>
      {transfer && (
        <div className="progress-item">
          <div>
            本批上传
            <span>
              {formatBytes(transfer.loaded)}
              {transfer.total !== null &&
                ` / ${formatBytes(transfer.total)} · ${Math.floor((transfer.loaded / transfer.total) * 100)}%`}
            </span>
          </div>
          <progress
            aria-label="本批上传进度"
            value={transfer.total ? transfer.loaded : undefined}
            max={transfer.total || 1}
          />
          {transfer.total !== null &&
            transfer.loaded >= transfer.total &&
            busy && <small>文件传输完成，后台正在保存和处理图片。</small>}
        </div>
      )}
      {autoLabel && busy && counts.processed === 0 && (
        <p>首次自动标注需要加载模型，处理进度会在完成首张图片后更新。</p>
      )}
      {busy && <small>请保持此窗口打开；停止后保留已成功保存的图片。</small>}
      {issue && <p className="danger">{issue}</p>}
    </div>
  );
}
