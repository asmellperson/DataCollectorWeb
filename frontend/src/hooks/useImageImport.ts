import { useEffect, useRef, useState } from "react";
import type { Dataset } from "../types";
import {
  api,
  post,
  uploadForm,
  errorText,
  type UploadProgress,
} from "../services/api";

export type ImportOptions = {
  auto_label: "off" | "yolo_world" | "custom_yolo";
  model_path: string | null;
  confidence: number;
};
type Counts = {
  total: number;
  processed: number;
  success: number;
  failed: number;
};
type ImportTask = Counts & {
  status: string;
  error: string;
  result?: { last_sample_id?: string };
};
const emptyCounts: Counts = { total: 0, processed: 0, success: 0, failed: 0 };
export const defaultImportOptions: ImportOptions = {
  auto_label: "off",
  model_path: null,
  confidence: 0.25,
};
export function imageFiles(list: FileList | null) {
  return Array.from(list || []).filter((file) =>
    /\.(jpe?g|png|bmp|webp)$/i.test(file.name),
  );
}
function batches(files: File[]) {
  const result: File[][] = [];
  let batch: File[] = [];
  let bytes = 0;
  for (const file of files) {
    if (
      batch.length &&
      (batch.length >= 50 || bytes + file.size > 32 * 1024 ** 2)
    ) {
      result.push(batch);
      batch = [];
      bytes = 0;
    }
    batch.push(file);
    bytes += file.size;
  }
  if (batch.length) result.push(batch);
  return result;
}

export function useImageImport() {
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [batchLabel, setBatchLabel] = useState("");
  const [transfer, setTransfer] = useState<UploadProgress | null>(null);
  const [counts, setCounts] = useState<Counts>(emptyCounts);
  const [issue, setIssue] = useState("");
  const [autoLabel, setAutoLabel] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const activeTask = useRef("");
  const activeCounts = useRef({ previous: emptyCounts, total: 0 });
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    if (busy) window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  function reset() {
    if (controller.current) return;
    setPhase("");
    setCounts(emptyCounts);
    setIssue("");
    setTransfer(null);
    setBatchLabel("");
  }
  function cancel() {
    setPhase("正在停止…");
    controller.current?.abort();
  }
  function updateCounts(task: ImportTask, previous: Counts, total: number) {
    setCounts({
      total: total || task.total,
      processed: previous.processed + task.processed,
      success: previous.success + task.success,
      failed: previous.failed + task.failed,
    });
  }
  async function watchTask(
    id: string,
    previous: Counts,
    total: number,
    signal: AbortSignal,
  ) {
    activeTask.current = id;
    activeCounts.current = { previous, total };
    while (true) {
      signal.throwIfAborted();
      const task = await api<ImportTask>(`/tasks/${id}`, { signal });
      updateCounts(task, previous, total);
      if (task.error) setIssue(task.error);
      if (!["queued", "running"].includes(task.status)) {
        activeTask.current = "";
        if (task.status === "failed")
          throw new Error(task.error || "导入任务失败");
        if (task.status === "cancelled")
          throw new DOMException("任务已取消", "AbortError");
        return task;
      }
      await new Promise((resolve) => setTimeout(resolve, 600));
    }
  }
  async function runImport(
    dataset: Dataset,
    source: { files: File[] } | { path: string },
    options = defaultImportOptions,
  ) {
    if (controller.current) throw new Error("已有导入任务正在执行");
    const total = "files" in source ? source.files.length : 0;
    const auto =
      dataset.task_type === "detection" && options.auto_label !== "off";
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setAutoLabel(auto);
    setCounts({ ...emptyCounts, total });
    setIssue("");
    setTransfer(null);
    setBatchLabel("");
    const body = { ...options, auto_label: auto ? options.auto_label : "off" };
    const processing = auto ? "正在导入并自动标注" : "正在保存图片";
    let lastSample = "";
    try {
      if ("path" in source) {
        setPhase("正在扫描服务器目录");
        const result = await post(`/datasets/${dataset.id}/images/import`, {
          ...body,
          path: source.path,
        });
        setPhase(processing);
        const task = await watchTask(
          result.task_id,
          emptyCounts,
          0,
          abort.signal,
        );
        lastSample = task.result?.last_sample_id || "";
      } else {
        if (!total) throw new Error("请选择包含图片的目录");
        const groups = batches(source.files);
        let completed = { ...emptyCounts, total };
        for (const [index, group] of groups.entries()) {
          abort.signal.throwIfAborted();
          setBatchLabel(
            `第 ${index + 1} / ${groups.length} 批 · 本批 ${group.length} 张`,
          );
          setPhase("正在上传图片");
          setTransfer({ loaded: 0, total: null });
          const form = new FormData();
          form.append("options", JSON.stringify(body));
          group.forEach((file) =>
            form.append("files", file, file.webkitRelativePath || file.name),
          );
          const result = await uploadForm(
            `/datasets/${dataset.id}/images/upload`,
            form,
            setTransfer,
            abort.signal,
          );
          setPhase(processing);
          const task = await watchTask(
            result.task_id,
            completed,
            total,
            abort.signal,
          );
          completed = {
            total,
            processed: completed.processed + task.processed,
            success: completed.success + task.success,
            failed: completed.failed + task.failed,
          };
          lastSample = task.result?.last_sample_id || lastSample;
        }
      }
      setPhase("导入完成");
      return { status: "completed" as const, lastSample };
    } catch (error) {
      const cancelled =
        error instanceof DOMException && error.name === "AbortError";
      if (!cancelled) setIssue(errorText(error));
      if (activeTask.current) {
        try {
          setPhase("正在停止，等待当前图片处理结束…");
          await post(`/tasks/${activeTask.current}/cancel`);
          while (true) {
            const task = await api<ImportTask>(`/tasks/${activeTask.current}`);
            updateCounts(
              task,
              activeCounts.current.previous,
              activeCounts.current.total,
            );
            if (!["queued", "running"].includes(task.status)) break;
            await new Promise((resolve) => setTimeout(resolve, 600));
          }
        } catch (cancelError) {
          setIssue(
            `停止任务失败：${errorText(cancelError)}。请在任务结束后再导入。`,
          );
        }
      }
      setPhase(
        cancelled ? "已停止，已保存的图片保留" : "导入失败，已保存的图片保留",
      );
      return {
        status: cancelled ? ("cancelled" as const) : ("failed" as const),
        lastSample,
      };
    } finally {
      activeTask.current = "";
      controller.current = null;
      setBusy(false);
    }
  }
  return {
    busy,
    phase,
    batchLabel,
    transfer,
    counts,
    issue,
    autoLabel,
    runImport,
    cancel,
    reset,
  };
}
