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
export function localBackupRecord(
  offline: boolean,
  record: ProductRecord,
  snapshot: Snapshot | null,
): ProductRecord | null {
  return offline || snapshot?.pending.length
    ? (snapshot?.records.find((r) => r.id === record.id) ?? record)
    : null;
}
