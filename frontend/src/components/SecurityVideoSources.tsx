import { useEffect, useState } from "react";
import { api, post, errorText } from "../services/api";
import { inspectionBase } from "../services/securityAnnotation";
import type { Camera } from "../types";

export default function SecurityVideoSources({ disabled, onCreated, notify }: {
  disabled: boolean; onCreated: (id: string) => Promise<void>; notify: (message: string) => void;
}) {
  const [mode, setMode] = useState("file");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [group, setGroup] = useState("");
  const [camera, setCamera] = useState("");
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);
  useEffect(() => {
    api<Camera[]>("/cameras").then(setCameras).catch(error => notify(errorText(error)));
  }, [notify]);
  return <details className="panel security-source" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>接入连续原视频</summary>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true);
      try {
        const value = await post<{ recording_id: string }>(mode === "file" ? `${inspectionBase}/recordings/import` : `${inspectionBase}/capture`,
          { ...(mode === "file" ? { path } : { camera_id: camera }), name, source_group: group });
        await onCreated(value.recording_id);
        setOpen(false);
      } catch (error) { notify(errorText(error)); }
      finally { setBusy(false); }
    }}><fieldset disabled={disabled || busy}>
      <div className="security-fields">
        <label>来源<select value={mode} onChange={e => setMode(e.target.value)}><option value="file">服务器已有录像</option><option value="rtsp">摄像头连续录制</option></select></label>
        {mode === "file" ? <label>服务器视频路径<input required value={path} onChange={e => setPath(e.target.value)} placeholder="/data/videos/下班安检.mp4" /></label> :
          <label>摄像头<select required value={camera} onChange={e => setCamera(e.target.value)}><option value="">请选择已配置摄像头</option>{cameras.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}
        <label>录像名称（可选）<input value={name} onChange={e => setName(e.target.value)} /></label>
        <label>采集分组（可选）<input value={group} onChange={e => setGroup(e.target.value)} placeholder="同一批连续录像使用相同分组" /></label>
      </div>
      <p>保存连续画面，保留安检开始前的过程。已有文件直接在服务器入库；录制在后台持续运行，可在录像列表停止。</p>
      <button className="primary">{busy ? "正在提交…" : mode === "file" ? "原视频入库" : "开始连续录制"}</button>
    </fieldset></form>
  </details>;
}
