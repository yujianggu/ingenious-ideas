import Products, { ProjectPicker } from "./src/products/Products";
import { removeAccountCache } from "./src/products/cache";
import { purgeRememberedAccount } from "./src/products/offline";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  BackHandler,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { ApiError, decisionPayload, request } from "./src/api";
import { accountBoundary } from "./src/account";
import { DirtyRegistry, formIdentity } from "./src/drafts";
import { storage } from "./src/storage";
import { download, upload } from "./src/files";
import type { Episode, Clip, User } from "./src/types";
const DEFAULT_API =
  process.env.EXPO_PUBLIC_API_URL || "http://localhost:8000/api";
type FieldProps = {
  label: string;
  value: string;
  onChange: (s: string) => void;
  multiline?: boolean;
  secure?: boolean;
  numeric?: boolean;
};
function Field({
  label,
  value,
  onChange,
  multiline,
  secure,
  numeric,
}: FieldProps) {
  return (
    <View style={s.field}>
      <Text style={s.label}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        style={[s.input, multiline && s.multiline]}
        value={value}
        onChangeText={onChange}
        multiline={multiline}
        secureTextEntry={secure}
        autoCapitalize="none"
        keyboardType={numeric ? "numeric" : "default"}
      />
    </View>
  );
}
function Button({
  title,
  onPress,
  disabled = false,
  secondary = false,
}: {
  title: string;
  onPress: () => void;
  disabled?: boolean;
  secondary?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={[s.button, secondary && s.secondary, disabled && s.disabled]}
    >
      <Text style={[s.buttonText, secondary && s.secondaryText]}>{title}</Text>
    </Pressable>
  );
}
function Toggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <View style={s.toggle}>
      <Switch
        accessibilityLabel={label}
        value={value}
        onValueChange={onChange}
        trackColor={{ true: "#137464" }}
      />
      <Text style={s.toggleText}>{label}</Text>
    </View>
  );
}
function Card({ children }: { children: React.ReactNode }) {
  return <View style={s.card}>{children}</View>;
}
export default function App() {
  const [project, setProject] = useState("B04");
  const [base, setBase] = useState(DEFAULT_API),
    [baseForm, setBaseForm] = useState(DEFAULT_API),
    [token, setToken] = useState<string | null>(null),
    [user, setUser] = useState<User | null>(null),
    [busy, setBusy] = useState(false),
    [boot, setBoot] = useState(true),
    [error, setError] = useState(""),
    [conflict, setConflict] = useState(false);
  const [mode, setMode] = useState("login"),
    [name, setName] = useState(""),
    [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [studio, setStudio] = useState(""),
    [invite, setInvite] = useState(""),
    [inviteResult, setInviteResult] = useState("");
  const [episodes, setEpisodes] = useState<Episode[]>([]),
    [episode, setEpisode] = useState<Episode | null>(null),
    [members, setMembers] = useState<User[]>([]),
    [creating, setCreating] = useState(false),
    [settings, setSettings] = useState(false);
  const dirtyRegistry = useRef(new DirtyRegistry());
  const [dirtyEntities, setDirtyEntities] = useState<string[]>([]);
  const [formEpoch, setFormEpoch] = useState(0);
  const trackDirty = useCallback((id: string, changed: boolean) => {
    const registry = dirtyRegistry.current;
    if (registry.entities.includes(id) === changed) return;
    registry.mark(id, changed);
    setDirtyEntities(registry.entities);
  }, []);
  const productDirty = useCallback(
    (changed: boolean) => trackDirty("project form", changed),
    [trackDirty],
  );
  function clearDrafts() {
    dirtyRegistry.current.discard(true);
    setDirtyEntities([]);
    setFormEpoch((x) => x + 1);
  }
  function clearAccount(
    event: Parameters<typeof accountBoundary>[1] = "signed-out",
  ) {
    if (user?.id) void removeAccountCache(base, user.id).catch(() => {});
    setProject("B04");
    const cleared = accountBoundary(
      { episode, episodes, members, invite, inviteResult, creating },
      event,
    );
    setEpisode(cleared.episode);
    setEpisodes(cleared.episodes);
    setMembers(cleared.members);
    setInvite(cleared.invite);
    setInviteResult(cleared.inviteResult);
    setCreating(cleared.creating);
    setName("");
    setEmail("");
    setStudio("");
    setPassword("");
    clearDrafts();
  }
  async function confirmDiscard(force = false) {
    if (!force && !dirtyRegistry.current.hasChanges) return true;
    const message =
      "Unsaved edits will be discarded. Save each changed form first if you want to keep them.";
    if (Platform.OS === "web") return window.confirm(message);
    return new Promise<boolean>((resolve) =>
      Alert.alert(
        "Discard unsaved edits?",
        message,
        [
          {
            text: "Keep editing",
            style: "cancel",
            onPress: () => resolve(false),
          },
          {
            text: "Discard edits",
            style: "destructive",
            onPress: () => resolve(true),
          },
        ],
        { cancelable: true, onDismiss: () => resolve(false) },
      ),
    );
  }
  async function guard(fn: () => void | Promise<unknown>, force = false) {
    if (await confirmDiscard(force)) await fn();
  }
  function leaveEpisode() {
    void guard(() => {
      clearDrafts();
      setEpisode(null);
    });
  }
  async function reloadEpisode(force = false) {
    await guard(
      () =>
        run(async () => {
          if (episode) {
            const fresh = await api(`/episodes/${episode.id}`);
            clearDrafts();
            replace(fresh);
          }
        }),
      force,
    );
  }
  useEffect(() => {
    if (Platform.OS === "web") {
      const listener = (event: BeforeUnloadEvent) => {
        if (dirtyRegistry.current.hasChanges) {
          event.preventDefault();
          event.returnValue = "";
        }
      };
      window.addEventListener("beforeunload", listener);
      return () => window.removeEventListener("beforeunload", listener);
    }
    const subscription = BackHandler.addEventListener(
      "hardwareBackPress",
      () => {
        if (episode) {
          leaveEpisode();
          return true;
        }
        if (creating) {
          void guard(() => {
            clearDrafts();
            setCreating(false);
          });
          return true;
        }
        return false;
      },
    );
    return () => subscription.remove();
  }, [episode, creating]);
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setConflict(false);
    try {
      await fn();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Something went wrong. Please try again.",
      );
      setConflict(e instanceof ApiError && e.status === 409);
      if (e instanceof ApiError && e.status === 401) {
        clearAccount("expired");
        setUser(null);
        setToken(null);
        await storage.remove("episode-token");
        await storage.remove("product-account");
      }
    } finally {
      setBusy(false);
    }
  }
  async function load(t = token, b = base) {
    const data = await request<{ episodes: Episode[] }>(b, t, "/episodes");
    setEpisodes(data.episodes);
    if (user?.role === "editor")
      setMembers(
        (await request<{ members: User[] }>(b, t, "/members")).members,
      );
  }
  useEffect(() => {
    (async () => {
      try {
        const savedBase = (await storage.get("episode-api")) || DEFAULT_API;
        setBase(savedBase);
        setBaseForm(savedBase);
        const t = await storage.get("episode-token");
        if (t) {
          setToken(t);
          let u: User;
          try {
            u = await request<User>(savedBase, t, "/auth/me");
          } catch (error) {
            const cached = await storage.get("product-account");
            if (error instanceof ApiError && error.status === 0 && cached) {
              const profile = JSON.parse(cached);
              if (profile.base === savedBase) {
                setUser(profile.user);
                setProject("C14");
                setError(
                  "Offline mode. Open a project saved on this device; cloud edits need a connection.",
                );
                return;
              }
            }
            throw error;
          }
          setUser(u);
          await storage.set(
            "product-account",
            JSON.stringify({ base: savedBase, user: u }),
          );
          await load(t, savedBase);
          if (u.role === "editor")
            setMembers(
              (await request<{ members: User[] }>(savedBase, t, "/members"))
                .members,
            );
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          await purgeRememberedAccount(
            () => storage.get("product-account"),
            removeAccountCache,
          );
          clearAccount("expired");
          setUser(null);
          setToken(null);
          await storage.remove("episode-token");
          await storage.remove("product-account");
        }
        setError(e instanceof Error ? e.message : "Could not restore session");
      } finally {
        setBoot(false);
      }
    })();
  }, []);
  async function auth() {
    const path = mode === "join" ? "accept-invite" : mode;
    const data = await request<any>(
      base,
      null,
      `/auth/${path}`,
      "POST",
      mode === "login"
        ? { email, password }
        : mode === "register"
          ? { name, email, password, workspaceName: studio }
          : { token: invite, name, password },
    );
    clearAccount("authenticated");
    await storage.set("episode-token", data.token);
    setToken(data.token);
    setUser(data.user);
    await storage.set(
      "product-account",
      JSON.stringify({ base, user: data.user }),
    );
    setPassword("");
    await load(data.token);
    if (data.user.role === "editor")
      setMembers(
        (await request<{ members: User[] }>(base, data.token, "/members"))
          .members,
      );
  }
  function replace(e: Episode) {
    if (episode && (episode.id !== e.id || episode.version !== e.version))
      clearDrafts();
    setEpisode(e);
    setEpisodes((xs) =>
      xs.some((x) => x.id === e.id)
        ? xs.map((x) => (x.id === e.id ? e : x))
        : [e, ...xs],
    );
  }
  const api = (path: string, method = "GET", body?: unknown) =>
    request<Episode>(base, token, path, method, body);
  const action = (name: string, fields: object = {}) =>
    run(async () => {
      if (!episode) return;
      if (["submit", "send"].includes(name) && dirtyRegistry.current.hasChanges)
        throw new Error("Save all changed forms before sending this package.");
      if (["revise", "reopen"].includes(name) && !(await confirmDiscard()))
        return;
      replace(
        await api(`/episodes/${episode.id}/actions/${name}`, "POST", {
          revision: episode.revision,
          ...fields,
        }),
      );
    });
  if (boot)
    return (
      <SafeAreaProvider>
        <SafeAreaView style={s.screen}>
          <ActivityIndicator accessibilityLabel="Restoring studio session" />
        </SafeAreaView>
      </SafeAreaProvider>
    );
  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.screen}>
        <ScrollView
          contentContainerStyle={s.container}
          keyboardShouldPersistTaps="handled"
        >
          <View style={s.top}>
            <View>
              <Text style={s.eyebrow}>OVERSEAS / STUDIO</Text>
              <Text style={s.title}>
                {user
                  ? project !== "B04"
                    ? "Your project workspace"
                    : episode
                      ? "Episode workspace"
                      : "Your production desk"
                  : "Independent tools.\nOne workspace."}
              </Text>
            </View>
            <Pressable
              accessibilityRole="button"
              onPress={() => setSettings(!settings)}
            >
              <Text style={s.link}>Connection</Text>
            </Pressable>
          </View>
          {settings && (
            <Card>
              <Text style={s.heading}>Studio connection</Text>
              <Field label="API URL" value={baseForm} onChange={setBaseForm} />
              <Text style={s.muted}>
                Use HTTPS for production. A phone needs your computer’s LAN
                address, not localhost. Switching clears this device’s sign-in.
              </Text>
              <Button
                title="Save connection"
                disabled={busy}
                onPress={() =>
                  guard(() =>
                    run(async () => {
                      let url: URL;
                      try {
                        url = new URL(baseForm);
                      } catch {
                        throw new Error("Enter a valid HTTP or HTTPS API URL.");
                      }
                      if (!["http:", "https:"].includes(url.protocol))
                        throw new Error("Use an HTTP or HTTPS API URL.");
                      await storage.set(
                        "episode-api",
                        baseForm.replace(/\/$/, ""),
                      );
                      await storage.remove("episode-token");
                      await storage.remove("product-account");
                      setBase(baseForm.replace(/\/$/, ""));
                      setToken(null);
                      setUser(null);
                      clearAccount();
                      setSettings(false);
                    }),
                  )
                }
              />
            </Card>
          )}
          {!!error && (
            <View accessibilityRole="alert" style={s.error}>
              <Text style={s.errorText}>{error}</Text>
              {conflict && (
                <Button
                  title="Reload latest version"
                  disabled={busy}
                  onPress={() => {
                    void reloadEpisode(true);
                  }}
                />
              )}
            </View>
          )}
          {busy && <ActivityIndicator color="#137464" />}
          {dirtyEntities.length > 0 && (
            <Text style={s.feedback}>
              Unsaved edits: {dirtyEntities.join(", ")}. Save each changed form
              before submitting or sending.
            </Text>
          )}
          {!user ? (
            <Card>
              <Text style={s.heading}>
                {mode === "login"
                  ? "Welcome back"
                  : mode === "register"
                    ? "Start your studio"
                    : "Join your studio"}
              </Text>
              <Text style={s.muted}>
                Choose a project after signing in. Your data stays in your
                account.
              </Text>
              <View style={s.row}>
                {["login", "register", "join"].map((m) => (
                  <Button
                    key={m}
                    title={
                      m === "login"
                        ? "Sign in"
                        : m === "register"
                          ? "Create studio"
                          : "Join invite"
                    }
                    secondary={mode !== m}
                    onPress={() => setMode(m)}
                  />
                ))}
              </View>
              {mode !== "login" && (
                <Field label="Your name" value={name} onChange={setName} />
              )}{" "}
              {mode !== "join" && (
                <Field label="Email" value={email} onChange={setEmail} />
              )}{" "}
              {mode === "register" && (
                <Field
                  label="Studio name"
                  value={studio}
                  onChange={setStudio}
                />
              )}{" "}
              {mode === "join" && (
                <Field
                  label="Invite token"
                  value={invite}
                  onChange={setInvite}
                />
              )}
              <Field
                label="Password (10+ characters)"
                value={password}
                onChange={setPassword}
                secure
              />
              <Button
                title={
                  mode === "login"
                    ? "Sign in"
                    : mode === "register"
                      ? "Create studio"
                      : "Accept invitation"
                }
                disabled={busy}
                onPress={() => run(auth)}
              />
            </Card>
          ) : (
            <>
              <View style={s.row}>
                <Text style={s.muted}>
                  {user.name} · {user.role}
                </Text>
                <Button
                  title="Sign out"
                  secondary
                  disabled={busy}
                  onPress={() =>
                    guard(() =>
                      run(async () => {
                        try {
                          await request(base, token, "/auth/logout", "POST");
                        } finally {
                          await storage.remove("episode-token");
                          await storage.remove("product-account");
                          await removeAccountCache(base, user.id);
                          setToken(null);
                          setUser(null);
                          clearAccount();
                        }
                      }),
                    )
                  }
                />
              </View>
              <ProjectPicker
                selected={project}
                onSelect={(code) => {
                  void guard(() => {
                    clearDrafts();
                    setProject(code);
                  });
                }}
              />
              {project !== "B04" ? (
                <Products
                  key={`${user.id}-${project}`}
                  code={project}
                  base={base}
                  token={token!}
                  userId={user.id}
                  onDirty={productDirty}
                  onExpired={() => {
                    void run(async () => {
                      throw new ApiError(
                        401,
                        "Session expired. Please sign in again.",
                      );
                    });
                  }}
                />
              ) : !episode ? (
                <>
                  <View style={s.row}>
                    <Button
                      title="+ New episode"
                      onPress={() => {
                        void guard(() => {
                          clearDrafts();
                          setCreating(!creating);
                        });
                      }}
                      disabled={busy}
                    />
                    <Button
                      title="Refresh"
                      secondary
                      disabled={busy}
                      onPress={() => run(() => load())}
                    />
                  </View>
                  {creating && (
                    <Brief
                      key={`new-${formEpoch}`}
                      onDirty={trackDirty}
                      members={members}
                      user={user}
                      busy={busy}
                      onSave={(fields) =>
                        run(async () => {
                          const created = await api(
                            "/episodes",
                            "POST",
                            fields,
                          );
                          clearDrafts();
                          replace(created);
                          setCreating(false);
                        })
                      }
                    />
                  )}
                  {episodes.length === 0 && !creating && (
                    <Card>
                      <Text style={s.heading}>
                        Your next episode starts here
                      </Text>
                      <Text style={s.muted}>
                        Create a brief with your source, brand and creative
                        direction. Your studio will confirm the production scope
                        before work begins.
                      </Text>
                    </Card>
                  )}
                  {episodes.map((e) => (
                    <Pressable
                      key={e.id}
                      accessibilityRole="button"
                      onPress={() =>
                        guard(() =>
                          run(async () => {
                            const fresh = await api(`/episodes/${e.id}`);
                            clearDrafts();
                            setCreating(false);
                            replace(fresh);
                          }),
                        )
                      }
                    >
                      <Card>
                        <Text style={s.badge}>
                          {e.phase.toUpperCase()} · V{e.version}
                        </Text>
                        <Text style={s.heading}>{e.title}</Text>
                        <Text style={s.muted}>
                          {e.brand} · {Math.ceil(e.duration / 60)} min source
                        </Text>
                        <Text style={s.link}>Open episode →</Text>
                      </Card>
                    </Pressable>
                  ))}
                  {user.role === "editor" && (
                    <Card>
                      <Text style={s.heading}>Invite a client</Text>
                      <Field
                        label="Client email"
                        value={invite}
                        onChange={setInvite}
                      />
                      <Button
                        title="Generate invitation"
                        disabled={busy}
                        onPress={() =>
                          run(async () => {
                            const result = await request<any>(
                              base,
                              token,
                              "/invites",
                              "POST",
                              { email: invite },
                            );
                            setInviteResult(result.token);
                          })
                        }
                      />
                      {!!inviteResult && (
                        <>
                          <Text style={s.muted}>
                            Share this one-use token with your client. No email
                            has been sent.
                          </Text>
                          <Text selectable style={s.code}>
                            {inviteResult}
                          </Text>
                        </>
                      )}
                    </Card>
                  )}
                </>
              ) : (
                <>
                  <Button
                    title="← All episodes"
                    secondary
                    disabled={busy}
                    onPress={leaveEpisode}
                  />
                  <Card>
                    <Text style={s.badge}>
                      {episode.phase.toUpperCase()} · VERSION {episode.version}
                    </Text>
                    <Text style={s.title}>{episode.title}</Text>
                    <Text style={s.muted}>
                      {episode.brand} · Revision {episode.revision}
                    </Text>
                    {episode.dueAt && (
                      <Text style={s.muted}>
                        Due {new Date(episode.dueAt).toLocaleDateString()} · 7
                        calendar days from readiness
                      </Text>
                    )}
                    <Button
                      title="Refresh episode"
                      secondary
                      disabled={busy}
                      onPress={() => {
                        void reloadEpisode();
                      }}
                    />
                  </Card>
                  {episode.phase === "draft" && user.role === "client" ? (
                    <>
                      <Brief
                        onDirty={trackDirty}
                        key={formIdentity(episode, formEpoch, "brief")}
                        episode={episode}
                        members={members}
                        user={user}
                        busy={busy}
                        onSave={(fields) =>
                          run(async () =>
                            replace(
                              await api(
                                `/episodes/${episode.id}/brief`,
                                "PUT",
                                { ...fields, revision: episode.revision },
                              ),
                            ),
                          )
                        }
                      />
                      <Button
                        title="Submit saved brief"
                        disabled={
                          busy || !episode.rights || dirtyEntities.length > 0
                        }
                        onPress={() => action("submit")}
                      />
                    </>
                  ) : (
                    <Card>
                      <Text style={s.heading}>The brief</Text>
                      <Text style={s.label}>
                        Source · {episode.duration} seconds
                      </Text>
                      <Text selectable style={s.body}>
                        {episode.source}
                      </Text>
                      <Text style={s.body}>
                        {episode.notes || "No additional notes"}
                      </Text>
                      <Text style={s.label}>Terminology</Text>
                      <Text style={s.body}>{episode.glossary || "None"}</Text>
                      <Text style={s.label}>Prohibited claims</Text>
                      <Text style={s.body}>
                        {episode.prohibitedClaims || "Not provided"}
                      </Text>
                      <Text style={s.muted}>
                        Source rights:{" "}
                        {episode.rights
                          ? "Confirmed"
                          : "Awaiting client authorization"}
                      </Text>
                    </Card>
                  )}
                  {episode.phase === "submitted" && user.role === "editor" && (
                    <Readiness
                      busy={busy}
                      onReady={(fields) => action("ready", fields)}
                    />
                  )}
                  {episode.clips.map((clip) => (
                    <ClipCard
                      onDirty={trackDirty}
                      key={formIdentity(episode, formEpoch, clip.id)}
                      clip={clip}
                      episode={episode}
                      user={user}
                      busy={busy}
                      onSave={(fields) =>
                        run(async () =>
                          replace(
                            await api(
                              `/episodes/${episode.id}/clips/${clip.id}`,
                              "PUT",
                              { ...fields, revision: episode.revision },
                            ),
                          ),
                        )
                      }
                      onDecision={(kind, read, feedback) =>
                        run(async () =>
                          replace(
                            await api(
                              `/episodes/${episode.id}/actions/${kind}`,
                              "POST",
                              decisionPayload(
                                episode.revision,
                                episode.version,
                                clip.id,
                                kind,
                                read,
                                feedback,
                              ),
                            ),
                          ),
                        )
                      }
                    />
                  ))}
                  <Chapters
                    onDirty={trackDirty}
                    key={formIdentity(episode, formEpoch, "chapters")}
                    episode={episode}
                    editor={user.role === "editor"}
                    busy={busy}
                    onSave={(chapters) =>
                      run(async () =>
                        replace(
                          await api(`/episodes/${episode.id}/chapters`, "PUT", {
                            revision: episode.revision,
                            chapters,
                          }),
                        ),
                      )
                    }
                  />
                  {episode.phase === "editing" && user.role === "editor" && (
                    <Button
                      title="Send complete package for review"
                      disabled={busy || dirtyEntities.length > 0}
                      onPress={() => action("send")}
                    />
                  )}
                  <Assets
                    episode={episode}
                    user={user}
                    busy={busy}
                    onUpload={(kind, clip) =>
                      run(async () => {
                        const result = await upload(
                          base,
                          token!,
                          episode,
                          kind,
                          clip,
                        );
                        if (result) replace(result);
                      })
                    }
                    onDownload={(asset) =>
                      run(() =>
                        download(
                          base,
                          token!,
                          `/episodes/${episode.id}/assets/${asset.id}/download`,
                          asset.filename,
                        ),
                      )
                    }
                    onCheck={(id) =>
                      run(async () =>
                        replace(
                          await api(
                            `/episodes/${episode.id}/assets/${id}/check`,
                            "POST",
                            { revision: episode.revision, checked: true },
                          ),
                        ),
                      )
                    }
                  />
                  {episode.phase === "approved" && user.role === "editor" && (
                    <Button
                      title="Deliver checked package"
                      disabled={busy}
                      onPress={() => action("deliver")}
                    />
                  )}
                  {episode.phase === "delivered" && user.role === "client" && (
                    <Acceptance
                      busy={busy}
                      onAccept={() => action("accept", { read: true })}
                    />
                  )}
                  {episode.phase === "accepted" && (
                    <Card>
                      <Text style={s.heading}>Package received ✓</Text>
                      <Text style={s.muted}>
                        Your acceptance is recorded in the activity log.
                        Delivery files remain available below.
                      </Text>
                    </Card>
                  )}
                  {episode.phase !== "accepted" && (
                    <Revision
                      episode={episode}
                      editor={user.role === "editor"}
                      busy={busy}
                      onAction={action}
                    />
                  )}
                  <Card>
                    <Text style={s.heading}>Working documents</Text>
                    <Button
                      title="Download review manifest"
                      secondary
                      disabled={busy}
                      onPress={() =>
                        run(() =>
                          download(
                            base,
                            token!,
                            `/episodes/${episode.id}/export`,
                            "review-manifest.json",
                          ),
                        )
                      }
                    />
                    <Button
                      title="Download clip copy & chapters"
                      secondary
                      disabled={busy}
                      onPress={() =>
                        run(() =>
                          download(
                            base,
                            token!,
                            `/episodes/${episode.id}/copy`,
                            "episode-copy.txt",
                          ),
                        )
                      }
                    />
                  </Card>
                  <Card>
                    <Text style={s.heading}>Version history</Text>
                    {!episode.history.length && (
                      <Text style={s.muted}>
                        Snapshots appear when your studio sends a review.
                      </Text>
                    )}
                    {episode.history.map((h, i) => (
                      <View key={i} style={s.divider}>
                        <Text style={s.label}>
                          Version {h.version} ·{" "}
                          {new Date(h.sentAt).toLocaleString()}
                        </Text>
                        <Text style={s.body}>{h.title}</Text>
                        {h.clips?.map((c: any) => (
                          <Text key={c.id} style={s.muted}>
                            {c.title} · {c.start}–{c.end}s
                          </Text>
                        ))}
                        {h.decisions?.map((d: any, j: number) => (
                          <Text key={j} style={s.body}>
                            {d.clipId}: {d.status}
                            {d.feedback ? ` — ${d.feedback}` : ""}
                          </Text>
                        ))}
                      </View>
                    ))}
                  </Card>
                  <Card>
                    <Text style={s.heading}>Activity</Text>
                    {[...episode.events].reverse().map((e) => (
                      <View key={e.id} style={s.divider}>
                        <Text style={s.body}>
                          {e.action.replace(/_/g, " ")} · {e.actorName}
                        </Text>
                        <Text style={s.muted}>
                          {new Date(e.time).toLocaleString()} · V{e.version}
                        </Text>
                      </View>
                    ))}
                  </Card>
                </>
              )}
            </>
          )}
          <Text style={s.footer}>
            OVERSEAS STUDIO · Independent tools, clear workflows
          </Text>
        </ScrollView>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
