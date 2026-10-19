import { useEffect, useState } from "react";
import { api, post, errorText } from "../services/api";
import { inspectionBase, recordingBase } from "../services/securityAnnotation";
import { useSecurityAnnotation } from "../hooks/useSecurityAnnotation";
import type { InspectionConfig, InspectionRecording, PrelabelOptions } from "../types/securityAnnotation";
import SecurityVideoSources from "../components/SecurityVideoSources";
import SecurityAnnotationReview from "../components/SecurityAnnotationReview";
import SecurityRecordingPreview from "../components/SecurityRecordingPreview";
import TaskProgress from "../components/TaskProgress";
import "./SecurityAnnotation.css";

const states = { preparing: "准备视频中", recording: "连续录制中", ready: "可标注", failed: "处理失败" };

export default function SecurityAnnotation({ notify, onBusyChange }: {
  notify: (message: string) => void; onBusyChange: (busy: boolean) => void;
}) {
  const review = useSecurityAnnotation(notify);
  const { detail, draft, dirty, busy } = review;
  const [config, setConfig] = useState<InspectionConfig | null>(null);
  const [options, setOptions] = useState<PrelabelOptions | null>(null);
  const [working, setWorking] = useState(false);
  const [selectedExports, setSelectedExports] = useState<string[]>([]);
  const [valRatio, setValRatio] = useState(0);
  const [manifest, setManifest] = useState<{ path: string; counts: Record<string, number>; skipped_rounds: number } | null>(null);
  const [legacyPath, setLegacyPath] = useState("");
  const [legacyId, setLegacyId] = useState("");
  const [stoppingRecordings, setStoppingRecordings] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    api<InspectionConfig>(`${inspectionBase}/config`, { signal: controller.signal }).then(value => {
      setConfig(value); setOptions(value.prelabel_defaults);
    }).catch(error => { if (!controller.signal.aborted) notify(errorText(error)); });
    return () => controller.abort();
  }, [notify]);
  useEffect(() => {
    onBusyChange(dirty || busy || working);
    return () => onBusyChange(false);
  }, [dirty, busy, working, onBusyChange]);
  async function operation(callback: () => Promise<unknown>) {
    if (working || busy) return;
    setWorking(true);
    try { await callback(); await review.refresh(); }
    catch (error) { notify(errorText(error)); }
    finally { setWorking(false); }
  }
  async function replace(path: string, body: unknown) {
    if (!detail || dirty) return;
    if (draft.rounds.length && !confirm("载入后会生成新版本并替换当前内容；已有人工版本仍保存在修订历史中。继续？")) return;
    await operation(async () => { await post(path, body); await review.refresh(true); });
  }
  async function stopRecording(id: string) {
    await operation(async () => {
      await post(`${recordingBase(id)}/stop`);
      setStoppingRecordings(values => [...values, id]);
    });
  }
  async function deleteRecording(recording: InspectionRecording) {
    if (working || busy) return;
    const unsaved = detail?.recording.id === recording.id && dirty ? "当前未保存的标注也会丢弃。\n" : "";
    if (!confirm(`删除录像“${recording.name}”？\n${unsaved}将永久删除该录像的原视频、播放副本及全部标注，已有训练清单对该录像的引用也会失效。此操作无法恢复。`)) return;
    if (await review.remove(recording.id)) {
      setSelectedExports(values => values.filter(id => id !== recording.id));
      setManifest(null);
      notify("录像已删除");
    }
  }
  return <div className="security-annotation" onPointerDown={review.activity} onKeyDown={review.activity}>
    <div className="page-head"><div><div className="eyebrow">VIDEO / SECURITY ANNOTATION</div><h1>安检视频标注</h1>
      <p>连续原视频 → 大模型初标 → 人工复核 → 服务器本地训练清单</p></div></div>
    <SecurityVideoSources disabled={dirty || busy || working} notify={notify} onCreated={review.select} />
    <div className="security-workspace">
      <aside className="panel security-recordings"><h2>连续录像</h2>
        {review.recordings.map(recording => <div className={`security-recording ${detail?.recording.id === recording.id ? "selected" : ""}`} key={recording.id}>
          <button type="button" className="security-recording-open" disabled={busy || working} onClick={() => void review.select(recording.id)}>
            <strong>{recording.name}</strong><small>{new Date(recording.created_at).toLocaleString()}</small>
            <span className="tag">{states[recording.state]}</span>
            <small>{recording.frame_count ? `${recording.duration_sec.toFixed(2)} 秒 · ${recording.round_count || 0} 轮 · 已确认 ${recording.approved_count || 0}` : "保留连续原画面"}</small>
          </button>
          {recording.state === "recording" && <button type="button" className="danger" disabled={busy || working || stoppingRecordings.includes(recording.id)} onClick={() => void stopRecording(recording.id)}>{stoppingRecordings.includes(recording.id) ? "正在停止…" : "停止并准备标注"}</button>}
          {recording.state === "ready" && <label className="security-check"><input type="checkbox" checked={selectedExports.includes(recording.id)} onChange={e => setSelectedExports(e.target.checked ? [...selectedExports, recording.id] : selectedExports.filter(id => id !== recording.id))} />加入训练清单</label>}
          <button type="button" className="danger" aria-label={`删除录像：${recording.name}`}
            disabled={busy || working || recording.state === "recording" || recording.state === "preparing" || !!recording.active_task_id}
            title={recording.state === "recording" ? "请先停止录制并等待处理完成" : recording.state === "preparing" || recording.active_task_id ? "请等待后台任务完成" : "删除原视频及全部标注"}
            onClick={() => void deleteRecording(recording)}>删除录像</button>
        </div>)}
        {!review.recordings.length && <p className="muted">导入一段连续录像，或从摄像头开始录制。</p>}
      </aside>
      <div className="security-main">
        {detail ? <>
          <section className="panel security-summary"><h2>{detail.recording.name}</h2>
            <div className="actions"><span className="tag">{states[detail.recording.state]}</span><span>采集分组：{detail.recording.source_group}</span></div>
            {detail.recording.error && <p role="alert" className="danger">{detail.recording.error}</p>}
            {!!detail.recording.gaps?.length && <p className="danger">原视频有 {detail.recording.gaps.length} 处时间戳缺口，相关轮次需核验可用性。</p>}
            {detail.recording.active_task_id && <TaskProgress key={detail.recording.active_task_id} id={detail.recording.active_task_id} notify={notify} onDone={() => void review.refresh()} />}
            {detail.recording.state === "failed" && <button disabled={working} onClick={() => void operation(() => post(`${recordingBase(detail.recording.id)}/prepare`))}>重新准备已保存的原文件</button>}
          </section>
          {detail.recording.state === "recording" && <SecurityRecordingPreview key={detail.recording.id}
            recording={detail.recording} stopping={stoppingRecordings.includes(detail.recording.id)} disabled={busy || working}
            onStop={() => void stopRecording(detail.recording.id)} />}
          {detail.recording.state === "ready" && options && config && <>
            <section className="panel security-prelabel"><h2>大模型自动初标</h2><p>当前模型：{config.model}。先自动提出人物、轮次与动作边界，所有结果进入待复核。</p>
              <fieldset disabled={working || busy || dirty || !!detail.recording.active_task_id}>
                <details><summary>窗口设置与动作提示</summary>
                <div className="security-fields">
                  <label>窗口长度（秒）<input type="number" min={1} max={30} step="0.5" value={options.window_sec} onChange={e => setOptions({ ...options, window_sec: Number(e.target.value) })} /></label>
                  <label>重叠时间（秒）<input type="number" min={0.1} max={15} step="0.1" value={options.overlap_sec} onChange={e => setOptions({ ...options, overlap_sec: Number(e.target.value) })} /></label>
                  <label>模型采样帧率<input type="number" min={1} max={60} step={1} value={options.sample_fps} onChange={e => setOptions({ ...options, sample_fps: Number(e.target.value) })} /></label>
                </div>
                <label>动作提示（可选）<textarea rows={2} value={options.action_hint} onChange={e => setOptions({ ...options, action_hint: e.target.value })} placeholder="填写准备验证的动作或观察重点，清单可以后续调整" /></label>
                </details>
                <small>提高采样密度可以提供更多短动作线索；原视频保持完整，边界不清仍标为待确认。</small>
                <button className="primary" onClick={() => void operation(() => post(`${recordingBase(detail.recording.id)}/prelabel`, options))}>开始大模型初标</button>
              </fieldset>
              {detail.latest_draft && <div className="actions"><small>最近完成的模型草稿：{detail.latest_draft.round_count} 轮 · {detail.latest_draft.elapsed_sec.toFixed(1)} 秒</small>
                <button disabled={dirty || working || busy} onClick={() => void replace(`${recordingBase(detail.recording.id)}/apply-draft`, { revision: detail.annotation.revision })}>载入最近模型草稿</button></div>}
            </section>
            <fieldset className="security-review-fieldset" disabled={busy || working}>
              <SecurityAnnotationReview key={detail.recording.id} recording={detail.recording} content={draft}
                regions={config.region_presets} change={value => { if (!busy && !working) review.change(value); }} notify={notify} />
            </fieldset>
            <div className="security-save panel"><div><strong>{dirty ? "有未保存修改" : "已与服务器同步"}</strong>
              <small>版本 {detail.annotation.revision} · 累计复核 {detail.annotation.review_seconds.toFixed(1)} 秒</small></div>
              <div className="actions"><button disabled={!dirty || busy || working} onClick={() => { if (confirm("放弃未保存修改，重新读取服务端标注？")) void review.refresh(true).catch(error => notify(errorText(error))); }}>放弃修改并重新读取</button>
                <button className="primary" disabled={!dirty || busy || working} onClick={() => void review.save()}>{busy ? "正在保存…" : "保存复核结果"}</button></div>
            </div>
            <details className="panel security-legacy"><summary>读取原手动工具的已有标注</summary>
              <label>原工具数据目录<input value={legacyPath} onChange={e => setLegacyPath(e.target.value)} placeholder="包含 labels.json / events.json 的服务器目录" /></label>
              <label>原片段 clip_id<input value={legacyId} onChange={e => setLegacyId(e.target.value)} /></label>
              <button disabled={dirty || busy || working || !legacyPath || !legacyId} onClick={() => void replace(`${recordingBase(detail.recording.id)}/legacy-import`, { revision: detail.annotation.revision, dataset_path: legacyPath, clip_id: legacyId })}>核对原视频并载入两级标注</button>
              <small>复用原工具的标签读取；仅接受与当前录像内容一致的原文件，导入后补充人物角色并复核。</small>
            </details>
          </>}
        </> : <div className="panel empty">选择左侧录像，开始大模型初标与人工复核。</div>}
        <section className="panel security-export"><h2>生成训练清单</h2>
          <p>已勾选 {selectedExports.length} 段录像。仅收录已确认、人物明确、可用且边界清楚的轮次；待确认内容不会转换成负样本。</p>
          <div className="security-fields"><label>验证集比例<select value={valRatio} onChange={e => setValRatio(Number(e.target.value))}>
            <option value={0}>0% · 仅生成训练清单</option><option value={0.2}>20% · 按独立录像组划分</option><option value={0.3}>30% · 按独立录像组划分</option>
          </select></label></div>
          <button disabled={!selectedExports.length || dirty || busy || working} onClick={() => void operation(async () => {
            setManifest(await post(`${inspectionBase}/manifests`, { recording_ids: selectedExports, val_ratio: valRatio }));
          })}>在服务器生成清单</button>
          {manifest && <div role="status"><p>已生成：训练 {manifest.counts.train} 轮，验证 {manifest.counts.val} 轮；跳过 {manifest.skipped_rounds} 轮。</p><code className="security-path">{manifest.path}</code><p>训练直接读取该目录中的 train.jsonl / val.jsonl 及引用的原视频。</p></div>}
          {config && <small>视频、标注与清单保存在服务器：{config.data_root}</small>}
        </section>
      </div>
    </div>
  </div>;
}
