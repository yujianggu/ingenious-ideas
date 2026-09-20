import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { products } from "../../../shared/products";
import {
  atPath,
  changeField,
  fieldOptions,
  formPayload,
  initialFields,
  readableKey,
} from "../../../shared/product-types";
import type {
  FieldDefinition,
  ProductRecord,
  ProductDefinition,
} from "../../../shared/product-types";
import { ApiError, request } from "../api";
import { download } from "../files";
import { accountCacheGuard, loadSnapshot, saveSnapshot } from "./cache";
import {
  createGeneration,
  localBackupRecord,
  ObsoleteWork,
  requireCurrent,
} from "./offline";
import type { CurrentWork, Snapshot } from "./offline";
import { openAttachment, pickFile } from "./product-files";

function Button({
  title,
  onPress,
  disabled = false,
  quiet = false,
}: {
  title: string;
  onPress: () => void;
  disabled?: boolean;
  quiet?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      disabled={disabled}
      onPress={onPress}
      style={[s.button, quiet && s.quiet, disabled && s.disabled]}
    >
      <Text style={[s.buttonText, quiet && s.quietText]}>{title}</Text>
    </Pressable>
  );
}
async function confirm(message: string) {
  if (Platform.OS === "web") return window.confirm(message);
  return new Promise<boolean>((resolve) =>
    Alert.alert(
      "Please confirm",
      message,
      [
        { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
        { text: "Continue", onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    ),
  );
}
function Value({ value, depth = 0 }: { value: any; depth?: number }) {
  if (value === null || value === undefined || value === "")
    return <Text style={s.muted}>—</Text>;
  if (typeof value !== "object")
    return (
      <Text style={s.value}>
        {typeof value === "boolean" ? (value ? "Yes" : "No") : String(value)}
      </Text>
    );
  if (depth > 4)
    return <Text style={s.muted}>See backup for deeper details.</Text>;
  if (Array.isArray(value))
    return (
      <View>
        {value.map((v, i) => (
          <View style={s.nested} key={i}>
            <Value value={v} depth={depth + 1} />
          </View>
        ))}
      </View>
    );
  return (
    <View>
      {Object.entries(value)
        .filter(([k]) => k !== "base64")
        .map(([k, v]) => (
          <View key={k} style={s.field}>
            <Text style={s.label}>{readableKey(k)}</Text>
            <Value value={v} depth={depth + 1} />
          </View>
        ))}
    </View>
  );
}
function ProductForm({
  fields,
  record,
  label,
  onSubmit,
  onDirty,
}: {
  fields: FieldDefinition[];
  record?: ProductRecord | null;
  label: string;
  onSubmit: (body: any) => Promise<void>;
  onDirty: (dirty: boolean) => void;
}) {
  const [values, setValues] = useState(() => initialFields(fields, record)),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [changed, setChanged] = useState(false);
  useEffect(() => {
    onDirty(changed);
    return () => onDirty(false);
  }, [changed, onDirty]);
  const set = (name: string, value: any) => {
    setValues((v) => changeField(fields, record, v, name, value));
    setChanged(true);
  };
  return (
    <View>
      {fields.map((f) => (
        <View key={f.name} style={s.field}>
          <Text style={s.label}>
            {f.label}
            {f.required ? " *" : ""}
          </Text>
          {f.type === "checkbox" ? (
            <Switch
              accessibilityLabel={f.label}
              value={!!values[f.name]}
              disabled={busy}
              onValueChange={(v) => set(f.name, v)}
            />
          ) : f.type === "select" ? (
            <View style={s.options}>
              {fieldOptions(f, record).length ? (
                fieldOptions(f, record).map((o) => (
                  <Button
                    key={o.value}
                    title={`${values[f.name] === String(o.value) ? "✓ " : ""}${o.label}`}
                    quiet={values[f.name] !== String(o.value)}
                    disabled={busy}
                    onPress={() => set(f.name, String(o.value))}
                  />
                ))
              ) : (
                <Text style={s.muted}>No options yet. Add an entry first.</Text>
              )}
            </View>
          ) : f.type === "file" ? (
            <Text style={s.muted}>
              {values[f.name]?.name || "No file selected"}
            </Text>
          ) : (
            <TextInput
              accessibilityLabel={f.label}
              value={
                typeof values[f.name] === "string"
                  ? values[f.name]
                  : String(values[f.name])
              }
              onChangeText={(v) => set(f.name, v)}
              editable={!busy}
              style={[
                s.input,
                ["textarea", "import", "json"].includes(f.type) && s.multiline,
              ]}
              multiline={["textarea", "import", "json"].includes(f.type)}
              keyboardType={f.type === "number" ? "numeric" : "default"}
              autoCapitalize="none"
            />
          )}
          {["file", "import"].includes(f.type) && (
            <Button
              quiet
              title={`Choose ${f.type === "file" ? "document" : "text file"}`}
              disabled={busy}
              onPress={() => {
                void pickFile(f.type === "file")
                  .then((value) => {
                    if (value !== null) set(f.name, value);
                  })
                  .catch((e) => setError(e.message));
              }}
            />
          )}
          {f.help && <Text style={s.muted}>{f.help}</Text>}
        </View>
      ))}
      {error && (
        <Text accessibilityRole="alert" style={s.error}>
          {error}
        </Text>
      )}
      {changed && <Text style={s.muted}>Unsaved edits</Text>}
      <Button
        title={busy ? "Saving…" : label}
        disabled={busy}
        onPress={() => {
          setBusy(true);
          setError("");
          void (async () => {
            try {
              await onSubmit(formPayload(fields, values));
              setChanged(false);
              setValues(initialFields(fields, record));
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          })();
        }}
      />
    </View>
  );
}
function DataView({
  view,
  record,
}: {
  view: ProductDefinition["views"][number];
  record: ProductRecord;
}) {
  const value = atPath(record, view.path),
    [limit, setLimit] = useState(10);
  return (
    <View style={s.card}>
      <Text style={s.heading}>
        {view.title}
        {Array.isArray(value) ? ` · ${value.length}` : ""}
      </Text>
      {Array.isArray(value) ? (
        value.length ? (
          value.slice(0, limit).map((row: any, i: number) => (
            <View key={row.id || i} style={s.nested}>
              {view.columns ? (
                view.columns.map((c) => (
                  <View key={c.key} style={s.field}>
                    <Text style={s.label}>{c.label}</Text>
                    <Value value={atPath(row, c.key)} />
                  </View>
                ))
              ) : (
                <Value value={row} />
              )}
            </View>
          ))
        ) : (
          <Text style={s.muted}>No entries yet.</Text>
        )
      ) : (
        <Value value={value} />
      )}
      {Array.isArray(value) && value.length > limit && (
        <Button
          title="Show 10 more"
          quiet
          onPress={() => setLimit(limit + 10)}
        />
      )}
    </View>
  );
}
export default function Products({
  code,
  base,
  token,
  userId,
  onDirty,
  onExpired,
}: {
  code: string;
  base: string;
  token: string;
  userId: string;
  onDirty: (dirty: boolean) => void;
  onExpired: () => void;
}) {
  const definition = products.find((p) => p.code === code)!;
  const [records, setRecords] = useState<ProductRecord[]>([]),
    [record, setRecord] = useState<ProductRecord | null>(null),
    [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [creating, setCreating] = useState(false),
    [action, setAction] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [offline, setOffline] = useState(false),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [epoch, setEpoch] = useState(0);
  const dirty = useRef(false),
    snapshotRef = useRef<Snapshot | null>(null),
    busyLock = useRef(false);
  const identity = JSON.stringify([base, userId, code, token]);
  const session = useRef<{ identity: string; current: CurrentWork } | null>(
    null,
  );
  const capture = (): CurrentWork =>
    session.current?.identity === identity
      ? session.current.current
      : () => false;
  const trackDirty = React.useCallback(
    (value: boolean) => {
      dirty.current = value;
      onDirty(value);
    },
    [onDirty],
  );
  const path = `/products/${code}/records`;
  const consumer = true;
  const checkError = (e: any, current = capture()) => {
    if (!current() || e instanceof ObsoleteWork) return;
    if (e instanceof ApiError && e.status === 401) {
      onExpired();
      return;
    }
    setError(e.message || "Request failed");
    if (e instanceof ApiError && e.status === 0) setOffline(true);
  };
  const guard = async (current = capture()) => {
    if (!current()) return false;
    const accepted =
      !dirty.current ||
      (await confirm("Discard your unsaved form edits and continue?"));
    return current() && accepted;
  };
  const persist = async (value: Snapshot, current: CurrentWork) => {
    requireCurrent(current);
    await saveSnapshot(base, userId, code, value, current);
    requireCurrent(current);
    setSnapshot(value);
    snapshotRef.current = value;
  };
  const fetchRecords = async (current = capture()) => {
    requireCurrent(current);
    const data = await request<{ records: ProductRecord[] }>(base, token, path);
    requireCurrent(current);
    return data.records;
  };
  useEffect(() => {
    const lifetime = createGeneration(),
      mounted = lifetime.capture(),
      accountCurrent = accountCacheGuard(base, userId),
      current = () => mounted() && accountCurrent();
    session.current = { identity, current };
    busyLock.current = false;
    snapshotRef.current = null;
    setSnapshot(null);
    setRecords([]);
    setRecord(null);
    setCreating(false);
    setAction("");
    setError("");
    setNotice("");
    setBusy(false);
    setLoading(true);
    void (async () => {
      try {
        const cached = consumer ? await loadSnapshot(base, userId, code) : null;
        if (!current()) return;
        setSnapshot(cached);
        snapshotRef.current = cached;
        if (cached) {
          setRecords(cached.records);
          setNotice(
            `Saved on this device: ${new Date(cached.savedAt).toLocaleString()}`,
          );
        }
        try {
          const latest = await fetchRecords(current);
          if (cached?.pending.length) {
            setRecords(cached.records);
            setNotice(
              "Local checks are waiting to sync. Cloud changes will be checked first.",
            );
          } else setRecords(latest);
          setOffline(false);
        } catch (e) {
          if (!current()) return;
          if (cached && e instanceof ApiError && e.status === 0) {
            setOffline(true);
            setNotice(
              "Offline copy. Only saved records and documents are available.",
            );
          } else checkError(e, current);
        }
      } catch (e) {
        checkError(e, current);
      } finally {
        if (current()) setLoading(false);
      }
    })();
    return () => {
      lifetime.invalidate();
      onDirty(false);
    };
  }, [code, base, userId, token]);
  async function run(fn: (current: CurrentWork) => Promise<void>) {
    const current = capture();
    if (busyLock.current || !current()) return;
    busyLock.current = true;
    setBusy(true);
    setError("");
    try {
      await fn(current);
    } catch (e) {
      checkError(e, current);
    } finally {
      if (current()) {
        busyLock.current = false;
        setBusy(false);
      }
    }
  }
  const update = async (r: ProductRecord, current: CurrentWork) => {
    requireCurrent(current);
    setRecord(r);
    setRecords((old) => [r, ...old.filter((x) => x.id !== r.id)]);
    setOffline(false);
    setNotice("Saved to server.");
    const cached = snapshotRef.current;
    if (cached && !cached.pending.length)
      await persist(
        {
          ...cached,
          records: [r, ...cached.records.filter((x) => x.id !== r.id)],
          savedAt: new Date().toISOString(),
        },
        current,
      );
  };
  const mutate = async (action: string, body: any, current = capture()) => {
    try {
      requireCurrent(current);
      if (snapshotRef.current?.pending.length)
        throw new Error(
          "Sync or discard queued checks before editing the cloud record.",
        );
      const changed = await request<ProductRecord>(
        base,
        token,
        `${path}/${record!.id}/actions/${action}`,
        "POST",
        { ...body, revision: record!.revision },
      );
      requireCurrent(current);
      await update(changed, current);
    } catch (e) {
      checkError(e, current);
      throw e;
    }
  };
  async function cacheAll(current: CurrentWork) {
    const latest = await fetchRecords(current);
    if (snapshotRef.current?.pending.length)
      throw new Error(
        "Sync queued checks before refreshing your offline copy.",
      );
    const saved = {
      records: latest,
      pending: [],
      savedAt: new Date().toISOString(),
    };
    await persist(saved, current);
    requireCurrent(current);
    setRecords(latest);
    if (record) setRecord(latest.find((r) => r.id === record.id) || null);
    setOffline(false);
    setNotice(
      "Saved on this device, including original documents. Available offline.",
    );
  }
  const available = definition.actions.filter(
      (a) => !a.when || atPath(record, a.when.path) === a.when.equals,
    ),
    chosen = available.find((a) => a.id === action);
  return (
    <View>
      <View style={s.card}>
        <Text style={s.eyebrow}>
          {code} / {offline ? "OFFLINE COPY" : "WORKSPACE"}
        </Text>
        <Text style={s.title}>{definition.name}</Text>
        <Text style={s.muted}>{definition.summary}</Text>
        <View style={s.row}>
          <Button
            title={`+ New ${definition.recordLabel.toLowerCase()}`}
            disabled={offline || busy}
            onPress={() => {
              void guard().then((ok) => {
                if (ok) {
                  setCreating(true);
                  setRecord(null);
                  setAction("");
                }
              });
            }}
          />
          <Button
            title="Refresh records"
            quiet
            disabled={busy}
            onPress={() =>
              void run(async (current) => {
                if (!(await guard(current))) return;
                if (snapshotRef.current?.pending.length)
                  throw new Error("Sync or discard queued checks first.");
                const latest = await fetchRecords(current);
                setRecords(latest);
                if (record)
                  setRecord(latest.find((r) => r.id === record.id) || null);
                setOffline(false);
                setEpoch((n) => n + 1);
              })
            }
          />
        </View>
        {consumer && (
          <View>
            <Text style={s.muted}>
              {snapshot
                ? `Offline copy saved ${new Date(snapshot.savedAt).toLocaleString()}`
                : "No offline copy saved on this device."}
            </Text>
            <View style={s.row}>
              <Button
                quiet
                title="Save for offline use"
                disabled={busy}
                onPress={() =>
                  void run(async (current) => {
                    if (await guard(current)) {
                      await cacheAll(current);
                      requireCurrent(current);
                      setEpoch((n) => n + 1);
                    }
                  })
                }
              />
            </View>
          </View>
        )}
      </View>
      {loading && <ActivityIndicator />}
      {busy && <ActivityIndicator />}
      {error && (
        <Text accessibilityRole="alert" style={s.error}>
          {error}
        </Text>
      )}
      {notice && (
        <Text accessibilityRole="summary" style={s.notice}>
          {notice}
        </Text>
      )}
      <View style={s.card}>
        <Text style={s.heading}>
          Your {definition.recordLabel.toLowerCase()}s
        </Text>
        {!records.length && <Text style={s.muted}>No records yet.</Text>}
        {records.map((r) => (
          <Button
            key={r.id}
            quiet={record?.id !== r.id}
            title={r.title}
            disabled={busy}
            onPress={() => {
              void guard().then((ok) => {
                if (ok) {
                  setRecord(r);
                  setCreating(false);
                  setAction("");
                  setEpoch((n) => n + 1);
                }
              });
            }}
          />
        ))}
      </View>
      {creating ? (
        <View style={s.card}>
          <Text style={s.heading}>
            New {definition.recordLabel.toLowerCase()}
          </Text>
          <ProductForm
            fields={definition.create}
            label="Create"
            onDirty={trackDirty}
            onSubmit={async (body) => {
              const current = capture();
              try {
                requireCurrent(current);
                const created = await request<ProductRecord>(
                  base,
                  token,
                  path,
                  "POST",
                  body,
                );
                requireCurrent(current);
                await update(created, current);
                requireCurrent(current);
                setCreating(false);
              } catch (e) {
                checkError(e, current);
                throw e;
              }
            }}
          />
        </View>
      ) : record ? (
        <>
          <View style={s.card}>
            <Text style={s.heading}>{record.title}</Text>
            <Text style={s.muted}>Revision {record.revision}</Text>
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
                <View key={k} style={s.field}>
                  <Text style={s.label}>{readableKey(k)}</Text>
                  <Value value={v} />
                </View>
              ))}
            <View style={s.row}>
              {definition.exports.map((e) => (
                <Button
                  key={e.format}
                  title={e.label}
                  quiet
                  disabled={offline || busy}
                  onPress={() =>
                    void run(() =>
                      download(
                        base,
                        token,
                        `${path}/${record.id}/export/${e.format}`,
                        `${code}-export.${e.format}`,
                      ),
                    )
                  }
                />
              ))}
              <Button
                title="Download backup"
                quiet
                disabled={busy}
                onPress={() =>
                  void run(async (current) => {
                    requireCurrent(current);
                    const local = localBackupRecord(
                      offline,
                      record,
                      snapshotRef.current,
                    );
                    if (local) {
                      const text = JSON.stringify(local);
                      const bytes = new TextEncoder().encode(text);
                      let binary = "";
                      for (const byte of bytes)
                        binary += String.fromCharCode(byte);
                      await openAttachment({
                        name: `${code}-backup.json`,
                        contentType: "application/json",
                        base64: btoa(binary),
                      });
                    } else
                      await download(
                        base,
                        token,
                        `${path}/${record.id}/export/json`,
                        `${code}-backup.json`,
                      );
                  })
                }
              />
              <Button
                title="Delete record"
                quiet
                disabled={offline || busy}
                onPress={() =>
                  void run(async (current) => {
                    if (
                      !(await confirm(
                        `Permanently delete ${record.title}? Export a backup first if needed.`,
                      ))
                    )
                      return;
                    requireCurrent(current);
                    if (snapshotRef.current?.pending.length)
                      throw new Error("Sync or discard local checks first.");
                    await request(
                      base,
                      token,
                      `${path}/${record.id}/delete`,
                      "POST",
                      { revision: record.revision, confirm: true },
                    );
                    requireCurrent(current);
                    const next = records.filter((r) => r.id !== record.id);
                    setRecords(next);
                    setRecord(null);
                    if (snapshotRef.current)
                      await persist(
                        {
                          ...snapshotRef.current,
                          records: snapshotRef.current.records.filter(
                            (r) => r.id !== record.id,
                          ),
                        },
                        current,
                      );
                  })
                }
              />
            </View>
          </View>
          <View style={s.card}>
            <Text style={s.heading}>Next action</Text>
            <View style={s.options}>
              {available.map((a) => (
                <Button
                  key={a.id}
                  title={a.label}
                  quiet={action !== a.id}
                  disabled={busy}
                  onPress={() => {
                    void guard().then((ok) => {
                      if (ok) {
                        setAction(a.id);
                        setEpoch((n) => n + 1);
                      }
                    });
                  }}
                />
              ))}
            </View>
            {chosen && (
              <View key={`${record.id}-${chosen.id}-${epoch}`}>
                <Text style={s.muted}>{chosen.help}</Text>
                <ProductForm
                  fields={chosen.fields}
                  record={record}
                  label={`Confirm: ${chosen.label}`}
                  onDirty={trackDirty}
                  onSubmit={async (body) => {
                    const current = capture();
                    await mutate(chosen.id, body, current);
                    requireCurrent(current);
                    setEpoch((n) => n + 1);
                  }}
                />
              </View>
            )}
          </View>
          {definition.views.map((v) => (
            <DataView key={`${record.id}-${v.path}`} view={v} record={record} />
          ))}
        </>
      ) : null}
    </View>
  );
}
const s = StyleSheet.create({
  card: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#d9e5de",
    borderRadius: 12,
    padding: 18,
    marginBottom: 16,
  },
  eyebrow: {
    fontSize: 10,
    color: "#5c7869",
    fontWeight: "700",
    letterSpacing: 1.5,
    marginBottom: 10,
  },
  title: {
    fontSize: 28,
    fontWeight: "700",
    color: "#143d2d",
    marginBottom: 12,
  },
  heading: {
    fontSize: 19,
    fontWeight: "700",
    color: "#1d4633",
    marginBottom: 14,
  },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginVertical: 10 },
  button: {
    backgroundColor: "#196649",
    borderRadius: 6,
    padding: 12,
    marginVertical: 4,
    alignSelf: "flex-start",
  },
  quiet: { backgroundColor: "#edf5ef", borderWidth: 1, borderColor: "#d4e2d8" },
  buttonText: { color: "#fff", fontWeight: "600", fontSize: 13 },
  quietText: { color: "#29583e" },
  disabled: { opacity: 0.45 },
  muted: { fontSize: 12, color: "#667c6d", lineHeight: 20, marginBottom: 6 },
  field: { marginBottom: 14, gap: 6 },
  label: { fontSize: 12, fontWeight: "600", color: "#4b6857" },
  input: {
    borderWidth: 1,
    borderColor: "#bfcfc5",
    borderRadius: 5,
    backgroundColor: "#fcfdfc",
    padding: 12,
    fontSize: 15,
    color: "#243e2e",
  },
  multiline: { minHeight: 110, textAlignVertical: "top" },
  options: { gap: 2, marginBottom: 12 },
  error: {
    backgroundColor: "#fff0ed",
    color: "#923c2a",
    padding: 13,
    marginBottom: 12,
    lineHeight: 22,
  },
  notice: {
    backgroundColor: "#eaf4ec",
    color: "#30623e",
    padding: 12,
    marginBottom: 12,
    lineHeight: 20,
  },
  value: { fontSize: 14, color: "#273f2f", lineHeight: 22 },
  nested: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#e2ebe5",
  },
  checkRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginVertical: 10,
  },
  checkLabel: { flex: 1, fontSize: 15, color: "#214d35" },
});
