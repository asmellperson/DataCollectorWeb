import type { ClassItem } from "../types";

export type LabelImportData = {
  classes: ClassItem[];
  mapping: Record<number, number>;
  labels: File[];
};

// Directory inputs include the selected root. Pair relative paths below each root.
export function relativeImportName(file: Pick<File, "name" | "webkitRelativePath">) {
  const path = file.webkitRelativePath;
  return path ? path.slice(path.indexOf("/") + 1) : file.name;
}

export function labelKey(name: string) {
  return name.replace(/\.[^/.]+$/, ".txt");
}

export function indexLabels(files: File[]) {
  const index = new Map<string, File>();
  for (const file of files) {
    const key = labelKey(relativeImportName(file));
    if (index.has(key)) throw new Error(`标签名称冲突：${key}`);
    index.set(key, file);
  }
  return index;
}

export function matchClasses(source: ClassItem[], target: ClassItem[]) {
  const result: Record<number, number> = {};
  for (const item of source) {
    const match = target.find((candidate) => candidate.name === item.name);
    if (match) result[item.id] = match.id;
  }
  return result;
}

export function appendImportFiles(form: FormData, files: File[], data?: LabelImportData) {
  const labels = data ? indexLabels(data.labels) : null;
  for (const file of files) {
    const name = data ? relativeImportName(file) : file.webkitRelativePath || file.name;
    form.append("files", file, name);
    const label = labels?.get(labelKey(name));
    if (label) form.append("labels", label, relativeImportName(label));
  }
}
