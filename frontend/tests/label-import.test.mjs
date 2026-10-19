import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const { outputText } = ts.transpileModule(
  readFileSync(new URL("../src/services/labelImport.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
);
const service = {};
new Function("exports", outputText)(service);
function file(path, text = "") {
  const value = new File([text], path.split("/").at(-1));
  Object.defineProperty(value, "webkitRelativePath", { value: path });
  return value;
}

test("relative matching preserves subdirectories and strips only selected root", () => {
  assert.equal(service.relativeImportName(file("images/train/a.jpg")), "train/a.jpg");
  assert.equal(service.relativeImportName({ name: "a.jpg", webkitRelativePath: "" }), "a.jpg");
  assert.equal(service.labelKey("train.v2/a.JPG"), "train.v2/a.txt");
  const index = service.indexLabels([file("labels/train/a.txt"), file("labels/val/a.TXT")]);
  assert.equal(index.size, 2);
  assert.ok(index.has("val/a.txt"));
  assert.throws(() => service.indexLabels([file("labels/a.txt"), file("labels/a.TXT")]), /冲突/);
});

test("classes match by name even if IDs differ, with unresolved names left for user", () => {
  assert.deepEqual(service.matchClasses(
    [{ id: 4, name: "car" }, { id: 1, name: "person" }, { id: 2, name: "new" }],
    [{ id: 4, name: "person" }, { id: 9, name: "car" }],
  ), { 4: 9, 1: 4 });
});

test("each batch sends its matched TXT files, retaining empty labels and missing images", async () => {
  const labels = [file("labels/train/a.txt", "0 .5 .5 .2 .2"), file("labels/val/a.txt"), file("labels/unused.txt")];
  const form = new FormData();
  service.appendImportFiles(form, [file("images/val/a.jpg"), file("images/missing.jpg")], { labels });
  assert.deepEqual(form.getAll("files").map((f) => f.name), ["val/a.jpg", "missing.jpg"]);
  assert.deepEqual(form.getAll("labels").map((f) => f.name), ["val/a.txt"]);
  assert.equal(await form.get("labels").text(), "");
  const plain = new FormData();
  service.appendImportFiles(plain, [file("images/train/a.jpg")]);
  assert.equal(plain.get("files").name, "images/train/a.jpg");
  assert.equal(plain.getAll("labels").length, 0);
});
