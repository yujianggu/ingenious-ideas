import type { ProductRecord } from "../../../shared/product-types.ts";
export type PendingCheck = {
  recordId: string;
  revision: number;
  itemId: string;
  checked: boolean;
};
export type Snapshot = {
  records: ProductRecord[];
  savedAt: string;
  pending: PendingCheck[];
};
export type CurrentWork = () => boolean;
export class ObsoleteWork extends Error {
  constructor() {
    super("This screen or account is no longer active.");
    this.name = "ObsoleteWork";
  }
}
export function requireCurrent(current: CurrentWork) {
  if (!current()) throw new ObsoleteWork();
}
export function createGeneration() {
  let generation = 0;
  return {
    capture() {
      const captured = generation;
      return () => captured === generation;
    },
    invalidate() {
      generation++;
    },
  };
}
export function createGuardedQueue() {
  let queue: Promise<unknown> = Promise.resolve();
  return function serial<T>(
    action: () => Promise<T>,
    current: CurrentWork = () => true,
  ): Promise<T> {
    const task = queue.then(() => {
      requireCurrent(current);
      return action();
    });
    queue = task.catch(() => {});
    return task;
  };
}
export async function purgeRememberedAccount(
  read: () => Promise<string | null>,
  purge: (base: string, user: string) => Promise<void>,
): Promise<void> {
  const saved = await read();
  if (!saved) return;
  let profile: any;
  try {
    profile = JSON.parse(saved);
  } catch {
    return;
  }
  if (
    typeof profile?.base === "string" &&
    profile.base &&
    typeof profile?.user?.id === "string" &&
    profile.user.id
  )
    await purge(profile.base, profile.user.id);
}
function packingStatus(record: ProductRecord): ProductRecord {
  return record.currentTrip
    ? {
        ...record,
        currentTrip: {
          ...record.currentTrip,
          status:
            record.items.length &&
            record.items.every((item: any) => item.checked)
              ? "packed"
              : "packing",
        },
      }
    : record;
}
export function queueCheck(
  snapshot: Snapshot,
  recordId: string,
  itemId: string,
  checked: boolean,
): Snapshot {
  const record = snapshot.records.find((r) => r.id === recordId);
  if (!record || !record.items?.some((i: any) => i.id === itemId))
    throw new Error("Save this packing list for offline use first.");
  const existing = snapshot.pending.filter((p) => p.recordId === recordId);
  const revision = existing[0]?.revision ?? record.revision;
  return {
    ...snapshot,
    pending: [
      ...snapshot.pending.filter(
        (p) => !(p.recordId === recordId && p.itemId === itemId),
      ),
      { recordId, revision, itemId, checked },
    ],
    records: snapshot.records.map((r) =>
      r.id !== recordId
        ? r
        : packingStatus({
            ...r,
            items: r.items.map((i: any) =>
              i.id === itemId ? { ...i, checked } : i,
            ),
          }),
    ),
  };
}
export function acknowledgeCheck(
  snapshot: Snapshot,
  command: PendingCheck,
  server: ProductRecord,
): Snapshot {
  const pending = snapshot.pending
    .filter((p) => p !== command)
    .map((p) =>
      p.recordId === server.id ? { ...p, revision: server.revision } : p,
    );
  const overlay = {
    ...server,
    items: server.items.map((item: any) => {
      const change = pending.find(
        (p) => p.recordId === server.id && p.itemId === item.id,
      );
      return change ? { ...item, checked: change.checked } : item;
    }),
  };
  return {
    ...snapshot,
    pending,
    records: snapshot.records.map((r) =>
      r.id === server.id ? packingStatus(overlay) : r,
    ),
  };
}
export function assertUnchanged(command: PendingCheck, server: ProductRecord) {
  if (command.revision !== server.revision)
    throw new Error(
      "Cloud data changed. Your local checks are preserved. Review the cloud version before discarding or reapplying them.",
    );
}
export function localBackupRecord(
  offline: boolean,
  record: ProductRecord,
  snapshot: Snapshot | null,
): ProductRecord | null {
  return offline || snapshot?.pending.length
    ? (snapshot?.records.find((r) => r.id === record.id) ?? record)
    : null;
}
export async function syncPendingChecks(
  initial: Snapshot,
  io: {
    get: (recordId: string) => Promise<ProductRecord>;
    post: (
      command: PendingCheck,
      server: ProductRecord,
    ) => Promise<ProductRecord>;
    persist: (value: Snapshot) => Promise<void>;
    progress?: (value: Snapshot) => void;
  },
  current: CurrentWork,
): Promise<Snapshot> {
  let state = initial;
  requireCurrent(current);
  while (state.pending.length) {
    const command = state.pending[0];
    requireCurrent(current);
    const server = await io.get(command.recordId);
    requireCurrent(current);
    assertUnchanged(command, server);
    const changed = await io.post(command, server);
    requireCurrent(current);
    state = acknowledgeCheck(state, command, changed);
    requireCurrent(current);
    await io.persist(state);
    requireCurrent(current);
    io.progress?.(state);
  }
  return state;
}
