import { beforeEach, expect, it, vi } from "vitest";
import { request, ApiError } from "./api";
beforeEach(() => {
  sessionStorage.clear();
});
it("surfaces stale revision as recoverable conflict with server explanation", async () => {
  vi.stubGlobal(
    "fetch",
    async () =>
      new Response(JSON.stringify({ detail: "Record changed. Reload." }), {
        status: 409,
      }),
  );
  await expect(request("/products/C14/records/one")).rejects.toMatchObject({
    status: 409,
    message: "Record changed. Reload.",
  });
});
it("normalizes validation arrays into readable errors", async () => {
  vi.stubGlobal(
    "fetch",
    async () =>
      new Response(
        JSON.stringify({
          detail: [{ loc: ["body", "duration"], msg: "Must be at least 90" }],
        }),
        { status: 422 },
      ),
  );
  await expect(request("/products/C14/records")).rejects.toThrow(
    "duration: Must be at least 90",
  );
});
it("expires the session on unauthorized response", async () => {
  sessionStorage.setItem("os-c14-token", "expired");
  vi.stubGlobal(
    "fetch",
    async () =>
      new Response(JSON.stringify({ detail: "Session expired" }), {
        status: 401,
      }),
  );
  await expect(request("/products/C14/records")).rejects.toBeInstanceOf(
    ApiError,
  );
  expect(sessionStorage.getItem("os-c14-token")).toBeNull();
});
