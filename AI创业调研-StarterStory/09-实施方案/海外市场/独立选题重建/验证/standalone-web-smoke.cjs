// Run from any directory. Requires existing per-project frontend/dist and Python/backend + Playwright dependencies.
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const http = require("node:http");
const net = require("node:net");
const { spawn } = require("node:child_process");
const assert = require("node:assert/strict");
const N = path.resolve(__dirname, "..");
const manifest = JSON.parse(
  fs.readFileSync(path.join(N, "代码迁移清单.json"), "utf8"),
);
const resolveProject = (p) => path.resolve(N, p.path);
const b04 = manifest.find((p) => p.code === "B04");
const reference = path.join(resolveProject(b04), "coding");
const { chromium } = require(
  process.env.PLAYWRIGHT_MODULE ||
    path.join(reference, "tests/node_modules/playwright"),
);
const projects = manifest.filter((p) => p.code !== "B04");
const specs = Object.fromEntries(
  projects.map((p) => [
    p.code,
    JSON.parse(
      fs.readFileSync(
        path.join(
          resolveProject(p),
          "coding/shared/products",
          p.code.toLowerCase() + ".json",
        ),
        "utf8",
      ),
    ),
  ]),
);
const runDir = fs.mkdtempSync(path.join(os.tmpdir(), "standalone-web-smoke-"));
const processes = [],
  servers = [],
  dataDirs = [];
