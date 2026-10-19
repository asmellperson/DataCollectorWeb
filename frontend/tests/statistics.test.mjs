import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const { outputText } = ts.transpileModule(
  readFileSync(
    new URL("../src/services/statistics.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  },
);
const service = {};
new Function("exports", outputText)(service);
const samples = [
  {
    id: "multi",
    filename: "路口.jpg",
    image: "images/multi.jpg",
    status: "approved",
    class_ids: [0, 9],
    box_count: 3,
    annotation_ok: true,
    empty: false,
    issues: {},
  },
  {
    id: "negative",
    filename: "empty.jpg",
    image: "images/empty.jpg",
    status: "approved",
    class_ids: [],
    box_count: 0,
    annotation_ok: true,
    empty: true,
    issues: {},
  },
  {
    id: "pending",
    filename: "pending.jpg",
    image: "images/pending.jpg",
    status: "pending",
    class_ids: [],
    box_count: 0,
    annotation_ok: true,
    empty: true,
    issues: {},
  },
  {
    id: "broken",
    filename: "broken.jpg",
    image: "images/broken.jpg",
    status: "approved",
    class_ids: [],
    box_count: 0,
    annotation_ok: false,
    empty: false,
    issues: { invalid_annotation: 1 },
  },
  {
    id: "invalid",
    filename: "invalid.jpg",
    image: "images/invalid.jpg",
    status: "approved",
    class_ids: [],
    box_count: 0,
    annotation_ok: true,
    empty: false,
    issues: { invalid_box: 2 },
  },
];
const ids = (selection, query = "") =>
  service.selectStatisticsImages(samples, selection, query).map((s) => s.id);

test("class zero and multiclass images drill down by presence, not box count", () => {
  assert.deepEqual(ids({ kind: "class", id: 0 }), ["multi"]);
  assert.deepEqual(ids({ kind: "class", id: 9 }), ["multi"]);
  assert.deepEqual(ids({ kind: "class", id: 12 }), []);
  assert.deepEqual(ids({ kind: "class", id: 0 }, "路口"), ["multi"]);
  assert.deepEqual(ids({ kind: "class", id: 0 }, "no match"), []);
});

test("negative samples exclude pending, corrupt and invalid-only annotations", () => {
  assert.deepEqual(ids({ kind: "negative" }), ["negative"]);
  assert.deepEqual(ids({ kind: "pending_empty" }), ["pending"]);
  assert.deepEqual(ids({ kind: "bucket", name: "0" }), [
    "negative",
    "pending",
    "invalid",
  ]);
  assert.deepEqual(ids({ kind: "bucket", name: "2–5" }), ["multi"]);
});

test("issue drill down deduplicates images and search supports IDs and filenames", () => {
  assert.deepEqual(ids({ kind: "issues" }), ["broken", "invalid"]);
  assert.deepEqual(ids({ kind: "issue", code: "invalid_box" }), ["invalid"]);
  assert.deepEqual(ids({ kind: "all" }, " MULTI "), ["multi"]);
});

test("CSV preserves commas, quotes, newlines and protects spreadsheet formula cells", () => {
  assert.equal(
    service.toCsv([[0, '车,"人"\n类别', "=SUM(1,2)", " +formula", 0.5]]),
    '\uFEFF"0","车,""人""\n类别","\'=SUM(1,2)","\' +formula","0.5"',
  );
});

test("export contains scope, ID zero, counts and issue rows for the same snapshot", () => {
  const data = {
    dataset_name: "示例",
    generated_at: "2026-10-13T10:00:00Z",
    task_type: "detection",
    scope: { status: "approved" },
    overview: { images: 5, boxes: 3, invalid_records: 2 },
    classes: [
      {
        id: 0,
        name: "人员",
        box_count: 3,
        image_count: 1,
        box_share: 1,
        image_coverage: 0.2,
        average_boxes: 3,
        max_boxes: 3,
        approved: 1,
        pending: 0,
        rejected: 0,
        small: 1,
        medium: 2,
        large: 0,
      },
    ],
    quality: [{ code: "invalid_box", name: "框坐标非法", severity: "error" }],
    samples,
  };
  const csv = service.statisticsCsv(data, "classes");
  assert.ok(csv.includes('"审核范围","已确认"'));
  assert.ok(csv.includes('"0","人员","3","1","100.0%","20.0%","3.00"'));
  const issues = service.statisticsCsv(data, "issues");
  assert.ok(
    issues.includes(
      '"invalid","invalid.jpg","images/invalid.jpg","已确认","框坐标非法","2","异常"',
    ),
  );
  const classification = service.statisticsCsv(
    { ...data, task_type: "classification" },
    "classes",
  );
  assert.ok(classification.includes('"类别 ID","类别名称","包含图片数"'));
  assert.ok(!classification.includes("单图最多框数"));
});
