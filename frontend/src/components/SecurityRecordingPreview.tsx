import { useEffect, useState } from "react";
import { useLiveCamera } from "../hooks/useLiveCamera";
import type { InspectionRecording } from "../types/securityAnnotation";

export function SecurityCameraPreview({ cameraId, name }: { cameraId: string; name: string }) {
  const { url, status } = useLiveCamera(cameraId, false, 200);
  return <div className="security-capture-preview">
    {url && <img src={url} alt={`${name}实时画面`} />}
    {!status.connected && <div className="security-preview-message" role="status">
      {status.error || "正在连接摄像头…"}
    </div>}
    {status.connected && !url && <div className="security-preview-message" role="status">正在加载实时画面…</div>}
    {status.connected && url && <span className="security-live-label">实时画面</span>}
  </div>;
}

export default function SecurityRecordingPreview({ recording, stopping, disabled, onStop }: {
  recording: InspectionRecording;
  stopping: boolean;
  disabled: boolean;
  onStop: () => void;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - Date.parse(recording.created_at)) / 1000));
  const elapsed = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map(value => String(value).padStart(2, "0")).join(":");
  return <section className="panel security-capture">
    <div className="panel-title"><h2>录制实时预览</h2>
      <span className="security-recording-time" role="timer" aria-label="已录制时长">已录制 {elapsed}</span>
    </div>
    {recording.camera_id ? <SecurityCameraPreview cameraId={recording.camera_id} name={recording.name} /> :
      <p role="status">此录像缺少摄像头信息，无法显示实时预览。</p>}
    <div className="actions security-capture-actions">
      <span>{stopping ? "正在结束录制，随后准备标注视频…" : "观察现场画面，在需要的位置停止录制。"}</span>
      <button type="button" className="danger" disabled={disabled || stopping} onClick={onStop}>
        {stopping ? "正在停止…" : "停止并准备标注"}
      </button>
    </div>
  </section>;
}
