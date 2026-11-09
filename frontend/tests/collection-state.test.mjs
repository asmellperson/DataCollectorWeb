import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { beforeEach, test } from "node:test";
import ts from "typescript";

function load(name, dependencies = {}) {
  const source = readFileSync(new URL(`../src/services/${name}.ts`, import.meta.url), "utf8");
  const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}});
  const exports = {};
  new Function("exports", "require", outputText)(exports, (path) => {
    assert.ok(path in dependencies, path);
    return dependencies[path];
  });
  return exports;
}
const mapping = load("classMapping");
const preferences = load("preferences", {"./classMapping": mapping});
const state = load("collectionState", {"./preferences": preferences, "./classMapping": mapping});
const face = {id: "face", task_type: "detection", annotation_mode: "face_5pt", classes: [{id:0, name:"face"}]};
const regular = {id: "cars", task_type: "detection", classes: [{id:4, name:"person"}, {id:9, name:"car"}]};

beforeEach(() => {
  const values = new Map();
  globalThis.localStorage = {getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
});

test("face collection defaults to SCRFD for both trigger and five-point labels", () => {
  const saved = state.readCollectionPreferences(face);
  assert.equal(saved.detector, "scrfd");
  assert.equal(saved.autoLabel, "scrfd");
  assert.equal(saved.targets, "face");
  assert.equal(saved.hasSettings, false);
});

test("returning from review retains preview selection, ROI and detection settings", () => {
  preferences.writePreference(face.id, "collection", {
    source: "rtsp", selectedCameras: ["camera-2"], mode: "detect_raw", interval: 1.3,
    rois: {"camera-2": [2, 3, 100, 200]}, autoLabel: "scrfd", detector: "scrfd",
    confidence: .3, similarity: .98, targets: "face", videoPath: "/video/test.mp4", sampling: "seconds", every: .5,
  });
  const saved = state.readCollectionPreferences(face);
  assert.deepEqual(saved.selectedCameras, ["camera-2"]);
  assert.equal(saved.hasSelection, true);
  assert.equal(saved.hasSettings, true);
  assert.equal(saved.mode, "detect_raw");
  assert.equal(saved.interval, 1.3);
  assert.equal(saved.confidence, .3);
  assert.equal(saved.similarity, .98);
  assert.deepEqual(saved.rois, {"camera-2": [2, 3, 100, 200]});
  assert.equal(saved.every, .5);
});

test("hiding all previews is remembered independently of background capture", () => {
  preferences.writePreference(face.id, "collection", {selectedCameras: [], mode: "detect_raw"});
  const saved = state.readCollectionPreferences(face);
  assert.equal(saved.hasSelection, true);
  assert.deepEqual(saved.selectedCameras, []);
  assert.deepEqual(state.readCollectionPreferences(regular).selectedCameras, []);
  assert.equal(state.readCollectionPreferences(regular).hasSelection, false);
});

test("invalid cached preferences fall back without corrupting request coordinates", () => {
  preferences.writePreference(face.id, "collection", {interval: -1, confidence: Infinity, similarity: 2,
    mode:"bad", detector:"bad", autoLabel:"custom_yolo", every:1.5, rois:{a:[10,10,2,3], b:[1,2,3], c:[1,2,5,6]},
    selectedCameras:[null, 3, "", "a", "a"], labelClasses:[9], classMapping:{0:67}});
  const saved = state.readCollectionPreferences(face);
  assert.equal(saved.interval, 2);
  assert.equal(saved.confidence, .25);
  assert.equal(saved.similarity, .92);
  assert.equal(saved.detector, "scrfd");
  assert.equal(saved.autoLabel, "scrfd");
  assert.equal(saved.every, 1);
  assert.deepEqual(saved.rois, {c:[1,2,5,6]});
  assert.deepEqual(saved.selectedCameras, ["a"]);
  assert.equal(saved.labelClasses, null);
  assert.deepEqual(saved.classMapping, {});
});

test("legacy YOLO class mappings and selected IDs survive reopening collection", () => {
  preferences.writePreference(regular.id, "collection", {mode:"detect_raw", detector:"custom_yolo",
    triggerModelPath:"/model/coco.pt", autoLabel:"custom_yolo", modelPath:"/model/labels.pt",
    labelClasses:[9,999], classMapping:{4:0,9:"2,3",999:5}, targets:"2, 3"});
  const saved = state.readCollectionPreferences(regular);
  assert.equal(saved.detector, "custom_yolo");
  assert.equal(saved.triggerModelPath, "/model/coco.pt");
  assert.equal(saved.modelPath, "/model/labels.pt");
  assert.deepEqual(saved.labelClasses, [9]);
  assert.deepEqual(saved.classMapping, {4:[0], 9:[2,3]});
});

test("raw and classification datasets can use SCRFD triggers without enabling labels", () => {
  for (const task_type of ["raw", "classification"]) {
    const dataset = {...regular, id: task_type, task_type};
    preferences.writePreference(dataset.id, "collection", {detector:"scrfd", autoLabel:"scrfd"});
    const saved = state.readCollectionPreferences(dataset);
    assert.equal(saved.detector, "scrfd");
    assert.equal(saved.autoLabel, "off");
  }
});

test("storage restrictions do not prevent returning to collection", () => {
  globalThis.localStorage = {getItem: () => {throw Error("blocked");}};
  assert.equal(state.readCollectionPreferences(face).autoLabel, "scrfd");
});

test("start acknowledgements replace a camera session without losing other datasets", () => {
  const session = (camera_id, task_id, active = true) => ({camera_id, task_id, active, dataset_id: "a"});
  const previous = {...state.emptyCollectionStatus(), sessions:[session("a","old",false),session("b","other")], active:true, active_count:1};
  const started = [session("a","new")];
  const next = state.mergeStartedSessions(previous, started);
  assert.equal(next.active_count, 2);
  assert.deepEqual(next.sessions.map((s) => s.task_id), ["other", "new"]);
  assert.equal(previous.active_count, 1);
  state.rememberCollectionStatus("cached-cameras", next);
  assert.equal(state.cachedCollectionStatus("cached-cameras").active_count, 2);
  assert.equal(state.cachedCollectionStatus("different-dataset").active_count, 0);
});

test("loading, stopping and failure have distinct labels; legacy sessions remain readable", () => {
  assert.equal(state.collectionPhase({active:true, phase:"loading_model"}), "正在加载检测模型");
  assert.equal(state.collectionPhase({active:true, phase:"collecting", stopping:true}), "正在停止");
  assert.equal(state.collectionPhase({active:false, error:"failed"}), "采集失败");
  assert.equal(state.collectionPhase({active:false}), "已停止");
  assert.equal(state.collectionPhase({active:true}), "采集中");
});
