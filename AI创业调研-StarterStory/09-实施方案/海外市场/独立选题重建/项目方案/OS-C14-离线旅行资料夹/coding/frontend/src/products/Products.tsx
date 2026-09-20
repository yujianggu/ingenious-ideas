import { useEffect, useId, useRef, useState } from "react";
import { product as definition } from "../../../shared/products";
import {
  atPath,
  changeField,
  fieldOptions,
  formPayload,
  initialFields,
  readableKey,
} from "../../../shared/product-types";
import type {
  ProductDefinition,
  ProductRecord,
  FieldDefinition,
} from "../../../shared/product-types";
import { download, json, request } from "../api";
import { useDirty } from "../dirty";
import "./products.css";

function readFile(
  file: File,
  binary: boolean,
): Promise<string | { name: string; contentType: string; base64: string }> {
  const limit = binary ? 500 * 1024 : 16 * 1024 * 1024;
  if (file.size > limit)
    return Promise.reject(
      new Error(
        binary
          ? "Choose a file smaller than 500 KiB."
          : "Choose a text import smaller than 16 MiB.",
      ),
    );
  if (!binary) return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read this file."));
    reader.onload = () =>
      resolve({
        name: file.name,
        contentType: file.type || "application/octet-stream",
        base64: String(reader.result).split(",")[1],
      });
    reader.readAsDataURL(file);
  });
}

