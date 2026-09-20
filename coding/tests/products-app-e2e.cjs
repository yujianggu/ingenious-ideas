const { chromium } = require("playwright");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const seed = JSON.parse(fs.readFileSync("/tmp/overseas-products-seed.json"));
const base = process.env.APP_URL || "http://127.0.0.1:8081";
const api = process.env.API_URL || "http://127.0.0.1:8000/api";
(async () => {
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    acceptDownloads: true,
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(base);
  await page.getByLabel("Email", { exact: true }).fill(seed.email);
  await page
    .getByLabel("Password (10+ characters)", { exact: true })
    .fill(seed.password);
  await page
    .getByRole("button", { name: "Sign in", exact: true })
    .last()
    .click();
  await page
    .getByRole("button", { name: "C15 · Pack Modules", exact: true })
    .waitFor();
  async function project(code, name) {
    await page
      .getByRole("button", { name: `${code} · ${name}`, exact: true })
      .click();
    await page
      .getByRole("button", { name: "Refresh records", exact: true })
      .waitFor();
  }
  for (const [code, name] of [
    ["B01", "Answer Ledger"],
    ["B06", "Local Clean Pages"],
    ["B09", "Company Brief"],
    ["C04", "Story Keepsake"],
    ["C17", "Shelf Catalog"],
  ]) {
    await project(code, name);
    await page
      .getByRole("button", { name: seed.records[code].title, exact: true })
      .click();
    await page
      .getByText(/^Revision \d+$/)
      .first()
      .waitFor();
  }
  await project("C14", "Travel Dossier");
  await page
    .getByRole("button", { name: "Save for offline use", exact: true })
    .click();
  await page
    .getByText(
      "Saved on this device, including original documents. Available offline.",
      { exact: true },
    )
    .waitFor();
  await page
    .getByRole("button", { name: "Tokyo family trip", exact: true })
    .click();
  await context.setOffline(true);
  const pdfDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Open booking-proof.pdf", exact: true })
    .click();
  const pdf = await pdfDownload;
  const stream = await pdf.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  assert(
    Buffer.concat(chunks).equals(
      fs.readFileSync("/tmp/overseas-story-proof.pdf"),
    ),
    "offline original bytes differ",
  );
  console.log("C14 original PDF works offline");
  await context.setOffline(false);
  await project("C15", "Pack Modules");
  await page
    .getByRole("button", { name: "Save for offline use", exact: true })
    .click();
  await page
    .getByText(
      "Saved on this device, including original documents. Available offline.",
      { exact: true },
    )
    .waitFor();
  await page
    .getByRole("button", { name: "Family packing", exact: true })
    .click();
  await context.setOffline(true);
  await page.getByRole("switch", { name: "Pack Socks", exact: true }).click();
  await page
    .getByRole("button", { name: "Sync 1 local checks", exact: true })
    .waitFor();
  await page
    .getByRole("switch", { name: "Pack Passport", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Sync 2 local checks", exact: true })
    .waitFor();
  await context.setOffline(false);
  await project("C17", "Shelf Catalog");
  await project("C15", "Pack Modules");
  await page
    .getByRole("button", { name: "Family packing", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Sync 2 local checks", exact: true })
    .waitFor();
  const backupDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download backup", exact: true })
    .click();
  const backup = await backupDownload;
  const backupStream = await backup.createReadStream();
  const backupChunks = [];
  for await (const chunk of backupStream) backupChunks.push(chunk);
  const local = JSON.parse(Buffer.concat(backupChunks).toString());
  assert(
    local.items.every((i) => i.checked),
    "online backup omitted local changes",
  );
  assert.equal(local.currentTrip.status, "packed");
  await page
    .getByRole("button", { name: "Sync 2 local checks", exact: true })
    .click();
  await page.getByText("All local checks synced.", { exact: true }).waitFor();
  const read = await fetch(
    api + "/products/C15/records/" + seed.records.C15.id,
    { headers: { Authorization: "Bearer " + seed.token } },
  );
  const cloud = await read.json();
  assert(cloud.items.every((i) => i.checked));
  assert.equal(cloud.currentTrip.status, "packed");
  console.log("C15 offline queue, reopen, local backup and sync pass");
  // Real server conflict must preserve pending local intent.
  await context.setOffline(true);
  await page.getByRole("switch", { name: "Pack Socks", exact: true }).click();
  await page
    .getByRole("button", { name: "Sync 1 local checks", exact: true })
    .waitFor();
  await context.setOffline(false);
  const serverEdit = await fetch(
    api + "/products/C15/records/" + cloud.id + "/actions/toggle-item",
    {
      method: "POST",
      headers: {
        Authorization: "Bearer " + seed.token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        revision: cloud.revision,
        itemId: cloud.items.find((i) => i.label === "Passport").id,
        checked: false,
      }),
    },
  );
  assert.equal(serverEdit.status, 200);
  await page
    .getByRole("button", { name: "Sync 1 local checks", exact: true })
    .click();
  await page
    .getByText(/Cloud data changed\. Your local checks are preserved/)
    .waitFor();
  assert.equal(
    await page
      .getByRole("switch", { name: "Pack Socks", exact: true })
      .isChecked(),
    false,
  );
  await page.screenshot({
    path: "/tmp/overseas-products-app.png",
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .getByRole("button", {
      name: "Review cloud and discard local checks",
      exact: true,
    })
    .click();
  await page
    .getByRole("button", { name: "Sync 1 local checks", exact: true })
    .waitFor({ state: "hidden" });
  // Network-down cold session restoration: application bundle remains available, API unavailable.
  await page.route("**/api/**", (route) => route.abort());
  await page.reload();
  await page
    .getByRole("button", { name: "C15 · Pack Modules", exact: true })
    .waitFor();
  await project("C15", "Pack Modules");
  await page
    .getByRole("button", { name: "Family packing", exact: true })
    .click();
  await page.getByRole("switch", { name: "Pack Socks", exact: true }).waitFor();
  await page.unroute("**/api/**");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByLabel("Email", { exact: true }).waitFor();
  const cacheKeys = await page.evaluate(
    () =>
      new Promise((resolve) => {
        const request = indexedDB.open("overseas-offline", 1);
        request.onsuccess = () => {
          const db = request.result;
          const tx = db.transaction("snapshots", "readonly");
          const keys = tx.objectStore("snapshots").getAllKeys();
          keys.onsuccess = () => resolve(keys.result);
        };
      }),
  );
  assert.equal(cacheKeys.length, 0, "logout left offline account data");
  assert.deepEqual(errors, []);
  await browser.close();
  console.log(
    "PASS: mobile seven project entry points, offline original PDF, packing sync/conflict, local backup, cached session restore, logout purge, 390px layout.",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
