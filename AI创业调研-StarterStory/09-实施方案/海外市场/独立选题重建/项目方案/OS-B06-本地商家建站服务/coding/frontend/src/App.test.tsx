import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import App from "./App";
import { DirtyProvider } from "./dirty";
import { product, products } from "../../shared/products";
import { TOKEN_KEY } from "./api";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});
const user = {
  id: "member",
  name: "Private member",
  email: "member@example.test",
};
function mount() {
  render(
    <DirtyProvider>
      <App />
    </DirtyProvider>,
  );
}
it("signs in directly to this project's private workspace without a project picker", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (input: string, init: RequestInit = {}) => {
    calls.push(input);
    if (input === "/api/auth/login") {
      expect(JSON.parse(String(init.body))).toEqual({
        email: "member@example.test",
        password: "Long-password-123",
      });
      return Response.json({ token: "own-session", user });
    }
    if (input === `/api/products/${product.code}/records`) {
      expect(new Headers(init.headers).get("Authorization")).toBe(
        "Bearer own-session",
      );
      return Response.json({ records: [] });
    }
    throw Error("Unexpected API: " + input);
  });
  mount();
  fireEvent.change(await screen.findByLabelText("Email address"), {
    target: { value: "member@example.test" },
  });
  fireEvent.change(screen.getByLabelText("Password (at least 10 characters)"), {
    target: { value: "Long-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByRole("button", {
    name: `+ New ${product.recordLabel.toLowerCase()}`,
  });
  expect(products.map((p) => p.code)).toEqual([product.code]);
  expect(sessionStorage.getItem(TOKEN_KEY)).toBe("own-session");
  expect(screen.queryByRole("navigation", { name: "Projects" })).toBeNull();
  expect(
    screen.queryByText(/Invite a client|Episode Desk|Overseas Studio/),
  ).toBeNull();
  await waitFor(() =>
    expect(calls).toEqual([
      "/api/auth/login",
      `/api/products/${product.code}/records`,
    ]),
  );
});
it("registers a private account using the auth contract", async () => {
  vi.stubGlobal("fetch", async (input: string, init: RequestInit = {}) => {
    if (input === "/api/auth/register") {
      expect(JSON.parse(String(init.body))).toMatchObject({
        name: "Member",
        workspaceName: "Private workspace",
        email: "member@example.test",
      });
      return Response.json({ token: "registered", user });
    }
    return Response.json({ records: [] });
  });
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "New here? Create an account" }),
  );
  for (const [label, value] of [
    ["Your name", "Member"],
    ["Workspace name", "Private workspace"],
    ["Email address", "member@example.test"],
    ["Password (at least 10 characters)", "Long-password-123"],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  await screen.findByText("Private account");
  expect(sessionStorage.getItem(TOKEN_KEY)).toBe("registered");
});
it("guards unsaved work on logout and clears only its own token after confirmation", async () => {
  sessionStorage.setItem(TOKEN_KEY, "session");
  sessionStorage.setItem("another-project-token", "untouched");
  const fetcher = vi.fn(async (input: string) =>
    input === "/api/auth/me"
      ? Response.json(user)
      : Response.json({ records: [] }),
  );
  vi.stubGlobal("fetch", fetcher);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  mount();
  fireEvent.click(
    await screen.findByRole("button", {
      name: `+ New ${product.recordLabel.toLowerCase()}`,
    }),
  );
  const field = product.create.find((f) => f.type === "text")!;
  fireEvent.change(screen.getByLabelText(field.label), {
    target: { value: "Unsaved draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await screen.findByText(/Sign out cancelled/);
  expect(confirm).toHaveBeenCalled();
  expect(sessionStorage.getItem(TOKEN_KEY)).toBe("session");
  expect((screen.getByLabelText(field.label) as HTMLInputElement).value).toBe(
    "Unsaved draft",
  );
  expect(fetcher.mock.calls.some(([path]) => path === "/api/auth/logout")).toBe(
    false,
  );
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await screen.findByRole("button", { name: "Sign in" });
  expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  expect(sessionStorage.getItem("another-project-token")).toBe("untouched");
});
it("returns to sign in after a 401 from this project's API", async () => {
  sessionStorage.setItem(TOKEN_KEY, "session");
  vi.stubGlobal("fetch", async (input: string) =>
    input === "/api/auth/me"
      ? Response.json(user)
      : Response.json({ detail: "Session expired" }, { status: 401 }),
  );
  mount();
  await screen.findByRole("button", { name: "Sign in" });
  expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  expect(screen.getByRole("alert").textContent).toMatch(/expired/);
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull(),
  );
});
