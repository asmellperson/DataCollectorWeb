import type { ClassItem, ClassMapping } from "../types";
import { parseSourceIds } from "../services/classMapping";

export default function ClassSelector({
  classes,
  value,
  onChange,
  title = "自动标注类别",
  hint,
  modelClassIds,
  onModelClassIdsChange,
}: {
  classes: ClassItem[];
  value: number[] | null;
  onChange: (ids: number[] | null) => void;
  title?: string;
  hint?: string;
  modelClassIds?: ClassMapping;
  onModelClassIdsChange?: (mapping: ClassMapping) => void;
}) {
  const selected = value ?? classes.map((item) => item.id);
  function sourceError(id: number) {
    if (
      !modelClassIds ||
      !selected.includes(id) ||
      modelClassIds[id] === undefined
    )
      return "";
    try {
      parseSourceIds(modelClassIds[id]);
      return "";
    } catch (error) {
      return (error as Error).message;
    }
  }
  return (
    <fieldset className="class-selector">
      <legend>{title}</legend>
      <div className="actions">
        <button type="button" onClick={() => onChange(null)}>
          全选
        </button>
        <button type="button" onClick={() => onChange([])}>
          清空
        </button>
        <small>
          已选 {classes.filter((item) => selected.includes(item.id)).length} /{" "}
          {classes.length}
        </small>
      </div>
      <div className="class-selector-list">
        {classes.map((item) => (
          <div
            className={modelClassIds ? "class-mapping-row" : undefined}
            key={item.id}
          >
            <label className="check">
              <input
                type="checkbox"
                checked={selected.includes(item.id)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...selected, item.id]
                      : selected.filter((id) => id !== item.id),
                  )
                }
              />
              {item.id} · {item.name}
            </label>
            {modelClassIds && onModelClassIdsChange && (
              <label className="class-source-id">
                检测类别 ID → {item.name}
                <input
                  type="text"
                  disabled={!selected.includes(item.id)}
                  placeholder="例如 2,3；留空按名称匹配"
                  aria-invalid={!!sourceError(item.id)}
                  value={
                    Array.isArray(modelClassIds[item.id])
                      ? (modelClassIds[item.id] as number[]).join(", ")
                      : String(modelClassIds[item.id] ?? "")
                  }
                  onChange={(event) => {
                    const mapping = { ...modelClassIds };
                    if (event.target.value === "") delete mapping[item.id];
                    else mapping[item.id] = event.target.value;
                    onModelClassIdsChange(mapping);
                  }}
                />
                {sourceError(item.id) && (
                  <small className="danger" role="alert">
                    {sourceError(item.id)}
                  </small>
                )}
              </label>
            )}
          </div>
        ))}
      </div>
      {!selected.length && (
        <p className="danger" role="alert">
          请至少选择一个类别。
        </p>
      )}
      <small>
        {hint ?? "可选一个或多个类别。新增类别请到“数据集 → 类别 / 设置”。"}
      </small>
      {modelClassIds && (
        <small>
          支持多个检测 ID，用逗号分隔，例如 car 填 2,3，汽车和摩托车的框都保存为
          car。 COCO：1 bicycle、2 car、3 motorcycle、67 cell
          phone；没有独立的电动车类别。 自定义 YOLO 按模型原始 ID，YOLO-World 按
          COCO ID；留空按名称匹配。
        </small>
      )}
    </fieldset>
  );
}
