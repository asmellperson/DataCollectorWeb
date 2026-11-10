import { memo, useState } from "react";
import type { BBox, Camera, CollectionSession } from "../types";
import { useLiveCamera } from "../hooks/useLiveCamera";
import CameraPreview from "./CameraPreview";
import { collectionPhase } from "../services/collectionState";

export default memo(function CollectionCameraTile({
  camera,
  session,
  roi,
  roiMode,
  onRoi,
  onStop,
  intervalMs,
  busy,
  canStop = true,
  onViewPending,
}: {
  camera: Camera;
  session?: CollectionSession;
  roi: BBox | null;
  roiMode: boolean;
  onRoi: (roi: BBox | null) => void;
  onStop: () => void;
  intervalMs: number;
  busy: boolean;
  canStop?: boolean;
  onViewPending?: (cameraId: string, pending: boolean) => void;
}) {
  const [drawing, setDrawing] = useState(false);
  const live = useLiveCamera(
    camera.id,
    session?.active && session.detecting,
    intervalMs,
  );
  const editing = drawing && roiMode && !session?.active && !busy;
  return (
    <section
      className={`collection-camera-tile${editing ? " roi-editing" : ""}`}
      aria-label={`${camera.name} 实时画面`}
    >
      <div className="camera-tile-head">
        <strong>{camera.name}</strong>
        <span className={session?.active ? "capture-active" : "muted"}>
          {collectionPhase(session)}
        </span>
      </div>
      <CameraPreview
        cameraId={camera.id}
        live={live}
        onViewPending={onViewPending}
        roi={session?.active ? session.config.roi : roiMode ? roi : null}
        drawing={editing}
        onRoi={(box) => {
          onRoi(box);
          setDrawing(false);
        }}
        message={session?.error || (session?.active && session.phase && session.phase !== "collecting"
          ? collectionPhase(session) : live.status.error || "等待实时画面…")}
      />
      <div className="camera-tile-status">
        <span>
          {live.status.connected ? "● 已连接" : live.status.error || "未连接"} ·{" "}
          {live.status.fps} FPS
        </span>
        <span>
          保存 {session?.saved ?? 0} · 预标 {session?.labeled ?? 0} · 重复{" "}
          {session?.duplicates ?? 0}
        </span>
        {session?.error && (
          <span role="alert" className="danger">
            {session.error}
          </span>
        )}
        {session?.active && <small>
          {session.config.detector === "scrfd" && session.mode.startsWith("detect_") ? "SCRFD 人脸触发 · " : ""}
          保存间隔 {session.config.interval} 秒 · 置信度 {session.config.confidence} · 去重 {session.config.similarity}
        </small>}
      </div>
      <div className="camera-tile-actions">
        {roiMode && !session?.active && (
          <>
            <button
              disabled={!live.status.connected || busy}
              className={editing ? "primary" : ""}
              onClick={() => setDrawing(!editing)}
            >
              {editing ? "取消绘制" : roi ? "重绘 ROI" : "绘制 ROI"}
            </button>
            {roi && (
              <button
                disabled={busy}
                onClick={() => {
                  onRoi(null);
                  setDrawing(false);
                }}
              >
                清除 ROI
              </button>
            )}
          </>
        )}
        {session?.active && canStop && (
          <button disabled={busy || session.stopping} onClick={onStop}>
            {session.stopping ? "正在停止…" : "停止此路"}
          </button>
        )}
      </div>
    </section>
  );
});
