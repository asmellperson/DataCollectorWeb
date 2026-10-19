import type { InspectionEvent, InspectionPerson, InspectionRound } from "../types/securityAnnotation";
import { newEvent } from "../services/securityAnnotation";

export default function SecurityRoundEditor({ item, persons, start, end, frame, playing, regions, change, jump }: {
  item: InspectionRound; persons: InspectionPerson[]; start: number; end: number; frame: number;
  playing: boolean;
  regions: { key: string; name: string }[]; change: (value: InspectionRound) => void; jump: (seconds: number) => void;
}) {
  function edit(patch: Partial<InspectionRound>) { change({ ...item, ...patch, review_status: "pending" }); }
  function eventEdit(id: string, patch: Partial<InspectionEvent>) {
    edit({ events: item.events.map(event => event.id === id ? { ...event, ...patch } : event) });
  }
  const personOptions = (role: "subject" | "inspector") => persons.filter(p => p.role === role || p.role === "unknown");
  return <section className="panel security-round-editor">
    <h2>本轮复核</h2>
    <div className="security-fields">
      <label>受检人<select value={item.subject_id || ""} onChange={e => edit({ subject_id: e.target.value || null })}>
        <option value="">待确认</option>{personOptions("subject").map(p => <option key={p.id} value={p.id}>{p.description || p.id}</option>)}
      </select></label>
      <label>安检员<select value={item.inspector_id || ""} onChange={e => edit({ inspector_id: e.target.value || null })}>
        <option value="">待确认</option>{personOptions("inspector").map(p => <option key={p.id} value={p.id}>{p.description || p.id}</option>)}
      </select></label>
      <label>是否发生安检<select value={item.inspection} onChange={e => edit({ inspection: e.target.value as InspectionRound["inspection"] })}>
        <option value="present">有安检</option><option value="absent">明确没有安检</option><option value="uncertain">待确认</option>
      </select></label>
      <label>画面可用性<select value={item.quality} onChange={e => edit({ quality: e.target.value as InspectionRound["quality"] })}>
        <option value="usable">可用</option><option value="uncertain">待确认</option><option value="unusable">不可用</option>
      </select></label>
    </div>
    <div className="security-fields">
      <label>开始时间（秒）<input type="number" min={0} step="0.001" value={item.start_sec} onChange={e => edit({ start_sec: Number(e.target.value) })} />
        <button type="button" disabled={playing} onClick={() => edit({ start_sec: start })}>当前帧设为起点</button></label>
      <label>结束时间（秒）<input type="number" min={0} step="0.001" value={item.end_sec} onChange={e => edit({ end_sec: Number(e.target.value) })} />
        <button type="button" disabled={playing} onClick={() => edit({ end_sec: end })}>当前帧末设为终点</button></label>
      <label className="security-check"><input type="checkbox" checked={item.boundary_uncertain} onChange={e => edit({ boundary_uncertain: e.target.checked })} />轮次边界待确认</label>
    </div>
    <small>暂停或逐帧定位后设置边界，结束时间取当前帧末。可回看本轮前后原画面，快慢不作为合规标签。</small>
    <label>本轮备注<textarea value={item.notes} rows={2} onChange={e => edit({ notes: e.target.value })} placeholder="遮挡、人物归属、边界或漏动作等复核说明" /></label>
    {Object.keys(item.coverage).length > 0 && <details><summary>原工具的部位覆盖标签</summary>
      <div className="security-fields">{Object.entries(item.coverage).map(([key, value]) => <label key={key}>{regions.find(r => r.key === key)?.name || key}
        <select value={value} onChange={e => edit({ coverage: { ...item.coverage, [key]: e.target.value as typeof value } })}>
          <option value="scanned">已扫描</option><option value="not_scanned">未扫描</option><option value="uncertain">待确认</option>
        </select></label>)}</div>
    </details>}
    <div className="panel-title"><h3>动作时间段 · {item.events.length}</h3>
      <button type="button" onClick={() => edit({ events: [...item.events, newEvent(Math.max(item.start_sec, Math.min(start, item.end_sec - 0.001)), item.end_sec, item.inspector_id)] })}>＋ 添加动作</button>
    </div>
    <datalist id="security-region-presets">{regions.map(r => <option key={r.key} value={r.key}>{r.name}</option>)}</datalist>
    {item.events.map((event, index) => <div className="security-event" key={event.id}>
      <div className="actions"><strong>动作 {index + 1}</strong>
        <button type="button" onClick={() => jump(event.start_sec)}>定位开始</button>
        <button type="button" onClick={() => edit({ events: item.events.filter(e => e.id !== event.id) })} className="danger">删除动作</button>
      </div>
      <div className="security-fields">
        <label>动作名称<input value={event.action} onChange={e => eventEdit(event.id, { action: e.target.value })} /></label>
        <label>部位（可空）<input list="security-region-presets" value={event.region} onChange={e => eventEdit(event.id, { region: e.target.value })} /></label>
        <label>执行人<select value={event.actor_id || ""} onChange={e => eventEdit(event.id, { actor_id: e.target.value || null })}>
          <option value="">待确认</option>{persons.filter(p => p.id !== item.subject_id).map(p => <option key={p.id} value={p.id}>{p.description || p.id}</option>)}
        </select></label>
      </div>
      <div className="security-fields">
        <label>开始（秒）<input type="number" step="0.001" min={0} value={event.start_sec} onChange={e => eventEdit(event.id, { start_sec: Number(e.target.value) })} />
          <button type="button" disabled={playing} onClick={() => eventEdit(event.id, { start_sec: start })}>当前帧设为开始</button></label>
        <label>结束（秒）<input type="number" step="0.001" min={0} value={event.end_sec} onChange={e => eventEdit(event.id, { end_sec: Number(e.target.value) })} />
          <button type="button" disabled={playing} onClick={() => eventEdit(event.id, { end_sec: end })}>当前帧末设为结束</button></label>
        <label>观察结果<select value={event.observation} onChange={e => eventEdit(event.id, { observation: e.target.value as InspectionEvent["observation"] })}>
          <option value="observed">明确观察到</option><option value="uncertain">待确认</option>
        </select></label>
        <label className="security-check"><input type="checkbox" checked={event.boundary_uncertain} onChange={e => eventEdit(event.id, { boundary_uncertain: e.target.checked })} />动作边界待确认</label>
      </div>
      <label>动作备注<input value={event.notes} onChange={e => eventEdit(event.id, { notes: e.target.value })} /></label>
      <div className="actions"><small>证据帧：{event.evidence_frames.map(f => f + 1).join("、") || "尚未记录"}</small>
        <button type="button" onClick={() => eventEdit(event.id, { evidence_frames: [...new Set([...event.evidence_frames, frame])] })}>记录当前帧</button>
      </div>
    </div>)}
    {!item.events.length && <p className="muted">暂无动作时间段；可以保存待确认结果，或先复核安检是否发生。</p>}
    <div className="actions">
      <button type="button" onClick={() => change({ ...item, review_status: "approved" })}>确认本轮</button>
      <button type="button" onClick={() => change({ ...item, review_status: "rejected" })}>排除此轮</button>
      <span className="tag">{{ pending: "待复核", approved: "已确认，保存后生效", rejected: "已排除，保存后生效" }[item.review_status]}</span>
    </div>
  </section>;
}
