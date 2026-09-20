import test from "node:test";
import assert from "node:assert/strict";
import { DirtyRegistry, formIdentity } from "../src/drafts.ts";
test("saving one entity preserves sibling dirty state and blocks submission", () => {
  const drafts = new DirtyRegistry();
  drafts.mark("clip-1", true);
  drafts.mark("chapters", true);
  drafts.mark("clip-1", false);
  assert.equal(drafts.hasChanges, true);
  assert.deepEqual(drafts.entities, ["chapters"]);
  drafts.mark("chapters", false);
  assert.equal(drafts.hasChanges, false);
});
test("routine revisions retain editor identity; explicit reload and version changes replace it", () => {
  const first = formIdentity({ id: "a", version: 1, revision: 2 }, 0, "clip-1");
  assert.equal(
    first,
    formIdentity({ id: "a", version: 1, revision: 3 }, 0, "clip-1"),
  );
  assert.notEqual(
    first,
    formIdentity({ id: "a", version: 1, revision: 3 }, 1, "clip-1"),
  );
  assert.notEqual(
    first,
    formIdentity({ id: "a", version: 2, revision: 3 }, 0, "clip-1"),
  );
});
test("cancelled discard keeps all drafts and successful discard clears registry", () => {
  const drafts = new DirtyRegistry();
  drafts.mark("brief", true);
  assert.equal(drafts.discard(false), false);
  assert.equal(drafts.hasChanges, true);
  assert.equal(drafts.discard(true), true);
  assert.equal(drafts.hasChanges, false);
});
import { accountBoundary } from "../src/account.ts";
test("expiration and account replacement remove all previous account data", () => {
  const accountA = {
    episode: { id: "private-a" },
    episodes: [{ id: "private-a" }],
    members: [{ id: "client-a" }],
    invite: "secret-a",
    inviteResult: "invite-a",
    creating: true,
  };
  for (const event of ["expired", "authenticated"] as const) {
    const next = accountBoundary(accountA, event);
    assert.deepEqual(next, {
      episode: null,
      episodes: [],
      members: [],
      invite: "",
      inviteResult: "",
      creating: false,
    });
    assert.equal(JSON.stringify(next).includes("private-a"), false);
  }
});
