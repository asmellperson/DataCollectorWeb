import { useState, useEffect, useRef } from "react";
import type {
  Dataset,
  BBox,
  ClassMapping,
  Camera,
  CollectionStatus,
} from "../types";
import { serializeClassMapping } from "../services/classMapping";
import {
  api,
  post,
  errorText,
  uploadForm,
  formatBytes,
  type UploadProgress,
} from "../services/api";
import CollectionCameraTile from "../components/CollectionCameraTile";
import ModelPicker from "../components/ModelPicker";
import ScrfdInfo from "../components/ScrfdInfo";
import TaskProgress from "../components/TaskProgress";
import ClassSelector from "../components/ClassSelector";
export default function Collection({
  dataset,
  reload,
  notify,
  onBusyChange,
}: {
  dataset: Dataset;
  reload: () => Promise<void>;
  notify: (s: string) => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [source, setSource] = useState("rtsp");
  const faceMode = dataset.annotation_mode === "face_5pt";
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedCameras, setSelectedCameras] = useState<string[]>([]);
  const [mode, setMode] = useState("raw_interval");
  const [interval, setIntervalValue] = useState(2);
  const [rois, setRois] = useState<Record<string, BBox | null>>({});
  const [collection, setCollection] = useState<CollectionStatus>({
    active: false,
    active_count: 0,
    max_cameras: 9,
    sessions: [],
  });
  const [starting, setStarting] = useState(false);
  const mounted = useRef(true);
  const [autoLabel, setAutoLabel] = useState(faceMode ? "scrfd" : "off");
  const [detector, setDetector] = useState("yolo_world");
  const [modelPath, setModelPath] = useState("");
  const [triggerModelPath, setTriggerModelPath] = useState("");
  const [labelClasses, setLabelClasses] = useState<number[] | null>(null);
  const [classMapping, setClassMapping] = useState<ClassMapping>({});
  const [confidence, setConfidence] = useState(0.25);
  const [similarity, setSimilarity] = useState(0.92);
  const [targets, setTargets] = useState("person");
  const restored = useRef(false);
  const ownSessions = collection.sessions.filter(
    (s) => s.dataset_id === dataset.id,
  );
  const activeSessions = ownSessions.filter((s) => s.active);
  const pendingCameras = selectedCameras.filter(
    (id) => !collection.sessions.some((s) => s.camera_id === id && s.active),
  );
  const selected = cameras.filter((c) => selectedCameras.includes(c.id));
  const counts = ownSessions.reduce(
    (sum, s) => ({
      saved: sum.saved + s.saved,
      labeled: sum.labeled + s.labeled,
      duplicates: sum.duplicates + s.duplicates,
    }),
    { saved: 0, labeled: 0, duplicates: 0 },
  );
  const ready = useRef(false);
  const [statusReady, setStatusReady] = useState(false);
  useEffect(() => {
    let live = true;
    api<Camera[]>("/cameras")
      .then((r) => {
        if (!live) return;
        setCameras(r);
        setSelectedCameras((ids) => (ids.length ? ids : r[0] ? [r[0].id] : []));
      })
      .catch((e) => live && notify(errorText(e)));
    return () => {
      live = false;
    };
  }, []);
  function acceptStatus(s: CollectionStatus) {
    if (!mounted.current) return;
    setCollection(s);
    setStatusReady(true);
    ready.current = true;
    const active = s.sessions.filter(
      (item) => item.active && item.dataset_id === dataset.id,
    );
    if (active.length) {
      // 刷新恢复所有运行中的画面；后续轮询只补充，不覆盖用户的新选择。
      setSelectedCameras((ids) =>
        [...new Set([...active.map((item) => item.camera_id), ...ids])].slice(
          0,
          9,
        ),
      );
      if (!restored.current) {
        restored.current = true;
        const config = active[0].config;
        setSource("rtsp");
        setMode(active[0].mode);
        setIntervalValue(config.interval);
        setRois(
          Object.fromEntries(
            active.map((item) => [item.camera_id, item.config.roi]),
          ),
        );
        setAutoLabel(config.auto_label);
        setDetector(config.detector);
        setModelPath(config.model_path || "");
        setTriggerModelPath(
          config.trigger_model_path || config.model_path || "",
        );
        setTargets((config.target_classes || ["person"]).join(", "));
        setLabelClasses(config.label_class_ids ?? null);
        setClassMapping(config.label_class_mapping ?? {});
        setConfidence(config.confidence);
        setSimilarity(config.similarity);
      }
    }
  }
  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function tick() {
      try {
        const s = await api<CollectionStatus>("/collection/status", {
          signal: controller.signal,
        });
        if (!stopped) acceptStatus(s);
      } catch (e) {
        if (!stopped && !ready.current) notify(errorText(e));
      }
      if (!stopped) timer = setTimeout(tick, 1000);
    }
    void tick();
    return () => {
      mounted.current = false;
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [dataset.id]);
  async function start() {
    if (starting || !pendingCameras.length) return;
    setStarting(true);
    restored.current = true;
    try {
      const results = await Promise.allSettled(
        pendingCameras.map((cameraId) =>
          post("/collection/start", {
            dataset_id: dataset.id,
            camera_id: cameraId,
            mode,
            interval,
            roi: mode === "roi_interval" ? (rois[cameraId] ?? null) : null,
            auto_label: autoLabel,
            detector,
            model_path: modelPath || null,
            trigger_model_path: triggerModelPath || null,
            label_class_ids: autoLabel === "off" ? null : labelClasses,
            label_class_mapping:
              autoLabel === "off"
                ? {}
                : serializeClassMapping(classMapping, labelClasses),
            confidence,
            similarity,
            target_classes: targets
              .split(/[,，\n]/)
              .map((s) => s.trim())
              .filter(Boolean),
          }),
        ),
      );
      const errors = results.flatMap((r, i) =>
        r.status === "rejected"
          ? [
              `${cameras.find((c) => c.id === pendingCameras[i])?.name}: ${errorText(r.reason)}`,
            ]
          : [],
      );
      if (errors.length)
        notify(
          `部分摄像头未启动：${errors.join("；")}。成功启动的摄像头继续采集。`,
        );
      acceptStatus(await api<CollectionStatus>("/collection/status"));
    } catch (e) {
      notify(errorText(e));
    } finally {
      setStarting(false);
    }
  }
  async function stop(cameraId?: string) {
    try {
      const query = new URLSearchParams({ dataset_id: dataset.id });
      if (cameraId) query.set("camera_id", cameraId);
      acceptStatus(await post<CollectionStatus>(`/collection/stop?${query}`));
      void reload().catch((e) => notify(errorText(e)));
    } catch (e) {
      notify(errorText(e));
    }
  }
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoPath, setVideoPath] = useState("");
  const [sampling, setSampling] = useState("frames");
  const [every, setEvery] = useState(30);
  async function importVideo() {
    setBusy(true);
    setTransfer(null);
    try {
      const options = {
        sampling,
        every,
        auto_label: autoLabel,
        model_path: modelPath || null,
        confidence,
        label_class_ids: autoLabel === "off" ? null : labelClasses,
        label_class_mapping:
          autoLabel === "off"
            ? {}
            : serializeClassMapping(classMapping, labelClasses),
      };
      let r;
      if (videoFile) {
        const form = new FormData();
        form.append("file", videoFile);
        form.append("options", JSON.stringify(options));
        r = await uploadForm(
          `/datasets/${dataset.id}/videos/upload`,
          form,
          setTransfer,
        );
      } else
        r = await post(`/datasets/${dataset.id}/videos/import`, {
          ...options,
          path: videoPath,
        });
      setTask(r.task_id);
    } catch (e) {
      notify(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const [task, setTask] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState("");
  const [transfer, setTransfer] = useState<UploadProgress | null>(null);
  useEffect(() => {
    onBusyChange(busy || starting);
    return () => onBusyChange(false);
  }, [busy, starting, onBusyChange]);
  useEffect(
    () => () => {
      if (preview.startsWith("blob:")) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">CAPTURE / IMPORT</div>
          <h1>数据采集</h1>
          <p>目标数据集：{dataset.name}</p>
          <p>
            当前数据集采集中 {activeSessions.length} 路 · 全部数据集{" "}
            {collection.active_count} / 9 路
          </p>
        </div>
      </div>
      <div className="source-tabs" role="group" aria-label="数据源">
        {[
          ["video", "视频抽帧"],
          ["rtsp", "RTSP 摄像头"],
        ].map(([value, label]) => (
          <button
            key={value}
            disabled={activeSessions.length > 0 || busy || starting}
            className={source === value ? "primary" : ""}
            onClick={() => setSource(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="collection-layout">
        <div className="capture-center">
          {source === "rtsp" ? (
            <div
              className={`collection-camera-grid columns-${selected.length <= 1 ? 1 : selected.length <= 4 ? 2 : 3}`}
            >
              {selected.map((camera) => (
                <CollectionCameraTile
                  key={camera.id}
                  camera={camera}
                  session={ownSessions.find((s) => s.camera_id === camera.id)}
                  roi={rois[camera.id] ?? null}
                  roiMode={mode === "roi_interval"}
                  onRoi={(roi) =>
                    setRois((all) => ({ ...all, [camera.id]: roi }))
                  }
                  onStop={() => void stop(camera.id)}
                  intervalMs={
                    selected.length > 4 ? 400 : selected.length > 1 ? 250 : 150
                  }
                  busy={starting}
                />
              ))}
              {!selected.length && (
                <div className="empty">勾选摄像头开始预览，最多显示 9 路。</div>
              )}
            </div>
          ) : (
            <div className="preview-stage">
              {preview ? (
                <video controls src={preview} />
              ) : (
                <div>选择视频文件后显示预览</div>
              )}
            </div>
          )}
          <div className="live-footer">
            <span>
              {source === "rtsp"
                ? `实时画面 ${selected.length} / 9 路`
                : "视频抽帧"}
            </span>
            <span>本次保存：{counts.saved}</span>
            <span>自动预标：{counts.labeled}</span>
            <span>跳过重复：{counts.duplicates}</span>
          </div>
        </div>
        <section className="panel controls">
          <h3>采集设置</h3>
          <label>
            数据源
            <select
              aria-label="数据源"
              value={source}
              disabled={activeSessions.length > 0 || busy || starting}
              onChange={(e) => setSource(e.target.value)}
            >
              <option value="video">视频抽帧</option>
              <option value="rtsp">RTSP 摄像头</option>
            </select>
          </label>
          {source === "video" ? (
            <>
              <label>
                上传视频
                <input
                  type="file"
                  accept=".mp4,.avi,.mkv"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) {
                      setVideoFile(f);
                      setVideoPath("");
                      setPreview((old) => {
                        if (old.startsWith("blob:")) URL.revokeObjectURL(old);
                        return URL.createObjectURL(f);
                      });
                    }
                  }}
                />
              </label>
              <label>
                或服务器视频文件
                <input
                  placeholder="/data/video.mp4"
                  value={videoPath}
                  onChange={(e) => {
                    setVideoPath(e.target.value);
                    setVideoFile(null);
                  }}
                />
              </label>
              <button
                disabled={!videoPath}
                onClick={() =>
                  setPreview(
                    "/api/videos/preview?path=" + encodeURIComponent(videoPath),
                  )
                }
              >
                预览服务器视频
              </button>
              <label>
                采样方式
                <select
                  aria-label="采样方式"
                  value={sampling}
                  onChange={(e) => {
                    setSampling(e.target.value);
                    setEvery(e.target.value === "frames" ? 30 : 2);
                  }}
                >
                  <option value="frames">每 N 帧</option>
                  <option value="seconds">每 N 秒</option>
                </select>
              </label>
              <label>
                采样间隔
                <input
                  type="number"
                  min={sampling === "frames" ? 1 : 0.1}
                  step={sampling === "frames" ? 1 : 0.1}
                  value={every}
                  onChange={(e) => setEvery(Number(e.target.value))}
                />
              </label>
              {dataset.task_type === "detection" && (
                <>
                  <label>
                    自动预标
                    <select
                      aria-label="自动预标"
                      value={autoLabel}
                      onChange={(e) => setAutoLabel(e.target.value)}
                    >
                      <option value="off">关闭</option>
                      {faceMode ? <option value="scrfd">SCRFD 人脸五点</option> : <><option value="yolo_world">YOLO-World</option>
                      <option value="custom_yolo">自定义 YOLO</option>
                      </>}
                    </select>
                  </label>
                  {autoLabel === "custom_yolo" && (
                    <ModelPicker
                      value={modelPath}
                      onChange={setModelPath}
                      notify={notify}
                    />
                  )}
                  {faceMode && <ScrfdInfo />}
                  {autoLabel !== "off" && !faceMode && (
                    <ClassSelector
                      modelClassIds={classMapping}
                      onModelClassIdsChange={setClassMapping}
                      classes={dataset.classes}
                      value={labelClasses}
                      onChange={setLabelClasses}
                    />
                  )}
                  {autoLabel !== "off" && (
                    <label>
                      置信度
                      <input
                        type="number"
                        min="0.01"
                        max="1"
                        step="0.05"
                        value={confidence}
                        onChange={(e) => setConfidence(Number(e.target.value))}
                      />
                    </label>
                  )}
                </>
              )}
              <button
                className="primary"
                disabled={
                  busy ||
                  (!videoFile && !videoPath) ||
                  (autoLabel !== "off" && labelClasses?.length === 0)
                }
                onClick={() => void importVideo()}
              >
                开始抽帧导入
              </button>
              <p>MP4、AVI、MKV；浏览器无法播放的编码仍可在服务端抽帧。</p>
            </>
          ) : (
            <>
              <fieldset
                disabled={starting || !statusReady}
                className="camera-selection"
              >
                <legend>摄像头 · 已选 {selectedCameras.length} / 9</legend>
                <div className="camera-selection-list">
                  {cameras.map((camera) => {
                    const running = collection.sessions.find(
                      (s) => s.camera_id === camera.id && s.active,
                    );
                    const checked = selectedCameras.includes(camera.id);
                    const elsewhere =
                      running && running.dataset_id !== dataset.id;
                    return (
                      <label key={camera.id}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={
                            !!running ||
                            (!checked && selectedCameras.length >= 9)
                          }
                          onChange={(e) =>
                            setSelectedCameras((ids) =>
                              e.target.checked
                                ? [...ids, camera.id].slice(0, 9)
                                : ids.filter((id) => id !== camera.id),
                            )
                          }
                        />
                        <span>
                          {camera.name}
                          {elsewhere
                            ? "（其他数据集采集中）"
                            : running
                              ? "（采集中）"
                              : ""}
                        </span>
                      </label>
                    );
                  })}
                  {!cameras.length && <p>请先在摄像头页面添加摄像头。</p>}
                </div>
              </fieldset>
              <p>以下设置用于新启动的摄像头，运行中的各路配置保持不变。</p>
              <fieldset disabled={starting}>
                <label>
                  采集模式
                  <select
                    aria-label="采集模式"
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    <option value="raw_interval">固定间隔 · 原始帧</option>
                    <option value="roi_interval">固定间隔 · ROI 裁剪</option>
                    <option value="detect_raw">检测到目标 · 完整原图</option>
                    <option value="detect_crop">检测到目标 · 目标裁剪</option>
                  </select>
                </label>
                <label>
                  保存间隔（秒）
                  <input
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={interval}
                    onChange={(e) => setIntervalValue(Number(e.target.value))}
                  />
                </label>
                {mode === "roi_interval" && (
                  <p>
                    在每个摄像头画面下点击“绘制 ROI”，各路分别设置裁剪区域。
                  </p>
                )}
                {mode.startsWith("detect_") && (
                  <>
                    <h4>保存条件</h4>
                    <label>
                      触发检测器
                      <select
                        aria-label="触发检测器"
                        value={detector}
                        onChange={(e) => setDetector(e.target.value)}
                      >
                        <option value="yolo_world">YOLO-World</option>
                        <option value="custom_yolo">自定义 YOLO</option>
                      </select>
                    </label>
                    {detector === "custom_yolo" && (
                      <ModelPicker
                        value={triggerModelPath}
                        onChange={setTriggerModelPath}
                        notify={notify}
                      />
                    )}
                    <label>
                      触发目标名称或 ID（逗号分隔）
                      <input
                        value={targets}
                        placeholder="例如 car 或 2；多种车辆可填 2, 5, 7"
                        onChange={(e) => setTargets(e.target.value)}
                      />
                    </label>
                    <p>
                      检测到任意一个所选目标即保存。目标与下方标注类别独立。
                    </p>
                    <small>
                      {detector === "yolo_world"
                        ? "YOLO-World 支持目标名称；数字按 YOLO COCO 的 0–79 类别解释：0 person、2 car、5 bus、7 truck。"
                        : "数字是触发模型的原始类别 ID，请先读取模型类别。仅使用 COCO 权重时，2 才代表 car；这里不是数据集类别 ID。"}
                    </small>
                  </>
                )}
                <h4>保存后自动标注</h4>
                <label>
                  自动预标
                  <select
                    disabled={dataset.task_type !== "detection"}
                    aria-label="自动预标"
                    value={autoLabel}
                    onChange={(e) => setAutoLabel(e.target.value)}
                  >
                    <option value="off">关闭</option>
                    {faceMode ? <option value="scrfd">SCRFD 人脸五点</option> : <><option value="yolo_world">YOLO-World</option>
                    <option value="custom_yolo">自定义 YOLO</option>
                    </>}
                  </select>
                </label>
                {autoLabel === "custom_yolo" && (
                  <ModelPicker
                    value={modelPath}
                    onChange={setModelPath}
                    notify={notify}
                  />
                )}
                {faceMode && <ScrfdInfo />}
                {autoLabel !== "off" && !faceMode && (
                  <ClassSelector
                    modelClassIds={classMapping}
                    onModelClassIdsChange={setClassMapping}
                    classes={dataset.classes}
                    value={labelClasses}
                    onChange={setLabelClasses}
                  />
                )}
                {(autoLabel !== "off" || mode.startsWith("detect_")) && (
                  <label>
                    置信度
                    <input
                      type="number"
                      min="0.01"
                      max="1"
                      step="0.05"
                      value={confidence}
                      onChange={(e) => setConfidence(Number(e.target.value))}
                    />
                  </label>
                )}
                <label>
                  去重相似度阈值
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={similarity}
                    onChange={(e) => setSimilarity(Number(e.target.value))}
                  />
                </label>
                <p>相似度 ≥ 阈值则跳过；数值越小去重越严格。</p>
              </fieldset>
              <div className="actions">
                <button
                  className="primary"
                  disabled={
                    starting ||
                    !statusReady ||
                    !pendingCameras.length ||
                    collection.active_count + pendingCameras.length > 9 ||
                    (mode === "roi_interval" &&
                      pendingCameras.some((id) => !rois[id])) ||
                    (mode.startsWith("detect_") &&
                      (!targets.trim() ||
                        (detector === "custom_yolo" &&
                          !triggerModelPath.trim()))) ||
                    (autoLabel !== "off" && labelClasses?.length === 0) ||
                    (autoLabel === "custom_yolo" && !modelPath.trim())
                  }
                  onClick={() => void start()}
                >
                  {starting
                    ? "正在启动…"
                    : `开始采集（${pendingCameras.length} 路）`}
                </button>
                <button
                  disabled={!activeSessions.length || starting}
                  onClick={() => void stop()}
                >
                  停止本数据集全部
                </button>
              </div>
              {collection.active_count + pendingCameras.length > 9 && (
                <p role="alert">全局最多 9 路采集，请先停止其他摄像头。</p>
              )}
            </>
          )}
          {busy && (
            <div className="task" role="status">
              <strong>视频上传中</strong>
              {transfer && (
                <>
                  <p>
                    {formatBytes(transfer.loaded)} /{" "}
                    {transfer.total === null
                      ? "计算中"
                      : formatBytes(transfer.total)}
                  </p>
                  <progress
                    aria-label="视频上传进度"
                    value={transfer.total ? transfer.loaded : undefined}
                    max={transfer.total || 1}
                  />
                </>
              )}
              <p>文件传输完成后开始后台抽帧。</p>
            </div>
          )}
          {task && (
            <TaskProgress
              id={task}
              onDone={() => void reload()}
              notify={notify}
            />
          )}
        </section>
      </div>
    </>
  );
}
