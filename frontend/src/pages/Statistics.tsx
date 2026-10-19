import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import "./Statistics.css";
import type { Dataset } from "../types";
import type {
  DatasetStatistics,
  Distribution,
  ImageSelection,
  ReviewStatus,
  StatisticsClass,
} from "../types/statistics";
import { api, errorText } from "../services/api";
import {
  downloadStatistics,
  percent,
  selectStatisticsImages,
  statusNames,
} from "../services/statistics";

const PAGE_SIZE = 24;
const palette = [
  "#526fe5",
  "#249b90",
  "#9370d5",
  "#dc9a3e",
  "#528fb9",
  "#ce7498",
];
const classColor = (id: number) => palette[id % palette.length];
const iconPaths = {
  images: "M4 4h16v16H4z M4 15l5-5 5 5 3-3 3 3 M15 8h.01",
  box: "M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5 M8 8h8v8H8z",
  classes: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  check: "M20 6L9 17l-5-5",
  clock: "M12 8v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  alert:
    "M12 8v5 M12 17h.01 M10.3 3.8L2.4 18a2 2 0 0 0 1.8 3h15.6a2 2 0 0 0 1.8-3L13.7 3.8a2 2 0 0 0-3.4 0",
  refresh:
    "M20 7v5h-5 M4 17v-5h5 M6 6a8 8 0 0 1 13 2l1 4 M4 12l1 4a8 8 0 0 0 13 2",
  download: "M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  chart: "M4 3v17h17 M8 15v-4 M13 15V6 M18 15v-7",
};
function Icon({ name }: { name: keyof typeof iconPaths }) {
  return (
    <svg
      className="statistics-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={iconPaths[name]} />
    </svg>
  );
}
function Metric({
  label,
  value,
  unit,
  detail,
  icon,
  tone,
  secondary = false,
  onSelect,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  detail: string;
  icon: keyof typeof iconPaths;
  tone: string;
  secondary?: boolean;
  onSelect?: () => void;
}) {
  const Tag = onSelect ? "button" : "div";
  return (
    <Tag
      className={`statistics-metric tone-${tone}${secondary ? " secondary" : ""}`}
      onClick={onSelect}
    >
      <div className="statistics-metric-top">
        <span className="statistics-metric-icon">
          <Icon name={icon} />
        </span>
        <span>{label}</span>
        {onSelect && (
          <span className="statistics-metric-arrow">
            <Icon name="arrow" />
          </span>
        )}
      </div>
      <div className="statistics-metric-value">
        <strong>{value}</strong>
        {unit && <span>{unit}</span>}
      </div>
      <small>{detail}</small>
    </Tag>
  );
}
function Share({ value, color }: { value: number; color: string }) {
  return (
    <div className="statistics-share">
      <span>{percent(value)}</span>
      <div aria-hidden="true">
        <i
          style={{ width: `${Math.min(100, value * 100)}%`, background: color }}
        />
      </div>
    </div>
  );
}
const sourceNames: Record<string, string> = {
  image: "图片导入",
  video: "视频抽帧",
  rtsp: "摄像头采集",
  manual: "人工标注",
  yolo_world: "YOLO-World",
  custom_yolo: "自定义 YOLO",
  unknown: "未记录",
};

function Bars({
  title,
  rows,
  unit,
  onSelect,
  colors,
  caption,
}: {
  title: string;
  rows: Distribution;
  unit: string;
  onSelect?: (name: string) => void;
  colors?: string[];
  caption?: string;
}) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  return (
    <section className="panel statistics-chart">
      <div className="panel-title">
        <div>
          <h2>{title}</h2>
          {caption && <p>{caption}</p>}
        </div>
        <span className="statistics-unit">{unit}</span>
      </div>
      <div className="statistics-bars">
        {rows.map((row, index) => (
          <div
            className="statistics-bar-row"
            key={row.name}
            style={
              {
                "--bar-color":
                  colors?.[index] ?? palette[index % palette.length],
              } as CSSProperties
            }
          >
            <span className="statistics-color-dot" aria-hidden="true" />
            {onSelect ? (
              <button
                className="statistics-link"
                onClick={() => onSelect(row.name)}
                title={row.name}
              >
                {row.name}
              </button>
            ) : (
              <span title={row.name}>{sourceNames[row.name] || row.name}</span>
            )}
            <div className="statistics-track" aria-hidden="true">
              <i style={{ width: `${(row.count / max) * 100}%` }} />
            </div>
            <strong>{row.count.toLocaleString()}</strong>
          </div>
        ))}
        {!rows.length && <div className="empty">暂无数据</div>}
      </div>
    </section>
  );
}

