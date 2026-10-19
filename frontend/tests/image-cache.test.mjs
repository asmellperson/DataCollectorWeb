import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

function setup() {
  const requested = [];
  globalThis.window = {
    Image: class {
      complete = false;
      naturalWidth = 0;
      set src(url) {
        this.url = url;
        requested.push(this);
      }
      succeed() {
        this.complete = true;
        this.naturalWidth = 1920;
        this.onload();
      }
      fail() {
        this.onerror();
      }
    },
  };
  const { outputText } = ts.transpileModule(
    readFileSync(
      new URL("../src/services/imageCache.ts", import.meta.url),
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
  return { ...service, requested };
}

test("prefetch and foreground share the same in-flight image and decoded object", async () => {
  const cache = setup();
  const pending = cache.loadImage("/one");
  const foreground = cache.loadImage("/one");
  assert.equal(pending, foreground);
  assert.equal(cache.requested.length, 1);
  assert.equal(cache.cachedImage("/one"), undefined);
  cache.requested[0].succeed();
  assert.equal(await foreground, cache.cachedImage("/one"));
  await cache.loadImage("/one");
  assert.equal(cache.requested.length, 1);
});

test("failed preloads can be retried", async () => {
  const cache = setup();
  const pending = cache.loadImage("/one");
  const failure = assert.rejects(pending);
  cache.requested[0].fail();
  await failure;
  const retry = cache.loadImage("/one");
  assert.equal(cache.requested.length, 2);
  cache.requested[1].succeed();
  await retry;
});

test("image memory stays bounded and keeps recently revisited images", async () => {
  const cache = setup();
  for (let i = 0; i < 6; i++) {
    const pending = cache.loadImage("/" + i);
    cache.requested.at(-1).succeed();
    await pending;
  }
  await cache.loadImage("/0");
  const next = cache.loadImage("/6");
  cache.requested.at(-1).succeed();
  await next;
  assert.ok(cache.cachedImage("/0"));
  assert.equal(cache.cachedImage("/1"), undefined);
  assert.ok(cache.cachedImage("/6"));
});
