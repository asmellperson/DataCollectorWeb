import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Stage,
  Layer,
  Image as KImage,
  Rect,
  Text,
  Group,
  Transformer,
} from "react-konva";
import Konva from "konva";
import type { AnnotationObject, BBox } from "../types";
const colors = ["#51bffc", "#f6c557", "#aa93ff", "#51d7a9", "#f58c9d"];
export const classColor = (id: number) => colors[id % colors.length];
function Box({
  obj,
  selected,
  scale,
  width,
  height,
  onSelect,
  onChange,
  disabled,
}: {
  obj: AnnotationObject;
  selected: boolean;
  scale: number;
  width: number;
  height: number;
  onSelect: () => void;
  onChange: (b: BBox) => void;
  disabled: boolean;
}) {
  const rect = useRef<Konva.Rect>(null);
  const tr = useRef<Konva.Transformer>(null);
  useEffect(() => {
    if (selected && tr.current && rect.current) {
      tr.current.nodes([rect.current]);
      tr.current.getLayer()?.batchDraw();
    }
  }, [selected, obj.bbox]);
  const [x1, y1, x2, y2] = obj.bbox;
  return (
    <>
      <Rect
        ref={rect}
        name={obj.id}
        x={x1}
        y={y1}
        width={x2 - x1}
        height={y2 - y1}
        stroke={classColor(obj.class_id)}
        strokeWidth={(selected ? 3 : 2) / scale}
        fill={selected ? "#ffffff18" : "#ffffff01"}
        draggable={!disabled}
        onClick={onSelect}
        onTap={onSelect}
        onDragStart={onSelect}
        onDragMove={() => {
          const n = rect.current!;
          n.x(Math.max(0, Math.min(width - n.width(), n.x())));
          n.y(Math.max(0, Math.min(height - n.height(), n.y())));
        }}
        onDragEnd={() => {
          const n = rect.current!;
          onChange([n.x(), n.y(), n.x() + n.width(), n.y() + n.height()]);
        }}
        onTransformEnd={() => {
          const n = rect.current!;
          const w = Math.max(2, n.width() * n.scaleX());
          const h = Math.max(2, n.height() * n.scaleY());
          n.scaleX(1);
          n.scaleY(1);
          const x = Math.max(0, Math.min(width - 2, n.x()));
          const y = Math.max(0, Math.min(height - 2, n.y()));
          onChange([x, y, Math.min(width, x + w), Math.min(height, y + h)]);
        }}
      />
      <Text
        x={x1}
        y={Math.max(0, y1 - 23 / scale)}
        text={`${obj.class_name}${obj.confidence === null ? "" : ` ${obj.confidence.toFixed(2)}`}`}
        fontSize={16 / scale}
        fill={classColor(obj.class_id)}
        padding={2 / scale}
        listening={false}
      />
      {selected && !disabled && (
        <Transformer
          ref={tr}
          rotateEnabled={false}
          flipEnabled={false}
          keepRatio={false}
          ignoreStroke
          anchorSize={9}
          borderStroke="#ffffff"
          boundBoxFunc={(old, next) =>
            next.width < 3 || next.height < 3 ? old : next
          }
        />
      )}
    </>
  );
}
export default function AnnotationCanvas({
  url,
  width,
  height,
  objects,
  selected,
  onSelect,
  onChange,
  onAdd,
  drawing,
  disabled = false,
  cancelToken = 0,
}: {
  url: string;
  width: number;
  height: number;
  objects: AnnotationObject[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onChange: (id: string, b: BBox) => void;
  onAdd: (b: BBox) => void;
  drawing: boolean;
  disabled?: boolean;
  cancelToken?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const [size, setSize] = useState({ width: 700, height: 550 });
  const [image, setImage] = useState<HTMLImageElement>();
  const [loadError, setLoadError] = useState(false);
  const [draft, setDraft] = useState<BBox | null>(null);
  useLayoutEffect(() => {
    const el = host.current!;
    const r = new ResizeObserver((entries) => {
      const b = entries[0].contentRect;
      setSize({ width: b.width, height: b.height });
    });
    r.observe(el);
    return () => r.disconnect();
  }, []);
  useEffect(() => {
    setDraft(null);
    setImage(undefined);
    setLoadError(false);
    const im = new window.Image();
    im.onload = () => setImage(im);
    im.onerror = () => setLoadError(true);
    im.src = url;
    return () => {
      im.onload = null;
      im.onerror = null;
    };
  }, [url]);
  useEffect(() => setDraft(null), [cancelToken, drawing]);
  const scale = Math.min(
    (size.width - 32) / width,
    (size.height - 32) / height,
  );
  const ox = (size.width - width * scale) / 2;
  const oy = (size.height - height * scale) / 2;
  function pointer() {
    const p = stage.current?.getPointerPosition();
    if (!p) return null;
    return {
      x: Math.max(0, Math.min(width, (p.x - ox) / scale)),
      y: Math.max(0, Math.min(height, (p.y - oy) / scale)),
    };
  }
  function end() {
    if (!draft) return;
    const [a, b, c, d] = draft;
    const box: BBox = [
      Math.min(a, c),
      Math.min(b, d),
      Math.max(a, c),
      Math.max(b, d),
    ];
    if (box[2] - box[0] >= 2 && box[3] - box[1] >= 2) onAdd(box);
    setDraft(null);
  }
  return (
    <div
      ref={host}
      className={`annotation-canvas ${drawing ? "crosshair" : ""}`}
      data-testid="annotation-canvas"
    >
      {image ? (
        <Stage
          ref={stage}
          width={size.width}
          height={size.height}
          onMouseDown={(e) => {
            if (disabled) return;
            if (drawing) {
              const p = pointer();
              if (p) setDraft([p.x, p.y, p.x, p.y]);
            } else if (
              e.target === e.target.getStage() ||
              e.target.name() === "image"
            )
              onSelect(null);
          }}
          onMouseMove={() => {
            if (draft) {
              const p = pointer();
              if (p) setDraft([draft[0], draft[1], p.x, p.y]);
            }
          }}
          onMouseUp={end}
          onMouseLeave={() => draft && end()}
        >
          <Layer>
            <Group x={ox} y={oy} scaleX={scale} scaleY={scale}>
              <KImage
                image={image}
                width={width}
                height={height}
                name="image"
              />
              {objects.map((obj) => (
                <Box
                  key={obj.id}
                  obj={obj}
                  selected={selected === obj.id}
                  scale={scale}
                  width={width}
                  height={height}
                  disabled={disabled || drawing}
                  onSelect={() => !drawing && onSelect(obj.id)}
                  onChange={(b) => onChange(obj.id, b)}
                />
              ))}
              {draft && (
                <Rect
                  x={Math.min(draft[0], draft[2])}
                  y={Math.min(draft[1], draft[3])}
                  width={Math.abs(draft[2] - draft[0])}
                  height={Math.abs(draft[3] - draft[1])}
                  stroke="#f5d165"
                  strokeWidth={2 / scale}
                  dash={[6 / scale, 4 / scale]}
                  listening={false}
                />
              )}
            </Group>
          </Layer>
        </Stage>
      ) : (
        <div className="canvas-loading">
          {loadError
            ? "图片加载失败，请检查连接或重新打开样本"
            : "正在加载图片…"}
        </div>
      )}
    </div>
  );
}
