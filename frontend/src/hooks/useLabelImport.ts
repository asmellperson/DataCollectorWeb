import { useRef, useState } from "react";
import type { ClassItem } from "../types";
import { errorText, post } from "../services/api";
import { indexLabels, matchClasses, relativeImportName, type LabelImportData } from "../services/labelImport";

type Report = {
  classes: ClassItem[];
  images: number;
  matched: number;
  missing: number;
  empty: number;
  unmatched: number;
  errors: string[];
  error_count: number;
};

export function useLabelImport(initialClasses: string, targetClasses?: ClassItem[]) {
  const [labels, setLabels] = useState<File[]>([]);
  const [yaml, setYaml] = useState<File | null>(null);
  const [method, setMethod] = useState<"manual" | "yaml">("manual");
  const [text, setText] = useState(initialClasses);
  const [report, setReport] = useState<Report | null>(null);
  const [mapping, setMapping] = useState<Record<number, number>>({});
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);

  function invalidate() {
    generation.current += 1;
    setReport(null);
    setError("");
  }
  function reset(classes = initialClasses) {
    invalidate();
    setLabels([]);
    setYaml(null);
    setMethod("manual");
    setText(classes);
    setMapping({});
  }
  async function check(images: File[]) {
    invalidate();
    const version = generation.current;
    setChecking(true);
    try {
      if (!images.length) throw new Error("请选择图片目录");
      if (!labels.length) throw new Error("请选择含 TXT 文件的标签目录");
      if (method === "yaml" && !yaml) throw new Error("请选择 YAML 文件");
      if (yaml && method === "yaml" && yaml.size > 1024 ** 2)
        throw new Error("YAML 文件不能超过 1 MB");
      if (labels.some((file) => file.size > 2 * 1024 ** 2))
        throw new Error("单个标签不能超过 2 MB");
      if (labels.reduce((sum, file) => sum + file.size, 0) > 32 * 1024 ** 2)
        throw new Error("标签总大小不能超过 32 MB，请分批导入");
      indexLabels(labels);
      const contents: Record<string, string> = {};
      const decoder = new TextDecoder("utf-8", { fatal: true });
      for (const file of labels) {
        try {
          contents[relativeImportName(file)] = decoder.decode(await file.arrayBuffer());
        } catch {
          throw new Error(`${file.name}：标签需要使用 UTF-8 编码`);
        }
      }
      const result = await post<Report>("/datasets/import-labels/preview", {
        image_names: images.map(relativeImportName),
        labels: contents,
        classes_text: method === "manual" ? text : "",
        yaml_text: method === "yaml" && yaml ? decoder.decode(await yaml.arrayBuffer()) : "",
      });
      if (version !== generation.current) return;
      setReport(result);
      setMapping(matchClasses(result.classes, targetClasses || result.classes));
      if (!result.matched) setError("没有匹配的标签，请检查目录层级");
    } catch (reason) {
      if (version === generation.current) setError(errorText(reason));
    } finally {
      setChecking(false);
    }
  }
  const ready = !!report && report.matched > 0 && !report.error_count && !checking &&
    report.classes.every((item) =>
      targetClasses
        ? targetClasses.some((target) => target.id === mapping[item.id])
        : mapping[item.id] === item.id,
    );
  function data(): LabelImportData {
    if (!ready || !report) throw new Error("请检查文件并完成类别对应");
    return { classes: report.classes, mapping, labels };
  }
  return {
    labels, yaml, method, text, report, mapping, checking, error, ready,
    invalidate, reset, check, data, setMapping,
    chooseLabels(files: FileList | null) {
      invalidate();
      const selected = Array.from(files || []).filter((file) => /\.txt$/i.test(file.name));
      setLabels(selected);
      if (!selected.length) setError("目录中没有 TXT 标签");
    },
    chooseYaml(file: File | null) { invalidate(); setYaml(file); },
    changeMethod(value: "manual" | "yaml") { invalidate(); setMethod(value); },
    changeText(value: string) { invalidate(); setText(value); },
  };
}
