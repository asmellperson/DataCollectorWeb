import { useEffect, useState } from "react";
import { api, errorText } from "../services/api";
export function useLiveCamera(cameraId: string, detecting = false) {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<any>({
    connected: false,
    fps: 0,
    width: 0,
    height: 0,
    error: "未连接",
  });
  useEffect(() => {
    let stopped = false;
    let current = "";
    let frameTimer: ReturnType<typeof setTimeout>;
    let statusTimer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setUrl("");
    setStatus({
      connected: false,
      fps: 0,
      width: 0,
      height: 0,
      error: "正在连接",
    });
    if (!cameraId) return;
    async function pollStatus() {
      try {
        const s = await api(`/cameras/${cameraId}/status`, {
          signal: controller.signal,
        });
        if (!stopped) setStatus(s);
      } catch (e) {
        if (!stopped)
          setStatus((s: any) => ({
            ...s,
            connected: false,
            error: errorText(e),
          }));
      }
      if (!stopped) statusTimer = setTimeout(pollStatus, 1000);
    }
    async function pollFrame() {
      try {
        const response = await fetch(
          detecting
            ? "/api/collection/frame"
            : `/api/cameras/${cameraId}/frame`,
          { signal: controller.signal },
        );
        if (response.ok) {
          const blob = await response.blob();
          if (!stopped) {
            const next = URL.createObjectURL(blob);
            setUrl(next);
            if (current) URL.revokeObjectURL(current);
            current = next;
          }
        } else if (response.status !== 503) {
          if (!stopped)
            setStatus((s: any) => ({ ...s, error: "预览加载失败" }));
        }
      } catch (e) {
        if (!stopped)
          setStatus((s: any) => ({
            ...s,
            connected: false,
            error: errorText(e),
          }));
      }
      if (!stopped) frameTimer = setTimeout(pollFrame, 150);
    }
    void pollStatus();
    void pollFrame();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(frameTimer);
      clearTimeout(statusTimer);
      if (current) URL.revokeObjectURL(current);
    };
  }, [cameraId, detecting]);
  return { url, status };
}
