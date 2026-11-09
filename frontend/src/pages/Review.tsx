import { useEffect, useRef, useState } from "react";
import type { Dataset, Annotation, AnnotationObject, BBox, FacePoint } from "../types";
import { facePointNames, facePointColors, moveFaceBox, setFacePoint } from "../services/face5pt";
import { api, post, errorText } from "../services/api";
import AnnotationCanvas, { classColor } from "../components/AnnotationCanvas";
import AutoLabel from "../components/AutoLabel";
import { loadImage } from "../services/imageCache";
import { saveAnnotation } from "../services/annotationSave";
import {
  readReviewPreferences,
  writePreference,
} from "../services/preferences";
const statusNames = {
  pending: "待审核",
  approved: "已确认",
  rejected: "已拒绝",
};
type Snapshot = Pick<Annotation, "objects" | "classification">;
type PendingEdit = Snapshot & { undo: boolean };
type SamplePage = {
  items: Annotation[];
  total: number;
  offset: number;
  anchor_index: number | null;
};
type LoadOptions = {
  offset?: number;
  filter?: string;
  prefer?: string | null;
  index?: number;
  restore?: boolean;
};
const PAGE_SIZE = 100;
export default function Review({
  dataset,
  notify,
  reload,
  initialSampleId,
  onBusyChange,
}: {
  dataset: Dataset;
  notify: (s: string) => void;
  reload: () => Promise<void>;
  initialSampleId?: string | null;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [saved] = useState(() => {
    const preferences = readReviewPreferences(dataset);
    return initialSampleId
      ? { ...preferences, sampleId: initialSampleId, index: 0, filter: "" }
      : preferences;
  });
  const [items, setItems] = useState<Annotation[]>([]);
  const [sample, setSample] = useState<Annotation | null>(null);
  const [offset, setOffset] = useState(
    Math.floor(saved.index / PAGE_SIZE) * PAGE_SIZE,
  );
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState(saved.filter);
  const [selected, setSelected] = useState<string | null>(null);
  const [classId, setClassId] = useState(saved.classId);
  const [drawing, setDrawing] = useState(false);
  const faceMode = dataset.annotation_mode === "face_5pt";
  const [placingPoint, setPlacingPoint] = useState<number | null>(null);
  const [showKeypoints, setShowKeypoints] = useState(true);
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [resumeMessage, setResumeMessage] = useState("");
  const [remembered, setRemembered] = useState(false);
  const locked = useRef(false);
  const [cancelToken, setCancelToken] = useState(0);
  const [saveText, setSaveText] = useState("已保存");
  const [pendingEdit, setPendingEdit] = useState<PendingEdit | null>(null);
  const pendingEditRef = useRef<PendingEdit | null>(null);
  const [saveError, setSaveError] = useState("");
  const blocked = busy || loading || pendingEdit !== null;
  const request = useRef(0);
  const selectedThumbnail = useRef<HTMLButtonElement>(null);
  const lastPosition = useRef<ReturnType<typeof readReviewPreferences> | null>(
    null,
  );
  const refreshCurrent = useRef(() => {});
  const base = `/datasets/${dataset.id}`;
  const sampleIndex = sample
    ? offset + items.findIndex((item) => item.id === sample.id)
    : 0;
  if (ready)
    lastPosition.current = {
      sampleId: sample?.id ?? null,
      index: Math.max(0, sampleIndex),
      filter,
      classId,
    };
  useEffect(() => {
    if (ready)
      setRemembered(
        writePreference(dataset.id, "review", lastPosition.current),
      );
  }, [dataset.id, ready, sample?.id, sampleIndex, filter, classId]);
  useEffect(() => {
    selectedThumbnail.current?.scrollIntoView({ block: "nearest" });
  }, [sample?.id, offset]);
  useEffect(() => {
    onBusyChange?.(busy || pendingEdit !== null);
  }, [busy, pendingEdit, onBusyChange]);
  useEffect(() => {
    const preventLoss = (event: BeforeUnloadEvent) => {
      if (!locked.current && !pendingEditRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventLoss);
    return () => {
      window.removeEventListener("beforeunload", preventLoss);
      onBusyChange?.(false);
    };
  }, [onBusyChange]);
  function display(s: Annotation | null) {
    setSample(s);
    setSelected(null);
    setHistory([]);
    setDrawing(false);
    setPlacingPoint(null);
    setSaveText("已保存");
  }
  async function choose(id: string) {
    if (locked.current || pendingEditRef.current) return;
    const token = ++request.current;
    setLoading(true);
    const next = items.find((item) => item.id === id);
    if (next) void loadImage(next.image_url).catch(() => {});
    try {
      const s = await api<Annotation>(`${base}/samples/${id}`, {
        cache: "no-store",
      });
      if (token !== request.current) return;
      display(s);
      setResumeMessage("");
    } catch (e) {
      if (token === request.current) notify(errorText(e));
    } finally {
      if (token === request.current) setLoading(false);
    }
  }
  async function load(options: LoadOptions = {}) {
    if (locked.current || pendingEditRef.current) return;
    const token = ++request.current;
    const nextFilter = options.filter ?? filter;
    const nextOffset = options.offset ?? offset;
    setLoading(true);
    try {
      const query = new URLSearchParams({
        offset: String(nextOffset),
        limit: String(PAGE_SIZE),
      });
      if (nextFilter) query.set("status", nextFilter);
      if (options.prefer) query.set("anchor_id", options.prefer);
      const r = await api<SamplePage>(`${base}/samples?${query}`);
      if (token !== request.current) return;
      const index = Math.max(
        0,
        Math.min(
          (r.anchor_index ?? options.index ?? r.offset) - r.offset,
          r.items.length - 1,
        ),
      );
      const id = r.items[index]?.id;
      if (id) void loadImage(r.items[index].image_url).catch(() => {});
      const nextSample = id
        ? await api<Annotation>(`${base}/samples/${id}`, { cache: "no-store" })
        : null;
      if (token !== request.current) return;
      setItems(r.items);
      setTotal(r.total);
      setOffset(r.offset);
      setFilter(nextFilter);
      display(nextSample);
      setReady(true);
      setResumeMessage(
        options.restore && options.prefer
          ? id === options.prefer
            ? "已恢复上次审核位置。"
            : id
              ? "上次图片已删除或不在当前筛选中，已定位到附近图片。"
              : "上次图片已删除或不在当前筛选中，当前没有可恢复的图片。"
          : "",
      );
    } catch (e) {
      if (token === request.current) notify(errorText(e));
    } finally {
      if (token === request.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load({
      prefer: saved.sampleId,
      index: saved.index,
      restore: !initialSampleId,
    });
    const remember = () => {
      if (lastPosition.current)
        writePreference(dataset.id, "review", lastPosition.current);
    };
    window.addEventListener("pagehide", remember);
    return () => {
      request.current++;
      remember();
      window.removeEventListener("pagehide", remember);
    };
  }, [dataset.id]);
  useEffect(() => {
    if (!sample || loading || document.hidden) return;
    const index = items.findIndex((item) => item.id === sample.id);
    // 当前图优先；有限预取相邻两张，避免抢占首图和缩略图带宽。
    let cancelled = false;
    void loadImage(sample.image_url)
      .then(() => {
        if (cancelled) return;
        for (const item of [items[index + 1], items[index - 1]])
          if (item) void loadImage(item.image_url).catch(() => {});
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sample?.id, items, loading]);
  refreshCurrent.current = () => {
    if (sample && !loading) void choose(sample.id);
    void reload();
  };
  async function edit(
    objects: AnnotationObject[],
    classification = sample?.classification ?? null,
    undo = false,
  ) {
    if (pendingEditRef.current) return;
    await saveEdit({ objects, classification, undo });
  }
  async function saveEdit(draft: PendingEdit) {
    if (!sample || locked.current || loading) return;
    locked.current = true;
    request.current++;
    pendingEditRef.current = draft;
    setPendingEdit(draft);
    onBusyChange?.(true);
    setBusy(true);
    setSaveText("保存中…");
    setSaveError("");
    const previous = {
      objects: sample.objects,
      classification: sample.classification,
    };
    try {
      const result = await saveAnnotation(base, sample, draft);
      setSample(result);
      setItems((rows) =>
        rows.map((s) => (s.id === result.id ? { ...s, ...result } : s)),
      );
      setHistory((h) =>
        draft.undo ? h.slice(0, -1) : [...h.slice(-49), previous],
      );
      pendingEditRef.current = null;
      setPendingEdit(null);
      setSaveText("已保存");
      if (sample.status !== result.status)
        void reload().catch((e) => notify(errorText(e)));
    } catch (e) {
      setSaveText("未保存");
      setSaveError(errorText(e));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  async function discardEdit() {
    if (
      !sample ||
      locked.current ||
      !confirm("放弃本次未保存的修改，并重新读取服务器上的标注？")
    )
      return;
    locked.current = true;
    setBusy(true);
    try {
      const current = await api<Annotation>(`${base}/samples/${sample.id}`, {
        cache: "no-store",
      });
      pendingEditRef.current = null;
      setPendingEdit(null);
      setSaveError("");
      setItems((rows) =>
        rows.map((row) =>
          row.id === current.id ? { ...row, ...current } : row,
        ),
      );
      display(current);
    } catch (e) {
      setSaveError(errorText(e));
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
        o.id === id ? moveFaceBox(o, bbox, sample.width, sample.height) : o,
      ),
    );
  }
  function changePoint(id: string, index: number, point: FacePoint) {
    if (!sample || blocked) return;
    setPlacingPoint(null);
    void edit(sample.objects.map((o) => o.id === id ? setFacePoint(o, index, point) : o));
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
        ...(faceMode ? { keypoints: Array<FacePoint>(5).fill(null) } : {}),
      },
    ]);
  }
  function removeBox() {
    if (sample && selected) {
      void edit(sample.objects.filter((o) => o.id !== selected));
      setSelected(null);
      setPlacingPoint(null);
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
    if (!sample || locked.current || loading || pendingEditRef.current) return;
    const index = items.findIndex((s) => s.id === sample.id);
    if (items[index + delta]) await choose(items[index + delta].id);
    else if (delta > 0 && offset + items.length < total)
      await load({ offset: offset + PAGE_SIZE, index: offset + PAGE_SIZE });
    else if (delta < 0 && offset > 0)
      await load({
        offset: Math.max(0, offset - PAGE_SIZE),
        index: offset - 1,
      });
  }
  async function status(approved: boolean) {
    if (!sample || locked.current || loading || pendingEditRef.current) return;
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
      void reload().catch((e) => notify(errorText(e)));
      locked.current = false;
      if (filter && result.status !== filter) {
        const index = items.findIndex((s) => s.id === sample.id);
        await load({ prefer: items[index + 1]?.id, index: offset + index });
      } else await move(1);
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
      loading ||
      pendingEditRef.current ||
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
      await load({ prefer: next, index: offset + index });
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
        pendingEditRef.current ||
        loading ||
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
        setPlacingPoint(null);
        setSelected(null);
      } else if (k === "escape") {
        setPlacingPoint(null);
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
  const visibleObjects = pendingEdit?.objects ?? sample?.objects ?? [];
  const current = visibleObjects.find((o) => o.id === selected);
  return (
    <>
      <div className="page-head compact">
        <div>
          <div className="eyebrow">ANNOTATION / REVIEW</div>
          <h1>标注审核</h1>
          <p>
            {dataset.name} · {total} 张图片
          </p>
          <p aria-label="审核位置" aria-live="polite">
            {loading
              ? "正在读取审核图片…"
              : sample
                ? `第 ${sampleIndex + 1} / ${total} 张（当前筛选）`
                : "当前筛选没有图片"}
            {remembered && " · 已记住位置和选项（当前浏览器）"}
          </p>
          {resumeMessage && <small role="status">{resumeMessage}</small>}
          {sample && (
            <small>图片：{String(sample.source.filename || sample.id)}</small>
          )}
        </div>
      </div>
      <div className="review-layout">
        <aside className="sample-list">
          <select
            aria-label="审核状态筛选"
            value={filter}
            disabled={blocked}
            onChange={(e) => {
              void load({ filter: e.target.value, offset: 0, index: 0 });
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
              disabled={blocked}
              className={s.id === sample?.id ? "selected" : ""}
              key={s.id}
              ref={s.id === sample?.id ? selectedThumbnail : undefined}
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
              disabled={!offset || blocked}
              onClick={() =>
                void load({ offset: Math.max(0, offset - PAGE_SIZE) })
              }
            >
              上一页
            </button>
            <button
              disabled={offset + PAGE_SIZE >= total || blocked}
              onClick={() => void load({ offset: offset + PAGE_SIZE })}
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
                    disabled={blocked || !sample}
                    className={drawing ? "primary" : ""}
                    onClick={() => {
                      setDrawing(!drawing);
                      setPlacingPoint(null);
                      setSelected(null);
                    }}
                  >
                    ＋ 新增框 <kbd>N</kbd>
                  </button>
                  <button disabled={blocked || !selected} onClick={removeBox}>
                    删除框 <kbd>Del</kbd>
                  </button>
                  {faceMode && <button disabled={blocked || !visibleObjects.length} onClick={() => {
                    setSelected(null); setPlacingPoint(null); void edit([]);
                  }}>清空人脸（负样本）</button>}
                  <button disabled={blocked || !history.length} onClick={undo}>
                    撤销
                  </button>
                  {faceMode && <label className="check" title="仅控制画布显示，不改变保存和导出的五点标注">
                    <input type="checkbox" checked={showKeypoints} onChange={(e) => setShowKeypoints(e.target.checked)} />显示五点
                  </label>}
                </>
              )}
            </div>
            <span className="muted" role="status" aria-live="polite">
              {sample && `${sample.width} × ${sample.height}`} · {saveText}
            </span>
          </div>
          {pendingEdit && saveError && (
            <div className="error" role="alert">
              <div>本次修改尚未保存，请重试。{saveError}</div>
              <div className="actions">
                <button
                  disabled={busy}
                  onClick={() => void saveEdit(pendingEdit)}
                >
                  重试保存
                </button>
                <button disabled={busy} onClick={() => void discardEdit()}>
                  放弃修改并重新读取
                </button>
              </div>
            </div>
          )}
          {sample ? (
            <AnnotationCanvas
              url={sample.image_url}
              width={sample.width}
              height={sample.height}
              objects={visibleObjects}
              selected={selected}
              onSelect={(id) => { setSelected(id); setPlacingPoint(null); }}
              onChange={changeBox}
              onAdd={addBox}
              drawing={drawing}
              disabled={blocked}
              cancelToken={cancelToken}
              placingPoint={placingPoint}
              onPointChange={faceMode ? changePoint : undefined}
              showKeypoints={showKeypoints}
            />
          ) : (
            <div className="preview-stage">
              {loading ? "正在恢复审核位置…" : "暂无图片，请先导入或采集。"}
            </div>
          )}
          <div className="review-footer">
            <div className="actions">
              <button
                disabled={blocked || !sample}
                onClick={() => void move(-1)}
              >
                ← 上一张
              </button>
              <button
                disabled={blocked || !sample}
                onClick={() => void move(1)}
              >
                下一张 →
              </button>
            </div>
            <div className="actions">
              <button
                className="danger"
                disabled={blocked || !sample}
                onClick={() => void removeSample()}
              >
                删除样本
              </button>
              <button
                disabled={blocked || !sample}
                onClick={() => void status(false)}
              >
                拒绝
              </button>
              <button
                className="primary"
                disabled={blocked || !sample}
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
              <span>{visibleObjects.length}</span>
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
                  disabled={blocked}
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
                {visibleObjects.map((obj, i) => (
                  <button
                    key={obj.id}
                    className={
                      selected === obj.id ? "object selected" : "object"
                    }
                    disabled={blocked}
                    onClick={() => { setSelected(obj.id); setPlacingPoint(null); }}
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
                            : obj.source === "scrfd" ? "SCRFD" : "自定义 YOLO"}{" "}
                        ·{" "}
                        {obj.confidence === null
                          ? "—"
                          : obj.confidence.toFixed(3)}
                      </small>
                    </div>
                  </button>
                ))}
              </div>
              {faceMode && <div className="face-keypoints">
                <strong>人脸五点（按画面左右）</strong>
                <p>小点按人脸大小显示，悬停查看编号；可关闭“显示五点”观察脸部。点“放置”后点击画布，或拖动已有点。缺失点保持“缺失”。</p>
                {current ? facePointNames.map((name, index) => {
                  const point = current.keypoints?.[index];
                  return <div key={name} className="face-point-row">
                    <span><i className="color-dot" style={{background: facePointColors[index]}} />{index + 1}. {name}</span>
                    <small>{point ? `${point[0].toFixed(1)}, ${point[1].toFixed(1)}` : "缺失"}</small>
                    <div className="actions">
                      <button disabled={blocked} className={placingPoint === index ? "primary" : ""} onClick={() => {
                        setShowKeypoints(true); setDrawing(false); setPlacingPoint(placingPoint === index ? null : index);
                      }}>{placingPoint === index ? "点击画布…" : "放置 / 重置"}</button>
                      <button disabled={blocked || !point} onClick={() => changePoint(current.id, index, null)}>删除点</button>
                    </div>
                  </div>;
                }) : <p>先选择或新增一个人脸框。</p>}
                {sample && !visibleObjects.length && <p>当前为零目标负样本，可直接确认通过审核。</p>}
              </div>}
              <AutoLabel
                classes={dataset.classes}
                key={dataset.id}
                datasetId={dataset.id}
                disabled={blocked}
                faceMode={faceMode}
                sampleId={sample?.id}
                onDone={() => refreshCurrent.current()}
                notify={notify}
              />
            </>
          )}
          {dataset.task_type === "classification" && (
            <div className="class-buttons">
              {dataset.classes.map((c, i) => (
                <button
                  key={c.id}
                  disabled={blocked || !sample}
                  className={
                    (pendingEdit?.classification ?? sample?.classification)
                      ?.class_id === c.id
                      ? "primary"
                      : ""
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
