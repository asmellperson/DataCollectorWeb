import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

function setup(api) {
  const source = readFileSync(
    new URL("../src/services/annotationSave.ts", import.meta.url),
    "utf8",
  );
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  });
  const exports = {};
  new Function("exports", "require", outputText)(exports, (name) => {
    assert.equal(name, "./api");
    return { api };
  });
  return exports.saveAnnotation;
}

const box = {
  id: "manual-1",
  class_id: 0,
  class_name: "person",
  bbox: [1, 2, 30, 40],
  confidence: null,
  source: "manual",
};
const sample = {
  id: "image-1",
  revision: 7,
  objects: [],
  classification: null,
};

test("adding a box waits for the server and submits the original revision", async () => {
  let finish;
  const draft = { objects: [box], classification: null };
  const saved = { ...sample, ...draft, revision: 8 };
  const save = setup((path, options) => {
    assert.equal(path, "/datasets/data/samples/image-1/annotation");
    assert.equal(options.method, "PUT");
    assert.deepEqual(JSON.parse(options.body), { revision: 7, ...draft });
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  let completed = false;
  const pending = save("/datasets/data", sample, draft).then((result) => {
    completed = true;
    return result;
  });
  await Promise.resolve();
  assert.equal(completed, false);
  finish(saved);
  assert.deepEqual(await pending, saved);
});

for (const [name, original, draft] of [
  ["add", sample, { objects: [box], classification: null }],
  [
    "delete last box",
    { ...sample, objects: [box] },
    { objects: [], classification: null },
  ],
]) {
  test(`verifies a committed ${name} after the response is lost`, async () => {
    const saved = { ...original, ...draft, revision: 8 };
    const requests = [];
    const save = setup(async (path, options) => {
      requests.push({ path, options });
      if (options.method === "PUT") throw new Error("connection lost");
      assert.equal(options.cache, "no-store");
      return saved;
    });
    assert.deepEqual(await save("/datasets/data", original, draft), saved);
    assert.equal(requests.length, 2);
    assert.equal(requests[1].path, "/datasets/data/samples/image-1");
  });
}

test("a failed request stays failed if the server still has the old annotation", async () => {
  const failure = new Error("保存失败");
  const draft = { objects: [box], classification: null };
  let attempts = 0;
  const save = setup(async (_path, options) => {
    if (options.method === "PUT") {
      attempts++;
      if (attempts === 1) throw failure;
      return { ...sample, ...draft, revision: 8 };
    }
    return sample;
  });
  await assert.rejects(
    save("/datasets/data", sample, draft),
    (e) => e === failure,
  );
  assert.equal(
    (await save("/datasets/data", sample, draft)).objects[0].id,
    box.id,
  );
});

test("a conflicting newer edit is never silently overwritten or marked saved", async () => {
  const conflict = new Error("样本已被其他操作更新");
  const draft = { objects: [box], classification: null };
  let writes = 0;
  const save = setup(async (_path, options) => {
    if (options.method === "PUT") {
      writes++;
      throw conflict;
    }
    return {
      ...sample,
      revision: 9,
      objects: [{ ...box, bbox: [5, 5, 10, 10] }],
    };
  });
  await assert.rejects(
    save("/datasets/data", sample, draft),
    (e) => e === conflict,
  );
  assert.equal(writes, 1);
  assert.deepEqual(draft.objects[0].bbox, [1, 2, 30, 40]);
});

test("read-back failure preserves the original save error and the draft", async () => {
  const original = new Error("save disconnected");
  const draft = { objects: [box], classification: null };
  const save = setup(async (_path, options) => {
    throw options.method === "PUT" ? original : new Error("read disconnected");
  });
  await assert.rejects(
    save("/datasets/data", sample, draft),
    (e) => e === original,
  );
  assert.deepEqual(draft.objects, [box]);
});

test("an unchanged revision does not count as a verified write", async () => {
  const original = new Error("write rejected");
  const save = setup(async (_path, options) => {
    if (options.method === "PUT") throw original;
    return sample;
  });
  await assert.rejects(
    save("/datasets/data", sample, sample),
    (e) => e === original,
  );
});
