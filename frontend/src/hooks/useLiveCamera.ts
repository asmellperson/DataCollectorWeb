import { useEffect, useState } from "react";
import { api, errorText } from "../services/api";
import { withRequestTimeout } from "../services/requestTimeout";
import type { CameraView } from "../types";
import { defaultCameraView } from "../services/cameraView";
export function useLiveCamera(
  cameraId: string,
  detecting = false,
  intervalMs = 150,
  original = false,
) {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<any>({
    connected: false,
    fps: 0,
    width: 0,
    height: 0,
    error: "未连接",
  });
  const [frameView, setFrameView] = useState<CameraView>(defaultCameraView);
  useEffect(() => {
    let stopped = false;
    let current = "";
    let frameTimer: ReturnType<typeof setTimeout>;
    let statusTimer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setUrl("");
    setFrameView(defaultCameraView);
    setStatus({
      connected: false,
      fps: 0,
      width: 0,
      height: 0,
      error: "正在连接",
    });
    if (!cameraId) return;
    async function pollStatus() {
      if (document.hidden) {
        statusTimer = setTimeout(pollStatus, 1000);
        return;
      }
      try {
        const s = await withRequestTimeout(
          (signal) => api(`/cameras/${encodeURIComponent(cameraId)}/status`, {
            signal,
            cache: "no-store",
          }),
          {
            signal: controller.signal,
            timeoutMs: 5000,
            message: "摄像头状态请求超时，请检查网络连接后重试。",
          },
        );
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
      if (document.hidden) {
        frameTimer = setTimeout(pollFrame, 1000);
        return;
      }
      try {
        const frame = await withRequestTimeout(async (signal) => {
          const response = await fetch(
            detecting
              ? `/api/collection/frame?camera_id=${encodeURIComponent(cameraId)}`
              : `/api/cameras/${encodeURIComponent(cameraId)}/frame${original ? "?original=true" : ""}`,
            { signal, cache: "no-store" },
          );
          if (response.status === 503) return null;
          if (!response.ok) throw new Error("预览加载失败");
          return { blob: await response.blob(), view: JSON.parse(response.headers.get("X-Camera-View") || JSON.stringify(defaultCameraView)) as CameraView };
        }, {
          signal: controller.signal,
          timeoutMs: 5000,
          message: "摄像头预览请求超时，请检查网络连接后重试。",
        });
        if (frame) {
          if (!stopped) {
            const next = URL.createObjectURL(frame.blob);
            setUrl(next);
            setFrameView(frame.view);
            if (current) URL.revokeObjectURL(current);
            current = next;
          }
        } else {
          if (!stopped) setUrl("");
        }
      } catch (e) {
        if (!stopped)
          setStatus((s: any) => ({
            ...s,
            connected: false,
            error: errorText(e),
          }));
      }
      if (!stopped) frameTimer = setTimeout(pollFrame, intervalMs);
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
  }, [cameraId, detecting, intervalMs, original]);
  return { url, status, frameView };
}