export default function Statistics({
  dataset,
  onReview,
}: {
  dataset: Dataset;
  onReview: (sampleId: string) => void;
}) {
  const [status, setStatus] = useState<ReviewStatus | "">("");
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<DatasetStatistics | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<"id" | "box_count" | "image_count">("id");
  const [selection, setSelection] = useState<ImageSelection>({ kind: "all" });
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [showMore, setShowMore] = useState(false);
  const [showAllChecks, setShowAllChecks] = useState(false);
  const imagesPanel = useRef<HTMLElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    setSelection({ kind: "all" });
    setQuery("");
    setPage(0);
    api<DatasetStatistics>(
      `/datasets/${dataset.id}/statistics${status ? `?status=${status}` : ""}`,
      { signal: controller.signal },
    )
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [dataset.id, status, refresh]);
  const classes = useMemo(
    () =>
      [...(data?.classes ?? [])].sort((a, b) =>
        sort === "id" ? a.id - b.id : b[sort] - a[sort] || a.id - b.id,
      ),
    [data, sort],
  );
  const images = useMemo(
    () => selectStatisticsImages(data?.samples ?? [], selection, query),
    [data, selection, query],
  );
  const detection = dataset.task_type === "detection";
  const raw = dataset.task_type === "raw";
  function showImages(next: ImageSelection) {
    setSelection(next);
    setQuery("");
    setPage(0);
    imagesPanel.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const selectionLabel =
    selection.kind === "class"
      ? `类别 ${selection.id} · ${data?.classes.find((c) => c.id === selection.id)?.name ?? ""}`
      : selection.kind === "issue"
        ? data?.quality.find((q) => q.code === selection.code)?.name
        : selection.kind === "negative"
          ? "已确认无目标"
          : selection.kind === "pending_empty"
            ? "待审核无标签"
            : selection.kind === "issues"
              ? "有异常或待核查问题"
              : selection.kind === "bucket"
                ? `每图 ${selection.name} 个有效框`
                : "范围内全部图片";
  function classChart(metric: "box_count" | "image_count") {
    return classes.map((c) => ({
      name: `${c.id} · ${c.name}`,
      count: c[metric],
    }));
  }
  function selectChartClass(name: string) {
    const cls = classes.find((c) => `${c.id} · ${c.name}` === name);
    if (cls) showImages({ kind: "class", id: cls.id });
  }
  return (
    <div className="statistics-page">
      <div className="statistics-heading">
        <span className="statistics-heading-icon">
          <Icon name="chart" />
        </span>
        <h1>数据统计</h1>
        <button
          className="statistics-refresh"
          disabled={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          <Icon name="refresh" />
          {loading ? "统计中…" : "刷新统计"}
        </button>
      </div>
      <div className="statistics-toolbar">
        <label>
          统计范围{" "}
          <select
            aria-label="统计审核范围"
            value={status}
            onChange={(e) => setStatus(e.target.value as ReviewStatus | "")}
          >
            <option value="">全部状态（含已拒绝）</option>
            <option value="approved">仅已确认</option>
            <option value="pending">仅待审核</option>
            <option value="rejected">仅已拒绝</option>
          </select>
        </label>
        {data && (
          <span
            className="statistics-updated"
            title={new Date(data.generated_at).toLocaleString()}
          >
            <Icon name="clock" />
            更新于{" "}
            {new Date(data.generated_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        )}
        <span className="statistics-toolbar-spacer" />
        {!raw && (
          <button
            disabled={!data || loading}
            onClick={() => data && downloadStatistics(data, "classes")}
          >
            <Icon name="download" />
            导出类别 CSV
          </button>
        )}
        <button
          disabled={!data || loading}
          onClick={() => data && downloadStatistics(data, "issues")}
        >
          <Icon name="download" />
          导出异常 CSV
        </button>
      </div>
      {error && (
        <div className="error" role="alert">
          统计失败：{error}
          <button onClick={() => setRefresh((value) => value + 1)}>重试</button>
        </div>
      )}
      {loading && (
        <div className="panel empty" role="status">
          正在读取标注并统计，图片较多时请稍候…
        </div>
      )}
      {data && !loading && (
        <>
          <div className={`statistics-cards is-${dataset.task_type}`}>
            <Metric
              label="图片总数"
              value={data.overview.images.toLocaleString()}
              unit="张"
              detail={`当前范围 / 数据集共 ${data.scope.dataset_total.toLocaleString()} 张`}
              icon="images"
              tone="blue"
              onSelect={() => showImages({ kind: "all" })}
            />
            {!raw && (
              <Metric
                label={detection ? "有效标注框" : "已分类图片"}
                value={(detection
                  ? data.overview.boxes
                  : data.overview.images_with_labels
                ).toLocaleString()}
                unit={detection ? "个" : "张"}
                detail={
                  detection
                    ? `分布在 ${data.overview.images_with_labels.toLocaleString()} 张图片中`
                    : `还有 ${data.overview.empty_images.toLocaleString()} 张未分类`
                }
                icon="box"
                tone="teal"
              />
            )}
            {!raw && (
              <Metric
                label="已标注类别"
                value={
                  <>
                    {data.overview.present_classes}
                    <em> / {data.overview.configured_classes}</em>
                  </>
                }
                unit="类"
                detail={
                  data.overview.present_classes ===
                  data.overview.configured_classes
                    ? "所有类别均有标注"
                    : `${data.overview.configured_classes - data.overview.present_classes} 个类别尚无有效标注`
                }
                icon="classes"
                tone="violet"
              />
            )}
            {detection && (
              <Metric
                secondary
                label="已确认无目标"
                value={data.overview.confirmed_negative.toLocaleString()}
                unit="张"
                detail="已审核的空标注图片"
                icon="check"
                tone="green"
                onSelect={() => showImages({ kind: "negative" })}
              />
            )}
            {!raw && (
              <Metric
                secondary
                label="待审核无标签"
                value={data.overview.pending_empty.toLocaleString()}
                unit="张"
                detail="还需要人工检查"
                icon="clock"
                tone="amber"
                onSelect={() => showImages({ kind: "pending_empty" })}
              />
            )}
            <Metric
              secondary
              label="需要检查的图片"
              value={data.overview.issue_images.toLocaleString()}
              unit="张"
              detail={
                data.overview.issue_images
                  ? `${data.overview.invalid_records} 条异常标注记录`
                  : "未发现基础检查问题"
              }
              icon={data.overview.issue_images ? "alert" : "check"}
              tone={data.overview.issue_images ? "rose" : "green"}
              onSelect={() => showImages({ kind: "issues" })}
            />
          </div>
          <div className="statistics-summary">
            <div className="statistics-review-progress">
              <div className="statistics-review-heading">
                <strong>审核分布</strong>
                <span>
                  已确认{" "}
                  <b>
                    {percent(
                      data.overview.images
                        ? data.overview.approved / data.overview.images
                        : 0,
                    )}
                  </b>
                </span>
              </div>
              <div className="statistics-stacked-track" aria-hidden="true">
                {(["approved", "pending", "rejected"] as const).map((key) => (
                  <i
                    className={`status-${key}`}
                    key={key}
                    style={{
                      width: `${data.overview.images ? (data.overview[key] / data.overview.images) * 100 : 0}%`,
                    }}
                  />
                ))}
              </div>
              <div className="statistics-review-legend">
                {(["approved", "pending", "rejected"] as const).map((key) => (
                  <span key={key}>
                    <i className={`status-${key}`} />
                    {statusNames[key]}
                    <b>{data.overview[key].toLocaleString()}</b>
                  </span>
                ))}
              </div>
            </div>
            {detection && (
              <div className="statistics-density">
                <div>
                  <span>平均每图框数</span>
                  <strong>
                    {data.overview.average_boxes.toFixed(2)}
                    <small>个</small>
                  </strong>
                </div>
                <div>
                  <span>框数中位数</span>
                  <strong>
                    {data.overview.median_boxes}
                    <small>个</small>
                  </strong>
                </div>
                <div>
                  <span>单图最多框数</span>
                  <strong>
                    {data.overview.max_boxes}
                    <small>个</small>
                  </strong>
                </div>
              </div>
            )}
          </div>
          {!data.overview.images && (
            <div className="panel empty">
              当前范围没有图片。可以切换审核范围，或先向数据集导入图片。
            </div>
          )}
          {!raw && (
            <section className="panel">
              <div className="panel-title">
                <div className="statistics-section-heading">
                  <h2>类别明细</h2>
                  <span className="statistics-count-badge">
                    {classes.length} 类
                  </span>
                </div>
                <div className="statistics-table-actions">
                  <button
                    className="statistics-more-button"
                    aria-expanded={showMore}
                    onClick={() => setShowMore(!showMore)}
                  >
                    {showMore ? "收起指标" : "更多指标"}
                    <span aria-hidden="true">{showMore ? "−" : "+"}</span>
                  </button>
                  <label className="statistics-sort">
                    排序{" "}
                    <select
                      aria-label="类别统计排序"
                      value={sort}
                      onChange={(e) => setSort(e.target.value as typeof sort)}
                    >
                      <option value="id">类别 ID</option>
                      {detection && (
                        <option value="box_count">框数量从多到少</option>
                      )}
                      <option value="image_count">图片数从多到少</option>
                    </select>
                  </label>
                </div>
              </div>
              <div className="statistics-table-scroll">
                <table className="statistics-class-table">
                  <thead>
                    <tr>
                      <th>类别 ID</th>
                      <th>类别名称</th>
                      {detection && <th>有效框数</th>}
                      <th>包含图片数</th>
                      {detection && <th>框数量占比</th>}
                      <th>图片覆盖率</th>
                      {showMore && detection && (
                        <>
                          <th>含该类图片平均框数</th>
                          <th>单图最多框数</th>
                        </>
                      )}
                      {showMore && <th>已确认 / 待审核 / 已拒绝</th>}
                      {showMore && detection && <th>小 / 中 / 大目标</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {classes.map((c: StatisticsClass) => (
                      <tr
                        key={c.id}
                        className={c.image_count ? "" : "statistics-zero-row"}
                      >
                        <td>
                          <code className="statistics-class-id">{c.id}</code>
                        </td>
                        <td>
                          <strong className="statistics-class-name">
                            <i style={{ background: classColor(c.id) }} />
                            {c.name}
                          </strong>
                          {!c.image_count && <small>尚无有效标注</small>}
                        </td>
                        {detection && (
                          <td className="statistics-box-count">
                            {c.box_count.toLocaleString()}
                          </td>
                        )}
                        <td>
                          <button
                            className="statistics-link"
                            aria-label={`查看类别 ${c.id} ${c.name} 的 ${c.image_count} 张图片`}
                            onClick={() =>
                              showImages({ kind: "class", id: c.id })
                            }
                          >
                            {c.image_count.toLocaleString()} 张 ↗
                          </button>
                        </td>
                        {detection && (
                          <td>
                            <Share
                              value={c.box_share}
                              color={classColor(c.id)}
                            />
                          </td>
                        )}
                        <td>
                          <Share
                            value={c.image_coverage}
                            color={classColor(c.id)}
                          />
                        </td>
                        {showMore && detection && (
                          <>
                            <td>
                              {c.image_count ? c.average_boxes.toFixed(2) : "—"}
                            </td>
                            <td>{c.max_boxes}</td>
                          </>
                        )}
                        {showMore && (
                          <td>
                            {c.approved} / {c.pending} / {c.rejected}
                          </td>
                        )}
                        {showMore && detection && (
                          <td>
                            {c.small} / {c.medium} / {c.large}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          <div className="statistics-grid">
            {!raw && (
              <Bars
                title="类别图片分布"
                rows={classChart("image_count")}
                unit="张图片"
                colors={classes.map((c) => classColor(c.id))}
                onSelect={selectChartClass}
              />
            )}
            {detection && (
              <Bars
                title="类别框数量分布"
                rows={classChart("box_count")}
                unit="个有效框"
                colors={classes.map((c) => classColor(c.id))}
                onSelect={selectChartClass}
              />
            )}
            {detection && (
              <Bars
                title="每张图片框数分布"
                caption="按单张图片中的有效框数量分组"
                rows={data.distributions.boxes_per_image}
                unit="张图片"
                onSelect={(name) => showImages({ kind: "bucket", name })}
              />
            )}
            {detection && (
              <Bars
                title="目标大小分布"
                caption="按目标框占整张图片的面积划分"
                rows={[
                  {
                    name: "小目标 <1%",
                    count: classes.reduce((sum, c) => sum + c.small, 0),
                  },
                  {
                    name: "中目标 1%–10%",
                    count: classes.reduce((sum, c) => sum + c.medium, 0),
                  },
                  {
                    name: "大目标 ≥10%",
                    count: classes.reduce((sum, c) => sum + c.large, 0),
                  },
                ]}
                unit="个有效框"
              />
            )}
          </div>
          <details className="panel statistics-details">
            <summary>图片规格与来源分布</summary>
            <div className="statistics-grid">
              <Bars
                title="图片分辨率"
                rows={data.distributions.resolutions}
                unit="张图片"
              />
              <Bars
                title="横竖图分布"
                rows={data.distributions.orientations}
                unit="张图片"
              />
              <Bars
                title="图片格式"
                rows={data.distributions.formats}
                unit="张图片"
              />
              <Bars
                title="采集来源"
                rows={data.distributions.sources}
                unit="张图片"
              />
              {detection && (
                <Bars
                  title="标注来源"
                  rows={data.distributions.box_sources}
                  unit="个有效框"
                />
              )}
            </div>
          </details>
          <section className="panel statistics-quality">
            <div className="panel-title">
              <div className="statistics-section-heading">
                <h2>数据质量检查</h2>
                {data.overview.issue_images > 0 && (
                  <span className="statistics-count-badge tone-rose">
                    {data.overview.issue_images} 张待检查
                  </span>
                )}
              </div>
              <button
                className="statistics-more-button"
                aria-expanded={showAllChecks}
                onClick={() => setShowAllChecks(!showAllChecks)}
              >
                {showAllChecks
                  ? "仅看问题"
                  : `全部 ${data.quality.length} 项检查`}
                <span aria-hidden="true">{showAllChecks ? "−" : "+"}</span>
              </button>
            </div>
            {!data.overview.issue_images && !showAllChecks && (
              <div className="statistics-quality-clear">
                <span>
                  <Icon name="check" />
                </span>
                <div>
                  <strong>未发现基础检查问题</strong>
                  <p>
                    当前范围内的 {data.overview.images.toLocaleString()}{" "}
                    张图片已完成检查
                  </p>
                </div>
              </div>
            )}
            {(showAllChecks || data.overview.issue_images > 0) && (
              <div className="statistics-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>检查项</th>
                      <th>级别</th>
                      <th>问题记录数</th>
                      <th>涉及图片数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.quality
                      .filter((issue) => showAllChecks || issue.count > 0)
                      .map((issue) => (
                        <tr key={issue.code}>
                          <td>{issue.name}</td>
                          <td>
                            <span
                              className={`tag ${issue.count ? (issue.severity === "error" ? "statistics-error-tag" : "statistics-warning-tag") : "statistics-passed-tag"}`}
                            >
                              {issue.count
                                ? issue.severity === "warning"
                                  ? "待核查"
                                  : "异常"
                                : "未发现"}
                            </span>
                          </td>
                          <td>{issue.count}</td>
                          <td>
                            <button
                              className="statistics-link"
                              aria-label={`查看${issue.name}的图片`}
                              onClick={() =>
                                showImages({ kind: "issue", code: issue.code })
                              }
                            >
                              {issue.image_count} 张 ↗
                            </button>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
            <details className="statistics-check-notes">
              <summary>检查说明</summary>
              <p>
                同一条标注可能触发多项检查。疑似重复、极小和细长框仍计入有效框，不自动修改或删除。图片文件检查仅检查已入库原图是否存在，尺寸取自导入记录；未进行图片解码、相似图或漏标检查。训练
                / 验证 / 测试划分在导出时生成，不属于当前原始数据集统计范围。
              </p>
            </details>
          </section>
          <section className="panel statistics-images" ref={imagesPanel}>
            <div className="panel-title">
              <div className="statistics-section-heading">
                <h2>图片明细</h2>
                <span className="statistics-count-badge">
                  {images.length.toLocaleString()} 张
                </span>
              </div>
            </div>
            <div className="statistics-image-toolbar">
              <span className="tag">{selectionLabel}</span>
              {selection.kind !== "all" && (
                <button onClick={() => showImages({ kind: "all" })}>
                  清除筛选
                </button>
              )}
              <input
                aria-label="搜索统计图片"
                placeholder="搜索文件名或图片 ID"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(0);
                }}
              />
            </div>
            <div className="statistics-image-grid">
              {images
                .slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
                .map((sample) => (
                  <article key={sample.id} className="statistics-image-card">
                    <div className="statistics-thumbnail">
                      {sample.image_exists && sample.reviewable ? (
                        <img
                          loading="lazy"
                          src={`/api/datasets/${dataset.id}/samples/${sample.id}/thumbnail`}
                          alt={sample.filename}
                          onError={(e) => {
                            e.currentTarget.style.display = "none";
                            e.currentTarget.parentElement?.setAttribute(
                              "data-preview-error",
                              "预览暂不可用",
                            );
                          }}
                        />
                      ) : (
                        <span>
                          {sample.image_exists
                            ? "标注异常，预览不可用"
                            : "原图缺失"}
                        </span>
                      )}
                    </div>
                    <div className="statistics-image-info">
                      <strong title={sample.filename}>{sample.filename}</strong>
                      <small title={sample.id}>{sample.id}</small>
                      <div className="statistics-image-meta">
                        <span
                          className={`statistics-status-pill status-${sample.status}`}
                        >
                          {statusNames[sample.status]}
                        </span>
                        <span>
                          {sample.width} × {sample.height}
                          {detection ? ` · ${sample.box_count} 框` : ""}
                        </span>
                      </div>
                      {sample.class_ids.length > 0 && (
                        <small>
                          {sample.class_ids
                            .map(
                              (id) =>
                                `${id} ${data.classes.find((c) => c.id === id)?.name ?? ""}`,
                            )
                            .join("、")}
                        </small>
                      )}
                      {Object.keys(sample.issues).length > 0 && (
                        <span className="statistics-image-issues">
                          {data.quality
                            .filter((q) => sample.issues[q.code])
                            .map((q) => q.name)
                            .join("；")}
                        </span>
                      )}
                      <button
                        disabled={!sample.reviewable}
                        onClick={() => onReview(sample.id)}
                      >
                        进入标注审核 ↗
                      </button>
                      {!sample.reviewable && (
                        <small>请先根据异常清单修复文件或标注。</small>
                      )}
                    </div>
                  </article>
                ))}
            </div>
            {!images.length && (
              <div className="empty">没有符合条件的图片。</div>
            )}
            {images.length > 0 && (
              <div className="statistics-pagination">
                <button
                  disabled={page === 0}
                  onClick={() => setPage((value) => value - 1)}
                >
                  上一页
                </button>
                <span>
                  第 {page + 1} /{" "}
                  {Math.max(1, Math.ceil(images.length / PAGE_SIZE))} 页 · 每页{" "}
                  {PAGE_SIZE} 张
                </span>
                <button
                  disabled={(page + 1) * PAGE_SIZE >= images.length}
                  onClick={() => setPage((value) => value + 1)}
                >
                  下一页
                </button>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
