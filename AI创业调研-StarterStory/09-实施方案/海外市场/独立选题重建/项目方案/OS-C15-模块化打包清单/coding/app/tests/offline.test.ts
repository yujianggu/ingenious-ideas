import test from "node:test";
import assert from "node:assert/strict";
import * as offlineTools from "../src/products/offline.ts";
const { queueCheck, acknowledgeCheck, assertUnchanged } = offlineTools;
import { formPayload, initialFields } from "../../shared/product-types.ts";
const record: any = {
  id: "r1",
  code: "C15",
  title: "Trip",
  revision: 4,
  items: [
    { id: "a", label: "Socks", checked: false },
    { id: "b", label: "Passport", checked: false },
  ],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
test("offline checks collapse repeat edits and preserve original revision", () => {
  let snapshot = { records: [record], pending: [], savedAt: "today" } as any;
  snapshot = queueCheck(snapshot, "r1", "a", true);
  snapshot = queueCheck(snapshot, "r1", "a", false);
  snapshot = queueCheck(snapshot, "r1", "b", true);
  assert.equal(snapshot.pending.length, 2);
  assert.equal(snapshot.pending[0].revision, 4);
  assert.equal(snapshot.records[0].items[1].checked, true);
  assert.equal(record.items[1].checked, false);
});
test("sync advances revisions one command at a time without losing remaining local checks", () => {
  let s: any = queueCheck(
    { records: [record], pending: [], savedAt: "today" },
    "r1",
    "a",
    true,
  );
  s = queueCheck(s, "r1", "b", true);
  const first = s.pending[0];
  assertUnchanged(first, record);
  s = acknowledgeCheck(s, first, {
    ...record,
    revision: 5,
    items: [{ ...record.items[0], checked: true }, record.items[1]],
  });
  assert.equal(s.pending.length, 1);
  assert.equal(s.pending[0].revision, 5);
  assert.equal(s.records[0].items[1].checked, true);
  assert.throws(
    () => assertUnchanged(s.pending[0], { ...record, revision: 6 }),
    /Cloud data changed/,
  );
});
test("forms keep false and numeric zero, reject invalid JSON and missing confirmation", () => {
  assert.deepEqual(
    formPayload(
      [
        { name: "n", label: "Count", type: "number", min: 0 },
        { name: "b", label: "Toggle", type: "checkbox" },
      ],
      { n: "0", b: false },
    ),
    { n: 0, b: false },
  );
  assert.throws(
    () =>
      formPayload(
        [
          {
            name: "agree",
            label: "Approval",
            type: "checkbox",
            required: true,
          },
        ],
        { agree: false },
      ),
    /required/,
  );
  assert.throws(
    () =>
      formPayload([{ name: "data", label: "Backup", type: "json" }], {
        data: "{no",
      }),
    /valid JSON/,
  );
  assert.deepEqual(
    initialFields([{ name: "n", label: "Count", type: "number", default: 0 }]),
    { n: 0 },
  );
});
test("local packing status follows all queued checks and acknowledgement overlays", () => {
  const trip = {
    ...record,
    currentTrip: { id: "trip-1", title: "Weekend", status: "packing" },
  };
  let snapshot: any = queueCheck(
    { records: [trip], pending: [], savedAt: "today" },
    "r1",
    "a",
    true,
  );
  snapshot = queueCheck(snapshot, "r1", "b", true);
  assert.equal(snapshot.records[0].currentTrip.status, "packed");
  const first = snapshot.pending[0];
  snapshot = acknowledgeCheck(snapshot, first, {
    ...trip,
    revision: 5,
    items: [{ ...trip.items[0], checked: true }, trip.items[1]],
  });
  assert.equal(snapshot.records[0].currentTrip.status, "packed");
  snapshot = queueCheck(snapshot, "r1", "a", false);
  assert.equal(snapshot.records[0].currentTrip.status, "packing");
  assert.equal(trip.currentTrip.status, "packing");
});
test("online backup includes pending checks rather than an older cloud record", () => {
  assert.equal(
    typeof offlineTools.localBackupRecord,
    "function",
    "local backup selection is missing",
  );
  const snapshot = queueCheck(
    { records: [record], pending: [], savedAt: "today" },
    "r1",
    "a",
    true,
  );
  const backup = offlineTools.localBackupRecord(false, record, snapshot);
  assert.equal(backup?.items[0].checked, true);
  assert.equal(
    offlineTools.localBackupRecord(false, record, { ...snapshot, pending: [] }),
    null,
  );
});
test("a cancelled sync cannot persist its late POST or submit remaining checks", async () => {
  assert.equal(
    typeof offlineTools.syncPendingChecks,
    "function",
    "cancellable sync is missing",
  );
  const response = deferred<any>(),
    started = deferred<void>();
  let active = true,
    posts = 0;
  const writes: any[] = [];
  const snapshot = queueCheck(
    queueCheck(
      { records: [record], pending: [], savedAt: "today" },
      "r1",
      "a",
      true,
    ),
    "r1",
    "b",
    true,
  );
  const syncing = offlineTools.syncPendingChecks(
    snapshot,
    {
      get: async () => record,
      post: async () => {
        posts++;
        started.resolve();
        return response.promise;
      },
      persist: async (value) => {
        writes.push(value);
      },
    },
    () => active,
  );
  await started.promise;
  active = false;
  response.resolve({
    ...record,
    revision: 5,
    items: [{ ...record.items[0], checked: true }, record.items[1]],
  });
  await assert.rejects(syncing, /no longer active/i);
  assert.equal(posts, 1);
  assert.deepEqual(writes, []);
  assert.equal(snapshot.pending.length, 2);
});
test("a cancelled GET cannot start a packing mutation", async () => {
  assert.equal(
    typeof offlineTools.syncPendingChecks,
    "function",
    "cancellable sync is missing",
  );
  const response = deferred<any>();
  let active = true,
    posts = 0;
  const snapshot = queueCheck(
    { records: [record], pending: [], savedAt: "today" },
    "r1",
    "a",
    true,
  );
  const syncing = offlineTools.syncPendingChecks(
    snapshot,
    {
      get: () => response.promise,
      post: async () => {
        posts++;
        return record;
      },
      persist: async () => {},
    },
    () => active,
  );
  active = false;
  response.resolve(record);
  await assert.rejects(syncing, /no longer active/i);
  assert.equal(posts, 0);
});
test("account invalidation prevents a queued stale save from recreating a purged cache", async () => {
  assert.equal(
    typeof offlineTools.createGeneration,
    "function",
    "account cache generation is missing",
  );
  assert.equal(
    typeof offlineTools.createGuardedQueue,
    "function",
    "guarded persistence queue is missing",
  );
  const generation = offlineTools.createGeneration(),
    serial = offlineTools.createGuardedQueue(),
    pending = deferred<void>(),
    started = deferred<void>();
  const old = generation.capture();
  let stored = "old";
  const blocking = serial(async () => {
    started.resolve();
    await pending.promise;
  });
  await started.promise;
  generation.invalidate();
  const purging = serial(async () => {
    stored = "";
  });
  const obsolete = serial(async () => {
    stored = "stale";
  }, old);
  const rejected = assert.rejects(obsolete, /no longer active/i);
  pending.resolve();
  await blocking;
  await purging;
  await rejected;
  assert.equal(stored, "");
  await serial(async () => {
    stored = "new account session";
  }, generation.capture());
  assert.equal(stored, "new account session");
});
test("successful sync persists each acknowledged revision and keeps the remaining overlay", async () => {
  const snapshot = queueCheck(
    queueCheck(
      {
        records: [{ ...record, currentTrip: { status: "packing" } }],
        pending: [],
        savedAt: "today",
      },
      "r1",
      "a",
      true,
    ),
    "r1",
    "b",
    true,
  );
  let server = snapshot.records[0];
  server = { ...server, items: record.items };
  const posted: any[] = [],
    saved: any[] = [];
  const synced = await offlineTools.syncPendingChecks(
    snapshot,
    {
      get: async () => server,
      post: async (command) => {
        posted.push({ ...command });
        server = {
          ...server,
          revision: server.revision + 1,
          items: server.items.map((item: any) =>
            item.id === command.itemId
              ? { ...item, checked: command.checked }
              : item,
          ),
        };
        return server;
      },
      persist: async (value) => {
        saved.push(value);
      },
    },
    () => true,
  );
  assert.deepEqual(
    posted.map((c) => [c.itemId, c.revision]),
    [
      ["a", 4],
      ["b", 5],
    ],
  );
  assert.equal(saved[0].pending.length, 1);
  assert.equal(saved[0].records[0].items[1].checked, true);
  assert.equal(synced.pending.length, 0);
  assert.equal(synced.records[0].revision, 6);
  assert.equal(synced.records[0].currentTrip.status, "packed");
});
test("expired startup can purge the saved account before its profile is forgotten", async () => {
  assert.equal(
    typeof offlineTools.purgeRememberedAccount,
    "function",
    "saved-account cleanup is missing",
  );
  let profile: string | null = JSON.stringify({
    base: "https://studio.example/api",
    user: { id: "previous-user" },
  });
  const disk = new Map([
    ["https://studio.example/api:previous-user", "private packing list"],
  ]);
  await offlineTools.purgeRememberedAccount(
    async () => profile,
    async (base, user) => {
      disk.delete(base + ":" + user);
    },
  );
  profile = null;
  assert.equal(disk.size, 0);
  assert.equal(profile, null);
  await offlineTools.purgeRememberedAccount(
    async () => "{broken",
    async () => {
      assert.fail("Invalid profiles must not select another account");
    },
  );
});
