import type { ClassMapping } from "../types";

export function parseSourceIds(value: unknown): number[] | null {
  if (typeof value === "string" && !value.trim()) return null;
  const parts =
    typeof value === "string"
      ? value
          .trim()
          .split(/[,，\s]+/)
          .filter(Boolean)
      : Array.isArray(value)
        ? value
        : [value];
  if (
    !parts.length ||
    parts.some(
      (part) =>
        (typeof part !== "number" &&
          (typeof part !== "string" || !/^\d+$/.test(part))) ||
        !Number.isInteger(Number(part)) ||
        Number(part) < 0 ||
        Number(part) > 9999,
    )
  )
    throw new Error("检测类别 ID 必须为 0–9999 的整数，多个 ID 用逗号分隔");
  return [...new Set(parts.map(Number))];
}

export function serializeClassMapping(
  mapping: ClassMapping = {},
  selected: number[] | null = null,
): Record<number, number | number[]> {
  const result: Record<number, number | number[]> = {};
  for (const [target, value] of Object.entries(mapping)) {
    if (selected && !selected.includes(Number(target))) continue;
    const ids = parseSourceIds(value);
    if (ids) result[Number(target)] = ids.length === 1 ? ids[0] : ids;
  }
  return result;
}
