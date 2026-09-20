import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import Products, { ProductForm } from "./Products";
import { DirtyProvider } from "../dirty";
const saved: any = {
  id: "packing1",
  code: "C15",
  title: "Weekend travel",
  revision: 1,
  modules: [],
  items: [],
  currentTrip: null,
  history: [],
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});
it("keeps unsaved action fields on conflict and reloads only after explicit discard", async () => {
  let latest = saved;
  vi.stubGlobal("fetch", async (input: string, options: RequestInit = {}) => {
    if (input === "/api/products/C15/records")
      return Response.json({ records: [latest] });
    if (input.endsWith("/actions/add-module")) {
      latest = { ...saved, revision: 2 };
      return Response.json({ detail: "This record changed." }, { status: 409 });
    }
    throw Error("Unexpected request " + input);
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(
    <DirtyProvider>
      <Products code="C15" />
    </DirtyProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /Weekend travel/ }),
  );
  fireEvent.change(screen.getByLabelText("Choose an action"), {
    target: { value: "add-module" },
  });
  const field = screen.getByLabelText("Module name");
  fireEvent.change(field, { target: { value: "Keep my draft" } });
  fireEvent.click(
    screen.getByRole("button", { name: "Create reusable module" }),
  );
  await screen.findAllByText("This record changed.");
  expect((field as HTMLInputElement).value).toBe("Keep my draft");
  fireEvent.click(screen.getByRole("button", { name: "Reload latest data" }));
  expect(confirm).toHaveBeenCalled();
  expect((field as HTMLInputElement).value).toBe("Keep my draft");
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Reload latest data" }));
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Module name") as HTMLInputElement).value,
    ).toBe(""),
  );
  expect(screen.getAllByText("Revision 2").length).toBeGreaterThan(0);
});
it("prepopulates selected item fields and submits validated typed values", async () => {
  const submit = vi.fn().mockResolvedValue(undefined);
  render(
    <DirtyProvider>
      <ProductForm
        label="Save item"
        record={{
          ...saved,
          items: [{ id: "x", title: "A", description: "Original" }],
        }}
        fields={[
          { name: "id", label: "Item", type: "select", optionsPath: "items" },
          {
            name: "description",
            label: "Description",
            type: "textarea",
            selectedFrom: {
              selector: "id",
              path: "items",
              value: "description",
            },
          },
          {
            name: "quantity",
            label: "Count",
            type: "number",
            default: 0,
            min: 0,
          },
        ]}
        onSubmit={submit}
      />
    </DirtyProvider>,
  );
  fireEvent.change(screen.getByLabelText("Item"), { target: { value: "x" } });
  expect(
    (screen.getByLabelText("Description") as HTMLTextAreaElement).value,
  ).toBe("Original");
  fireEvent.click(screen.getByRole("button", { name: "Save item" }));
  await waitFor(() =>
    expect(submit).toHaveBeenCalledWith({
      id: "x",
      description: "Original",
      quantity: 0,
    }),
  );
});
