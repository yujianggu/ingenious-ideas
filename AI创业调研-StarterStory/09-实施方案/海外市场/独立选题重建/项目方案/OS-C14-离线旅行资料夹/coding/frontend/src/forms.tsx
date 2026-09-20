import { useRef, useState, useEffect, useId, type ReactNode } from "react";
import { useDirty } from "./dirty";
export function AsyncForm({
  children,
  submit,
  reload,
  disabled = false,
}: {
  children: ReactNode;
  submit: (data: FormData) => Promise<unknown>;
  reload?: () => void;
  disabled?: boolean;
}) {
  const id = useId();
  const { setDirty } = useDirty();
  useEffect(() => () => setDirty(id, false), [id, setDirty]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<{ message: string; status?: number } | null>(
      null,
    ),
    [saved, setSaved] = useState(false);
  const lock = useRef(false);
  return (
    <form
      onChange={() => {
        setDirty(id, true);
        setSaved(false);
      }}
      onSubmit={async (e) => {
        e.preventDefault();
        if (lock.current) return;
        const data = new FormData(e.currentTarget);
        lock.current = true;
        setBusy(true);
        setError(null);
        setSaved(false);
        try {
          await submit(data);
          setDirty(id, false);
          setSaved(true);
        } catch (err) {
          setError(
            err instanceof Error
              ? err
              : { message: "Request failed. Please try again." },
          );
        } finally {
          lock.current = false;
          setBusy(false);
        }
      }}
    >
      {error && (
        <div role="alert" className="notice error">
          {error.message}
          {error.status === 409 && reload && (
            <button
              type="button"
              className="secondary"
              onClick={reload}
              title="Reload the current server version and discard unsaved edits"
            >
              Reload latest
            </button>
          )}
        </div>
      )}
      <fieldset disabled={busy || disabled}>{children}</fieldset>
      {busy && (
        <p role="status" className="note">
          Saving…
        </p>
      )}
      {saved && (
        <p role="status" className="success">
          Saved successfully.
        </p>
      )}
    </form>
  );
}
export function Field({
  name,
  label,
  value = "",
  type = "text",
  required = false,
  multiline = false,
  min,
  max,
}: {
  name: string;
  label: string;
  value?: string | number;
  type?: string;
  required?: boolean;
  multiline?: boolean;
  min?: number;
  max?: number;
}) {
  return (
    <label>
      {label}
      {multiline ? (
        <textarea
          aria-label={label}
          name={name}
          defaultValue={value}
          required={required}
        />
      ) : (
        <input
          aria-label={label}
          name={name}
          type={type}
          defaultValue={value}
          required={required}
          min={min}
          max={max}
          minLength={type === "password" ? 10 : undefined}
        />
      )}
    </label>
  );
}
export function Check({
  name,
  children,
  checked = false,
  required = true,
}: {
  name: string;
  children: ReactNode;
  checked?: boolean;
  required?: boolean;
}) {
  return (
    <label className="check">
      <input
        type="checkbox"
        name={name}
        defaultChecked={checked}
        required={required}
      />
      <span>{children}</span>
    </label>
  );
}
export const text = (d: FormData, k: string) => String(d.get(k) || "");
