import { useEffect, useState } from "react";
import { api, errorText } from "../services/api";
import { frameAtTime, inspectionId, mergeRounds, newRound, recordingBase, splitRound } from "../services/securityAnnotation";
import type { InspectionContent, InspectionPerson, InspectionRecording, InspectionRound } from "../types/securityAnnotation";
import SecurityAnnotationPlayer from "./SecurityAnnotationPlayer";
import SecurityRoundEditor from "./SecurityRoundEditor";

export default function SecurityAnnotationReview({ recording, content, regions, change, notify }: {
  recording: InspectionRecording; content: InspectionContent; regions: { key: string; name: string }[];
  change: (value: InspectionContent) => void; notify: (message: string) => void;
}) {
  const [times, setTimes] = useState<number[]>([]);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState("");
  const [markPerson, setMarkPerson] = useState("");
  const [jumpTo, setJumpTo] = useState<{ frame: number; sequence: number } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api<{ times_sec: number[] }>(`${recordingBase(recording.id)}/timeline`, { signal: controller.signal })
      .then(value => setTimes(value.times_sec)).catch(error => { if (!controller.signal.aborted) notify(errorText(error)); });
    return () => controller.abort();
  }, [recording.id, notify]);
  const ordered = [...content.rounds].sort((a, b) => a.start_sec - b.start_sec);
  const item = ordered.find(r => r.id === selected) || ordered[0];
  const start = times[frame] || 0;
  const end = times[frame + 1] ?? recording.duration_sec;
  function jump(seconds: number) { setJumpTo({ frame: frameAtTime(times, seconds), sequence: Date.now() + Math.random() }); }
  function updateRound(value: InspectionRound) {
    change({ ...content, rounds: content.rounds.map(r => r.id === value.id ? value : r) });
  }
  function updatePerson(person: InspectionPerson) {
    change({ persons: content.persons.map(p => p.id === person.id ? person : p),
      rounds: content.rounds.map(r => r.subject_id === person.id || r.inspector_id === person.id || r.events.some(e => e.actor_id === person.id)
        ? { ...r, review_status: "pending" } : r) });
  }
  function split() {
    if (!item) return;
    try {
      const result = splitRound(item, start);
      change({ ...content, rounds: content.rounds.flatMap(r => r.id === item.id ? result : [r]) });
      setSelected(result[1].id);
    } catch (error) { notify(errorText(error)); }
  }
  function merge() {
    if (!item) return;
    const next = ordered[ordered.indexOf(item) + 1];
    if (!next || !confirm("合并当前轮与下一轮？合并后需重新复核人物、边界和重复动作。")) return;
    try {
      const merged = mergeRounds(item, next);
      change({ ...content, rounds: content.rounds.filter(r => r.id !== next.id).map(r => r.id === item.id ? merged : r) });
      setSelected(merged.id);
    } catch (error) { notify(errorText(error)); }
  }
  return <div className="security-review">
    <div className="security-viewer">
    <SecurityAnnotationPlayer recording={recording} times={times} frame={frame} seek={setFrame} jumpTo={jumpTo}
      persons={content.persons} markPerson={markPerson} onPlayingChange={setPlaying} onEvidence={(id, index, bbox) => {
        const person = content.persons.find(p => p.id === id);
        if (person) updatePerson({ ...person, evidence: [...person.evidence.filter(e => e.frame_index !== index), { frame_index: index, bbox }] });
        setMarkPerson("");
      }} />
    <details className="panel security-people" onToggle={event => { if (!event.currentTarget.open) setMarkPerson(""); }}>
      <summary>人物与角色 · {content.persons.length}</summary>
      <div className="panel-title"><button type="button" onClick={() => change({ ...content,
        persons: [...content.persons, { id: inspectionId(), role: "unknown", description: "", evidence: [] }] })}>＋ 添加人物</button></div>
      {!content.persons.length && <p className="muted">大模型初标后会列出人物；身份或角色不清时保留待确认。</p>}
      {content.persons.map(person => <div className="security-person" key={person.id}>
        <label>人物描述<input value={person.description} onChange={e => updatePerson({ ...person, description: e.target.value })} placeholder="衣着、所在位置等可见特征" /></label>
        <label>角色<select value={person.role} onChange={e => updatePerson({ ...person, role: e.target.value as InspectionPerson["role"] })}>
          <option value="unknown">待确认</option><option value="inspector">安检员</option><option value="subject">受检人</option>
        </select></label>
        <div className="actions">
          <button type="button" onClick={() => setMarkPerson(markPerson === person.id ? "" : person.id)}>{markPerson === person.id ? "取消画框" : "标记当前帧人物位置"}</button>
          {person.evidence.slice(-3).map(e => <button type="button" key={e.frame_index} onClick={() => jump(times[e.frame_index] || 0)}>帧 {e.frame_index + 1}</button>)}
          <button type="button" className="danger" onClick={() => {
            if (content.rounds.some(r => r.subject_id === person.id || r.inspector_id === person.id || r.events.some(e => e.actor_id === person.id))) {
              notify("此人物仍关联轮次或动作，请先修正关联再删除"); return;
            }
            change({ ...content, persons: content.persons.filter(p => p.id !== person.id) });
          }}>删除</button>
          <small title={person.id}>ID {person.id.length > 16 ? person.id.slice(0, 16) + "…" : person.id}</small>
        </div>
      </div>)}
    </details>
    </div>
    <div className="security-review-forms">
    <section className="panel security-rounds">
      <div className="panel-title"><h2>受检轮次 · {ordered.length}</h2><div className="actions">
        <button type="button" onClick={() => { const value = newRound(start, Math.min(recording.duration_sec, Math.max(end, start + 1))); change({ ...content, rounds: [...content.rounds, value] }); setSelected(value.id); }}>＋ 补充漏轮</button>
        <button type="button" disabled={!item || playing} onClick={split}>在当前帧拆轮</button>
        <button type="button" disabled={!item || ordered.indexOf(item) === ordered.length - 1} onClick={merge}>合并下一轮</button>
      </div></div>
      <div className="security-table-scroll"><table><thead><tr><th>轮次</th><th>受检人</th><th>时间范围</th><th>安检</th><th>复核</th></tr></thead>
        <tbody>{ordered.map((round, index) => <tr key={round.id} className={item?.id === round.id ? "selected" : ""}>
          <td><button type="button" onClick={() => { setSelected(round.id); jump(Math.max(0, round.start_sec - 1)); }}>第 {index + 1} 轮</button></td>
          <td>{content.persons.find(p => p.id === round.subject_id)?.description || round.subject_id || "待确认"}</td>
          <td>{round.start_sec.toFixed(3)}–{round.end_sec.toFixed(3)} 秒</td>
          <td>{{ present: "有", absent: "无", uncertain: "待确认" }[round.inspection]}</td>
          <td>{{ pending: "待复核", approved: "已确认", rejected: "已排除" }[round.review_status]}</td>
        </tr>)}</tbody></table></div>
      {!ordered.length && <p className="muted">先运行大模型初标；人工可在这里补充漏轮、拆轮或合轮。</p>}
    </section>
    {item && <SecurityRoundEditor item={item} persons={content.persons} start={start} end={end} frame={frame} playing={playing}
      regions={regions} change={updateRound} jump={jump} />}
    </div>
  </div>;
}
