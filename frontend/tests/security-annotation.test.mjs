import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

function service(api = async () => {}) {
  const source = readFileSync(new URL("../src/services/securityAnnotation.ts", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const result = {};
  new Function("exports", "require", "crypto", outputText)(result, () => ({ api }), webcrypto);
  return result;
}

const base = {
  id: "round", subject_id: "subject", inspector_id: "staff", start_sec: 0, end_sec: 2,
  inspection: "present", quality: "usable", review_status: "approved", boundary_uncertain: false,
  coverage: { left_hand: "scanned" }, notes: "",
  events: [{ id: "event", action: "扫描", region: "left_hand", actor_id: "staff", start_sec: 0.8,
    end_sec: 1.2, observation: "observed", boundary_uncertain: false, evidence_frames: [], notes: "" }],
};

test("split never assigns the first subject to a new round or silently confirms crossing actions", () => {
  const [first, second] = service().splitRound(base, 1);
  assert.equal(second.subject_id, null);
  assert.equal(first.subject_id, "subject");
  assert.equal(first.end_sec, 1);
  assert.equal(second.start_sec, 1);
  assert.equal(first.events[0].observation, "uncertain");
  assert.equal(second.events[0].observation, "uncertain");
  assert.notEqual(first.events[0].id, second.events[0].id);
  assert.equal(first.review_status, "pending");
  assert.deepEqual(first.coverage, {});
  assert.equal(base.end_sec, 2);
  assert.equal(base.events[0].end_sec, 1.2);
});

test("merge rejects different known subjects and clears inherited approval", () => {
  const { mergeRounds } = service();
  assert.throws(() => mergeRounds(base, { ...base, id: "other", subject_id: "another" }), /不同受检人/);
  const merged = mergeRounds(base, { ...base, id: "other", start_sec: 2, end_sec: 3, events: [] });
  assert.equal(merged.end_sec, 3);
  assert.equal(merged.review_status, "pending");
  assert.equal(merged.inspection, "uncertain");
});

test("frame lookup follows variable source timestamps and exclusive end boundaries", () => {
  const { frameAtTime } = service();
  const times = [0, 0.04, 0.08, 0.72, 0.84];
  assert.equal(frameAtTime(times, 0.6), 2);
  assert.equal(frameAtTime(times, 0.72), 3);
  assert.equal(frameAtTime(times, 99), 4);
});

test("saving after a lost response reads back the committed content without adding review time twice", async () => {
  let writes = 0;
  const content = { persons: [], rounds: [base] };
  const annotation = { ...content, revision: 2, review_seconds: 7 };
  const { saveInspection } = service(async (_path, options) => {
    if (options.method === "PUT") { writes++; throw new Error("response lost"); }
    return { annotation };
  });
  assert.deepEqual(await saveInspection("record", { revision: 1 }, content, 7), annotation);
  assert.equal(writes, 1);
});

test("save conflicts preserve the current draft instead of treating another edit as success", async () => {
  const draft = { persons: [], rounds: [base] };
  const { saveInspection } = service(async (_path, options) => {
    if (options.method === "PUT") throw new Error("version conflict");
    return { annotation: { revision: 3, persons: [], rounds: [] } };
  });
  await assert.rejects(saveInspection("record", { revision: 1 }, draft, 2), /version conflict/);
  assert.equal(draft.rounds[0].id, "round");
});

test("a lost deletion response is verified against the server before clearing local state", async () => {
  const requests = [];
  const { deleteInspectionRecording } = service(async (path, options) => {
    requests.push({ path, options });
    if (options.method === "DELETE") throw new Error("response lost");
    return [{ id: "other" }];
  });
  await deleteInspectionRecording("record");
  assert.equal(requests[0].path, "/security-annotations/recordings/record");
  assert.equal(requests[1].path, "/security-annotations/recordings");
  assert.equal(requests[1].options.cache, "no-store");
});

test("a failed deletion remains failed while the recording exists on the server", async () => {
  const { deleteInspectionRecording } = service(async (_path, options) => {
    if (options.method === "DELETE") throw new Error("录像正在处理");
    return [{ id: "record" }];
  });
  await assert.rejects(deleteInspectionRecording("record"), /录像正在处理/);
});

test("an unavailable deletion read-back preserves the original error", async () => {
  const { deleteInspectionRecording } = service(async (_path, options) => {
    throw new Error(options.method === "DELETE" ? "delete failed" : "read-back failed");
  });
  await assert.rejects(deleteInspectionRecording("record"), /delete failed/);
});
