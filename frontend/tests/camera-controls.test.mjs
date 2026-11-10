import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

function load(name) {
  const source = readFileSync(new URL(`../src/services/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const exports = {};
  new Function("exports", outputText)(exports);
  return exports;
}
const view = load("cameraView");
const { createLensControl } = load("lensControl");
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

test("clicking within an enlarged view selects the matching original-image point", () => {
  const current = { zoom: 2, center_x: .75, center_y: .25 };
  assert.deepEqual(view.viewBounds(100, 80, current), { x: 50, y: 0, width: 50, height: 40 });
  assert.deepEqual(view.selectViewCenter(.2, .8, 100, 80, current), { zoom: 2, center_x: .6, center_y: .4 });
});

test("corners clamp the crop without black borders, including odd pixel dimensions", () => {
  assert.deepEqual(view.viewBounds(101, 79, { zoom: 2, center_x: 1, center_y: 0 }), { x: 50, y: 0, width: 51, height: 40 });
  assert.deepEqual(view.viewBounds(101, 79, { zoom: 1, center_x: 0, center_y: 1 }), { x: 0, y: 0, width: 101, height: 79 });
});

test("holding renews the move and releasing sends Stop without further heartbeats", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const sent = [];
  const control = createLensControl(async speed => { sent.push(speed); }, assert.fail);
  control.setSpeed(.6); await flush();
  t.mock.timers.tick(300); await flush();
  assert.deepEqual(sent, [.6, .6]);
  control.stop(); await flush();
  t.mock.timers.tick(3000); await flush();
  assert.deepEqual(sent, [.6, .6, 0]);
  control.dispose();
});

test("release during a slow move discards all intermediate speeds and finishes with Stop", async () => {
  const sent = [];
  let finish;
  const control = createLensControl(speed => {
    sent.push(speed);
    return speed ? new Promise(resolve => { finish = resolve; }) : Promise.resolve();
  }, assert.fail);
  control.setSpeed(.2);
  control.setSpeed(.8);
  control.setSpeed(-.7);
  control.stop();
  assert.deepEqual(sent, [.2]);
  finish(); await flush();
  assert.deepEqual(sent, [.2, 0]);
  control.dispose();
});

test("unmount during a slow move still sends Stop after the last in-flight command", async () => {
  const sent = [];
  let finish;
  const control = createLensControl(speed => {
    sent.push(speed);
    return speed ? new Promise(resolve => { finish = resolve; }) : Promise.resolve();
  }, assert.fail);
  control.setSpeed(-.3);
  control.dispose();
  finish(); await flush();
  control.setSpeed(.9);
  assert.deepEqual(sent, [-.3, 0]);
});

test("a lost move response attempts Stop, reports the error and cancels renewal", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const sent = [], errors = [];
  const control = createLensControl(async speed => {
    sent.push(speed);
    if (speed) throw Error("network down");
  }, error => errors.push(error.message));
  control.setSpeed(.5); await flush();
  t.mock.timers.tick(3000); await flush();
  assert.deepEqual(sent, [.5, 0]);
  assert.deepEqual(errors, ["network down"]);
  control.dispose();
});
