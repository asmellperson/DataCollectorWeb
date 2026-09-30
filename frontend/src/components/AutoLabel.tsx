import { useState } from "react";
import { post, errorText } from "../services/api";
import ModelPicker from "./ModelPicker";
import TaskProgress from "./TaskProgress";
export default function AutoLabel({
  datasetId,
  sampleId,
  onDone,
  notify,
}: {
  datasetId: string;
  sampleId?: string;
  onDone: () => void;
  notify: (s: string) => void;
}) {
  const [provider, setProvider] = useState("yolo_world");
  const [path, setPath] = useState("");
  const [confidence, setConfidence] = useState(0.25);
  const [task, setTask] = useState("");
  async function label(single: boolean) {
    try {
      const r = await post(`/datasets/${datasetId}/auto-label`, {
        provider,
        model_path: provider === "custom_yolo" ? path : null,
        confidence,
        sample_ids: single ? [sampleId] : null,
      });
      setTask(r.task_id);
    } catch (e) {
      notify(errorText(e));
    }
  }
  return (
    <details className="auto-label" open>
      <summary>已有图片自动标注</summary>
      <label>
        检测器
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="yolo_world">YOLO-World</option>
          <option value="custom_yolo">自定义 YOLO</option>
        </select>
      </label>
      {provider === "custom_yolo" && (
        <ModelPicker value={path} onChange={setPath} notify={notify} />
      )}
      <label>
        置信度
        <input
          type="number"
          min="0.01"
          max="1"
          step="0.05"
          value={confidence}
          onChange={(e) => setConfidence(Number(e.target.value))}
        />
      </label>
      <div className="actions">
        <button disabled={!sampleId} onClick={() => void label(true)}>
          预标当前图片
        </button>
        <button onClick={() => void label(false)}>批量预标待审核</button>
      </div>
      <p>保留已有标注；完成后进入待审核。</p>
      {task && <TaskProgress id={task} onDone={onDone} notify={notify} />}
    </details>
  );
}
