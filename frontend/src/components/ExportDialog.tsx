import { useEffect, useRef, useState } from "react";
import type { Dataset } from "../types";
import { api, post, errorText } from "../services/api";
import TaskProgress from "./TaskProgress";

type SplitMode = "none" | "train_val" | "train_val_test";
type ExportPlan = { total: number; counts: Record<string, number> };
const partNames: Record<string, string> = {
  all: "全部图片",
  train: "训练集",
  val: "验证集",
  test: "测试集",
};

export default function ExportDialog({
  dataset,
  onClose,
  notify,
}: {
  dataset: Dataset;
  onClose: () => void;
  notify: (s: string) => void;
}) {
  const [mode, setMode] = useState<SplitMode>("train_val");
  const [train, setTrain] = useState("80");
  const [val, setVal] = useState("20");
  const [test, setTest] = useState("10");
  const [pending, setPending] = useState(false);
  const [task, setTask] = useState("");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [preview, setPreview] = useState<{
    key: string;
    plan?: ExportPlan;
    error?: string;
  } | null>(null);
  const [exports, setExports] = useState<
    { name: string; size: number; download_url: string }[]
  >([]);
  const ratios =
    mode === "none"
      ? []
      : mode === "train_val"
        ? [train, val]
        : [train, val, test];
  const sum = ratios.reduce((total, value) => total + Number(value), 0);
  const ratioError = ratios.some(
    (value) =>
      !value.trim() ||
      !Number.isFinite(Number(value)) ||
      Number(value) <= 0 ||
      Number(value) >= 100,
  )
    ? "每个分组的比例须大于 0%、小于 100%。"
    : mode !== "none" && Math.abs(sum - 100) > 0.000001
      ? "比例合计必须等于 100%。"
      : "";
  const requestKey = JSON.stringify({
    split_mode: mode,
    train_ratio: mode === "none" ? 0 : Number(train) / 100,
    val_ratio: mode === "none" ? 0 : Number(val) / 100,
    test_ratio: mode === "train_val_test" ? Number(test) / 100 : 0,
    include_pending: pending,
  });
  const currentPreview = preview?.key === requestKey ? preview : null;
  const error = ratioError || currentPreview?.error;

  function load() {
    api(`/datasets/${dataset.id}/exports`)
      .then(setExports)
      .catch((e) => notify(errorText(e)));
  }
  useEffect(load, [dataset.id]);
  useEffect(() => {
    if (ratioError) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<ExportPlan>(`/datasets/${dataset.id}/export/preview`, {
        method: "POST",
        body: requestKey,
        signal: controller.signal,
      })
        .then((plan) => {
          if (!controller.signal.aborted) setPreview({ key: requestKey, plan });
        })
        .catch((e) => {
          if (!controller.signal.aborted)
            setPreview({ key: requestKey, error: errorText(e) });
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [dataset.id, requestKey, ratioError]);

  function changeMode(value: SplitMode) {
    setMode(value);
    if (value !== "none") {
      setTrain(value === "train_val_test" ? "70" : "80");
      setVal("20");
      setTest("10");
    }
  }
  async function start() {
    if (submitting.current || busy || error || !currentPreview?.plan) return;
    submitting.current = true;
    setBusy(true);
    try {
      const r = await post(
        `/datasets/${dataset.id}/export${dataset.task_type === "detection" ? "/yolo" : ""}`,
        JSON.parse(requestKey),
      );
      setTask(r.task_id);
    } catch (e) {
      setBusy(false);
      notify(errorText(e));
    } finally {
      submitting.current = false;
    }
  }
  return (
    <div className="modal-shade">
      <div
        className="modal export-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-title"
      >
        <h2 id="export-title">导出「{dataset.name}」</h2>
        <p>
          {dataset.task_type === "detection"
            ? "YOLO Detect · 图片 + 归一化标签 + data.yaml"
            : dataset.task_type === "classification"
              ? "按类别整理图片 + 统一 JSON"
              : "原始图片 + 统一 JSON"}
        </p>
        <fieldset className="export-options" disabled={busy}>
          <label>
            划分方式
            <select
              value={mode}
              onChange={(e) => changeMode(e.target.value as SplitMode)}
            >
              <option value="none">不划分（整份导出）</option>
              <option value="train_val">训练集 + 验证集</option>
              <option value="train_val_test">训练集 + 验证集 + 测试集</option>
            </select>
          </label>
          {mode !== "none" ? (
            <>
              <div className="export-ratios">
                <label>
                  训练集（%）
                  <input
                    type="number"
                    min="0.01"
                    max="99.99"
                    step="any"
                    value={train}
                    onChange={(e) => setTrain(e.target.value)}
                  />
                </label>
                <label>
                  验证集（%）
                  <input
                    type="number"
                    min="0.01"
                    max="99.99"
                    step="any"
                    value={val}
                    onChange={(e) => setVal(e.target.value)}
                  />
                </label>
                {mode === "train_val_test" && (
                  <label>
                    测试集（%）
                    <input
                      type="number"
                      min="0.01"
                      max="99.99"
                      step="any"
                      value={test}
                      onChange={(e) => setTest(e.target.value)}
                    />
                  </label>
                )}
              </div>
              <p className="muted">
                比例合计：{Number(sum.toFixed(6))}% / 100%
              </p>
            </>
          ) : (
            <p className="muted">
              全部图片放在同一份目录中，不创建 train / val / test 子目录。
              {dataset.task_type === "detection" &&
                "用于训练时，请另行配置独立验证集。"}
            </p>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={pending}
              onChange={(e) => setPending(e.target.checked)}
            />
            包含待审核样本（默认仅导出已确认）
          </label>
        </fieldset>
        <div className="export-preview" aria-live="polite">
          {error ? (
            <p className="danger" role="alert">
              {error}
            </p>
          ) : currentPreview?.plan ? (
            <>
              <strong>预计导出 {currentPreview.plan.total} 张</strong>
              <div className="export-counts">
                {Object.entries(currentPreview.plan.counts).map(
                  ([part, count]) => (
                    <span key={part}>
                      {partNames[part]} <b>{count}</b> 张
                    </span>
                  ),
                )}
              </div>
              {mode !== "none" && (
                <small>按比例取整，每组至少 1 张；各组图片不重复。</small>
              )}
            </>
          ) : (
            <p>正在计算符合条件的图片数量…</p>
          )}
        </div>
        <div className="actions">
          <button onClick={onClose}>关闭</button>
          <button
            className="primary"
            disabled={busy || !!error || !currentPreview?.plan}
            onClick={() => void start()}
          >
            {busy ? "正在导出…" : "生成导出"}
          </button>
        </div>
        {task && (
          <TaskProgress
            key={task}
            id={task}
            onDone={() => {
              setBusy(false);
              load();
            }}
            notify={notify}
          />
        )}
        <div className="export-list">
          {exports.map((e) => (
            <a key={e.name} href={e.download_url}>
              ↓ {e.name}
              <small>{(e.size / 1024 / 1024).toFixed(1)} MB</small>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
