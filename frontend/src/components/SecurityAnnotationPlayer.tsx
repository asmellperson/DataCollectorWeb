import { useEffect, useRef, useState } from "react";
import { frameAtTime, recordingBase } from "../services/securityAnnotation";
import type { InspectionPerson, InspectionRecording } from "../types/securityAnnotation";

export default function SecurityAnnotationPlayer({ recording, times, frame, seek, jumpTo, persons, markPerson, onEvidence, onPlayingChange }: {
  recording: InspectionRecording; times: number[]; frame: number; seek: (frame: number) => void;
  jumpTo: { frame: number; sequence: number } | null;
  persons: InspectionPerson[]; markPerson: string;
  onEvidence: (id: string, index: number, bbox: [number, number, number, number]) => void;
  onPlayingChange: (playing: boolean) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [still, setStill] = useState(false);
  const [rate, setRate] = useState(1);
  const [origin, setOrigin] = useState<[number, number] | null>(null);
  const [drawing, setDrawing] = useState<[number, number, number, number] | null>(null);
  function jump(index: number) {
    const target = Math.max(0, Math.min(times.length - 1, index));
    video.current?.pause();
    if (video.current) video.current.currentTime = times[target] || 0;
    setStill(true);
    seek(target);
  }
  useEffect(() => { if (jumpTo) jump(jumpTo.frame); }, [jumpTo]);
  useEffect(() => {
    if (markPerson && video.current) jump(frameAtTime(times, video.current.currentTime));
  }, [markPerson]);
  function point(event: React.PointerEvent<SVGSVGElement>): [number, number] | null {
    const matrix = event.currentTarget.getScreenCTM();
    if (!matrix) return null;
    const p = event.currentTarget.createSVGPoint();
    p.x = event.clientX; p.y = event.clientY;
    const value = p.matrixTransform(matrix.inverse());
    return [Math.max(0, Math.min(1, value.x)), Math.max(0, Math.min(1, value.y))];
  }
  const boxes = persons.flatMap(person => person.evidence.filter(e => e.frame_index === frame && e.bbox).map(e => ({ person, box: e.bbox! })));
  return <section className="security-player panel">
    <div className="security-video-stage" style={{ aspectRatio: `${recording.width} / ${recording.height}` }}>
      <video ref={video} src={`/api${recordingBase(recording.id)}/video`} controls preload="metadata"
        onPlay={() => { setStill(false); onPlayingChange(true); }}
        onPause={() => { onPlayingChange(false); if (video.current) seek(frameAtTime(times, video.current.currentTime)); }}
        onTimeUpdate={() => { if (!still && video.current) seek(frameAtTime(times, video.current.currentTime)); }} />
      {still && <img className="security-still" src={`/api${recordingBase(recording.id)}/frame?index=${frame}`} alt={`原视频第 ${frame + 1} 帧`} />}
      {(still || markPerson) && <svg className={`security-boxes ${markPerson ? "drawing" : ""}`}
        viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="人物位置标记"
        onPointerDown={event => {
          if (!markPerson) return;
          jump(frame);
          const p = point(event); if (!p) return;
          event.currentTarget.setPointerCapture(event.pointerId); setOrigin(p); setDrawing([p[0], p[1], p[0], p[1]]);
        }} onPointerMove={event => {
          const p = point(event); if (!origin || !p) return;
          setDrawing([Math.min(origin[0], p[0]), Math.min(origin[1], p[1]), Math.max(origin[0], p[0]), Math.max(origin[1], p[1])]);
        }} onPointerUp={event => {
          const p = point(event);
          if (origin && p && Math.abs(p[0] - origin[0]) > 0.005 && Math.abs(p[1] - origin[1]) > 0.005) {
            onEvidence(markPerson, frame, [Math.min(origin[0], p[0]), Math.min(origin[1], p[1]), Math.max(origin[0], p[0]), Math.max(origin[1], p[1])]);
          }
          setOrigin(null); setDrawing(null);
        }} onPointerCancel={() => { setOrigin(null); setDrawing(null); }}>
        {boxes.map(({ person, box }) => <g key={person.id}>
          <rect x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} className={person.role} />
          <text x={box[0]} y={Math.max(0.025, box[1] - 0.01)}>{person.description || person.id}</text>
        </g>)}
        {drawing && <rect x={drawing[0]} y={drawing[1]} width={drawing[2] - drawing[0]} height={drawing[3] - drawing[1]} className="draft" />}
      </svg>}
    </div>
    <div className="security-player-controls">
      <div className="actions">
        <button type="button" onClick={() => jump(frame - 1)} disabled={!times.length || frame <= 0}>上一帧</button>
        <button type="button" onClick={() => jump(frame + 1)} disabled={!times.length || frame >= times.length - 1}>下一帧</button>
        <button type="button" onClick={() => { setStill(false); void video.current?.play().catch(() => {}); }}>继续播放</button>
        <select aria-label="播放速度" value={rate} onChange={e => { const value = Number(e.target.value); setRate(value); if (video.current) video.current.playbackRate = value; }}>
          {[0.25, 0.5, 1, 2].map(value => <option key={value} value={value}>{value} 倍速</option>)}
        </select>
        <span>第 {frame + 1} / {recording.frame_count} 帧 · {(times[frame] || 0).toFixed(3)} 秒</span>
      </div>
      <input aria-label="原视频帧时间轴" type="range" min={0} max={Math.max(0, times.length - 1)} value={frame} onChange={e => jump(Number(e.target.value))} />
      {markPerson && <p className="security-hint">正在标记人物位置：在画面上拖框。位置只附在当前证据帧。</p>}
      {!markPerson && <small>逐帧按钮显示原视频帧；人物框只显示在已有位置证据的帧上。</small>}
    </div>
  </section>;
}
