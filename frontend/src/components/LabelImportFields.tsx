import { useRef } from "react";
import type { ClassItem } from "../types";
import type { useLabelImport } from "../hooks/useLabelImport";

export default function LabelImportFields({ value, images, targets }: {
  value: ReturnType<typeof useLabelImport>;
  images: File[];
  targets?: ClassItem[];
}) {
  const labelsInput = useRef<HTMLInputElement>(null);
  const yamlInput = useRef<HTMLInputElement>(null);
  const { report } = value;
  return <div className="label-import-fields">
    <div className="actions">
      <input ref={labelsInput} type="file" multiple {...{ webkitdirectory: "" }}
        className="file-input-hidden" aria-label="选择标签目录"
        onChange={(e) => { value.chooseLabels(e.target.files); e.target.value = ""; }} />
      <button type="button" onClick={() => labelsInput.current?.click()}>选择标签目录</button>
      <span className="muted">{value.labels.length ? `${value.labels.length} 个 TXT` : "未选择"}</span>
    </div>
    <div className="source-tabs" role="group" aria-label="类别来源">
      <button type="button" className={value.method === "manual" ? "primary" : ""}
        onClick={() => value.changeMethod("manual")}>手动填写</button>
      <button type="button" className={value.method === "yaml" ? "primary" : ""}
        onClick={() => value.changeMethod("yaml")}>读取 YAML</button>
    </div>
    {value.method === "manual" ? <label>
      标签类别
      <textarea rows={4} aria-label="标签类别" placeholder={"0 person\n1 car"}
        value={value.text} onChange={(e) => value.changeText(e.target.value)} />
      <small>每行：编号 名称</small>
    </label> : <div className="actions">
      <input ref={yamlInput} type="file" accept=".yaml,.yml" className="file-input-hidden"
        aria-label="选择 YAML" onChange={(e) => { value.chooseYaml(e.target.files?.[0] || null); e.target.value = ""; }} />
      <button type="button" onClick={() => yamlInput.current?.click()}>选择 YAML</button>
      <span className="muted">{value.yaml?.name || "未选择"}</span>
    </div>}
    <button type="button" disabled={value.checking || !images.length || !value.labels.length}
      onClick={() => void value.check(images)}>{value.checking ? "检查中…" : "检查文件"}</button>
    {report && <>
      <div className="label-import-summary" role="status">
        <span>匹配 {report.matched}</span><span>空标签 {report.empty}</span>
        <span>缺失 {report.missing}</span>
        {report.unmatched > 0 && <span>多余标签 {report.unmatched}</span>}
      </div>
      {report.missing > 0 && <small>缺失标签的图片按未标注导入。</small>}
      {targets ? <details className="import-class-mapping" open={!value.ready || undefined}>
        <summary>类别对应 · {report.classes.length}</summary>
        {report.classes.map((item) => <label key={item.id} className="import-class-row">
          <span>{item.id} · {item.name}</span>
          <select aria-label={`${item.id} ${item.name} 对应类别`} value={value.mapping[item.id] ?? ""}
            onChange={(e) => value.setMapping((old) => {
              const next = { ...old };
              if (e.target.value === "") delete next[item.id];
              else next[item.id] = Number(e.target.value);
              return next;
            })}>
            <option value="">选择类别</option>
            {targets.map((target) => <option key={target.id} value={target.id}>{target.id} · {target.name}</option>)}
          </select>
        </label>)}
      </details> : <div className="class-tags">{report.classes.map((item) =>
        <span className="tag" key={item.id}>{item.id} · {item.name}</span>)}</div>}
      {report.error_count > 0 && <details className="danger" open>
        <summary>{report.error_count} 个错误</summary>
        <ul>{report.errors.map((error, i) => <li key={i}>{error}</li>)}</ul>
      </details>}
    </>}
    {value.error && <p className="danger" role="alert">{value.error}</p>}
  </div>;
}