function Brief({
  episode,
  members,
  user,
  busy,
  onSave,
  onDirty,
}: {
  episode?: Episode;
  members: User[];
  user: User;
  busy: boolean;
  onDirty: (id: string, changed: boolean) => void;
  onSave: (data: object) => void;
}) {
  const [title, setTitle] = useState(episode?.title || ""),
    [brand, setBrand] = useState(episode?.brand || ""),
    [source, setSource] = useState(episode?.source || ""),
    [duration, setDuration] = useState(String(episode?.duration || 1800)),
    [notes, setNotes] = useState(episode?.notes || ""),
    [glossary, setGlossary] = useState(episode?.glossary || ""),
    [claims, setClaims] = useState(episode?.prohibitedClaims || "None"),
    [rights, setRights] = useState(episode?.rights || false),
    [clientId, setClientId] = useState(episode?.clientId || "");
  const current = JSON.stringify([
    title,
    brand,
    source,
    duration,
    notes,
    glossary,
    claims,
    rights,
    clientId,
  ]);
  const baseline = JSON.stringify([
    episode?.title || "",
    episode?.brand || "",
    episode?.source || "",
    String(episode?.duration || 1800),
    episode?.notes || "",
    episode?.glossary || "",
    episode?.prohibitedClaims || "None",
    episode?.rights || false,
    episode?.clientId || "",
  ]);
  useEffect(() => {
    onDirty("brief", current !== baseline);
  }, [current, baseline, onDirty]);

  return (
    <Card>
      <Text style={s.heading}>
        {episode ? "Edit your brief" : "Create an episode"}
      </Text>
      <Field label="Episode title" value={title} onChange={setTitle} />
      <Field label="Brand / show" value={brand} onChange={setBrand} />
      <Field
        label="Source reference (link or file description)"
        value={source}
        onChange={setSource}
      />
      <Field
        label="Source duration in seconds (90–3600)"
        value={duration}
        onChange={setDuration}
        numeric
      />
      <Field
        label="Creative notes"
        value={notes}
        onChange={setNotes}
        multiline
      />
      <Field
        label="Names & terminology"
        value={glossary}
        onChange={setGlossary}
        multiline
      />
      <Field
        label="Prohibited claims (write None if none)"
        value={claims}
        onChange={setClaims}
        multiline
      />
      {user.role === "editor" && !episode && (
        <>
          <Text style={s.label}>Assign a client</Text>
          {members
            .filter((m) => m.role === "client")
            .map((m) => (
              <Button
                key={m.id}
                title={`${clientId === m.id ? "✓ " : ""}${m.name} · ${m.email}`}
                secondary={clientId !== m.id}
                onPress={() => setClientId(m.id)}
              />
            ))}
          {!members.some((m) => m.role === "client") && (
            <Text style={s.muted}>
              Invite a client first. Refresh this session after they join.
            </Text>
          )}
        </>
      )}
      {episode && (
        <Toggle
          label="I have permission to use and edit the source and confirm the brief."
          value={rights}
          onChange={setRights}
        />
      )}
      <Button
        title={episode ? "Save brief" : "Create episode"}
        disabled={
          busy ||
          !title.trim() ||
          !brand.trim() ||
          !source.trim() ||
          !Number.isInteger(Number(duration)) ||
          Number(duration) < 90 ||
          Number(duration) > 3600 ||
          (!episode && user.role === "editor" && !clientId)
        }
        onPress={() =>
          onSave({
            title,
            brand,
            source,
            duration: Number(duration),
            notes,
            glossary,
            prohibitedClaims: claims,
            ...(episode
              ? { rights }
              : user.role === "editor"
                ? { clientId }
                : {}),
          })
        }
      />
    </Card>
  );
}
function Readiness({
  busy,
  onReady,
}: {
  busy: boolean;
  onReady: (fields: object) => void;
}) {
  const [values, setValues] = useState([false, false, false, false]);
  const labels = [
    "I verified that the source is usable.",
    "The delivery scope is confirmed.",
    "A qualified editor is assigned.",
    "The payment path is confirmed (manual attestation).",
  ];
  return (
    <Card>
      <Text style={s.heading}>Confirm production readiness</Text>
      <Text style={s.muted}>
        This starts the 7-calendar-day delivery window.
      </Text>
      {labels.map((label, i) => (
        <Toggle
          key={label}
          label={label}
          value={values[i]}
          onChange={(v) =>
            setValues((xs) => xs.map((x, j) => (j === i ? v : x)))
          }
        />
      ))}
      <Button
        title="Start production"
        disabled={busy || values.some((v) => !v)}
        onPress={() =>
          onReady({
            sourceUsable: true,
            scopeConfirmed: true,
            editorQualified: true,
            paymentPathConfirmed: true,
          })
        }
      />
    </Card>
  );
}
function ClipCard({
  clip,
  episode,
  user,
  busy,
  onSave,
  onDirty,
  onDecision,
}: {
  clip: Clip;
  episode: Episode;
  user: User;
  busy: boolean;
  onDirty: (id: string, changed: boolean) => void;
  onSave: (data: object) => void;
  onDecision: (kind: string, read: boolean, feedback: string) => void;
}) {
  const [title, setTitle] = useState(clip.title),
    [start, setStart] = useState(String(clip.start)),
    [end, setEnd] = useState(String(clip.end)),
    [quote, setQuote] = useState(clip.quote),
    [context, setContext] = useState(clip.context),
    [post, setPost] = useState(clip.post),
    [checked, setChecked] = useState(clip.checked),
    [read, setRead] = useState(false),
    [feedback, setFeedback] = useState("");
  const editing = episode.phase === "editing" && user.role === "editor";
  const current = JSON.stringify([
    title,
    start,
    end,
    quote,
    context,
    post,
    checked,
  ]);
  const baseline = JSON.stringify([
    clip.title,
    String(clip.start),
    String(clip.end),
    clip.quote,
    clip.context,
    clip.post,
    clip.checked,
  ]);
  useEffect(() => {
    onDirty(
      clip.id,
      editing
        ? current !== baseline
        : episode.phase === "review" &&
            clip.status === "pending" &&
            !!feedback.trim(),
    );
  }, [
    current,
    baseline,
    editing,
    episode.phase,
    clip.status,
    clip.id,
    feedback,
    onDirty,
  ]);

  return (
    <Card>
      <Text style={s.badge}>
        {clip.id.toUpperCase()} · {clip.status.toUpperCase()}
      </Text>
      {editing ? (
        <>
          <Field label="Clip title" value={title} onChange={setTitle} />
          <View style={s.row}>
            <View style={s.flex}>
              <Field
                label="Start (seconds)"
                value={start}
                onChange={setStart}
                numeric
              />
            </View>
            <View style={s.flex}>
              <Field
                label="End (seconds)"
                value={end}
                onChange={setEnd}
                numeric
              />
            </View>
          </View>
          <Field
            label="Exact quote"
            value={quote}
            onChange={setQuote}
            multiline
          />
          <Field
            label="Context and caveats"
            value={context}
            onChange={setContext}
            multiline
          />
          <Field
            label="Social post"
            value={post}
            onChange={setPost}
            multiline
          />
          <Toggle
            label="I verified accuracy, context and the 30–90 second selection."
            value={checked}
            onChange={setChecked}
          />
          <Button
            title="Save clip"
            disabled={busy}
            onPress={() =>
              onSave({
                title,
                start: Number(start),
                end: Number(end),
                quote,
                context,
                post,
                checked,
              })
            }
          />
        </>
      ) : (
        <>
          <Text style={s.heading}>{clip.title || "Clip in preparation"}</Text>
          <Text style={s.muted}>
            {clip.start}–{clip.end} seconds
          </Text>
          <Text style={s.quote}>
            {clip.quote || "The editor will prepare this selection."}
          </Text>
          <Text style={s.label}>Context</Text>
          <Text style={s.body}>{clip.context || "—"}</Text>
          <Text style={s.label}>Post copy</Text>
          <Text style={s.body}>{clip.post || "—"}</Text>
        </>
      )}
      {clip.feedback && (
        <Text style={s.feedback}>Requested changes: {clip.feedback}</Text>
      )}
      {episode.phase === "review" &&
        user.role === "client" &&
        clip.status === "pending" && (
          <>
            <Toggle
              label="I read the entire quote, context and post for this version."
              value={read}
              onChange={setRead}
            />
            <Button
              title="Approve this clip"
              disabled={busy || !read}
              onPress={() => onDecision("approve", read, feedback)}
            />
            <Field
              label="Specific change request"
              value={feedback}
              onChange={setFeedback}
              multiline
            />
            <Button
              title="Request changes"
              secondary
              disabled={busy || !feedback.trim()}
              onPress={() => onDecision("changes", read, feedback)}
            />
          </>
        )}
    </Card>
  );
}
function Chapters({
  episode,
  editor,
  busy,
  onSave,
  onDirty,
}: {
  episode: Episode;
  editor: boolean;
  busy: boolean;
  onDirty: (id: string, changed: boolean) => void;
  onSave: (text: string) => void;
}) {
  const [text, setText] = useState(episode.chapters || "");
  useEffect(() => {
    onDirty(
      "chapters",
      editor &&
        episode.phase === "editing" &&
        text !== (episode.chapters || ""),
    );
  }, [text, episode.chapters, episode.phase, editor, onDirty]);

  return (
    <Card>
      <Text style={s.heading}>Chapter notes</Text>
      {editor && episode.phase === "editing" ? (
        <>
          <Field
            label="Chapters and timecodes"
            value={text}
            onChange={setText}
            multiline
          />
          <Button
            title="Save chapters"
            disabled={busy}
            onPress={() => onSave(text)}
          />
        </>
      ) : (
        <Text style={s.body}>
          {episode.chapters || "Your editor will add chapters here."}
        </Text>
      )}
    </Card>
  );
}
function Assets({
  episode,
  user,
  busy,
  onUpload,
  onDownload,
  onCheck,
}: {
  episode: Episode;
  user: User;
  busy: boolean;
  onUpload: (kind: string, clip: string) => void;
  onDownload: (asset: Episode["assets"][number]) => void;
  onCheck: (id: string) => void;
}) {
  const [kind, setKind] = useState("video"),
    [clip, setClip] = useState("clip-1");
  const sourceUpload =
    user.role === "client"
      ? ["draft", "submitted"].includes(episode.phase)
      : ["submitted", "editing"].includes(episode.phase);
  const finalUpload = user.role === "editor" && episode.phase === "approved";
  return (
    <Card>
      <Text style={s.heading}>Files & delivery</Text>
      <Text style={s.muted}>
        Private files · Every delivery is tied to its review version. Download
        to preview in your device’s viewer.
      </Text>
      {sourceUpload && (
        <Button
          title="Upload source video"
          secondary
          disabled={busy}
          onPress={() => onUpload("source", "")}
        />
      )}{" "}
      {finalUpload && (
        <>
          <Text style={s.label}>Add current delivery file</Text>
          <View style={s.row}>
            {["video", "subtitle", "project"].map((k) => (
              <Button
                key={k}
                title={k}
                secondary={kind !== k}
                disabled={busy}
                onPress={() => setKind(k)}
              />
            ))}
          </View>
          {kind !== "project" && (
            <View style={s.row}>
              {episode.clips.map((c) => (
                <Button
                  key={c.id}
                  title={c.id}
                  secondary={clip !== c.id}
                  disabled={busy}
                  onPress={() => setClip(c.id)}
                />
              ))}
            </View>
          )}
          <Text style={s.muted}>
            Video: 9:16 MP4 with audio, matching approved duration. Subtitles:
            valid UTF-8 SRT. Project: editable JSON/TXT. Maximum 100 MB by
            default.
          </Text>
          <Button
            title={`Choose ${kind} file`}
            disabled={busy}
            onPress={() => onUpload(kind, clip)}
          />
        </>
      )}
      {episode.assets
        .filter(
          (a) =>
            user.role === "editor" ||
            a.kind === "source" ||
            (a.version === episode.version &&
              ["approved", "delivered", "accepted"].includes(episode.phase)),
        )
        .map((a) => (
          <View key={a.id} style={s.divider}>
            <Text style={s.label}>{a.filename}</Text>
            <Text style={s.muted}>
              {a.kind} {a.clipId || ""} · V{a.version}
              {a.version !== episode.version ? " · ARCHIVED" : ""} ·{" "}
              {(a.size / 1024 / 1024).toFixed(1)} MB ·{" "}
              {a.checked ? "QC checked" : "Not QC checked"}
            </Text>
            <Button
              title="Download / preview"
              secondary
              disabled={busy}
              onPress={() => onDownload(a)}
            />
            {finalUpload &&
              a.version === episode.version &&
              a.kind !== "source" &&
              !a.checked && (
                <Button
                  title="Confirm I inspected this file"
                  disabled={busy}
                  onPress={() => onCheck(a.id)}
                />
              )}
          </View>
        ))}
      {!episode.assets.length && (
        <Text style={s.muted}>No files uploaded yet.</Text>
      )}
    </Card>
  );
}
function Acceptance({
  busy,
  onAccept,
}: {
  busy: boolean;
  onAccept: () => void;
}) {
  const [read, setRead] = useState(false);
  return (
    <Card>
      <Text style={s.heading}>Your package is ready</Text>
      <Text style={s.muted}>
        Download and inspect all three videos, subtitles and the editable
        project before accepting.
      </Text>
      <Toggle
        label="I downloaded and inspected the complete delivery package."
        value={read}
        onChange={setRead}
      />
      <Button
        title="Accept delivery"
        disabled={busy || !read}
        onPress={onAccept}
      />
    </Card>
  );
}
function Revision({
  episode,
  editor,
  busy,
  onAction,
}: {
  episode: Episode;
  editor: boolean;
  busy: boolean;
  onAction: (action: string, fields: object) => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <Card>
      <Text style={s.heading}>Need another pass?</Text>
      <Field
        label="Reason for revision or reopening"
        value={reason}
        onChange={setReason}
        multiline
      />
      {editor &&
        ["review", "approved", "delivered"].includes(episode.phase) && (
          <>
            <Text style={s.muted}>
              Revision starts a new version, clears all clip approvals and
              requires new delivery files. The existing due date remains.
            </Text>
            <Button
              title="Start package revision"
              secondary
              disabled={busy || !reason.trim()}
              onPress={() => onAction("revise", { reason })}
            />
          </>
        )}
      <Text style={s.muted}>
        Reopening returns to the brief, clears all approvals and readiness
        dates, and requires the client to authorize the source again.
      </Text>
      <Button
        title="Reopen brief / replace source"
        secondary
        disabled={busy || !reason.trim()}
        onPress={() => onAction("reopen", { reason })}
      />
    </Card>
  );
}
const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#f4f3ee" },
  container: {
    width: "100%",
    maxWidth: 760,
    alignSelf: "center",
    padding: 20,
    paddingBottom: 50,
    gap: 16,
  },
  top: { gap: 14, paddingVertical: 12 },
  eyebrow: {
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 3,
    color: "#137464",
    marginBottom: 12,
  },
  title: { fontSize: 30, fontWeight: "700", color: "#173b36", lineHeight: 37 },
  heading: {
    fontSize: 21,
    fontWeight: "700",
    color: "#173b36",
    marginBottom: 8,
  },
  card: {
    backgroundColor: "#fff",
    borderRadius: 18,
    padding: 22,
    gap: 10,
    borderWidth: 1,
    borderColor: "#e1e6e0",
  },
  label: { fontSize: 13, fontWeight: "700", color: "#31534d", marginBottom: 5 },
  field: { gap: 4, marginVertical: 3 },
  input: {
    borderWidth: 1,
    borderColor: "#cddbd3",
    borderRadius: 10,
    padding: 12,
    fontSize: 16,
    color: "#173b36",
    backgroundColor: "#fcfdfa",
    minHeight: 46,
  },
  multiline: { minHeight: 96, textAlignVertical: "top" },
  button: {
    borderRadius: 10,
    backgroundColor: "#137464",
    paddingHorizontal: 16,
    paddingVertical: 13,
    alignItems: "center",
    marginVertical: 3,
  },
  buttonText: { color: "#fff", fontSize: 14, fontWeight: "700" },
  secondary: {
    backgroundColor: "#edf4ef",
    borderWidth: 1,
    borderColor: "#cbded3",
  },
  secondaryText: { color: "#185e51" },
  disabled: { opacity: 0.45 },
  row: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    alignItems: "center",
  },
  flex: { flex: 1, minWidth: 100 },
  muted: { fontSize: 13, lineHeight: 21, color: "#657b73" },
  body: { fontSize: 15, lineHeight: 23, color: "#29473f" },
  quote: {
    fontSize: 19,
    lineHeight: 29,
    color: "#183e33",
    paddingVertical: 12,
  },
  link: { color: "#137464", fontWeight: "700", fontSize: 14 },
  badge: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.4,
    color: "#137464",
  },
  toggle: {
    flexDirection: "row",
    gap: 12,
    alignItems: "center",
    paddingVertical: 6,
  },
  toggleText: { flex: 1, fontSize: 14, lineHeight: 21, color: "#31534d" },
  error: { backgroundColor: "#ffeae3", padding: 16, borderRadius: 12, gap: 8 },
  errorText: { color: "#903b26", fontSize: 14, lineHeight: 22 },
  divider: {
    borderTopWidth: 1,
    borderColor: "#e5ebe5",
    paddingTop: 14,
    marginTop: 8,
    gap: 6,
  },
  feedback: {
    backgroundColor: "#fff5df",
    color: "#745418",
    padding: 12,
    borderRadius: 8,
    lineHeight: 22,
  },
  code: {
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
    fontSize: 12,
    color: "#244c3c",
  },
  footer: {
    textAlign: "center",
    fontSize: 10,
    letterSpacing: 1.3,
    color: "#819088",
    padding: 16,
  },
});
