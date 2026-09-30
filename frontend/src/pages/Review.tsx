import { useEffect, useRef, useState } from "react";
import type { Dataset, Annotation, AnnotationObject, BBox } from "../types";
import { api, post, errorText } from "../services/api";
import AnnotationCanvas, { classColor } from "../components/AnnotationCanvas";
import AutoLabel from "../components/AutoLabel";
const statusNames = {
  pending: "待审核",
  approved: "已确认",
  rejected: "已拒绝",
};
type Snapshot = Pick<Annotation, "objects" | "classification">;
export default function Review({
  dataset,
  notify,
  reload,
}: {
  dataset: Dataset;
  notify: (s: string) => void;
  reload: () => Promise<void>;
}) {
  const [items, setItems] = useState<Annotation[]>([]);
  const [sample, setSample] = useState<Annotation | null>(null);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [classId, setClassId] = useState(dataset.classes[0]?.id ?? 0);
  const [drawing, setDrawing] = useState(false);
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [cancelToken, setCancelToken] = useState(0);
  const [saveText, setSaveText] = useState("已保存");
  const request = useRef(0);
  const base = `/datasets/${dataset.id}`;
  async function choose(id: string) {
    if (locked.current) return;
    const token = ++request.current;
    try {
      const s = await api<Annotation>(`${base}/samples/${id}`);
      if (token !== request.current) return;
      setSample(s);
      setSelected(null);
      setHistory([]);
      setDrawing(false);
      setSaveText("已保存");
    } catch (e) {
      notify(errorText(e));
    }
  }
  async function load(prefer?: string) {
    const r = await api(
      `${base}/samples?offset=${offset}&limit=100${filter ? "&status=" + filter : ""}`,
    );
    setItems(r.items);
    setTotal(r.total);
    const id =
      prefer && r.items.some((s: Annotation) => s.id === prefer)
        ? prefer
        : r.items[0]?.id;
    if (id) await choose(id);
    else setSample(null);
  }
  useEffect(() => {
    void load().catch((e) => notify(errorText(e)));
    return () => {
      request.current++;
    };
  }, [dataset.id, offset, filter]);
  async function edit(
    objects: AnnotationObject[],
    classification = sample?.classification ?? null,
    undo = false,
  ) {
    if (!sample || locked.current) return;
    locked.current = true;
    setBusy(true);
    setSaveText("保存中…");
    const previous = {
      objects: sample.objects,
      classification: sample.classification,
    };
    try {
      const result = await api<Annotation>(
        `${base}/samples/${sample.id}/annotation`,
        {
          method: "PUT",
          body: JSON.stringify({
            revision: sample.revision,
            objects,
            classification: classification
              ? { id: classification.class_id, name: classification.class_name }
              : null,
          }),
        },
      );
      setSample(result);
      setItems((rows) =>
        rows.map((s) => (s.id === result.id ? { ...s, ...result } : s)),
      );
      setHistory((h) => (undo ? h.slice(0, -1) : [...h.slice(-49), previous]));
      setSaveText("已保存");
      void reload();
    } catch (e) {
      setSaveText("保存失败");
      notify(errorText(e));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  function undo() {
    const h = history.at(-1);
    if (h) void edit(h.objects, h.classification, true);
  }
  function changeBox(id: string, bbox: BBox) {
    if (!sample) return;
    void edit(
      sample.objects.map((o) =>
        o.id === id ? { ...o, bbox, source: "manual", confidence: null } : o,
      ),
    );
  }
  function addBox(bbox: BBox) {
    if (!sample) return;
    const cls = dataset.classes.find((c) => c.id === classId);
    if (!cls) {
      notify("请先添加类别");
      return;
    }
    const id =
      "obj_" +
      Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) =>
        v.toString(16).padStart(2, "0"),
      ).join("");
    setSelected(id);
    setDrawing(false);
    void edit([
      ...sample.objects,
      {
        id,
        class_id: cls.id,
        class_name: cls.name,
        bbox,
        confidence: null,
        source: "manual",
      },
    ]);
  }
  function removeBox() {
    if (sample && selected) {
      void edit(sample.objects.filter((o) => o.id !== selected));
      setSelected(null);
    }
  }
  function pickClass(cid: number) {
    setClassId(cid);
    if (dataset.task_type === "classification" && sample) {
      const c = dataset.classes.find((c) => c.id === cid)!;
      void edit([], { class_id: c.id, class_name: c.name });
      return;
    }
    if (sample && selected) {
      const cls = dataset.classes.find((c) => c.id === cid)!;
      void edit(
        sample.objects.map((o) =>
          o.id === selected
            ? {
                ...o,
                class_id: cid,
                class_name: cls.name,
                source: "manual",
                confidence: null,
              }
            : o,
        ),
      );
    }
  }
  async function move(delta: number) {
    if (!sample || locked.current) return;
    const index = items.findIndex((s) => s.id === sample.id);
    if (items[index + delta]) await choose(items[index + delta].id);
    else if (delta > 0 && offset + items.length < total)
      setOffset(offset + 100);
    else if (delta < 0 && offset > 0) setOffset(Math.max(0, offset - 100));
  }
  async function status(approved: boolean) {
    if (!sample || locked.current) return;
    locked.current = true;
    setBusy(true);
    try {
      const result = await post<Annotation>(
        `${base}/samples/${sample.id}/${approved ? "approve" : "reject"}`,
      );
      setSample(result);
      setItems((rows) =>
        rows.map((s) => (s.id === result.id ? { ...s, ...result } : s)),
      );
      await reload();
      locked.current = false;
      await move(1);
    } catch (e) {
      notify(errorText(e));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function removeSample() {
    if (
      !sample ||
      locked.current ||
      !confirm(
        "确定删除整个样本？原图、标注、预览及关联导出将被删除，无法撤销。",
      )
    )
      return;
    locked.current = true;
    setBusy(true);
    try {
      const index = items.findIndex((s) => s.id === sample.id);
      const next = items[index + 1]?.id || items[index - 1]?.id;
      await api(`${base}/samples/${sample.id}`, { method: "DELETE" });
      locked.current = false;
      await load(next);
      await reload();
    } catch (e) {
      notify(errorText(e));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    function key(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (
        target.closest('input,textarea,select,[contenteditable="true"]') ||
        locked.current ||
        e.altKey
      )
        return;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "z") {
        e.preventDefault();
        undo();
        return;
      }
      if (e.ctrlKey || e.metaKey) return;
      if (k === "a" || k === "arrowleft") {
        e.preventDefault();
        void move(-1);
      } else if (k === "d" || k === "arrowright") {
        e.preventDefault();
        void move(1);
      } else if (k === "enter") {
        e.preventDefault();
        void status(true);
      } else if (k === "delete") {
        e.preventDefault();
        removeBox();
      } else if (k === "n" && dataset.task_type === "detection") {
        e.preventDefault();
        setDrawing(true);
        setSelected(null);
      } else if (k === "escape") {
        setDrawing(false);
        setSelected(null);
        setCancelToken((n) => n + 1);
      } else if (/^[1-9]$/.test(k)) {
        const cls = dataset.classes[Number(k) - 1];
        if (cls) {
          e.preventDefault();
          pickClass(cls.id);
        }
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const current = sample?.objects.find((o) => o.id === selected);
  return (
    <>
      <div className="page-head compact">
        <div>
          <div className="eyebrow">ANNOTATION / REVIEW</div>
          <h1>标注审核</h1>
          <p>
            {dataset.name} · {total} 张图片
          </p>
        </div>
      </div>
      <div className="review-layout">
        <aside className="sample-list">
          <select
            aria-label="审核状态筛选"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部状态</option>
            {Object.entries(statusNames).map(([v, n]) => (
              <option key={v} value={v}>
                {n}
              </option>
            ))}
          </select>
          {items.map((s, i) => (
            <button
              disabled={busy}
              className={s.id === sample?.id ? "selected" : ""}
              key={s.id}
              onClick={() => void choose(s.id)}
            >
              <img
                loading="lazy"
                decoding="async"
                src={s.thumbnail_url}
                alt={`样本 ${offset + i + 1}`}
              />
              <small>
                {offset + i + 1} · {statusNames[s.status]}
              </small>
            </button>
          ))}
          <div className="actions">
            <button
              disabled={!offset || busy}
              onClick={() => setOffset(Math.max(0, offset - 100))}
            >
              上一页
            </button>
            <button
              disabled={offset + 100 >= total || busy}
              onClick={() => setOffset(offset + 100)}
            >
              下一页
            </button>
          </div>
        </aside>
        <div className="review-center">
          <div className="canvas-toolbar">
            <div className="actions">
              {dataset.task_type === "detection" && (
                <>
                  <button
                    disabled={busy || !sample}
                    className={drawing ? "primary" : ""}
                    onClick={() => {
                      setDrawing(!drawing);
                      setSelected(null);
                    }}
                  >
                    ＋ 新增框 <kbd>N</kbd>
                  </button>
                  <button disabled={busy || !selected} onClick={removeBox}>
                    删除框 <kbd>Del</kbd>
                  </button>
                  <button disabled={busy || !history.length} onClick={undo}>
                    撤销
                  </button>
                </>
              )}
            </div>
            <span className="muted">
              {sample && `${sample.width} × ${sample.height}`} · {saveText}
            </span>
          </div>
          {sample ? (
            <AnnotationCanvas
              url={sample.image_url}
              width={sample.width}
              height={sample.height}
              objects={sample.objects}
              selected={selected}
              onSelect={setSelected}
              onChange={changeBox}
              onAdd={addBox}
              drawing={drawing}
              disabled={busy}
              cancelToken={cancelToken}
            />
          ) : (
            <div className="preview-stage">暂无图片，请先导入或采集。</div>
          )}
          <div className="review-footer">
            <div className="actions">
              <button disabled={busy || !sample} onClick={() => void move(-1)}>
                ← 上一张
              </button>
              <button disabled={busy || !sample} onClick={() => void move(1)}>
                下一张 →
              </button>
            </div>
            <div className="actions">
              <button
                className="danger"
                disabled={busy || !sample}
                onClick={() => void removeSample()}
              >
                删除样本
              </button>
              <button
                disabled={busy || !sample}
                onClick={() => void status(false)}
              >
                拒绝
              </button>
              <button
                className="primary"
                disabled={busy || !sample}
                onClick={() => void status(true)}
              >
                确认并下一张 ↵
              </button>
            </div>
          </div>
        </div>
        <aside className="panel controls object-panel">
          <div className="panel-title-inline">
            <strong>
              {dataset.task_type === "detection"
                ? "标注对象"
                : dataset.task_type === "classification"
                  ? "图像类别"
                  : "样本信息"}
            </strong>
            {dataset.task_type === "detection" && (
              <span>{sample?.objects.length ?? 0}</span>
            )}
          </div>
          {sample && (
            <span className={`status status-${sample.status}`}>
              {statusNames[sample.status]}
            </span>
          )}
          {dataset.task_type === "detection" && (
            <>
              <label>
                {current ? "当前框类别" : "新增框类别"}
                <select
                  aria-label="当前框类别"
                  value={current?.class_id ?? classId}
                  disabled={busy}
                  onChange={(e) => pickClass(Number(e.target.value))}
                >
                  {dataset.classes.map((c, i) => (
                    <option key={c.id} value={c.id}>
                      {c.id} · {c.name}
                      {i < 9 ? ` [${i + 1}]` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <div className="objects">
                {sample?.objects.map((obj, i) => (
                  <button
                    key={obj.id}
                    className={
                      selected === obj.id ? "object selected" : "object"
                    }
                    onClick={() => setSelected(obj.id)}
                  >
                    <span
                      className="color-dot"
                      style={{ background: classColor(obj.class_id) }}
                    />
                    <div>
                      <strong>
                        {i + 1}. {obj.class_name}
                      </strong>
                      <small>
                        {obj.source === "manual"
                          ? "人工标注"
                          : obj.source === "yolo_world"
                            ? "YOLO-World"
                            : "自定义 YOLO"}{" "}
                        ·{" "}
                        {obj.confidence === null
                          ? "—"
                          : obj.confidence.toFixed(3)}
                      </small>
                    </div>
                  </button>
                ))}
              </div>
              <AutoLabel
                datasetId={dataset.id}
                sampleId={sample?.id}
                onDone={() => {
                  if (sample) void choose(sample.id);
                  void reload();
                }}
                notify={notify}
              />
            </>
          )}
          {dataset.task_type === "classification" && (
            <div className="class-buttons">
              {dataset.classes.map((c, i) => (
                <button
                  key={c.id}
                  disabled={busy || !sample}
                  className={
                    sample?.classification?.class_id === c.id ? "primary" : ""
                  }
                  onClick={() => pickClass(c.id)}
                >
                  {c.name} {i < 9 && <kbd>{i + 1}</kbd>}
                </button>
              ))}
            </div>
          )}
          {dataset.task_type === "raw" && (
            <p>原始数据无需标注。可浏览、拒绝无效图片，或确认后导出。</p>
          )}
          <div className="shortcuts">
            <strong>键盘快捷键</strong>
            <p>A / ← 上一张　D / → 下一张</p>
            <p>Enter 确认　Delete 删除框</p>
            <p>N 新增　1–9 类别　Ctrl+Z 撤销</p>
            <p>Esc 取消操作</p>
          </div>
        </aside>
      </div>
    </>
  );
}
