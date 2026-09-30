import { useEffect, useId, useState } from "react";
import { api, post, errorText } from "../services/api";
import TaskProgress from "./TaskProgress";
export default function ModelPicker({
  value,
  onChange,
  notify,
}: {
  value: string;
  onChange: (v: string) => void;
  notify: (s: string) => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [task, setTask] = useState("");
  const id = useId();
  useEffect(() => {
    api("/models")
      .then((r) => setModels(r.custom_models))
      .catch((e) => notify(errorText(e)));
  }, []);
  async function inspect() {
    try {
      setTask((await post("/models/inspect", { path: value })).task_id);
    } catch (e) {
      notify(errorText(e));
    }
  }
  return (
    <div>
      <label>
        服务器 .pt 模型路径
        <input
          list={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="/path/to/model.pt"
        />
        <datalist id={id}>
          {models.map((p) => (
            <option key={p} value={p} />
          ))}
        </datalist>
      </label>
      <button type="button" disabled={!value} onClick={() => void inspect()}>
        读取模型类别
      </button>
      {task && <TaskProgress id={task} notify={notify} />}
    </div>
  );
}
