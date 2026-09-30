import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Stage, Layer, Rect, Text } from "react-konva";
import Konva from "konva";
import type { BBox } from "../types";
export default function LivePreview({
  url,
  width,
  height,
  roi,
  drawing,
  onRoi,
  message,
}: {
  url: string;
  width: number;
  height: number;
  roi: BBox | null;
  drawing: boolean;
  onRoi: (b: BBox) => void;
  message: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const [draft, setDraft] = useState<BBox | null>(null);
  useEffect(() => setDraft(null), [drawing]);
  useLayoutEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ width: r.width, height: r.height });
    });
    observer.observe(host.current!);
    return () => observer.disconnect();
  }, []);
  const scale =
    width && height ? Math.min(size.width / width, size.height / height) : 1;
  const ox = (size.width - width * scale) / 2;
  const oy = (size.height - height * scale) / 2;
  const box = draft || roi;
  function pointer() {
    const p = stage.current?.getPointerPosition();
    return p
      ? {
          x: Math.max(0, Math.min(width, (p.x - ox) / scale)),
          y: Math.max(0, Math.min(height, (p.y - oy) / scale)),
        }
      : null;
  }
  return (
    <div ref={host} className="live-preview">
      {url && width ? (
        <>
          <img
            src={url}
            style={{
              position: "absolute",
              left: ox,
              top: oy,
              width: width * scale,
              height: height * scale,
            }}
          />
          <div
            className={drawing ? "roi-overlay drawing" : "roi-overlay"}
            style={{ pointerEvents: drawing ? "auto" : "none" }}
          >
            <Stage
              ref={stage}
              width={size.width}
              height={size.height}
              onMouseLeave={() => setDraft(null)}
              onMouseDown={() => {
                const p = pointer();
                if (drawing && p) setDraft([p.x, p.y, p.x, p.y]);
              }}
              onMouseMove={() => {
                const p = pointer();
                if (draft && p) setDraft([draft[0], draft[1], p.x, p.y]);
              }}
              onMouseUp={() => {
                if (draft) {
                  const b: BBox = [
                    Math.min(draft[0], draft[2]),
                    Math.min(draft[1], draft[3]),
                    Math.max(draft[0], draft[2]),
                    Math.max(draft[1], draft[3]),
                  ];
                  if (b[2] - b[0] > 2 && b[3] - b[1] > 2) onRoi(b);
                  setDraft(null);
                }
              }}
            >
              <Layer>
                {box && (
                  <>
                    <Rect
                      x={ox + Math.min(box[0], box[2]) * scale}
                      y={oy + Math.min(box[1], box[3]) * scale}
                      width={Math.abs(box[2] - box[0]) * scale}
                      height={Math.abs(box[3] - box[1]) * scale}
                      stroke="#59e6a6"
                      fill="#59e6a615"
                      strokeWidth={2}
                    />
                    <Text
                      x={ox + Math.min(box[0], box[2]) * scale + 5}
                      y={oy + Math.min(box[1], box[3]) * scale + 5}
                      text="ROI"
                      fill="#59e6a6"
                      fontSize={14}
                    />
                  </>
                )}
              </Layer>
            </Stage>
          </div>
        </>
      ) : (
        <div className="canvas-loading">{message || "选择摄像头开始预览"}</div>
      )}
    </div>
  );
}
