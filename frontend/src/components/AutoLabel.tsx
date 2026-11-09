import { useEffect, useState } from "react";
import { post, errorText } from "../services/api";
import ModelPicker from "./ModelPicker";
import ScrfdInfo from "./ScrfdInfo";
import TaskProgress from "./TaskProgress";
import ClassSelector from "./ClassSelector";
import type { ClassItem, ClassMapping } from "../types";
import { serializeClassMapping } from "../services/classMapping";
import {
  readAutoLabelPreferences,
  writePreference,
} from "../services/preferences";
export default function AutoLabel({
  datasetId,
  classes,
  sampleId,
  onDone,
  notify,
  disabled = false,
  faceMode = false,
}: {
  datasetId: string;
  classes: ClassItem[];
  sampleId?: string;
  onDone: () => void;
  notify: (s: string) => void;
  disabled?: boolean;
  faceMode?: boolean;
}) {
  const [saved] = useState(() => readAutoLabelPreferences(datasetId, classes));
  const [provider, setProvider] = useState(saved.provider);
  const [path, setPath] = useState(saved.path);
  const [confidence, setConfidence] = useState(saved.confidence);
  const [overwrite, setOverwrite] = useState(saved.overwrite);
  const [labelClasses, setLabelClasses] = useState<number[] | null>(
    saved.labelClasses,
  );
  const [classMapping, setClassMapping] = useState<ClassMapping>(
    saved.classMapping,
  );
  const [task, setTask] = useState("");
  useEffect(() => {
    writePreference(datasetId, "auto-label", {
      provider,
      path,
      confidence,
      overwrite,
      labelClasses,
      classMapping,
    });
  }, [
    datasetId,
    provider,
    path,
    confidence,
    overwrite,
    labelClasses,
    classMapping,
  ]);
  async function label(single: boolean) {
    if (disabled) return;
    try {
      const r = await post(`/datasets/${datasetId}/auto-label`, {
        provider: faceMode ? "scrfd" : provider,
        model_path: !faceMode && provider === "custom_yolo" ? path : null,
        confidence,
        overwrite,
        label_class_ids: faceMode ? null : labelClasses,
        label_class_mapping: faceMode ? {} : serializeClassMapping(classMapping, labelClasses),
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
      {faceMode ? <ScrfdInfo /> : <><label>
        检测器
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="yolo_world">YOLO-World</option>
          <option value="custom_yolo">自定义 YOLO</option>
        </select>
      </label>
      {provider === "custom_yolo" && (
        <ModelPicker value={path} onChange={setPath} notify={notify} />
      )}
      </>}
      <label>
        置信度阈值
        <input
          type="number"
          min="0.01"
          max="1"
          step="0.01"
          value={confidence}
          onChange={(e) => setConfidence(Number(e.target.value))}
        />
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={overwrite}
          onChange={(e) => setOverwrite(e.target.checked)}
        />
        覆盖所选类别已有标注（重新预标）
      </label>
      {!faceMode && <ClassSelector
        modelClassIds={classMapping}
        onModelClassIdsChange={setClassMapping}
        classes={classes}
        value={labelClasses}
        onChange={setLabelClasses}
      />}
      <div className="actions">
        <button
          disabled={disabled || !sampleId || (!faceMode && labelClasses?.length === 0)}
          onClick={() => void label(true)}
        >
          预标当前图片
        </button>
        <button
          disabled={disabled || (!faceMode && labelClasses?.length === 0)}
          onClick={() => void label(false)}
        >
          批量预标待审核
        </button>
      </div>
      <p>
        {overwrite
          ? "仅替换所选类别的已有框（包括人工框）；未检出时清空所选类别，其他类别保留。"
          : "已有所选类别标注的图片会跳过；可补标其他类别。修改阈值重标时请勾选覆盖。"}
      </p>
      {task && (
        <TaskProgress key={task} id={task} onDone={onDone} notify={notify} />
      )}
    </details>
  );
}
