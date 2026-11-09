import { useState, useEffect, useRef } from "react";
import type {
  Dataset,
  BBox,
  ClassMapping,
  Camera,
  CollectionStatus,
  CollectionSession,
} from "../types";
import { writePreference } from "../services/preferences";
import { cachedCollectionStatus, readCollectionPreferences, rememberCollectionStatus, mergeStartedSessions } from "../services/collectionState";
import { serializeClassMapping } from "../services/classMapping";
import { withRequestTimeout } from "../services/requestTimeout";
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
  const [saved] = useState(() => readCollectionPreferences(dataset));
  const [source, setSource] = useState(saved.source);
  const faceMode = dataset.annotation_mode === "face_5pt";
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedCameras, setSelectedCameras] = useState<string[]>(saved.selectedCameras);
  const [mode, setMode] = useState(saved.mode);
  const [interval, setIntervalValue] = useState(saved.interval);
  const [rois, setRois] = useState<Record<string, BBox | null>>(saved.rois);
  const [collection, setCollection] = useState<CollectionStatus>(() => cachedCollectionStatus(dataset.id));
  const [starting, setStarting] = useState(false);
  const startPending = useRef(false);
  const stopPending = useRef(new Set<string>());
  const [stoppingCameras, setStoppingCameras] = useState<string[]>([]);
  const statusVersion = useRef(0);
  const refreshStatus = useRef(() => {});
  const [statusError, setStatusError] = useState("");
  const selectionRestored = useRef(saved.hasSelection);
  const cameraList = useRef<Camera[]>([]);
  const mounted = useRef(true);
  const [autoLabel, setAutoLabel] = useState(saved.autoLabel);
  const [detector, setDetector] = useState(saved.detector);
  const [modelPath, setModelPath] = useState(saved.modelPath);
  const [triggerModelPath, setTriggerModelPath] = useState(saved.triggerModelPath);
  const [labelClasses, setLabelClasses] = useState<number[] | null>(saved.labelClasses);
  const [classMapping, setClassMapping] = useState<ClassMapping>(saved.classMapping);
  const [confidence, setConfidence] = useState(saved.confidence);
  const [similarity, setSimilarity] = useState(saved.similarity);
  const [targets, setTargets] = useState(saved.targets);
  const restored = useRef(saved.hasSettings);
  const displaySessions = collection.sessions.map((session) => stoppingCameras.includes(session.camera_id)
    ? { ...session, stopping: true } : session);
  const ownSessions = displaySessions.filter(
    (s) => s.dataset_id === dataset.id,
  );
  const activeSessions = ownSessions.filter((s) => s.active);
  const stoppableSessions = activeSessions.filter((s) => !s.stopping);
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
  const [statusReady, setStatusReady] = useState(false);
  useEffect(() => {
    let live = true;
    api<Camera[]>("/cameras")
      .then((r) => {
        if (!live) return;
        cameraList.current = r;
        setCameras(r);
        setSelectedCameras((ids) => {
          const valid = ids.filter((id) => r.some((camera) => camera.id === id));
          return valid.length || saved.hasSelection ? valid : r[0] ? [r[0].id] : [];
        });
      })
      .catch((e) => live && notify(errorText(e)));
    return () => {
      live = false;
    };
  }, []);
  function acceptStatus(s: CollectionStatus) {
    if (!mounted.current) return;
    setCollection(s);
    rememberCollectionStatus(dataset.id, s);
    setStatusReady(true);
    setStatusError("");
    const active = s.sessions.filter(
      (item) => item.active && item.dataset_id === dataset.id,
    );
    if (!selectionRestored.current) {
      selectionRestored.current = true;
      setSelectedCameras((ids) => active.length ? active.map((item) => item.camera_id).slice(0, 9)
        : ids.length ? ids : cameraList.current[0] ? [cameraList.current[0].id] : []);
    }
    if (active.length) {
      setSource("rtsp");
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
    let inFlight = false;
    async function tick() {
      if (inFlight || stopped) return;
      inFlight = true;
      clearTimeout(timer);
      const version = statusVersion.current;
      try {
        const s = await withRequestTimeout(
          (signal) => api<CollectionStatus>("/collection/status", {
            signal,
            cache: "no-store",
          }),
          {
            signal: controller.signal,
            timeoutMs: 10000,
            message: "采集状态同步超时（10 秒），请检查浏览器到服务器的网络连接后重试。",
          },
        );
        if (!stopped && version === statusVersion.current) acceptStatus(s);
      } catch (e) {
        if (!stopped && version === statusVersion.current) {
          setStatusError(errorText(e));
          setStatusReady(false);
        }
      } finally {
        inFlight = false;
        if (!stopped) timer = setTimeout(tick, 1000);
      }
    }
    refreshStatus.current = () => void tick();
    void tick();
    return () => {
      mounted.current = false;
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [dataset.id]);
  async function start() {
    if (startPending.current || !statusReady || !pendingCameras.length) return;
    startPending.current = true;
    setStarting(true);
    statusVersion.current++;
    restored.current = true;
    try {
      const results = await Promise.allSettled(
        pendingCameras.map((cameraId) =>
          post<{ task_id: string; session?: CollectionSession }>("/collection/start", {
            dataset_id: dataset.id,
            camera_id: cameraId,
            mode,
            interval,
            roi: mode === "roi_interval" ? (rois[cameraId] ?? null) : null,
            auto_label: autoLabel,
            detector,
            model_path: modelPath || null,
            trigger_model_path: triggerModelPath || null,
            label_class_ids: autoLabel === "off" || faceMode ? null : labelClasses,
            label_class_mapping:
              autoLabel === "off" || faceMode
                ? {}
                : serializeClassMapping(classMapping, labelClasses),
            confidence,
            similarity,
            target_classes: detector === "scrfd" ? ["face"] : targets
              .split(/[,，\n]/)
              .map((s) => s.trim())
              .filter(Boolean),
          }),
        ),
      );
      statusVersion.current++;
      const started = results.flatMap((result) => result.status === "fulfilled" && result.value.session ? [result.value.session] : []);
      if (mounted.current) {
        setCollection((previous) => {
          const next = mergeStartedSessions(previous, started);
          rememberCollectionStatus(dataset.id, next);
          return next;
        });
      }
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
    } catch (e) {
      notify(errorText(e));
    } finally {
      startPending.current = false;
      if (mounted.current) setStarting(false);
      refreshStatus.current();
    }
  }
  async function stop(cameraId?: string) {
    const ids = ownSessions.filter((s) => s.active && !s.stopping
      && (!cameraId || s.camera_id === cameraId) && !stopPending.current.has(s.camera_id)).map((s) => s.camera_id);
    if (!ids.length) return;
    ids.forEach((id) => stopPending.current.add(id));
    setStoppingCameras([...stopPending.current]);
    statusVersion.current++;
    try {
      const query = new URLSearchParams({ dataset_id: dataset.id });
      if (cameraId) query.set("camera_id", cameraId);
      const result = await post<CollectionStatus>(`/collection/stop?${query}`);
      statusVersion.current++;
      acceptStatus(result);
      void reload().catch((e) => notify(errorText(e)));
    } catch (e) {
      notify(errorText(e));
    } finally {
      statusVersion.current++;
      ids.forEach((id) => stopPending.current.delete(id));
      if (mounted.current) setStoppingCameras([...stopPending.current]);
      refreshStatus.current();
    }
  }
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoPath, setVideoPath] = useState(saved.videoPath);
  const [sampling, setSampling] = useState(saved.sampling);
  const [every, setEvery] = useState(saved.every);
  useEffect(() => {
    writePreference(dataset.id, "collection", {
      source, selectedCameras, mode, interval, rois, autoLabel, detector,
      modelPath, triggerModelPath, labelClasses, classMapping, confidence,
      similarity, targets, videoPath, sampling, every,
    });
  }, [dataset.id, source, selectedCameras, mode, interval, rois, autoLabel, detector,
    modelPath, triggerModelPath, labelClasses, classMapping, confidence, similarity, targets,
    videoPath, sampling, every]);
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
            {statusReady ? `当前数据集后台采集 ${activeSessions.length} 路 · 全部数据集 ${collection.active_count} / 9 路`
              : statusError ? "采集状态同步失败，请重试。" : "正在同步后台采集状态…"}
          </p>
        </div>
      </div>
      <div className="collection-status-message" role="status" aria-live="polite">
        {!statusReady ? <>
          <span>{statusError ? "采集状态同步失败，已保留上次显示的设置和状态。" : "正在恢复采集状态和预览；页面切换不会停止后台采集。"}</span>
          {statusError && <><small>{statusError}</small><button onClick={() => refreshStatus.current()}>重试同步</button></>}
        </> : activeSessions.length ? <span>
          后台正在采集 {activeSessions.length} 路，当前显示 {selected.length} 路预览。
          可以切换到标注审核；勾选摄像头只控制预览，停止采集请使用停止按钮。
        </span> : <span>采集状态已同步。设置和预览选择已保留，可以继续采集。</span>}
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
                  session={displaySessions.find((s) => s.camera_id === camera.id)}
                  canStop={ownSessions.some((s) => s.camera_id === camera.id)}
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
                disabled={starting}
                className="camera-selection"
              >
                <legend>预览摄像头 · 已选 {selectedCameras.length} / 9</legend>
                <p>勾选或取消仅显示 / 隐藏预览，不会启动或停止后台采集。</p>
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
                          aria-label={camera.name}
                          checked={checked}
                          disabled={
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
                              ? "（后台采集中，可切换预览）"
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
                        <option value="scrfd">SCRFD 人脸检测</option>
                      </select>
                    </label>
                    {detector === "custom_yolo" && (
                      <ModelPicker
                        value={triggerModelPath}
                        onChange={setTriggerModelPath}
                        notify={notify}
                      />
                    )}
                    {detector === "scrfd" ? <p>触发目标固定为 face。检测到人脸后保存；未检出人脸时跳过。</p> : <><label>
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
                    </>}
                    {detector === "scrfd" && !faceMode && <ScrfdInfo />}
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
                {faceMode && mode.startsWith("detect_") && <p>
                  {detector === "scrfd"
                    ? "由 SCRFD 人脸检测触发保存；自动预标选择 SCRFD 时，一次检测同时提供人脸框和五点。"
                    : "当前由 YOLO 目标触发保存，保存后的图片可独立使用 SCRFD 标注人脸五点。"}
                </p>}
                {faceMode && mode === "detect_raw" && <p>此模式保留完整原图。需要后脑勺、无人脸负样本时，可另用固定间隔采集。</p>}
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
                    stoppableSessions.length < activeSessions.length ||
                    !statusReady ||
                    !pendingCameras.length ||
                    collection.active_count + pendingCameras.length > 9 ||
                    (mode === "roi_interval" &&
                      pendingCameras.some((id) => !rois[id])) ||
                    (mode.startsWith("detect_") &&
                      ((detector !== "scrfd" && !targets.trim()) ||
                        (detector === "custom_yolo" &&
                          !triggerModelPath.trim()))) ||
                    (autoLabel !== "off" && !faceMode && labelClasses?.length === 0) ||
                    (autoLabel === "custom_yolo" && !modelPath.trim())
                  }
                  onClick={() => void start()}
                >
                  {starting
                    ? "正在提交启动请求…"
                    : !statusReady ? statusError ? "采集状态未同步" : "正在同步采集状态…"
                    : !selectedCameras.length ? "请选择摄像头"
                    : !pendingCameras.length && activeSessions.length ? "所选摄像头已在采集"
                    : `开始采集（${pendingCameras.length} 路）`}
                </button>
                <button
                  disabled={!stoppableSessions.length || starting}
                  onClick={() => void stop()}
                >
                  {(activeSessions.length > 0 || stoppingCameras.length > 0) && !stoppableSessions.length ? "正在停止，等待当前处理结束…" : "停止本数据集全部"}
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
