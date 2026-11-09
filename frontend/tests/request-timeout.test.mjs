import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

function load(path, dependencies = {}) {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  new Function("exports", "require", outputText)(exports, (name) => {
    assert.ok(name in dependencies, name);
    return dependencies[name];
  });
  return exports;
}

const timeout = load("services/requestTimeout.ts");
const untilAborted = signal => new Promise((_, reject) => {
  if (signal.aborted) reject(signal.reason);
  else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
});
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test("a stalled response body times out with an actionable message", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let requestSignal;
  const request = timeout.withRequestTimeout(async signal => {
    requestSignal = signal;
    // Headers arrived; reading the body still occupies the connection.
    await Promise.resolve();
    return untilAborted(signal);
  }, { timeoutMs: 5000, message: "预览请求超时" });
  const rejected = assert.rejects(request, /预览请求超时/);
  await flush();
  t.mock.timers.tick(5000);
  await rejected;
  assert.equal(requestSignal.aborted, true);
});

test("a completed request clears its deadline without aborting its parent", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const parent = new AbortController();
  let requestSignal;
  const result = await timeout.withRequestTimeout(async signal => {
    requestSignal = signal;
    return { active: false };
  }, { signal: parent.signal, timeoutMs: 10000, message: "同步超时" });
  assert.deepEqual(result, { active: false });
  t.mock.timers.tick(10000);
  parent.abort();
  assert.equal(requestSignal.aborted, false);
});

test("navigation cancels requests immediately and preserves the cancellation reason", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const parent = new AbortController();
  const reason = new DOMException("页面已切换", "AbortError");
  const request = timeout.withRequestTimeout(untilAborted, {
    signal: parent.signal, timeoutMs: 5000, message: "预览超时",
  });
  const rejected = assert.rejects(request, error => error === reason);
  parent.abort(reason);
  await rejected;
  let called = false;
  await assert.rejects(timeout.withRequestTimeout(async () => { called = true; }, {
    signal: parent.signal, timeoutMs: 5000, message: "预览超时",
  }), error => error === reason);
  assert.equal(called, false);
});

test("stalled camera status and frame requests release connections and retry successfully", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const previousDocument = globalThis.document;
  globalThis.document = { hidden: false };
  t.after(() => {
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  });
  const states = [];
  let cleanup;
  let statusAttempts = 0, frameAttempts = 0;
  const signals = [];
  const healthy = { connected: true, fps: 25, width: 640, height: 480, error: "" };
  t.mock.method(URL, "createObjectURL", () => "blob:recovered");
  const revoke = t.mock.method(URL, "revokeObjectURL", () => {});
  t.mock.method(globalThis, "fetch", async (_path, { signal }) => {
    frameAttempts++;
    if (frameAttempts === 1) {
      signals.push(signal);
      return { ok: true, status: 200, blob: () => untilAborted(signal) };
    }
    return { ok: true, status: 200, blob: async () => new Blob(["preview"]) };
  });
  const hook = load("hooks/useLiveCamera.ts", {
    react: {
      useEffect: effect => { cleanup = effect(); },
      useState: initial => {
        const index = states.push(initial) - 1;
        return [initial, value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
      },
    },
    "../services/requestTimeout": timeout,
    "../services/api": {
      api: (_path, { signal }) => {
        statusAttempts++;
        if (statusAttempts > 1) return Promise.resolve(healthy);
        signals.push(signal);
        return untilAborted(signal);
      },
      errorText: error => error.message,
    },
  });
  hook.useLiveCamera("camera", false, 150);
  t.after(() => cleanup?.());
  await flush();
  assert.equal(signals.length, 2);
  t.mock.timers.tick(5000);
  await flush();
  assert.ok(signals.every(signal => signal.aborted));
  assert.match(states[1].error, /超时/);
  t.mock.timers.tick(1000);
  await flush();
  assert.equal(statusAttempts, 2);
  assert.equal(frameAttempts, 2);
  assert.deepEqual(states[1], healthy);
  assert.equal(states[0], "blob:recovered");
  cleanup();
  cleanup = undefined;
  const attempts = [statusAttempts, frameAttempts];
  t.mock.timers.tick(10000);
  await flush();
  assert.deepEqual([statusAttempts, frameAttempts], attempts);
  assert.equal(revoke.mock.calls.length, 1);
});
