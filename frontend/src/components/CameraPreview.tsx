import { useEffect, useState } from "react";
import type { BBox } from "../types";
import { useCameraView } from "../hooks/useCameraView";
import type { useLiveCamera } from "../hooks/useLiveCamera";
import { selectViewCenter, viewBounds } from "../services/cameraView";
import CameraControls from "./CameraControls";
import LivePreview from "./LivePreview";

export default function CameraPreview({ cameraId, live, roi = null, drawing = false, onRoi = () => {}, message = "等待实时画面…", onViewPending }: {
  cameraId: string; live: ReturnType<typeof useLiveCamera>;
  roi?: BBox | null; drawing?: boolean; onRoi?: (box: BBox) => void; message?: string;
  onViewPending?: (cameraId: string, pending: boolean) => void;
}) {
  const local = useCameraView(cameraId);
  const [selecting, setSelecting] = useState(false);
  useEffect(() => { if (drawing) setSelecting(false); }, [drawing]);
  useEffect(() => { onViewPending?.(cameraId, local.pending); }, [cameraId, local.pending, onViewPending]);
  useEffect(() => () => onViewPending?.(cameraId, false), [cameraId, onViewPending]);
  const width = live.status.width, height = live.status.height;
  const crop = viewBounds(width || 1, height || 1, live.frameView);
  const center = {
    x: (local.view.center_x * width - crop.x) / crop.width,
    y: (local.view.center_y * height - crop.y) / crop.height,
  };
  return <>
    <LivePreview url={live.status.connected ? live.url : ""} width={width} height={height}
      roi={roi} drawing={drawing} onRoi={onRoi} message={message}
      center={local.ready && live.url && (local.view.zoom > 1 || local.view.center_x !== .5 || local.view.center_y !== .5)
        && center.x >= 0 && center.x <= 1 && center.y >= 0 && center.y <= 1 ? center : undefined}
      onCenter={selecting && !drawing && !local.pending ? (x, y) => {
        const selected = selectViewCenter(x, y, width, height, live.frameView);
        local.changeView({ ...selected, zoom: local.view.zoom });
        setSelecting(false);
      } : undefined} />
    <CameraControls cameraId={cameraId} view={local.view} onView={local.changeView}
      ready={local.ready} pending={local.pending} viewError={local.error}
      connected={live.status.connected && !drawing} selecting={selecting} onSelect={() => setSelecting(value => !value)} />
  </>;
}
