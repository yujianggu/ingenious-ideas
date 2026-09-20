import test from "node:test";
import assert from "node:assert/strict";
import * as offlineTools from "../src/products/offline.ts";
import { formPayload, initialFields } from "../../shared/product-types.ts";
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
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
