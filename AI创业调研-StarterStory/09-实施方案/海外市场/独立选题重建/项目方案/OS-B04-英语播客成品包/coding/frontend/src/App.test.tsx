import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import { DirtyProvider } from "./dirty";
import type { Episode } from "./types";
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});
it("blocks submitting unsaved brief and reloads actual server fields after a conflict", async () => {
  const episode: Episode = {
    id: "e1",
    title: "Original episode",
    brand: "Show",
    source: "recording.mp4",
    duration: 900,
    notes: "",
    glossary: "",
    prohibitedClaims: "None",
    rights: true,
    clientId: "c1",
    phase: "draft",
    version: 1,
    revision: 1,
    createdAt: "2026-09-20T00:00:00Z",
    updatedAt: "2026-09-20T00:00:00Z",
    readyAt: null,
    dueAt: null,
    clips: [],
    chapters: "",
    history: [],
    events: [],
    assets: [],
  };
  let latest = episode;
  sessionStorage.setItem("episode-token", "valid");
  vi.stubGlobal("fetch", async (input: string, options: RequestInit = {}) => {
    if (input === "/api/auth/me")
      return Response.json({
        id: "c1",
        name: "Client",
        email: "client@example.com",
        role: "client",
        workspaceId: "w1",
      });
    if (input === "/api/episodes")
      return Response.json({ episodes: [episode] });
    if (input === "/api/episodes/e1") return Response.json(latest);
    if (input === "/api/episodes/e1/brief" && options.method === "PUT") {
      latest = { ...episode, title: "Updated on another device", revision: 2 };
      return Response.json({ detail: "Newer version exists" }, { status: 409 });
    }
    throw Error(`Unexpected endpoint ${input}`);
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <DirtyProvider>
      <App />
    </DirtyProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /Original episode/ }),
  );
  const title = await screen.findByLabelText("Episode title");
  fireEvent.change(title, { target: { value: "Unsaved title" } });
  expect(
    (screen.getByRole("button", { name: "Submit brief" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save brief" }));
  await screen.findByText("Newer version exists");
  expect((title as HTMLInputElement).value).toBe("Unsaved title");
  fireEvent.click(screen.getByRole("button", { name: "Reload latest" }));
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Episode title") as HTMLInputElement).value,
    ).toBe("Updated on another device"),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Submit brief",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
});
it("opens an invitation even when this tab already has a studio session", async () => {
  sessionStorage.setItem("episode-token", "editor-session");
  history.replaceState(null, "", "/join?token=private-invite");
  vi.stubGlobal("fetch", async (input: string) =>
    Response.json(
      input === "/api/auth/me"
        ? {
            id: "editor",
            name: "Editor",
            email: "editor@example.com",
            role: "editor",
            workspaceId: "w1",
          }
        : input === "/api/members"
          ? { members: [] }
          : { episodes: [] },
    ),
  );
  render(
    <DirtyProvider>
      <App />
    </DirtyProvider>,
  );
  expect(
    await screen.findByRole("heading", { name: "Join your studio" }),
  ).toBeTruthy();
  history.replaceState(null, "", "/");
});
