import { Platform } from "react-native";
import * as FileSystem from "expo-file-system/legacy";
import type { Snapshot } from "./offline";
import {
  createGeneration,
  createGuardedQueue,
  requireCurrent,
} from "./offline";
import type { CurrentWork } from "./offline";
const codes = ["C04"];
const key = (base: string, user: string, code: string) =>
  encodeURIComponent(base) + "_" + user + "_" + code;
const serial = createGuardedQueue();
const generations = new Map<string, ReturnType<typeof createGeneration>>();
function accountGeneration(base: string, user: string) {
  const id = JSON.stringify([base, user]);
  let generation = generations.get(id);
  if (!generation) {
    generation = createGeneration();
    generations.set(id, generation);
  }
  return generation;
}
export function accountCacheGuard(base: string, user: string): CurrentWork {
  return accountGeneration(base, user).capture();
}
function webStore<T>(
  operation: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open("os-c04-offline", 1);
    opening.onupgradeneeded = () =>
      opening.result.createObjectStore("snapshots");
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      const db = opening.result;
      const tx = db.transaction("snapshots", "readwrite");
      let result: T;
      tx.oncomplete = () => {
        db.close();
        resolve(result);
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error || new Error("Device storage failed."));
      };
      try {
        const request = operation(tx.objectStore("snapshots"));
        request.onsuccess = () => {
          result = request.result;
        };
      } catch (error) {
        tx.abort();
        db.close();
        reject(error);
      }
    };
  });
}
async function directory() {
  const dir = FileSystem.documentDirectory + "os-c04-offline/";
  await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
  return dir;
}
export function loadSnapshot(
  base: string,
  user: string,
  code: string,
): Promise<Snapshot | null> {
  return serial(async () => {
    const id = key(base, user, code);
    if (Platform.OS === "web")
      return (await webStore<any>((store) => store.get(id))) || null;
    const path = (await directory()) + id + ".json";
    const info = await FileSystem.getInfoAsync(path);
    return info.exists
      ? JSON.parse(await FileSystem.readAsStringAsync(path))
      : null;
  });
}
export function saveSnapshot(
  base: string,
  user: string,
  code: string,
  value: Snapshot,
  isCurrent: CurrentWork,
): Promise<void> {
  const accountCurrent = accountCacheGuard(base, user);
  const current = () => isCurrent() && accountCurrent();
  return serial(async () => {
    const id = key(base, user, code);
    if (Platform.OS === "web") {
      await webStore((store) => {
        requireCurrent(current);
        return store.put(value, id);
      });
      return;
    }
    const path = (await directory()) + id + ".json";
    requireCurrent(current);
    try {
      await FileSystem.writeAsStringAsync(path + ".tmp", JSON.stringify(value));
      requireCurrent(current);
      await FileSystem.moveAsync({ from: path + ".tmp", to: path });
    } finally {
      await FileSystem.deleteAsync(path + ".tmp", { idempotent: true });
    }
  }, current);
}
export function removeAccountCache(base: string, user?: string): Promise<void> {
  if (!user) return Promise.resolve();
  accountGeneration(base, user).invalidate();
  return serial(async () => {
    for (const code of codes) {
      const id = key(base, user, code);
      if (Platform.OS === "web") await webStore((store) => store.delete(id));
      else {
        const path = (await directory()) + id + ".json";
        await FileSystem.deleteAsync(path, { idempotent: true });
        await FileSystem.deleteAsync(path + ".tmp", { idempotent: true });
      }
    }
  });
}
