import test from "node:test";
import assert from "node:assert/strict";
import {
  ApiError,
  normalizeError,
  decisionPayload,
  request,
} from "../src/api.ts";
test("normalizes server validation details into readable errors", () => {
  assert.equal(
    normalizeError({
      detail: [{ loc: ["body", "password"], msg: "Too short" }],
    }),
    "password: Too short",
  );
  assert.equal(
    normalizeError({ detail: "Version is stale" }),
    "Version is stale",
  );
});
test("approval binds version and revision and requires explicit reading", () => {
  assert.throws(
    () => decisionPayload(4, 2, "clip-1", "approve", false, ""),
    /read/i,
  );
  assert.deepEqual(decisionPayload(4, 2, "clip-1", "approve", true, ""), {
    revision: 4,
    version: 2,
    clipId: "clip-1",
    read: true,
  });
});
test("change requests require a concrete reason", () => {
  assert.throws(
    () => decisionPayload(4, 2, "clip-1", "changes", false, "  "),
    /feedback/i,
  );
  assert.equal(
    decisionPayload(4, 2, "clip-1", "changes", false, "Fix quote").feedback,
    "Fix quote",
  );
});
test("HTTP conflict throws a typed error without mutating supplied data", async () => {
  const server = await import("node:http");
  const s = server.createServer((_req, res) => {
    res.writeHead(409, { "Content-Type": "application/json" });
    res.end('{"detail":"Refresh this episode"}');
  });
  await new Promise<void>((resolve) => s.listen(0, "127.0.0.1", resolve));
  try {
    const addr = s.address() as { port: number };
    await assert.rejects(
      request(`http://127.0.0.1:${addr.port}`, "token", "/episodes/1"),
      (e: any) =>
        e instanceof ApiError &&
        e.status === 409 &&
        e.message === "Refresh this episode",
    );
  } finally {
    s.close();
  }
});
test("HTTP requests authenticate in headers and send unchanged revision JSON", async () => {
  const server = await import("node:http");
  let received: any;
  const s = server.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    received = {
      url: req.url,
      authorization: req.headers.authorization,
      body: JSON.parse(body),
    };
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end('{"revision":9,"phase":"review"}');
  });
  await new Promise<void>((resolve) => s.listen(0, "127.0.0.1", resolve));
  try {
    const addr = s.address() as { port: number };
    const result = await request<any>(
      `http://127.0.0.1:${addr.port}/`,
      "private-token",
      "/actions/send",
      "POST",
      { revision: 8 },
    );
    assert.equal(result.revision, 9);
    assert.deepEqual(received, {
      url: "/actions/send",
      authorization: "Bearer private-token",
      body: { revision: 8 },
    });
  } finally {
    s.close();
  }
});
test("non-JSON server errors still produce actionable typed errors", async () => {
  const server = await import("node:http");
  const s = server.createServer((_req, res) => {
    res.writeHead(503);
    res.end("Service unavailable");
  });
  await new Promise<void>((resolve) => s.listen(0, "127.0.0.1", resolve));
  try {
    const addr = s.address() as { port: number };
    await assert.rejects(
      request(`http://127.0.0.1:${addr.port}`, null, "/"),
      (e: any) =>
        e instanceof ApiError &&
        e.status === 503 &&
        e.message.includes("try again"),
    );
  } finally {
    s.close();
  }
});
