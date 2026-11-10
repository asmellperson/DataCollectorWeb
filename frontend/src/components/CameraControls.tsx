import { useEffect, useRef, useState } from "react";
import type { CameraView } from "../types";
import { api, errorText } from "../services/api";
import { defaultCameraView } from "../services/cameraView";
import { createLensControl } from "../services/lensControl";
import { withRequestTimeout } from "../services/requestTimeout";

export default function CameraControls({ cameraId, view, onView, ready, pending, viewError, connected, selecting, onSelect }: {
  cameraId: string; view: CameraView; onView: (view: CameraView) => void;
  ready: boolean; pending: boolean; viewError: string; connected: boolean;
  selecting: boolean; onSelect: () => void;
}) {
  const [lensReady, setLensReady] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [lensError, setLensError] = useState("");
  const [speed, setSpeed] = useState(0);
  const held = useRef(false);
  const controller = useRef<ReturnType<typeof createLensControl> | null>(null);
  const path = `/cameras/${encodeURIComponent(cameraId)}/lens`;
  useEffect(() => {
    let mounted = true;
    const control = createLensControl(value => withRequestTimeout(signal => api(path, {
      method: "POST", body: JSON.stringify({ speed: value }), keepalive: true, signal,
    }), { timeoutMs: 5000, message: "镜头控制请求超时，请检查摄像头网络" }), error => {
      if (mounted) { setLensError(errorText(error)); held.current = false; setSpeed(0); setLensReady(false); }
    });
    controller.current = control;
    function stop() { held.current = false; setSpeed(0); control.stop(); }
    function hidden() { if (document.hidden) stop(); }
    window.addEventListener("blur", stop);
    window.addEventListener("pagehide", stop);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      mounted = false;
      held.current = false;
      control.dispose();
      window.removeEventListener("blur", stop);
      window.removeEventListener("pagehide", stop);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [path]);
  function stop() { held.current = false; setSpeed(0); controller.current?.stop(); }
  async function connect() {
    setConnecting(true); setLensError("");
    try {
      await withRequestTimeout(signal => api(path, { signal }), {
        timeoutMs: 20000, message: "镜头连接超时，请检查 ONVIF 服务设置",
      });
      setLensReady(true);
    }
    catch (e) { setLensError(errorText(e)); }
    finally { setConnecting(false); }
  }
  return <div className="camera-controls">
    <div className="camera-control-title">
      <strong>摄像头变焦</strong>
      {!lensReady && <button type="button" disabled={connecting} onClick={() => void connect()}>
        {connecting ? "正在连接…" : "连接镜头控制"}
      </button>}
      <span>{speed === 0 ? "已停止" : speed > 0 ? "持续拉近" : "持续拉远"}</span>
    </div>
    <label className="camera-slider">
      <span>拉远 −</span>
      <input type="range" min={-100} max={100} step={5} value={speed}
        aria-label="摄像头变焦速度" aria-valuetext={speed === 0 ? "停止" : `${speed > 0 ? "拉近" : "拉远"} ${Math.abs(speed)}%`}
        disabled={!lensReady}
        onPointerDown={e => { held.current = true; e.currentTarget.setPointerCapture(e.pointerId); }}
        onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={stop} onBlur={stop}
        onKeyDown={e => {
          if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) held.current = true;
        }}
        onKeyUp={stop}
        onChange={e => {
          if (!held.current) return;
          const value = Number(e.target.value);
          setSpeed(value); controller.current?.setSpeed(value / 100);
        }} />
      <span>＋ 拉近</span>
    </label>
    <small>拖动并保持即可持续调整，松手停止；离中心越远，调整越快。</small>
    {lensError && <p role="alert" className="danger">{lensError}</p>}
    <div className="camera-control-title">
      <strong>局部查看</strong>
      <button type="button" disabled={!ready || !connected || pending} className={selecting ? "primary" : ""} onClick={onSelect}>
        {selecting ? "取消选中心" : "点击画面选中心"}
      </button>
      <button type="button" disabled={!ready} onClick={() => onView({ ...defaultCameraView })}>恢复全景</button>
    </div>
    <label className="camera-slider">
      <span>1×</span>
      <input aria-label="局部放大倍率" type="range" min={1} max={8} step={.1} value={view.zoom}
        disabled={!ready} onChange={e => onView({ ...view, zoom: Number(e.target.value) })} />
      <output>{view.zoom.toFixed(1)}×</output>
    </label>
    <small>{pending ? "正在应用放大设置…" : selecting ? "点击画面中的目标设为中心，然后拖动倍率滑条。" : "以所选中心放大；数据采集会保存同样的放大画面。"}</small>
    {viewError && <p role="alert" className="danger">{viewError}</p>}
  </div>;
}
