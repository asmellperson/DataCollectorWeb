import type {
  DatasetStatistics,
  ImageSelection,
  StatisticsSample,
} from "../types/statistics";

export const statusNames = {
  pending: "待审核",
  approved: "已确认",
  rejected: "已拒绝",
};
export const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

export function selectStatisticsImages(
  samples: StatisticsSample[],
  selection: ImageSelection,
  query = "",
) {
  const search = query.trim().toLocaleLowerCase();
  return samples.filter((sample) => {
    if (
      search &&
      !`${sample.id} ${sample.filename} ${sample.image}`
        .toLocaleLowerCase()
        .includes(search)
    )
      return false;
    switch (selection.kind) {
      case "class":
        return sample.class_ids.includes(selection.id);
      case "issue":
        return (sample.issues[selection.code] ?? 0) > 0;
      case "issues":
        return Object.keys(sample.issues).length > 0;
      case "negative":
        return sample.empty && sample.status === "approved";
      case "pending_empty":
        return sample.empty && sample.status === "pending";
      case "bucket": {
        if (!sample.annotation_ok) return false;
        const count = sample.box_count;
        const name =
          count === 0
            ? "0"
            : count === 1
              ? "1"
              : count <= 5
                ? "2–5"
                : count <= 10
                  ? "6–10"
                  : ">10";
        return name === selection.name;
      }
      default:
        return true;
    }
  });
}

export function toCsv(rows: (string | number)[][]) {
  // Quote all cells and keep spreadsheet applications from executing filenames
  // or user-supplied category names as formulas.
  const cell = (value: string | number) => {
    const text =
      typeof value === "string" && /^[\s]*[=+@-]/.test(value)
        ? `'${value}`
        : String(value);
    return `"${text.replace(/"/g, '""')}"`;
  };
  return "\uFEFF" + rows.map((row) => row.map(cell).join(",")).join("\r\n");
}

export function statisticsCsv(
  data: DatasetStatistics,
  kind: "classes" | "issues",
) {
  const rows: (string | number)[][] = [
    ["数据集", data.dataset_name],
    ["统计时间", data.generated_at],
    [
      "审核范围",
      data.scope.status
        ? statusNames[data.scope.status]
        : "全部状态（包含已拒绝）",
    ],
    ["范围内图片数", data.overview.images],
    ["有效框数", data.overview.boxes],
    ["异常标注记录数", data.overview.invalid_records],
    [],
  ];
  if (kind === "classes") {
    if (data.task_type === "detection") {
      rows.push([
        "类别 ID",
        "类别名称",
        "有效框数",
        "包含图片数",
        "框占比",
        "图片覆盖率",
        "含该类图片平均框数",
        "单图最多框数",
        "已确认图片数",
        "待审核图片数",
        "已拒绝图片数",
        "小目标框数（面积<1%）",
        "中目标框数（1%≤面积<10%）",
        "大目标框数（面积≥10%）",
      ]);
      for (const c of data.classes)
        rows.push([
          c.id,
          c.name,
          c.box_count,
          c.image_count,
          percent(c.box_share),
          percent(c.image_coverage),
          c.average_boxes.toFixed(2),
          c.max_boxes,
          c.approved,
          c.pending,
          c.rejected,
          c.small,
          c.medium,
          c.large,
        ]);
    } else {
      rows.push([
        "类别 ID",
        "类别名称",
        "包含图片数",
        "图片覆盖率",
        "已确认图片数",
        "待审核图片数",
        "已拒绝图片数",
      ]);
      for (const c of data.classes)
        rows.push([
          c.id,
          c.name,
          c.image_count,
          percent(c.image_coverage),
          c.approved,
          c.pending,
          c.rejected,
        ]);
    }
  } else {
    rows.push([
      "图片 ID",
      "来源文件名",
      "图片路径",
      "审核状态",
      "问题",
      "问题记录数",
      "级别",
    ]);
    const issues = new Map(data.quality.map((issue) => [issue.code, issue]));
    for (const sample of data.samples) {
      for (const [code, count] of Object.entries(sample.issues)) {
        const issue = issues.get(code);
        rows.push([
          sample.id,
          sample.filename,
          sample.image,
          statusNames[sample.status],
          issue?.name ?? code,
          count,
          issue?.severity === "warning" ? "待核查" : "异常",
        ]);
      }
    }
  }
  return toCsv(rows);
}

export function downloadStatistics(
  data: DatasetStatistics,
  kind: "classes" | "issues",
) {
  const url = URL.createObjectURL(
    new Blob([statisticsCsv(data, kind)], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `${data.dataset_name.replace(/[\\/:*?"<>|]/g, "_")}_${kind === "classes" ? "类别统计" : "异常清单"}_${data.generated_at.slice(0, 10)}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
