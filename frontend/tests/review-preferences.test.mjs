import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { beforeEach, test } from "node:test";
import ts from "typescript";

function loadService(name, dependencies = {}) {
  const source = readFileSync(
    new URL(`../src/services/${name}.ts`, import.meta.url),
    "utf8",
  );
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  });
  const result = {};
  new Function("exports", "require", outputText)(result, (path) => {
    assert.ok(path in dependencies, `Unexpected dependency: ${path}`);
    return dependencies[path];
  });
  return result;
}
const classMapping = loadService("classMapping");
const preferences = loadService("preferences", {
  "./classMapping": classMapping,
});
const dataset = {
  id: "cars",
  classes: [
    { id: 4, name: "person" },
    { id: 9, name: "play_phone" },
    { id: 12, name: "car" },
  ],
};

beforeEach(() => {
  const values = new Map();
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
});

test("remembers image 188 by ID and position independently for each dataset", () => {
  preferences.writePreference("cars", "review", {
    sampleId: "image-188",
    index: 187,
    filter: "pending",
    classId: 9,
  });
  preferences.writePreference("people", "review", {
    sampleId: "person-25",
    index: 24,
    filter: "",
    classId: 4,
  });
  assert.deepEqual(preferences.readReviewPreferences(dataset), {
    sampleId: "image-188",
    index: 187,
    filter: "pending",
    classId: 9,
  });
  assert.equal(
    preferences.readReviewPreferences({ ...dataset, id: "people" }).sampleId,
    "person-25",
  );
});

test("restores model options and mapping ID zero without mixing them with review position", () => {
  preferences.writePreference("cars", "auto-label", {
    provider: "custom_yolo",
    path: "/models/coco.pt",
    confidence: 0.41,
    overwrite: true,
    labelClasses: [4, 9],
    classMapping: { 4: 0, 9: 67 },
  });
  assert.deepEqual(
    preferences.readAutoLabelPreferences("cars", dataset.classes),
    {
      provider: "custom_yolo",
      path: "/models/coco.pt",
      confidence: 0.41,
      overwrite: true,
      labelClasses: [4, 9],
      classMapping: { 4: 0, 9: 67 },
    },
  );
  assert.equal(preferences.readReviewPreferences(dataset).sampleId, null);
});

test("removed categories are not restored or silently replaced with all categories", () => {
  preferences.writePreference("cars", "review", { classId: 999 });
  preferences.writePreference("cars", "auto-label", {
    labelClasses: [999],
    classMapping: { 999: 67, 4: 0 },
  });
  assert.equal(preferences.readReviewPreferences(dataset).classId, 4);
  const options = preferences.readAutoLabelPreferences("cars", dataset.classes);
  assert.deepEqual(options.labelClasses, []);
  assert.deepEqual(options.classMapping, { 4: 0 });
});

test("corrupt and invalid saved values fall back to usable defaults", () => {
  localStorage.setItem(
    preferences.preferenceKey("cars", "review"),
    "broken JSON",
  );
  assert.equal(preferences.readReviewPreferences(dataset).index, 0);
  preferences.writePreference("cars", "review", { index: -188, filter: "bad" });
  assert.equal(preferences.readReviewPreferences(dataset).filter, "");
  preferences.writePreference("cars", "auto-label", {
    provider: "bad",
    confidence: 10,
    classMapping: { 4: true, 9: -1, 12: 1.5 },
  });
  const options = preferences.readAutoLabelPreferences("cars", dataset.classes);
  assert.equal(options.provider, "yolo_world");
  assert.equal(options.confidence, 0.25);
  assert.deepEqual(options.classMapping, {});
});

test("unavailable browser storage does not interrupt annotation", () => {
  globalThis.localStorage = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("full");
    },
  };
  assert.equal(preferences.readReviewPreferences(dataset).index, 0);
  assert.equal(
    preferences.writePreference("cars", "review", { index: 187 }),
    false,
  );
});

test("workspace remembers the review page and dataset across refreshes", () => {
  preferences.writePreference("", "workspace", {
    page: "review",
    selected: "cars",
  });
  assert.deepEqual(preferences.readWorkspacePreferences(), {
    page: "review",
    selected: "cars",
  });
  preferences.writePreference("", "workspace", { page: "unknown" });
  assert.equal(preferences.readWorkspacePreferences().page, "datasets");
});

test("multiple detector IDs normalize without losing zero or legacy scalar mappings", () => {
  const { serializeClassMapping: serialize } = classMapping;
  assert.deepEqual(serialize({ 4: "0", 9: 67, 12: "2,3，2 7" }), {
    4: 0,
    9: 67,
    12: [2, 3, 7],
  });
  assert.deepEqual(serialize({ 4: " ", 12: [2, 3, 2] }), { 12: [2, 3] });
  assert.deepEqual(serialize({ 12: "2," }), { 12: 2 });
  assert.deepEqual(serialize({ 4: "invalid", 12: "2,3" }, [12]), {
    12: [2, 3],
  });
  for (const value of [
    "2,abc",
    "2,-3",
    "2,1.5",
    "10000",
    ",",
    [],
    [2, true],
    null,
  ]) {
    assert.throws(() => serialize({ 12: value }), /检测类别 ID/);
  }
});

test("multiple source IDs survive saving and reopening annotation options", () => {
  for (const value of [[2, 3, 2], "2,3"]) {
    preferences.writePreference("cars", "auto-label", {
      labelClasses: [4, 9, 12],
      classMapping: { 4: 0, 9: 67, 12: value, 999: [2, 3] },
    });
    const options = preferences.readAutoLabelPreferences(
      "cars",
      dataset.classes,
    );
    assert.deepEqual(options.classMapping, { 4: 0, 9: 67, 12: [2, 3] });
    assert.deepEqual(classMapping.serializeClassMapping(options.classMapping), {
      4: 0,
      9: 67,
      12: [2, 3],
    });
  }
});
