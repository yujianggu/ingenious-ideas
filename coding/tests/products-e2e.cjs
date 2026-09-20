const { chromium } = require("playwright");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const path = require("node:path");
const base = process.env.WEB_URL || "http://127.0.0.1:5173";
const api = process.env.API_URL || "http://127.0.0.1:8000/api";
const password = "Product-verification-987";
const specs = Object.fromEntries(
  ["B01", "B06", "B09", "C04", "C14", "C15", "C17"].map((c) => [
    c,
    JSON.parse(
      fs.readFileSync(
        path.join(__dirname, "../shared/products", c.toLowerCase() + ".json"),
      ),
    ),
  ]),
);
(async () => {
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1050 },
    acceptDownloads: true,
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const email = `products-${Date.now()}@example.com`;
  const registration = await fetch(api + "/auth/register", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: "Product QA",
      email,
      password,
      workspaceName: "Overseas testing",
    }),
  });
  assert.equal(registration.status, 200);
  const session = await registration.json();
  await page.goto(base);
  await page.evaluate(
    (token) => sessionStorage.setItem("episode-token", token),
    session.token,
  );
  await page.reload();
  const records = {};
  async function project(code) {
    await page
      .getByRole("button", { name: new RegExp("^" + code + " ") })
      .click();
    await page
      .getByRole("heading", { name: specs[code].name, exact: true })
      .waitFor();
  }
  async function fill(fields, values) {
    for (const f of fields) {
      if (!(f.name in values)) continue;
      const value = values[f.name];
      const control = page.getByLabel(f.label, { exact: true });
      if (f.type === "checkbox") await control.setChecked(value);
      else if (f.type === "select") await control.selectOption(String(value));
      else if (f.type === "file")
        await page
          .getByLabel(f.label + " file", { exact: true })
          .setInputFiles(value);
      else await control.fill(String(value));
    }
  }
  async function submit(code, id, button) {
    const match = id
      ? `/products/${code}/records/${records[code].id}/actions/${id}`
      : `/products/${code}/records`;
    const pending = page.waitForResponse(
      (r) => r.url().endsWith(match) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: button, exact: true }).click();
    const response = await pending;
    assert.equal(response.status(), 200, await response.text());
    records[code] = await response.json();
    console.log(code, id || "created", "revision", records[code].revision);
    return records[code];
  }
  async function create(code, values) {
    await project(code);
    await page
      .getByRole("button", {
        name: "+ New " + specs[code].recordLabel.toLowerCase(),
        exact: true,
      })
      .click();
    await fill(specs[code].create, values);
    return submit(code, null, "Create");
  }
  async function action(code, id, values = {}) {
    const spec = specs[code].actions.find((a) => a.id === id);
    await page.getByLabel("Choose an action", { exact: true }).selectOption(id);
    await fill(spec.fields, values);
    return submit(code, id, spec.label);
  }
  await create("B01", {
    title: "Refund support",
    escalationQueue: "Support owner",
  });
  await action("B01", "add-source", {
    title: "Refund policy",
    url: "https://example.com/refunds",
    content: "Customers can request a refund within 30 days of purchase.",
    authorized: true,
  });
  await action("B01", "add-ticket", {
    question: "How many days can customers request a refund?",
    customerLabel: "Order QA-1",
  });
  const ticket = records.B01.tickets[0].id;
  await action("B01", "draft-response", { ticketId: ticket });
  await action("B01", "review-response", { ticketId: ticket, confirmed: true });
  assert.equal(records.B01.tickets[0].status, "approved");
  await create("B09", {
    title: "Acme research",
    companyName: "Acme Logistics",
    website: "https://example.com",
    icp: "logistics, warehouse",
    offer: "warehouse planning workshops",
  });
  await action("B09", "add-source", {
    title: "Company overview",
    url: "https://example.com/about",
    content: "Acme operates three logistics warehouses in Boston.",
  });
  await action("B09", "add-fact", {
    sourceId: records.B09.sources[0].id,
    category: "operations",
    statement: "Acme operates three logistics warehouses in Boston.",
    quote: "Acme operates three logistics warehouses in Boston.",
  });
  await action("B09", "verify-fact", {
    factId: records.B09.facts[0].id,
    confirmed: true,
  });
  await action("B09", "review-research", {
    fit: "good",
    notes: "Published warehouse operations match our offer.",
    confirmed: true,
  });
  await action("B09", "draft-email");
  await action("B09", "review-email", { confirmed: true });
  await create("B06", {
    title: "Bright Cleaning",
    tagline: "A welcoming home",
    about: "A local cleaning team for homes and apartments.",
    email: "hello@example.com",
    phone: "+1 212 555 0100",
    areas: "Brooklyn\nQueens",
    hours: "Monday to Friday",
  });
  await action("B06", "add-service", {
    title: "Home cleaning",
    description: "Kitchen and bathroom care.",
    priceNote: "Request a quote",
  });
  await action("B06", "publish", { publicConsent: true });
  await action("B06", "update-site", { tagline: "A bright new day" });
  assert.equal(
    await page.getByLabel("Homepage headline", { exact: true }).inputValue(),
    "A bright new day",
  );
  await action("B06", "update-site", { phone: "+1 212 555 0101" });
  assert.equal(records.B06.site.tagline, "A bright new day");
  const publicPage = await context.newPage();
  await publicPage.goto(
    api.replace(/\/api$/, "") + "/api/public/sites/" + records.B06.id,
  );
  await publicPage
    .getByRole("heading", { name: "A welcoming home", exact: true })
    .waitFor();
  const publicHtml = await publicPage.content();
  assert(!publicHtml.includes("A bright new day"));
  await publicPage
    .getByLabel("Your name", { exact: true })
    .fill("Taylor Client");
  await publicPage
    .getByLabel("Email", { exact: true })
    .fill("taylor@example.com");
  await publicPage
    .getByLabel("Service", { exact: true })
    .selectOption(records.B06.site.services[0].id);
  await publicPage
    .getByLabel("Service area", { exact: true })
    .selectOption("Queens");
  await publicPage
    .getByLabel("What can we help with?", { exact: true })
    .fill("Please quote a weekly clean.");
  await publicPage.getByRole("checkbox").check();
  await publicPage
    .getByRole("button", { name: "Request a quote", exact: true })
    .click();
  await publicPage.getByRole("heading", { name: "Thank you" }).waitFor();
  await publicPage.close();
  const siteRead = await fetch(
    api + "/products/B06/records/" + records.B06.id,
    { headers: { Authorization: "Bearer " + session.token } },
  );
  assert.equal((await siteRead.json()).inquiries.length, 1);
  await create("C04", {
    title: "Mia's adventure",
    childName: "Mia",
    companion: "fox",
    setting: "garden",
    dedication: "For Mia, with love.",
  });
  await action("C04", "approve-proof", {
    proofVersion: 1,
    approverName: "Alex Reader",
    adult: true,
    reviewedAllPages: true,
  });
  const pdf = await fetch(
    api + "/products/C04/records/" + records.C04.id + "/export/pdf",
    { headers: { Authorization: "Bearer " + session.token } },
  );
  assert.equal(pdf.status, 200);
  const pdfBytes = Buffer.from(await pdf.arrayBuffer());
  assert(pdfBytes.subarray(0, 5).equals(Buffer.from("%PDF-")));
  fs.writeFileSync("/tmp/overseas-story-proof.pdf", pdfBytes);
  await create("C14", {
    title: "Tokyo family trip",
    destination: "Tokyo",
    timezone: "Asia/Tokyo",
    notes: "Bring original booking receipts.",
  });
  await action("C14", "preview-import", {
    format: "ics",
    content:
      "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:qa-hotel\r\nSUMMARY:Hotel check-in\r\nDTSTART;TZID=Asia/Tokyo:20261001T150000\r\nDTEND;TZID=Asia/Tokyo:20261001T160000\r\nLOCATION:Tokyo Station Hotel\r\nEND:VEVENT\r\nEND:VCALENDAR",
  });
  assert.equal(records.C14.importPreview.entries.length, 1);
  await action("C14", "commit-import");
  await action("C14", "add-attachment", {
    file: {
      name: "booking-proof.pdf",
      mimeType: "application/pdf",
      buffer: pdfBytes,
    },
  });
  assert.equal(records.C14.attachments.length, 1);
  await create("C15", { title: "Family packing" });
  await action("C15", "add-module", { title: "Daily essentials" });
  const mod = records.C15.modules[0].id;
  await action("C15", "add-module-item", {
    moduleId: mod,
    label: "Socks",
    quantity: 1,
    rule: "person-day",
  });
  await action("C15", "add-module-item", {
    moduleId: mod,
    label: "Passport",
    quantity: 1,
    rule: "person",
  });
  await action("C15", "new-trip", {
    title: "Tokyo weekend",
    people: 3,
    days: 2,
  });
  assert.equal(records.C15.items.find((i) => i.label === "Socks").quantity, 6);
  await create("C17", { title: "Mixed collection" });
  await action("C17", "add-entry", {
    type: "magazine",
    title: "Field Notes",
    volume: 2,
    issue: 1,
    location: "Box A",
  });
  await action("C17", "add-entry", {
    type: "magazine",
    title: "Field Notes",
    volume: 2,
    issue: 3,
    location: "Box B",
  });
  await action("C17", "find-gaps", {
    title: "Field Notes",
    type: "magazine",
    volume: 2,
    fromIssue: 1,
    toIssue: 3,
  });
  await page.screenshot({
    path: "/tmp/overseas-products-web.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "/tmp/overseas-products-web-mobile.png",
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
    "mobile layout overflow",
  );
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    "/tmp/overseas-products-seed.json",
    JSON.stringify({ email, password, ...session, records }),
  );
  await browser.close();
  console.log(
    "PASS: seven project browser workflows, B06 live inquiry + publication snapshot, sequential edits, C04 PDF, C14 original document, C15 quantity rules, C17 gaps, 390px layout.",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