export function ProductForm({
  fields,
  record,
  label,
  onSubmit,
}: {
  fields: FieldDefinition[];
  record?: ProductRecord | null;
  label: string;
  onSubmit: (body: Record<string, any>) => Promise<void>;
}) {
  const [values, setValues] = useState(() => initialFields(fields, record));
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [changed, setChanged] = useState(false);
  const id = useId();
  const { setDirty } = useDirty();
  useEffect(() => {
    setDirty(id, changed);
    return () => setDirty(id, false);
  }, [changed, id, setDirty]);
  const set = (key: string, value: any) => {
    setValues((v) => changeField(fields, record, v, key, value));
    setChanged(true);
  };
  return (
    <form
      className="product-form"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          await onSubmit(formPayload(fields, values));
          setChanged(false);
          setValues(initialFields(fields, record));
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      {fields.map((f) => (
        <label
          className={`product-field ${f.type === "checkbox" ? "product-check" : ""}`}
          key={f.name}
        >
          <span>
            {f.label}
            {f.required ? " *" : ""}
          </span>
          {f.type === "checkbox" ? (
            <input
              aria-label={f.label}
              type="checkbox"
              checked={!!values[f.name]}
              onChange={(e) => set(f.name, e.target.checked)}
              disabled={busy}
            />
          ) : f.type === "select" ? (
            <select
              aria-label={f.label}
              value={String(values[f.name])}
              onChange={(e) => set(f.name, e.target.value)}
              required={f.required}
              disabled={busy}
            >
              <option value="">Choose…</option>
              {fieldOptions(f, record).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : ["textarea", "json", "import"].includes(f.type) ? (
            <textarea
              aria-label={f.label}
              value={
                typeof values[f.name] === "string"
                  ? values[f.name]
                  : JSON.stringify(values[f.name], null, 2)
              }
              onChange={(e) => set(f.name, e.target.value)}
              required={f.required}
              disabled={busy}
              rows={f.type === "import" ? 7 : 4}
            />
          ) : f.type !== "file" ? (
            <input
              aria-label={f.label}
              type={
                f.type === "number"
                  ? "number"
                  : f.type === "email"
                    ? "email"
                    : f.type === "date"
                      ? "date"
                      : "text"
              }
              step="any"
              min={f.min}
              max={f.max}
              value={values[f.name]}
              required={f.required}
              disabled={busy}
              onChange={(e) => set(f.name, e.target.value)}
            />
          ) : null}
          {["file", "import"].includes(f.type) && (
            <input
              type="file"
              aria-label={`${f.label} file`}
              accept={f.accept}
              disabled={busy}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                try {
                  set(f.name, await readFile(file, f.type === "file"));
                  setError("");
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            />
          )}
          {f.type === "file" && values[f.name] && (
            <small>{values[f.name].name} · ready to upload</small>
          )}
          {f.help && <small>{f.help}</small>}
        </label>
      ))}
      {error && (
        <p role="alert" className="product-error">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy}>
        {busy ? "Saving…" : label}
      </button>
      {changed && <small className="product-unsaved">Unsaved changes</small>}
    </form>
  );
}

function Value({ value, depth = 0 }: { value: any; depth?: number }) {
  if (value === null || value === undefined || value === "")
    return <span className="muted">—</span>;
  if (typeof value === "boolean") return <>{value ? "Yes" : "No"}</>;
  if (typeof value !== "object")
    return <span className="product-value">{String(value)}</span>;
  if (depth > 4)
    return (
      <span>
        {Array.isArray(value)
          ? `${value.length} entries`
          : "Details available in backup"}
      </span>
    );
  if (Array.isArray(value))
    return (
      <ul className="product-values">
        {value.map((v, i) => (
          <li key={i}>
            <Value value={v} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  return (
    <dl className="product-values">
      {Object.entries(value)
        .filter(([k]) => k !== "base64")
        .map(([k, v]) => (
          <div key={k}>
            <dt>{readableKey(k)}</dt>
            <dd>
              <Value value={v} depth={depth + 1} />
            </dd>
          </div>
        ))}
    </dl>
  );
}
function DataView({
  view,
  record,
}: {
  view: ProductDefinition["views"][number];
  record: ProductRecord;
}) {
  const value = atPath(record, view.path);
  const [page, setPage] = useState(0);
  const size = 10;
  const safePage = Array.isArray(value)
    ? Math.min(page, Math.max(0, Math.ceil(value.length / size) - 1))
    : 0;
  return (
    <section className="product-panel">
      <h3>
        {view.title}
        {Array.isArray(value) && <small> {value.length}</small>}
      </h3>
      {Array.isArray(value) && view.columns ? (
        <>
          <div className="product-table-wrap">
            <table>
              <thead>
                <tr>
                  {view.columns.map((c) => (
                    <th key={c.key}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {value
                  .slice(safePage * size, (safePage + 1) * size)
                  .map((row: any, i: number) => (
                    <tr key={row.id || i}>
                      {view.columns!.map((c) => (
                        <td key={c.key}>
                          <Value value={atPath(row, c.key)} />
                        </td>
                      ))}
                    </tr>
                  ))}
              </tbody>
            </table>
            {!value.length && <p className="muted">No entries yet.</p>}
          </div>
          {value.length > size && (
            <div className="product-tools">
              <button
                className="quiet"
                disabled={!safePage}
                onClick={() => setPage(safePage - 1)}
              >
                Previous
              </button>
              <span>
                Page {safePage + 1} of {Math.ceil(value.length / size)}
              </span>
              <button
                className="quiet"
                disabled={(safePage + 1) * size >= value.length}
                onClick={() => setPage(safePage + 1)}
              >
                Next
              </button>
            </div>
          )}
        </>
      ) : (
        <Value value={value} />
      )}
    </section>
  );
}
function attachmentFile(item: any) {
  const raw = atob(item.base64);
  const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(
    new Blob([bytes], { type: item.contentType }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = item.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export default function Products() {
  const code = definition.code;
  const { dirty } = useDirty();
  const [records, setRecords] = useState<ProductRecord[]>([]),
    [record, setRecord] = useState<ProductRecord | null>(null);
  const [creating, setCreating] = useState(false),
    [action, setAction] = useState(""),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [epoch, setEpoch] = useState(0),
    [notice, setNotice] = useState("");
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  const path = `/products/${code}/records`;
  const guard = () =>
    !dirty || window.confirm("Discard your unsaved changes and continue?");
  const load = async () => {
    const data = await request<{ records: ProductRecord[] }>(path);
    if (mounted.current) setRecords(data.records);
    return data.records;
  };
  useEffect(() => {
    mounted.current = true;
    load()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [code]);
  const update = (r: ProductRecord) => {
    if (!mounted.current) return;
    setRecord(r);
    setRecords((old) => [r, ...old.filter((x) => x.id !== r.id)]);
    setNotice("Saved.");
    setError("");
  };
  const perform = async (task: () => Promise<void>) => {
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const available = definition.actions.filter(
    (a) => !a.when || atPath(record, a.when.path) === a.when.equals,
  );
  const chosen = available.find((a) => a.id === action);
  return (
    <main className="products-shell">
      <div className="product-heading">
        <div>
          <span className="eyebrow">{definition.name}</span>
          <h1>{definition.name}</h1>
          <p>{definition.summary}</p>
        </div>
        <button
          onClick={() => {
            if (guard()) {
              setCreating(true);
              setRecord(null);
              setAction("");
              setNotice("");
            }
          }}
        >
          + New {definition.recordLabel.toLowerCase()}
        </button>
      </div>
      {error && (
        <p role="alert" className="product-error">
          {error}{" "}
          <button
            className="quiet"
            onClick={() => {
              if (guard())
                void perform(async () => {
                  const data = await load();
                  setRecord(data.find((r) => r.id === record?.id) || null);
                  setEpoch((n) => n + 1);
                });
            }}
          >
            Reload latest data
          </button>
        </p>
      )}
      {notice && (
        <p role="status" className="product-notice">
          {notice}
        </p>
      )}
      <div className="product-layout">
        <aside className="product-records">
          <h2>Your {definition.recordLabel.toLowerCase()}s</h2>
          <p>Private to your account.</p>
          <button
            className="quiet"
            onClick={() =>
              void perform(async () => {
                await load();
              })
            }
          >
            Refresh list
          </button>
          {loading ? (
            <p>Loading…</p>
          ) : records.length === 0 ? (
            <p>
              Create your first {definition.recordLabel.toLowerCase()} to begin.
            </p>
          ) : (
            records.map((r) => (
              <button
                key={r.id}
                className={record?.id === r.id ? "selected" : ""}
                onClick={() => {
                  if (guard()) {
                    setRecord(r);
                    setCreating(false);
                    setAction("");
                    setNotice("");
                    setEpoch((n) => n + 1);
                  }
                }}
              >
                <strong>{r.title}</strong>
                <small>{r.status || r.phase || `Revision ${r.revision}`}</small>
              </button>
            ))
          )}
        </aside>
        <div className="product-content">
          {creating ? (
            <section className="product-panel">
              <h2>New {definition.recordLabel.toLowerCase()}</h2>
              <ProductForm
                fields={definition.create}
                label="Create"
                onSubmit={async (b) => {
                  update(await request<ProductRecord>(path, json(b)));
                  setCreating(false);
                }}
              />
            </section>
          ) : record ? (
            <>
              <section className="product-panel">
                <div className="product-tools">
                  <h2>{record.title}</h2>
                  <span className="product-revision">
                    Revision {record.revision}
                  </span>
                </div>
                <dl className="product-summary">
                  {Object.entries(record)
                    .filter(
                      ([k, v]) =>
                        ![
                          "id",
                          "code",
                          "title",
                          "revision",
                          "createdAt",
                          "updatedAt",
                        ].includes(k) && typeof v !== "object",
                    )
                    .map(([k, v]) => (
                      <div key={k}>
                        <dt>{readableKey(k)}</dt>
                        <dd>
                          <Value value={v} />
                        </dd>
                      </div>
                    ))}
                </dl>
                <div className="product-tools">
                  {definition.exports.map((e) => (
                    <button
                      className="quiet"
                      key={e.format}
                      onClick={() =>
                        void perform(() =>
                          download(
                            `${path}/${record.id}/export/${e.format}`,
                            `${code}-${record.title}.${e.format}`,
                          ),
                        )
                      }
                    >
                      {e.label}
                    </button>
                  ))}
                  <button
                    className="quiet"
                    onClick={() =>
                      void perform(() =>
                        download(
                          `${path}/${record.id}/export/json`,
                          `${code}-backup.json`,
                        ),
                      )
                    }
                  >
                    Download backup
                  </button>

                  <button
                    className="quiet danger"
                    onClick={() => {
                      if (
                        window.confirm(
                          `Permanently delete “${record.title}” and its data? Export a backup first if needed.`,
                        )
                      )
                        void perform(async () => {
                          await request(
                            `${path}/${record.id}/delete`,
                            json({ revision: record.revision, confirm: true }),
                          );
                          setRecords((old) =>
                            old.filter((r) => r.id !== record.id),
                          );
                          setRecord(null);
                          setNotice("Deleted.");
                        });
                    }}
                  >
                    Delete
                  </button>
                </div>
              </section>
              <section className="product-panel">
                <h3>Next action</h3>
                <label className="product-field">
                  <span>Choose an action</span>
                  <select
                    aria-label="Choose an action"
                    value={chosen?.id || ""}
                    onChange={(e) => {
                      if (guard()) {
                        setAction(e.target.value);
                        setEpoch((n) => n + 1);
                        setNotice("");
                      }
                    }}
                  >
                    <option value="">Select a task…</option>
                    {available.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.label}
                      </option>
                    ))}
                  </select>
                </label>
                {chosen && (
                  <div key={`${record.id}-${chosen.id}-${epoch}`}>
                    <p className="muted">{chosen.help}</p>
                    <ProductForm
                      fields={chosen.fields}
                      record={record}
                      label={chosen.label}
                      onSubmit={async (b) => {
                        try {
                          update(
                            await request<ProductRecord>(
                              `${path}/${record.id}/actions/${chosen.id}`,
                              json({ ...b, revision: record.revision }),
                            ),
                          );
                          setEpoch((n) => n + 1);
                        } catch (e) {
                          setError((e as Error).message);
                          throw e;
                        }
                      }}
                    />
                  </div>
                )}
              </section>
              {code === "C14" && record.attachments?.length > 0 && (
                <section className="product-panel">
                  <h3>Original documents</h3>
                  {record.attachments.map((a: any) => (
                    <button
                      className="quiet"
                      key={a.id}
                      onClick={() => attachmentFile(a)}
                    >
                      {a.name} · {Math.round(a.size / 1024)} KiB ↓
                    </button>
                  ))}
                </section>
              )}
              {definition.views.map((v) => (
                <DataView
                  key={`${record.id}-${v.path}`}
                  view={v}
                  record={record}
                />
              ))}
            </>
          ) : (
            <section className="product-panel product-empty">
              <span className="eyebrow">{code} / WORKSPACE</span>
              <h2>A clear place for every step.</h2>
              <p>
                Select a record or create your first{" "}
                {definition.recordLabel.toLowerCase()}.
              </p>
              <div className="product-roadmap">
                {definition.actions.slice(0, 5).map((a, i) => (
                  <div key={a.id}>
                    <b>{String(i + 1).padStart(2, "0")}</b>
                    <span>{a.label}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </main>
  );
}
