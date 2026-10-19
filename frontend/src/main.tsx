import React, { lazy, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Dataset, Page } from "./types";
import { api, errorText } from "./services/api";
const Datasets = lazy(() => import("./pages/Datasets"));
const Collection = lazy(() => import("./pages/Collection"));
const Cameras = lazy(() => import("./pages/Cameras"));
const Review = lazy(() => import("./pages/Review"));
const Statistics = lazy(() => import("./pages/Statistics"));
const SecurityAnnotation = lazy(() => import("./pages/SecurityAnnotation"));
import "./styles.css";
import {
  readWorkspacePreferences,
  writePreference,
} from "./services/preferences";
function App() {
  const [saved] = useState(readWorkspacePreferences);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [page, setPage] = useState<Page>(saved.page);
  const [selected, setSelected] = useState(saved.selected);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [healthy, setHealthy] = useState(false);
  const [navigationLocked, setNavigationLocked] = useState(false);
  const [reviewSampleId, setReviewSampleId] = useState<string | null>(null);
  async function reload() {
    const data = await api<Dataset[]>("/datasets");
    setDatasets(data);
    setSelected((id) =>
      data.some((d) => d.id === id) ? id : data[0]?.id || "",
    );
    if (
      !loaded &&
      saved.selected &&
      !data.some((d) => d.id === saved.selected) &&
      ["review", "collection", "statistics"].includes(saved.page)
    )
      setPage("datasets");
    setLoaded(true);
  }
  useEffect(() => {
    if (loaded) writePreference("", "workspace", { page, selected });
  }, [loaded, page, selected]);
  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
    api("/health")
      .then(() => setHealthy(true))
      .catch((e) => setError(errorText(e)));
  }, []);
  const dataset = datasets.find((d) => d.id === selected);
  function navigate(p: Page, id: string) {
    if (navigationLocked) return;
    setReviewSampleId(null);
    setSelected(id);
    setPage(p);
  }
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <b>◈</b>
          <div>
            DataCollector<small>视觉数据工作台</small>
          </div>
        </div>
        <div className="nav-label">工作空间</div>
        <nav>
          {(
            [
              ["datasets", "▦", "数据集"],
              ["collection", "◉", "数据采集"],
              ["review", "⌘", "标注审核"],
              ["statistics", "▥", "数据统计"],
              ["cameras", "▣", "摄像头"],
              ["security-annotation", "▷", "安检视频标注"],
            ] as const
          ).map(([key, icon, label]) => (
            <button
              className={page === key ? "active" : ""}
              key={key}
              disabled={navigationLocked && page !== key}
              onClick={() => navigate(key, selected)}
            >
              <span>{icon}</span>
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <i className={healthy ? "online" : ""} />{" "}
          {healthy ? "服务已连接" : "服务未连接"}
          <small>采集 · 标注 · 审核 · 导出</small>
        </div>
      </aside>
      <main>
        <header>
          <span>视觉数据平台</span>
          <span className="muted">本地工作空间</span>
        </header>
        <div className="content">
          {error && (
            <div role="alert" className="error">
              {error}
              <button onClick={() => setError("")}>关闭</button>
            </div>
          )}
          {page !== "datasets" && page !== "cameras" && page !== "security-annotation" && (
            <div className="dataset-picker">
              <span>目标数据集</span>
              <select
                disabled={navigationLocked}
                value={selected}
                onChange={(e) => navigate(page, e.target.value)}
              >
                {datasets.map((d) => (
                  <option value={d.id} key={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <Suspense fallback={<div className="empty">正在加载页面…</div>}>
            {page === "datasets" ? (
              <Datasets
                datasets={datasets}
                reload={reload}
                navigate={navigate}
                notify={setError}
                onBusyChange={setNavigationLocked}
              />
            ) : page === "security-annotation" ? (
              <SecurityAnnotation notify={setError} onBusyChange={setNavigationLocked} />
            ) : page === "cameras" ? (
              <Cameras notify={setError} />
            ) : page === "collection" && dataset ? (
              <Collection
                key={dataset.id}
                dataset={dataset}
                reload={reload}
                notify={setError}
                onBusyChange={setNavigationLocked}
              />
            ) : page === "review" && dataset ? (
              <Review
                key={dataset.id}
                dataset={dataset}
                initialSampleId={reviewSampleId}
                onBusyChange={setNavigationLocked}
                notify={setError}
                reload={reload}
              />
            ) : page === "statistics" && dataset ? (
              <Statistics
                key={dataset.id}
                dataset={dataset}
                onReview={(sampleId) => {
                  setReviewSampleId(sampleId);
                  setPage("review");
                }}
              />
            ) : (
              <div className="empty">
                请先在数据集页面创建或管理数据集。
                {selected && `当前数据集：${selected}`}
              </div>
            )}
          </Suspense>
        </div>
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
