import { useState, useEffect, useRef } from "react";
import type { Dataset, BBox } from "../types";
import {
  api,
  post,
  errorText,
  uploadForm,
  formatBytes,
  type UploadProgress,
} from "../services/api";
import LivePreview from "../components/LivePreview";
import { useLiveCamera } from "../hooks/useLiveCamera";
import ModelPicker from "../components/ModelPicker";
import TaskProgress from "../components/TaskProgress";
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
  const [cameras, setCameras] = useState<any[]>([]);
  const [camera, setCamera] = useState("");
  const [mode, setMode] = useState("raw_interval");
  const [interval, setIntervalValue] = useState(2);
  const [roi, setRoi] = useState<BBox | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [collection, setCollection] = useState<any>({
    active: false,
    saved: 0,
    labeled: 0,
    duplicates: 0,
  });
  const [autoLabel, setAutoLabel] = useState("off");
  const [detector, setDetector] = useState("yolo_world");
  const [modelPath, setModelPath] = useState("");
  const [confidence, setConfidence] = useState(0.25);
  const [similarity, setSimilarity] = useState(0.92);
  const [targets, setTargets] = useState("person");
  const restored = useRef(false);
  const live = useLiveCamera(
    source === "rtsp" ? camera : "",
    collection.active &&
      collection.camera_id === camera &&
      collection.detecting,
  );
  useEffect(() => {
    api("/cameras")
      .then((r) => {
        setCameras(r);
        setCamera(r[0]?.id || "");
      })
      .catch((e) => notify(errorText(e)));
  }, []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const s = await api("/collection/status");
        if (!stopped) {
          setCollection(s);
          if (s.active && !restored.current) {
            restored.current = true;
            setSource("rtsp");
            setCamera(s.camera_id);
            setMode(s.mode);
            setTask(s.task_id);
            if (s.config) {
              setIntervalValue(s.config.interval);
              setRoi(s.config.roi);
              setAutoLabel(s.config.auto_label);
              setDetector(s.config.detector);
              setModelPath(s.config.model_path || "");
              setConfidence(s.config.confidence);
              setSimilarity(s.config.similarity);
            }
          }
          if (!s.active) restored.current = false;
        }
      } catch (e) {
        if (!stopped) notify(errorText(e));
      }
      if (!stopped) timer = setTimeout(tick, 1000);
    }
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);
  async function start() {
    try {
      const r = await post("/collection/start", {
        dataset_id: dataset.id,
        camera_id: camera,
        mode,
        interval,
        roi,
        auto_label: autoLabel,
        detector,
        model_path: modelPath || null,
        confidence,
        similarity,
        target_classes: targets
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setTask(r.task_id);
      setCollection((c: any) => ({ ...c, active: true }));
    } catch (e) {
      notify(errorText(e));
    }
  }
  async function stop() {
    try {
      await post("/collection/stop");
      await reload();
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
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
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
          {collection.active && collection.dataset_id !== dataset.id && (
            <p>当前运行中的采集目标：{collection.dataset_id}；停止后可切换。</p>
          )}
        </div>
      </div>
      <div className="source-tabs" role="group" aria-label="数据源">
        {[
          ["video", "视频抽帧"],
          ["rtsp", "RTSP 摄像头"],
        ].map(([value, label]) => (
          <button
            key={value}
            disabled={collection.active || busy}
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
            <LivePreview
              url={live.status.connected ? live.url : ""}
              width={live.status.width}
              height={live.status.height}
              roi={roi}
              drawing={drawing}
              onRoi={(b) => {
                setRoi(b);
                setDrawing(false);
              }}
              message={live.status.error}
            />
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
                ? live.status.connected
                  ? "● 已连接"
                  : live.status.error
                : "视频抽帧"}
            </span>
            <span>实际 FPS：{source === "rtsp" ? live.status.fps : "—"}</span>
            <span>本次保存：{source === "rtsp" ? collection.saved : "—"}</span>
            <span>
              自动预标：{source === "rtsp" ? collection.labeled : "—"}
            </span>
            <span>
              跳过重复：{source === "rtsp" ? collection.duplicates : "—"}
            </span>
          </div>
        </div>
        <section className="panel controls">
          <h3>采集设置</h3>
          <label>
            数据源
            <select
              aria-label="数据源"
              value={source}
              disabled={collection.active || busy}
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
                      <option value="yolo_world">YOLO-World</option>
                      <option value="custom_yolo">自定义 YOLO</option>
                    </select>
                  </label>
                  {autoLabel === "custom_yolo" && (
                    <ModelPicker
                      value={modelPath}
                      onChange={setModelPath}
                      notify={notify}
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
                disabled={busy || (!videoFile && !videoPath)}
                onClick={() => void importVideo()}
              >
                开始抽帧导入
              </button>
              <p>MP4、AVI、MKV；浏览器无法播放的编码仍可在服务端抽帧。</p>
            </>
          ) : (
            <>
              <fieldset disabled={collection.active}>
                <label>
                  摄像头
                  <select
                    aria-label="摄像头"
                    value={camera}
                    onChange={(e) => {
                      setCamera(e.target.value);
                      setRoi(null);
                    }}
                  >
                    {cameras.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
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
                <label>ROI（原图坐标）</label>
                <div className="actions">
                  <button
                    disabled={!live.status.connected}
                    className={drawing ? "primary" : ""}
                    onClick={() => setDrawing(!drawing)}
                  >
                    {roi ? "重新绘制 ROI" : "绘制 ROI"}
                  </button>
                  <button
                    onClick={() => {
                      setRoi(null);
                      setDrawing(false);
                    }}
                  >
                    清除
                  </button>
                </div>
                <p>
                  {roi
                    ? roi.map((v) => Math.round(v)).join(", ")
                    : "在实时画面上拖动绘制"}
                </p>
                <label>
                  自动预标
                  <select
                    disabled={dataset.task_type !== "detection"}
                    aria-label="自动预标"
                    value={autoLabel}
                    onChange={(e) => setAutoLabel(e.target.value)}
                  >
                    <option value="off">关闭</option>
                    <option value="yolo_world">YOLO-World</option>
                    <option value="custom_yolo">自定义 YOLO</option>
                  </select>
                </label>
                {mode.startsWith("detect_") && (
                  <>
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
                    {dataset.task_type !== "detection" && (
                      <label>
                        触发目标（逗号分隔）
                        <input
                          value={targets}
                          onChange={(e) => setTargets(e.target.value)}
                        />
                      </label>
                    )}
                  </>
                )}
                {(autoLabel === "custom_yolo" ||
                  (mode.startsWith("detect_") &&
                    detector === "custom_yolo")) && (
                  <ModelPicker
                    value={modelPath}
                    onChange={setModelPath}
                    notify={notify}
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
                  disabled={collection.active || !camera}
                  onClick={() => void start()}
                >
                  开始采集
                </button>
                <button
                  disabled={!collection.active}
                  onClick={() => void stop()}
                >
                  停止
                </button>
              </div>
              {collection.error && <p className="danger">{collection.error}</p>}
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
