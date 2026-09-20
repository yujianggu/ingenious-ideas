export type ProductRecord = {
  id: string;
  code: string;
  title: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
  [key: string]: any;
};
export type FieldDefinition = {
  name: string;
  label: string;
  type: string;
  required?: boolean;
  default?: any;
  help?: string;
  options?: { value: string | number; label: string }[];
  optionsPath?: string;
  optionValue?: string;
  optionLabel?: string;
  min?: number;
  max?: number;
  accept?: string;
  defaultPath?: string;
  selectedFrom?: { selector: string; path: string; value: string };
};
export type ProductDefinition = {
  code: string;
  name: string;
  summary: string;
  recordLabel: string;
  create: FieldDefinition[];
  actions: {
    id: string;
    label: string;
    help?: string;
    fields: FieldDefinition[];
    when?: { path: string; equals: any };
  }[];
  views: {
    path: string;
    title: string;
    columns?: { key: string; label: string }[];
  }[];
  exports: { format: string; label: string }[];
};
export function atPath(value: any, path: string): any {
  return path.split(".").reduce((v, key) => v?.[key], value);
}
export function fieldOptions(
  field: FieldDefinition,
  record?: ProductRecord | null,
) {
  const list = field.optionsPath ? atPath(record, field.optionsPath) : null;
  return Array.isArray(list)
    ? list.map((item: any) => ({
        value: String(item[field.optionValue || "id"]),
        label: String(
          item[field.optionLabel || "title"] ??
            item.label ??
            item.name ??
            item.id,
        ),
      }))
    : field.options || [];
}
export function initialFields(
  fields: FieldDefinition[],
  record?: ProductRecord | null,
): Record<string, any> {
  return Object.fromEntries(
    fields.map((f) => {
      let value =
        (f.defaultPath ? atPath(record, f.defaultPath) : undefined) ??
        f.default ??
        (f.type === "checkbox" ? false : "");
      if (
        ["textarea", "import"].includes(f.type) &&
        Array.isArray(value) &&
        value.every((x) => typeof x === "string")
      )
        value = value.join("\n");
      return [f.name, value];
    }),
  );
}
export function formPayload(
  fields: FieldDefinition[],
  values: Record<string, any>,
): Record<string, any> {
  const body: Record<string, any> = {};
  for (const f of fields) {
    const value = values[f.name];
    if (
      f.required &&
      (value === undefined ||
        value === null ||
        value === "" ||
        (typeof value === "string" && !value.trim()) ||
        (f.type === "checkbox" && value !== true))
    )
      throw new Error(`${f.label} is required.`);
    if (f.type === "number") {
      if (value === "" || value === null || value === undefined) {
        if (f.required) throw new Error(`${f.label} is required.`);
        continue;
      }
      const number = Number(value);
      if (
        !Number.isFinite(number) ||
        (f.min !== undefined && number < f.min) ||
        (f.max !== undefined && number > f.max)
      )
        throw new Error(
          `${f.label} must be a valid number${f.min !== undefined ? ` ≥ ${f.min}` : ""}${f.max !== undefined ? ` and ≤ ${f.max}` : ""}.`,
        );
      body[f.name] = number;
    } else if (f.type === "json") {
      if (!value && !f.required) continue;
      try {
        body[f.name] = typeof value === "string" ? JSON.parse(value) : value;
      } catch {
        throw new Error(`${f.label} must contain valid JSON.`);
      }
    } else if (f.type === "file") {
      if (value) body[f.name] = value;
    } else body[f.name] = value ?? "";
  }
  return body;
}
export function readableKey(key: string) {
  return key
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]/g, " ")
    .replace(/^./, (s) => s.toUpperCase());
}

export function changeField(
  fields: FieldDefinition[],
  record: ProductRecord | null | undefined,
  values: Record<string, any>,
  name: string,
  value: any,
): Record<string, any> {
  const next = { ...values, [name]: value };
  for (const f of fields)
    if (f.selectedFrom?.selector === name) {
      const selected = atPath(record, f.selectedFrom.path)?.find(
        (row: any) => row.id === value,
      );
      const raw = selected ? atPath(selected, f.selectedFrom.value) : "";
      next[f.name] = Array.isArray(raw) ? raw.join("\n") : (raw ?? "");
    }
  return next;
}
