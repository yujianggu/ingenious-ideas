import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AsyncForm } from "./forms";
afterEach(cleanup);
it("preserves unsaved input and offers reload after a failed conflict", async () => {
  render(
    <AsyncForm
      submit={async () => {
        throw Object.assign(new Error("Newer version exists"), { status: 409 });
      }}
      reload={() => {}}
    >
      <label>
        Title
        <input name="title" defaultValue="" />
      </label>
      <button>Save</button>
    </AsyncForm>,
  );
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "My unsaved episode" },
  });
  fireEvent.click(screen.getByText("Save"));
  await screen.findByRole("alert");
  expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe(
    "My unsaved episode",
  );
  expect(screen.getByText("Reload latest")).toBeTruthy();
});
it("disables duplicate submit until request settles", async () => {
  let finish!: () => void;
  let count = 0;
  render(
    <AsyncForm
      submit={() => {
        count++;
        return new Promise<void>((r) => {
          finish = r;
        });
      }}
    >
      <button>Save</button>
    </AsyncForm>,
  );
  fireEvent.click(screen.getByText("Save"));
  expect(
    (screen.getByText("Save").closest("fieldset") as HTMLFieldSetElement)
      .disabled,
  ).toBe(true);
  finish();
  await waitFor(() =>
    expect(
      (screen.getByText("Save").closest("fieldset") as HTMLFieldSetElement)
        .disabled,
    ).toBe(false),
  );
  expect(count).toBe(1);
});
it("keeps a failed form dirty so workflow transitions stay blocked until saved", async () => {
  const { DirtyProvider, useDirty } = await import("./dirty");
  let fail = true;
  function Actions() {
    const { dirty } = useDirty();
    return <button disabled={dirty}>Send package</button>;
  }
  render(
    <DirtyProvider>
      <AsyncForm
        submit={async () => {
          if (fail) throw new Error("Network failed");
        }}
      >
        <label>
          Draft
          <input name="draft" />
        </label>
        <button>Save draft</button>
      </AsyncForm>
      <Actions />
    </DirtyProvider>,
  );
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "Changed words" },
  });
  expect((screen.getByText("Send package") as HTMLButtonElement).disabled).toBe(
    true,
  );
  fireEvent.click(screen.getByText("Save draft"));
  await screen.findByRole("alert");
  expect((screen.getByText("Send package") as HTMLButtonElement).disabled).toBe(
    true,
  );
  fail = false;
  fireEvent.click(screen.getByText("Save draft"));
  await waitFor(() =>
    expect(
      (screen.getByText("Send package") as HTMLButtonElement).disabled,
    ).toBe(false),
  );
});
it('keeps a prefilled textarea accessible by its label alone',async()=>{
 const {Field}=await import('./forms');
 render(<Field name="post" label="Post copy" value="Words saved in a previous version" multiline/>);
 expect(screen.getByRole('textbox',{name:'Post copy'})).toBeTruthy();
 expect(screen.getByLabelText('Post copy',{exact:true})).toBeTruthy();
});