const password = "Product-verification-987";
let browser, context, page, api, session, currentCode, currentEmail;
const errors = [],
  records = {};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function freePort(port) {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  await new Promise((resolve) => server.close(resolve));
}
async function serve(p) {
  await freePort(p.apiPort);
  const coding = path.join(resolveProject(p), "coding");
  const python =
    process.env.PYTHON || path.join(coding, "backend/.venv/bin/python");
  const dist = path.join(coding, "frontend/dist");
  assert(
    fs.existsSync(path.join(dist, "index.html")),
    `${p.code}: run npm run build first`,
  );
  const data = path.join(runDir, p.code, "data");
  dataDirs.push(data);
  fs.mkdirSync(data, { recursive: true });
  const log = fs.openSync(path.join(runDir, p.code, "backend.log"), "a");
  const child = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(p.apiPort),
    ],
    {
      cwd: path.join(coding, "backend"),
      env: { ...process.env, [p.code + "_DATA_DIR"]: data },
      stdio: ["ignore", log, log],
    },
  );
  fs.closeSync(log);
  processes.push(child);
  child.on("error", (e) => errors.push(`${p.code} backend: ${e.message}`));
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null)
      throw Error(`${p.code} backend exited; see ${runDir}`);
    try {
      if ((await fetch(`http://127.0.0.1:${p.apiPort}/api/health`)).ok) {
        ready = true;
        break;
      }
    } catch {}
    await sleep(100);
  }
  assert(ready, `${p.code} backend did not start`);
  await serveStatic(p, dist, p.webPort);
  await serveStatic(p, path.join(coding, "app/dist"), p.appPort);
}
async function serveStatic(p, dist, port) {
  assert(
    fs.existsSync(path.join(dist, "index.html")),
    `${p.code}: missing built dist ${dist}`,
  );
  const server = http.createServer((req, res) => {
    if (req.url.startsWith("/api/")) {
      const upstream = http.request(
        {
          hostname: "127.0.0.1",
          port: p.apiPort,
          path: req.url,
          method: req.method,
          headers: req.headers,
        },
        (response) => {
          res.writeHead(response.statusCode, response.headers);
          response.pipe(res);
        },
      );
      upstream.on("error", (e) => {
        res.writeHead(502);
        res.end(e.message);
      });
      req.pipe(upstream);
      return;
    }
    const pathname = decodeURIComponent(
      new URL(req.url, "http://localhost").pathname,
    );
    const file = path.resolve(dist, "." + pathname);
    if (file !== dist && !file.startsWith(dist + path.sep)) {
      res.writeHead(403);
      res.end();
      return;
    }
    const target =
      fs.existsSync(file) && fs.statSync(file).isFile()
        ? file
        : path.join(dist, "index.html");
    const mime = {
      ".html": "text/html",
      ".js": "text/javascript",
      ".css": "text/css",
      ".json": "application/json",
    };
    res.setHeader(
      "Content-Type",
      mime[path.extname(target)] || "application/octet-stream",
    );
    fs.createReadStream(target).pipe(res);
  });
  servers.push(server);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
}
async function signout() {
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByRole("button", { name: "Sign in", exact: true }).waitFor();
  assert.equal(
    await page.evaluate(
      (code) => sessionStorage.getItem(`os-${code.toLowerCase()}-token`),
      currentCode,
    ),
    null,
  );
}
async function finishProject() {
  if (!currentCode) return;
  const record = records[currentCode];
  await page.reload();
  await page
    .getByRole("heading", { name: specs[currentCode].name, exact: true })
    .waitFor();
  await page
    .getByRole("button", {
      name: new RegExp(record.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
    })
    .click();
  await page
    .getByRole("heading", { name: record.title, exact: true })
    .waitFor();
  const read = await fetch(
    api + `/products/${currentCode}/records/${record.id}`,
    { headers: { Authorization: "Bearer " + session.token } },
  );
  assert.equal(read.status, 200);
  assert.deepEqual(await read.json(), record);
  assert.equal(
    await page.getByRole("navigation", { name: "Projects" }).count(),
    0,
  );
  for (const code of Object.keys(specs).filter((c) => c !== currentCode))
    assert.equal(
      await page
        .getByRole("heading", { name: specs[code].name, exact: true })
        .count(),
      0,
    );
  await page.screenshot({
    path: path.join(runDir, currentCode, "desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    `${currentCode}: mobile overflow`,
  );
  await page.screenshot({
    path: path.join(runDir, currentCode, "mobile.png"),
    fullPage: true,
  });
  await signout();
  await page.setViewportSize({ width: 1440, height: 1050 });
  await mobileSmoke(
    projects.find((p) => p.code === currentCode),
    record,
  );
  console.log(
    currentCode,
    "PASS: register/login, own workflow, reload persistence, no picker, mobile layout, signout",
  );
}
async function mobileSmoke(p, record) {
  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    acceptDownloads: true,
  });
  try {
    const app = await mobile.newPage();
    app.on("pageerror", (e) => errors.push(`${p.code} app: ${e.message}`));
    await app.goto(`http://127.0.0.1:${p.appPort}`);
    await app.getByLabel("Email", { exact: true }).fill(currentEmail);
    await app.getByLabel("Password", { exact: true }).fill(password);
    await app
      .getByRole("button", { name: "Sign in", exact: true })
      .last()
      .click();
    await app
      .getByRole("button", { name: "Refresh records", exact: true })
      .waitFor();
    await app.getByRole("button", { name: record.title, exact: true }).click();
    await app
      .getByText(/^Revision \d+$/)
      .first()
      .waitFor();
    for (const code of Object.keys(specs).filter((c) => c !== p.code))
      assert.equal(
        await app
          .getByRole("button", {
            name: `${code} · ${specs[code].name}`,
            exact: true,
          })
          .count(),
        0,
      );
    assert(
      await app.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `${p.code}: app mobile overflow`,
    );
    await app.screenshot({
      path: path.join(runDir, p.code, "app-mobile.png"),
      fullPage: true,
    });
    await app.getByRole("button", { name: "Sign out", exact: true }).click();
    await app.getByLabel("Email", { exact: true }).waitFor();
    console.log(
      p.code,
      "APP PASS: mobile login, own record read, no picker, signout",
    );
  } finally {
    await mobile.close();
  }
}
async function project(code) {
  await finishProject();
  currentCode = code;
  const p = projects.find((p) => p.code === code);
  api = `http://127.0.0.1:${p.apiPort}/api`;
  await page.goto(`http://127.0.0.1:${p.webPort}`);
  await page
    .getByRole("button", { name: "New here? Create an account", exact: true })
    .click();
  const email = `${code.toLowerCase()}-${Date.now()}@example.test`;
  currentEmail = email;
  await page.getByLabel("Your name", { exact: true }).fill("Product QA");
  await page
    .getByLabel("Workspace name", { exact: true })
    .fill(code + " private workspace");
  await page.getByLabel("Email address", { exact: true }).fill(email);
  await page
    .getByLabel("Password (at least 10 characters)", { exact: true })
    .fill(password);
  const registration = page.waitForResponse(
    (r) =>
      r.url().endsWith("/auth/register") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  const registered = await registration;
  assert.equal(registered.status(), 200, await registered.text());
  await page
    .getByRole("heading", { name: specs[code].name, exact: true })
    .waitFor();
  await signout();
  await page.getByLabel("Email address", { exact: true }).fill(email);
  await page
    .getByLabel("Password (at least 10 characters)", { exact: true })
    .fill(password);
  const login = page.waitForResponse(
    (r) => r.url().endsWith("/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const logged = await login;
  assert.equal(logged.status(), 200, await logged.text());
  session = await logged.json();
  await page
    .getByRole("heading", { name: specs[code].name, exact: true })
    .waitFor();
}
(async () => {
  try {
    for (const p of projects) await serve(p);
    browser = await chromium.launch({
      headless: true,
      channel: process.env.BROWSER_CHANNEL || "chrome",
    });
    context = await browser.newContext({
      viewport: { width: 1440, height: 1050 },
      acceptDownloads: true,
    });
    page = await context.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
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
      await page
        .getByLabel("Choose an action", { exact: true })
        .selectOption(id);
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
    await action("B01", "review-response", {
      ticketId: ticket,
      confirmed: true,
    });
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
    records.B06 = await siteRead.json();
    assert.equal(records.B06.inquiries.length, 1);
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
    fs.writeFileSync(path.join(runDir, "story-proof.pdf"), pdfBytes);
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
    assert.equal(
      records.C15.items.find((i) => i.label === "Socks").quantity,
      6,
    );
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

    await finishProject();
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(runDir, "results.json"),
      JSON.stringify(
        {
          passed: Object.keys(records),
          records: Object.fromEntries(
            Object.entries(records).map(([code, r]) => [
              code,
              { id: r.id, revision: r.revision },
            ]),
          ),
        },
        null,
        2,
      ),
    );
    console.log(
      "PASS: all seven standalone full browser workflows. Evidence:",
      runDir,
    );
  } finally {
    if (browser) await browser.close();
    await Promise.all(
      servers.map((server) => new Promise((resolve) => server.close(resolve))),
    );
    await Promise.all(
      processes.map(async (child) => {
        if (child.exitCode !== null || child.signalCode !== null) return;
        child.kill("SIGTERM");
        await Promise.race([
          new Promise((resolve) => child.once("exit", resolve)),
          sleep(5000),
        ]);
        if (child.exitCode === null && child.signalCode === null) {
          child.kill("SIGKILL");
          await new Promise((resolve) => child.once("exit", resolve));
        }
      }),
    );
    for (const data of dataDirs)
      fs.rmSync(data, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  console.error("Evidence:", runDir);
  process.exitCode = 1;
});
