import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  BackHandler,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import Products from "./src/products/Products";
import { removeAccountCache } from "./src/products/cache";
import {
  createGeneration,
  purgeRememberedAccount,
} from "./src/products/offline";
import { ApiError, request } from "./src/api";
import { storage } from "./src/storage";
import type { User } from "./src/types";
import { products } from "../shared/products";
const CODE = "C15";
const DEFAULT_API =
  process.env.EXPO_PUBLIC_API_URL || "http://localhost:8035/api";
const KEY = {
  base: "os-c15-api",
  token: "os-c15-token",
  account: "os-c15-account",
};
async function confirmDiscard() {
  const message = "Discard your unsaved form edits and continue?";
  if (Platform.OS === "web") return window.confirm(message);
  return new Promise<boolean>((resolve) =>
    Alert.alert(
      "Unsaved edits",
      message,
      [
        { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
        { text: "Discard", style: "destructive", onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    ),
  );
}
function Button({
  title,
  onPress,
  disabled = false,
}: {
  title: string;
  onPress: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={[s.button, disabled && { opacity: 0.45 }]}
    >
      <Text style={s.buttonText}>{title}</Text>
    </Pressable>
  );
}
function Field({
  label,
  value,
  onChange,
  secure = false,
}: {
  label: string;
  value: string;
  onChange: (s: string) => void;
  secure?: boolean;
}) {
  return (
    <View style={s.field}>
      <Text>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        style={s.input}
        value={value}
        onChangeText={onChange}
        secureTextEntry={secure}
        autoCapitalize="none"
      />
    </View>
  );
}
export default function App() {
  const [base, setBase] = useState(DEFAULT_API),
    [baseForm, setBaseForm] = useState(DEFAULT_API);
  const [token, setToken] = useState<string | null>(null),
    [user, setUser] = useState<User | null>(null);
  const [boot, setBoot] = useState(true),
    [busy, setBusy] = useState(false),
    [settings, setSettings] = useState(false);
  const [mode, setMode] = useState<"login" | "register">("login"),
    [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [name, setName] = useState("");
  const [error, setError] = useState("");
  const dirty = useRef(false),
    lifetime = useRef(createGeneration()),
    lock = useRef(false),
    expiring = useRef(false);
  const onDirty = useCallback((value: boolean) => {
    dirty.current = value;
  }, []);
  const guard = async () => !dirty.current || (await confirmDiscard());
  async function clearSession() {
    lifetime.current.invalidate();
    // Invalidate cache work synchronously before any asynchronous storage cleanup.
    const purge = user
      ? removeAccountCache(base, user.id)
      : purgeRememberedAccount(
          () => storage.get(KEY.account),
          removeAccountCache,
        );
    setUser(null);
    setToken(null);
    setPassword("");
    dirty.current = false;
    // Read/purge the remembered account before forgetting its profile.
    const cleared = await Promise.allSettled([purge]);
    cleared.push(
      ...(await Promise.allSettled([
        storage.remove(KEY.token),
        storage.remove(KEY.account),
      ])),
    );
    const failure = cleared.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  }
  async function run(action: () => Promise<void>) {
    if (lock.current || expiring.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      lock.current = false;
      setBusy(expiring.current);
    }
  }
  const expire = () => {
    if (expiring.current) return;
    expiring.current = true;
    setBusy(true);
    void clearSession()
      .then(() => setError("Your session expired. Please sign in again."))
      .catch((e) => setError(e.message || "Could not clear expired session"))
      .finally(() => {
        expiring.current = false;
        setBusy(lock.current);
      });
  };
  useEffect(() => {
    const current = lifetime.current.capture();
    void (async () => {
      try {
        const savedBase = (await storage.get(KEY.base)) || DEFAULT_API;
        if (!current()) return;
        setBase(savedBase);
        setBaseForm(savedBase);
        const savedToken = await storage.get(KEY.token);
        if (!savedToken || !current()) return;
        let profile: User;
        try {
          profile = await request<User>(savedBase, savedToken, "/auth/me");
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 0)) throw e;
          const cached = await storage.get(KEY.account);
          const remembered = cached ? JSON.parse(cached) : null;
          if (remembered?.base !== savedBase || !remembered?.user?.id) throw e;
          profile = remembered.user;
          if (current())
            setError(
              "Offline mode. Saved records are available; cloud edits need a connection.",
            );
        }
        if (!current()) return;
        await storage.set(
          KEY.account,
          JSON.stringify({ base: savedBase, user: profile }),
        );
        if (!current()) return;
        setToken(savedToken);
        setUser(profile);
      } catch (e) {
        if (!current()) return;
        if (e instanceof ApiError && e.status === 401) {
          await purgeRememberedAccount(
            () => storage.get(KEY.account),
            removeAccountCache,
          );
          await storage.remove(KEY.token);
          await storage.remove(KEY.account);
        }
        if (current())
          setError(
            e instanceof Error ? e.message : "Could not restore session",
          );
      } finally {
        if (current()) setBoot(false);
      }
    })();
    return () => lifetime.current.invalidate();
  }, []);
  useEffect(() => {
    if (Platform.OS === "web") {
      const handler = (event: BeforeUnloadEvent) => {
        if (dirty.current) {
          event.preventDefault();
          event.returnValue = "";
        }
      };
      window.addEventListener("beforeunload", handler);
      return () => window.removeEventListener("beforeunload", handler);
    }
    const subscription = BackHandler.addEventListener(
      "hardwareBackPress",
      () => {
        if (!dirty.current) return false;
        void confirmDiscard().then((ok) => {
          if (ok) {
            dirty.current = false;
            BackHandler.exitApp();
          }
        });
        return true;
      },
    );
    return () => subscription.remove();
  }, []);
  async function authenticate() {
    const current = lifetime.current.capture();
    const result = await request<{ token: string; user: User }>(
      base,
      null,
      `/auth/${mode}`,
      "POST",
      mode === "login"
        ? { email, password }
        : { name, email, password, workspaceName: products[0].name },
    );
    if (!current()) return;
    await storage.remove(KEY.account);
    await storage.set(KEY.token, result.token);
    await storage.set(KEY.account, JSON.stringify({ base, user: result.user }));
    if (!current()) return;
    setToken(result.token);
    setUser(result.user);
    setPassword("");
  }
  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.screen}>
        <ScrollView
          contentContainerStyle={s.content}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={s.title}>{products[0].name}</Text>
          <Text style={s.muted}>{CODE} · Private workspace</Text>
          {boot ? (
            <ActivityIndicator accessibilityLabel="Restoring session" />
          ) : (
            <>
              <View style={s.row}>
                <Button
                  title={settings ? "Back to workspace" : "Connection"}
                  disabled={busy}
                  onPress={() =>
                    void run(async () => {
                      if (await guard()) {
                        setSettings((v) => !v);
                        dirty.current = false;
                      }
                    })
                  }
                />
                {user && (
                  <Button
                    title="Sign out"
                    disabled={busy}
                    onPress={() =>
                      void run(async () => {
                        if (!(await guard())) return;
                        const oldBase = base,
                          oldToken = token;
                        await clearSession();
                        try {
                          await request(
                            oldBase,
                            oldToken,
                            "/auth/logout",
                            "POST",
                          );
                        } catch (e) {
                          if (!(
                            e instanceof ApiError && [0, 401].includes(e.status)
                          ))
                            throw e;
                        }
                      })
                    }
                  />
                )}
              </View>
              {!!error && (
                <Text accessibilityRole="alert" style={s.error}>
                  {error}
                </Text>
              )}
              {settings ? (
                <View style={s.card}>
                  <Text style={s.heading}>API connection</Text>
                  <Field
                    label="API URL"
                    value={baseForm}
                    onChange={setBaseForm}
                  />
                  <Text style={s.muted}>
                    Use your computer's LAN address on a physical device.
                    Changing the connection signs you out and removes this
                    account's saved offline data.
                  </Text>
                  <Button
                    title="Save connection"
                    disabled={busy}
                    onPress={() =>
                      void run(async () => {
                        const next = baseForm.trim().replace(/\/$/, "");
                        const url = new URL(next);
                        if (
                          !["http:", "https:"].includes(url.protocol) ||
                          url.username ||
                          url.password
                        )
                          throw new Error(
                            "Enter an HTTP or HTTPS API URL without credentials.",
                          );
                        if (!(await guard())) return;
                        if (next !== base) {
                          await clearSession();
                          await storage.set(KEY.base, next);
                          setBase(next);
                        }
                        setBaseForm(next);
                        setSettings(false);
                      })
                    }
                  />
                </View>
              ) : user && token ? (
                <>
                  <Text style={s.muted}>
                    Signed in as {user.name} · {user.email}
                  </Text>
                  <Products
                    key={`${base}:${user.id}:${token}`}
                    code={CODE}
                    base={base}
                    token={token}
                    userId={user.id}
                    onDirty={onDirty}
                    onExpired={expire}
                  />
                </>
              ) : (
                <View style={s.card}>
                  <Text style={s.heading}>
                    {mode === "login" ? "Sign in" : "Create account"}
                  </Text>
                  {mode === "register" && (
                    <Field label="Name" value={name} onChange={setName} />
                  )}
                  <Field label="Email" value={email} onChange={setEmail} />
                  <Field
                    label="Password"
                    value={password}
                    onChange={setPassword}
                    secure
                  />
                  <Button
                    title={
                      busy
                        ? "Please wait…"
                        : mode === "login"
                          ? "Sign in"
                          : "Create account"
                    }
                    disabled={busy}
                    onPress={() => void run(authenticate)}
                  />
                  <Button
                    title={
                      mode === "login"
                        ? "Create a new account"
                        : "Already have an account? Sign in"
                    }
                    disabled={busy}
                    onPress={() => {
                      setMode((v) => (v === "login" ? "register" : "login"));
                      setError("");
                    }}
                  />
                </View>
              )}
            </>
          )}
        </ScrollView>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#f1f6f2" },
  content: { padding: 20, width: "100%", maxWidth: 900, alignSelf: "center" },
  title: { fontSize: 28, fontWeight: "700", color: "#143d2d", marginBottom: 8 },
  heading: { fontSize: 21, fontWeight: "600", marginBottom: 12 },
  muted: { color: "#527363", marginBottom: 16 },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  card: {
    backgroundColor: "white",
    padding: 20,
    borderRadius: 12,
    marginVertical: 16,
  },
  field: { marginBottom: 16 },
  input: {
    borderWidth: 1,
    borderColor: "#b0c5b7",
    borderRadius: 6,
    padding: 12,
    marginTop: 6,
  },
  button: {
    backgroundColor: "#245a40",
    padding: 12,
    borderRadius: 8,
    marginBottom: 12,
  },
  buttonText: { color: "white", fontWeight: "600" },
  error: { color: "#a62e2e", marginVertical: 12 },
});
