import { useEffect, useState } from "react";
import Products from "./products/Products";
import { product } from "../../shared/products";
import { request, json, TOKEN_KEY } from "./api";
import { AsyncForm, Field } from "./forms";
import { useDirty } from "./dirty";
type User = {
  id: string;
  name: string;
  email: string;
};
export default function App() {
  const { dirty } = useDirty();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    const expired = () => {
      setUser(null);
      setMessage("Your session expired. Sign in again to continue.");
    };
    window.addEventListener("session-expired", expired);
    if (sessionStorage.getItem(TOKEN_KEY))
      request<User>("/auth/me")
        .then((u) => {
          if (active) setUser(u);
        })
        .catch((e) => {
          if (active) setMessage(e.message);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    else setLoading(false);
    return () => {
      active = false;
      window.removeEventListener("session-expired", expired);
    };
  }, []);
  if (loading) return <p role="status">Opening your workspace…</p>;
  if (!user)
    return (
      <Auth
        message={message}
        onLogin={(u) => {
          setUser(u);
          setMessage("");
        }}
      />
    );
  return (
    <>
      <header className="top">
        <span className="brand">{product.name}</span>
        <div className="account">
          <span>
            {user.name} <small>Private account</small>
          </span>
          <AsyncForm
            submit={async () => {
              if (
                dirty &&
                !window.confirm("Discard your unsaved changes and sign out?")
              )
                throw new Error(
                  "Sign out cancelled; your unsaved edits are still here.",
                );
              await request("/auth/logout", json({}));
              sessionStorage.removeItem(TOKEN_KEY);
              setUser(null);
            }}
          >
            <button className="quiet">Sign out</button>
          </AsyncForm>
        </div>
      </header>
      <Products key={user.id} />
    </>
  );
}
function Auth({
  message,
  onLogin,
}: {
  message: string;
  onLogin: (user: User) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const { dirty } = useDirty();
  return (
    <main className="auth">
      <section className="auth-story">
        <span className="brand">{product.name}</span>
        <div>
          <h1>{product.name}</h1>
          <p>{product.summary}</p>
        </div>
        <p>Your private workspace.</p>
      </section>
      <section className="auth-form">
        <h2>{mode === "register" ? "Create your account" : "Welcome back"}</h2>
        {message && <p role="alert">{message}</p>}
        <AsyncForm
          key={mode}
          submit={async (d) => {
            const session = await request<{
              token: string;
              user: User;
            }>(`/auth/${mode}`, json(Object.fromEntries(d)));
            sessionStorage.setItem(TOKEN_KEY, session.token);
            onLogin(session.user);
          }}
        >
          {mode === "register" && (
            <>
              <Field name="name" label="Your name" required />
              <Field name="workspaceName" label="Workspace name" required />
            </>
          )}
          <Field name="email" label="Email address" type="email" required />
          <Field
            name="password"
            label="Password (at least 10 characters)"
            type="password"
            required
          />
          <button className="wide">
            {mode === "register" ? "Create account" : "Sign in"}
          </button>
        </AsyncForm>
        <button
          className="link"
          onClick={() => {
            if (
              !dirty ||
              window.confirm("Discard your unsaved changes and continue?")
            )
              setMode(mode === "login" ? "register" : "login");
          }}
        >
          {mode === "login"
            ? "New here? Create an account"
            : "Already have an account? Sign in"}
        </button>
        <p className="note">Your records are private to this account.</p>
      </section>
    </main>
  );
}
