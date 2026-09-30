import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Dataset, Page } from "./types";
import { api, errorText } from "./services/api";
import Datasets from "./pages/Datasets";
import Collection from "./pages/Collection";
import Cameras from "./pages/Cameras";
import Review from "./pages/Review";
import "./styles.css";
function App() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [page, setPage] = useState<Page>("datasets");
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [healthy, setHealthy] = useState(false);
  const [navigationLocked, setNavigationLocked] = useState(false);
  async function reload() {
    const data = await api<Dataset[]>("/datasets");
    setDatasets(data);
    setSelected((id) =>
      data.some((d) => d.id === id) ? id : data[0]?.id || "",
    );
  }
  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
    api("/health")
      .then(() => setHealthy(true))
      .catch((e) => setError(errorText(e)));
  }, []);
  const dataset = datasets.find((d) => d.id === selected);
  function navigate(p: Page, id: string) {
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
              ["cameras", "▣", "摄像头"],
            ] as const
          ).map(([key, icon, label]) => (
            <button
              className={page === key ? "active" : ""}
              key={key}
              disabled={navigationLocked && page !== key}
              onClick={() => setPage(key)}
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
          {page !== "datasets" && page !== "cameras" && (
            <div className="dataset-picker">
              <span>目标数据集</span>
              <select
                disabled={navigationLocked}
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                {datasets.map((d) => (
                  <option value={d.id} key={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          {page === "datasets" ? (
            <Datasets
              datasets={datasets}
              reload={reload}
              navigate={navigate}
              notify={setError}
              onBusyChange={setNavigationLocked}
            />
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
              notify={setError}
              reload={reload}
            />
          ) : (
            <div className="empty">
              请先在数据集页面创建或管理数据集。
              {selected && `当前数据集：${selected}`}
            </div>
          )}
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
