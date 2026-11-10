import { useEffect, useRef, useState } from "react";
import type { CameraView } from "../types";
import { api, errorText } from "../services/api";
import { defaultCameraView } from "../services/cameraView";
import { withRequestTimeout } from "../services/requestTimeout";

export function useCameraView(cameraId: string) {
  const [view, setView] = useState<CameraView>(defaultCameraView);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [pending, setPending] = useState(false);
  const queue = useRef<(value: CameraView) => void>(() => {});
  useEffect(() => {
    let stopped = false;
    let desired: CameraView | null = null;
    let sending = false;
    let applied = defaultCameraView;
    let debounce: ReturnType<typeof setTimeout>;
    setReady(false);
    setError("");
    setPending(false);
    setView(defaultCameraView);
    const path = `/cameras/${encodeURIComponent(cameraId)}/view`;
    async function send() {
      if (sending) return;
      sending = true;
      while (desired) {
        const next = desired;
        desired = null;
        try {
          applied = await withRequestTimeout(signal => api<CameraView>(path, {
            method: "PUT", body: JSON.stringify(next), keepalive: true, signal,
          }), { timeoutMs: 5000, message: "应用放大设置超时，请检查网络连接" });
          if (!stopped) {
            setError("");
            if (!desired) setView(applied);
          }
        } catch (e) {
          if (!stopped) {
            setError(errorText(e));
            if (!desired) setView(applied);
          }
        }
      }
      sending = false;
      if (!stopped) setPending(false);
    }
    queue.current = value => {
      desired = value;
      setView(value);
      setPending(true);
      clearTimeout(debounce);
      debounce = setTimeout(() => void send(), 80);
    };
    if (cameraId) {
      withRequestTimeout(signal => api<CameraView>(path, { signal }), {
        timeoutMs: 5000, message: "读取放大设置超时，请刷新页面重试",
      }).then(value => {
        if (!stopped) { applied = value; setView(value); setReady(true); }
      }).catch(e => { if (!stopped) setError(errorText(e)); });
    }
    return () => {
      clearTimeout(debounce);
      // Persist the user's final slider position even when leaving the page.
      stopped = true;
      if (desired) void send();
      queue.current = () => {};
    };
  }, [cameraId]);
  return { view, ready, pending, error, changeView: (value: CameraView) => queue.current(value) };
}
