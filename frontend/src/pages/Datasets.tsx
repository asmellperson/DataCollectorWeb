import { useEffect, useRef, useState } from "react";
import type { Dataset, Page } from "../types";
import ExportDialog from "../components/ExportDialog";
import { api, post, errorText, formatBytes } from "../services/api";
import ImageImport, { ImportProgress } from "../components/ImageImport";
import ModelPicker from "../components/ModelPicker";
import {
  useImageImport,
  imageFiles,
  defaultImportOptions,
  type ImportOptions,
} from "../hooks/useImageImport";
export default function Datasets({
  datasets,
  reload,
  navigate,
  notify,
  onBusyChange,
}: {
  datasets: Dataset[];
  reload: () => Promise<void>;
  navigate: (p: Page, id: string) => void;
  notify: (s: string) => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [exportDataset, setExportDataset] = useState<Dataset | null>(null);
  const [editing, setEditing] = useState<Dataset | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [type, setType] = useState<Dataset["task_type"]>("detection");
  const [classes, setClasses] = useState("0 person\n1 helmet\n2 forklift");
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [skipped, setSkipped] = useState(0);
  const [options, setOptions] = useState<ImportOptions>(defaultImportOptions);
  const [created, setCreated] = useState<Dataset | null>(null);
  const [formError, setFormError] = useState("");
  const [importDataset, setImportDataset] = useState<Dataset | null>(null);
  const [panelBusy, setPanelBusy] = useState(false);
  const directoryInput = useRef<HTMLInputElement>(null);
  const saving = useRef(false);
  const importer = useImageImport();
  const working = busy || importer.busy;
  useEffect(() => {
    onBusyChange(working || panelBusy);
    return () => onBusyChange(false);
  }, [working, panelBusy, onBusyChange]);

  function open(d: Dataset | null) {
    setFiles([]);
    setSkipped(0);
    setOptions(defaultImportOptions);
    setCreated(null);
    setFormError("");
    importer.reset();
    setEditing(d);
    setName(d?.name || "");
    setType(d?.task_type || "detection");
    setClasses(
      d
        ? d.classes.map((c) => `${c.id} ${c.name}`).join("\n")
        : "0 person\n1 helmet\n2 forklift",
    );
  }
  async function save() {
    if (saving.current || created) return;
    saving.current = true;
    setBusy(true);
    setFormError("");
    try {
      const parsed =
        type === "raw"
          ? []
          : classes
              .split("\n")
              .filter((l) => l.trim())
              .map((l) => {
                const match = l.trim().match(/^(\d+)\s+(.+)$/);
                if (!match) throw new Error("类别格式应为：0 person");
                return { id: Number(match[1]), name: match[2] };
              });
      if (
        !editing &&
        files.length &&
        type === "detection" &&
        options.auto_label === "custom_yolo" &&
        !options.model_path?.trim()
      ) {
        throw new Error("请选择自定义 YOLO 模型路径");
      }
      if (editing) {
        await api(`/datasets/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify({ name, classes: parsed }),
        });
        await reload();
        setEditing(undefined);
      } else {
        const dataset = await post<Dataset>("/datasets", {
          name,
          task_type: type,
          classes: parsed,
        });
        setCreated(dataset);
        if (files.length) {
          await importer.runImport(dataset, { files }, options);
          await reload();
        } else {
          await reload();
          setEditing(undefined);
        }
      }
    } catch (e) {
      setFormError(errorText(e));
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }
  async function remove(d: Dataset) {
    if (!confirm(`删除「${d.name}」及全部图片、标注和导出？此操作无法撤销。`))
      return;
    try {
      await api(`/datasets/${d.id}`, { method: "DELETE" });
      await reload();
    } catch (e) {
      notify(errorText(e));
    }
  }
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">WORKSPACE / DATASETS</div>
          <h1>数据集</h1>
          <p>从采集到训练，让每一张图像成为可靠的数据。</p>
        </div>
        <button className="primary" onClick={() => open(null)}>
          ＋ 新建数据集
        </button>
      </div>
      <section className="panel">
        <div className="panel-title">
          全部数据集 <span className="muted">{datasets.length} 个</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>任务类型</th>
              <th>总图片</th>
              <th>待审核</th>
              <th>已确认</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((d) => (
              <tr key={d.id}>
                <td>
                  <strong>{d.name}</strong>
                  <small>{d.id}</small>
                </td>
                <td>
                  <span className="tag">
                    {
                      {
                        raw: "原始数据",
                        classification: "图像分类",
                        detection: "目标检测",
                      }[d.task_type]
                    }
                  </span>
                </td>
                <td>{d.total}</td>
                <td>{d.pending}</td>
                <td>{d.approved}</td>
                <td className="actions">
                  <button onClick={() => navigate("review", d.id)}>
                    进入标注
                  </button>
                  <button onClick={() => setImportDataset(d)}>导入图片</button>
                  <button onClick={() => navigate("collection", d.id)}>
                    继续采集
                  </button>
                  <button onClick={() => setExportDataset(d)}>导出</button>
                  <button onClick={() => open(d)}>设置</button>
                  <button className="danger" onClick={() => remove(d)}>
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!datasets.length && (
          <div className="empty">创建第一个数据集，开始收集与审核图像。</div>
        )}
      </section>
      {exportDataset && (
        <ExportDialog
          dataset={exportDataset}
          onClose={() => setExportDataset(null)}
          notify={notify}
        />
      )}{" "}
      {importDataset && (
        <div className="modal-shade">
          <div
            className="modal dataset-import-modal"
            role="dialog"
            aria-modal="true"
            aria-label="导入图片"
          >
            <div className="modal-heading">
              <h2>导入图片 · {importDataset.name}</h2>
              <button
                disabled={panelBusy}
                onClick={() => setImportDataset(null)}
              >
                关闭
              </button>
            </div>
            <ImageImport
              dataset={importDataset}
              reload={reload}
              notify={notify}
              onBusyChange={setPanelBusy}
              onReview={() => {
                setImportDataset(null);
                navigate("review", importDataset.id);
              }}
            />
          </div>
        </div>
      )}
      {editing !== undefined && (
        <div className="modal-shade">
          {created ? (
            <section
              className="modal dataset-modal"
              role="dialog"
              aria-modal="true"
              aria-label="创建数据集与导入进度"
            >
              <h2>{working ? "数据集已创建，正在导入图片" : "数据集已创建"}</h2>
              <p>{created.name} · 图片导入完成后可进入标注审核</p>
              <ImportProgress importer={importer} />
              {formError && (
                <p role="alert" className="danger">
                  {formError}
                </p>
              )}
              <div className="actions modal-actions">
                {working ? (
                  <button disabled={!importer.busy} onClick={importer.cancel}>
                    停止导入
                  </button>
                ) : (
                  <>
                    <button onClick={() => setEditing(undefined)}>关闭</button>
                    <button
                      className="primary"
                      onClick={() => {
                        setEditing(undefined);
                        navigate("review", created.id);
                      }}
                    >
                      进入标注审核
                    </button>
                  </>
                )}
              </div>
            </section>
          ) : (
            <form
              className="modal dataset-modal"
              role="dialog"
              aria-modal="true"
              aria-label={editing ? "数据集设置" : "新建数据集"}
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <h2>{editing ? "数据集设置" : "新建数据集"}</h2>
              <fieldset disabled={working}>
                <label>
                  名称
                  <input
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  任务类型
                  <select
                    aria-label="任务类型"
                    disabled={!!editing}
                    value={type}
                    onChange={(e) =>
                      setType(e.target.value as Dataset["task_type"])
                    }
                  >
                    <option value="detection">目标检测</option>
                    <option value="classification">图像分类</option>
                    <option value="raw">原始数据</option>
                  </select>
                </label>
                {type !== "raw" && (
                  <label>
                    类别（每行 ID 名称，ID 创建后保持不变）
                    <textarea
                      rows={4}
                      value={classes}
                      onChange={(e) => setClasses(e.target.value)}
                    />
                  </label>
                )}
                {!editing && (
                  <div className="create-directory">
                    <h3>
                      导入本地图片目录 <span className="muted">（可选）</span>
                    </h3>
                    <p>不选择目录即可创建空数据集，之后也能补充图片。</p>
                    <input
                      ref={directoryInput}
                      className="file-input-hidden"
                      type="file"
                      multiple
                      {...{ webkitdirectory: "" }}
                      aria-label="新建数据集图片目录"
                      onChange={(event) => {
                        const selected = imageFiles(event.target.files);
                        setFiles(selected);
                        setSkipped(
                          (event.target.files?.length || 0) - selected.length,
                        );
                        setFormError(
                          event.target.files?.length && !selected.length
                            ? "所选目录没有支持的图片，可重新选择或直接创建空数据集。"
                            : "",
                        );
                      }}
                    />
                    <div className="actions">
                      <button
                        type="button"
                        className="directory-button"
                        onClick={() => directoryInput.current?.click()}
                      >
                        {files.length ? "重新选择图片目录" : "选择本地图片目录"}
                      </button>
                      {files.length > 0 && (
                        <button
                          type="button"
                          onClick={() => {
                            setFiles([]);
                            setSkipped(0);
                            setFormError("");
                            if (directoryInput.current)
                              directoryInput.current.value = "";
                          }}
                        >
                          不导入图片
                        </button>
                      )}
                    </div>
                    {files.length > 0 ? (
                      <div className="directory-selection">
                        <strong>
                          {files[0].webkitRelativePath.split("/")[0] ||
                            "所选目录"}{" "}
                          · {files.length} 张图片 ·{" "}
                          {formatBytes(
                            files.reduce((sum, file) => sum + file.size, 0),
                          )}
                        </strong>
                        <p>
                          {skipped > 0 && `已忽略 ${skipped} 个非图片文件。`}
                          点击“创建并导入”后开始上传，并显示导入张数和进度。
                        </p>
                      </div>
                    ) : (
                      <small>
                        支持 JPG、JPEG、PNG、BMP、WebP，包含子目录。
                      </small>
                    )}
                    {files.length > 0 && type === "detection" && (
                      <>
                        <label>
                          导入时自动标注
                          <select
                            aria-label="导入时自动标注"
                            value={options.auto_label}
                            onChange={(e) =>
                              setOptions({
                                ...options,
                                auto_label: e.target
                                  .value as ImportOptions["auto_label"],
                              })
                            }
                          >
                            <option value="off">关闭 · 仅导入图片</option>
                            <option value="yolo_world">
                              YOLO-World 自动标注
                            </option>
                            <option value="custom_yolo">
                              自定义 YOLO 自动标注
                            </option>
                          </select>
                        </label>
                        {options.auto_label === "custom_yolo" && (
                          <ModelPicker
                            value={options.model_path || ""}
                            onChange={(model_path) =>
                              setOptions({ ...options, model_path })
                            }
                            notify={setFormError}
                          />
                        )}
                        {options.auto_label !== "off" && (
                          <label>
                            置信度阈值
                            <input
                              type="number"
                              min="0.01"
                              max="1"
                              step="0.01"
                              value={options.confidence}
                              onChange={(e) =>
                                setOptions({
                                  ...options,
                                  confidence: Number(e.target.value),
                                })
                              }
                            />
                          </label>
                        )}
                      </>
                    )}
                  </div>
                )}
              </fieldset>
              {formError && (
                <p role="alert" className="danger">
                  {formError}
                </p>
              )}
              <div className="actions modal-actions">
                <button
                  type="button"
                  disabled={working}
                  onClick={() => setEditing(undefined)}
                >
                  取消
                </button>
                <button className="primary" disabled={working}>
                  {working
                    ? "正在保存…"
                    : editing
                      ? "保存"
                      : files.length
                        ? "创建并导入"
                        : "创建数据集"}
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </>
  );
}
