import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/services/face5pt.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const exports = {};
new Function("exports", outputText)(exports);
const { moveFaceBox, setFacePoint } = exports;
const face = {
  id: "face-1", class_id: 0, class_name: "face", bbox: [10, 10, 50, 50],
  keypoints: [[20, 20], [40, 20], null, [25, 40], [35, 40]], source: "scrfd", confidence: .95,
};

test("dragging a face translates landmarks while retaining missing points", () => {
  const moved = moveFaceBox(face, [20, 30, 60, 70], 100, 100);
  assert.deepEqual(moved.keypoints, [[30, 40], [50, 40], null, [35, 60], [45, 60]]);
  assert.equal(moved.source, "manual");
  assert.equal(moved.confidence, null);
  assert.deepEqual(face.keypoints[0], [20, 20]);
});

test("resizing a face scales landmarks relative to its old origin", () => {
  const moved = moveFaceBox(face, [0, 0, 80, 20], 100, 100);
  assert.deepEqual(moved.keypoints, [[20, 5], [60, 5], null, [30, 15], [50, 15]]);
});

test("landmarks outside a resized face remain in image bounds", () => {
  const moved = moveFaceBox({...face, keypoints: [[0, 0], [100, 100], null, null, null]}, [0, 0, 100, 100], 100, 100);
  assert.deepEqual(moved.keypoints, [[0, 0], [100, 100], null, null, null]);
});

test("setting and deleting points initializes five slots without inventing labels", () => {
  const original = {...face, keypoints: undefined};
  const placed = setFacePoint(original, 2, [15, 25]);
  assert.deepEqual(placed.keypoints, [null, null, [15, 25], null, null]);
  assert.equal(placed.source, "manual");
  assert.deepEqual(setFacePoint(placed, 2, null).keypoints, [null, null, null, null, null]);
  assert.equal(original.keypoints, undefined);
});

test("legacy bbox edits do not introduce keypoints", () => {
  const original = {...face};
  delete original.keypoints;
  const moved = moveFaceBox(original, [0, 0, 40, 40], 100, 100);
  assert.equal("keypoints" in moved, false);
});
