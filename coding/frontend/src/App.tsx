import Products, { ProjectPicker } from "./products/Products";
import { useEffect, useState } from "react";
import { request, json, download, response } from "./api";
import { AsyncForm, Field, Check, text } from "./forms";
import type { User, Episode, Clip, Asset } from "./types";
import { useDirty } from "./dirty";
const date = (v: string | null) =>
  v ? new Date(v).toLocaleString() : "Not started";
const phases = [
  "draft",
  "submitted",
  "editing",
  "review",
  "approved",
  "delivered",
  "accepted",
];
export default function App() {
  const { dirty } = useDirty();
  const [project, setProject] = useState("B04");
  const [reloadEpoch, setReloadEpoch] = useState(0);
  const canLeave = () =>
    !dirty || window.confirm("Discard your unsaved changes and continue?");
  const newEpisode = () => {
    if (canLeave()) {
      setCreate(true);
      setEpisode(null);
    }
  };
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [episodes, setEpisodes] = useState<Episode[]>([]),
    [episode, setEpisode] = useState<Episode | null>(null),
    [members, setMembers] = useState<User[]>([]),
    [create, setCreate] = useState(false),
    [invite, setInvite] = useState(false),
    [detailBusy, setDetailBusy] = useState(false);
  const load = async () => {
    const result = await request<{ episodes: Episode[] }>("/episodes");
    setEpisodes(result.episodes);
  };
  useEffect(() => {
    const expired = () => {
      setUser(null);
      setEpisodes([]);
      setEpisode(null);
    };
    window.addEventListener("session-expired", expired);
    if (
      sessionStorage.getItem("episode-token") &&
      !new URLSearchParams(location.search).has("token")
    )
      request<User>("/auth/me")
        .then(setUser)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    else setLoading(false);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  useEffect(() => {
    if (user) {
      load().catch((e) => setError(e.message));
      if (user.role === "editor")
        request<{ members: User[] }>("/members")
          .then((r) => setMembers(r.members))
          .catch((e) => setError(e.message));
    }
  }, [user]);
  const selected = async (id: string, explicitReload = false) => {
    if (!canLeave()) return;
    setDetailBusy(true);
    setError("");
    try {
      setEpisode(await request<Episode>(`/episodes/${id}`));
      if (explicitReload) setReloadEpoch((n) => n + 1);
      setCreate(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDetailBusy(false);
    }
  };
  const update = (ep: Episode) => {
    setEpisode(ep);
    setEpisodes((old) => [ep, ...old.filter((x) => x.id !== ep.id)]);
  };
  if (loading)
    return (
      <div className="loading" role="status">
        Opening your workspace…
      </div>
    );
  if (!user)
    return (
      <Auth
        onLogin={(u) => {
          setUser(u);
          setError("");
        }}
        message={error}
      />
    );
  return (
    <>
      <header className="top">
        <a className="brand" href="/">
          OS<span>Overseas Studio</span>
        </a>
        <div className="account">
          <span>
            {user.name} <small>{user.role}</small>
          </span>
          <AsyncForm
            submit={async () => {
              if (!canLeave())
                throw new Error(
                  "Sign out cancelled; your unsaved edits are still here.",
                );
              await request("/auth/logout", json({}));
              sessionStorage.removeItem("episode-token");
              setUser(null);
              setEpisode(null);
              setEpisodes([]);
            }}
          >
            <button className="quiet">Sign out</button>
          </AsyncForm>
        </div>
      </header>
      <ProjectPicker
        selected={project}
        onSelect={(code) => {
          if (canLeave()) setProject(code);
        }}
      />
      {project !== "B04" ? (
        <Products key={`${user.id}-${project}`} code={project} />
      ) : (
        <div className="layout">
          <aside className="sidebar">
            <div className="eyebrow">YOUR WORKSPACE</div>
            <button className="new" onClick={newEpisode}>
              + New episode
            </button>
            {user.role === "editor" && (
              <button
                className="secondary wide"
                onClick={() => setInvite(!invite)}
              >
                Invite a client
              </button>
            )}
            <div className="sidebar-title">
              EPISODES <span>{episodes.length}</span>
            </div>
            {episodes.map((ep) => (
              <button
                key={ep.id}
                className={`episode ${ep.id === episode?.id ? "active" : ""}`}
                onClick={() => selected(ep.id)}
              >
                <strong>{ep.title}</strong>
                <span>
                  {ep.brand} · V{ep.version} · {ep.phase}
                </span>
              </button>
            ))}
            {!episodes.length && (
              <p className="note">
                No episodes yet. Create your first brief to get started.
              </p>
            )}
            <div className="scope">
              <strong>ONE EPISODE. A COMPLETE PACKAGE.</strong>
              <p>
                3 vertical clips
                <br />
                Captions & post copy
                <br />
                Chapters & editable project
              </p>
              <p>7 calendar days from confirmed readiness.</p>
            </div>
          </aside>
          <main className="workspace">
            {error && (
              <div role="alert" className="notice error">
                {error}
                <button
                  className="secondary"
                  onClick={() =>
                    load()
                      .then(() => setError(""))
                      .catch((e) => setError(e.message))
                  }
                >
                  Retry
                </button>
              </div>
            )}
            {invite && <Invite onClose={() => setInvite(false)} />}{" "}
            {detailBusy && <p role="status">Loading episode…</p>}
            {create ? (
              <section className="card">
                <div className="eyebrow">START WITH THE SOURCE</div>
                <h1>Create an episode</h1>
                <p className="subtitle">
                  One source recording, three considered stories.
                </p>
                <AsyncForm
                  submit={async (d) => {
                    const ep = await request<Episode>(
                      "/episodes",
                      json({
                        ...briefData(d),
                        ...(user.role === "editor"
                          ? { clientId: text(d, "clientId") }
                          : {}),
                      }),
                    );
                    update(ep);
                    setCreate(false);
                  }}
                >
                  {user.role === "editor" && (
                    <label>
                      Client
                      <select required name="clientId">
                        <option value="">Choose a client</option>
                        {members
                          .filter((x) => x.role === "client")
                          .map((x) => (
                            <option key={x.id} value={x.id}>
                              {x.name} · {x.email}
                            </option>
                          ))}
                      </select>
                    </label>
                  )}
                  {user.role === "editor" &&
                    !members.some((x) => x.role === "client") && (
                      <p className="notice">
                        Invite a client and have them join, then{" "}
                        <button
                          type="button"
                          className="link"
                          onClick={() =>
                            request<{ members: User[] }>("/members")
                              .then((r) => setMembers(r.members))
                              .catch((e) => setError(e.message))
                          }
                        >
                          refresh clients
                        </button>
                        .
                      </p>
                    )}
                  <BriefFields />
                  <button>Create draft</button>
                </AsyncForm>
              </section>
            ) : episode ? (
              <EpisodeView
                key={`${episode.id}-${reloadEpoch}`}
                episode={episode}
                user={user}
                update={update}
                reload={() => selected(episode.id, true)}
              />
            ) : (
              <section className="welcome">
                <div className="eyebrow">THE EDITORIAL WORKSPACE</div>
                <h1>
                  Great conversations.
                  <br />
                  Ready for their next audience.
                </h1>
                <p>
                  Bring your source, shape three stories, and review every
                  detail in one place.
                </p>
                <button onClick={newEpisode}>
                  Create your first episode →
                </button>
                <div className="welcome-grid">
                  <div>
                    <b>01</b>
                    <h3>A clear brief</h3>
                    <p>Agree on the source, boundaries and goals.</p>
                  </div>
                  <div>
                    <b>02</b>
                    <h3>Thoughtful review</h3>
                    <p>Approve the words and context for each clip.</p>
                  </div>
                  <div>
                    <b>03</b>
                    <h3>A finished package</h3>
                    <p>Receive checked files and confirm delivery.</p>
                  </div>
                </div>
              </section>
            )}
          </main>
        </div>
      )}
    </>
  );
}
function Auth({
  onLogin,
  message,
}: {
  onLogin: (u: User) => void;
  message: string;
}) {
  const token = new URLSearchParams(location.search).get("token");
  const [mode, setMode] = useState(token ? "join" : "login");
  return (
    <main className="auth">
      <section className="auth-story">
        <a className="brand" href="/">
          OS<span>Overseas Studio</span>
        </a>
        <div>
          <div className="eyebrow">FROM CONVERSATION TO CONNECTION</div>
          <h1>
            Your best ideas.
            <br />A longer life.
          </h1>
          <p>
            A considered workspace for turning long conversations into short
            stories worth sharing.
          </p>
        </div>
        <p className="note">BRIEF · EDIT · REVIEW · DELIVER</p>
      </section>
      <section className="auth-form">
        <div className="eyebrow">WELCOME TO YOUR DESK</div>
        <h2>
          {mode === "join"
            ? "Join your studio"
            : mode === "register"
              ? "Create your studio"
              : "Welcome back"}
        </h2>
        {message && <p role="alert">{message}</p>}
        <AsyncForm
          key={mode}
          submit={async (d) => {
            const endpoint = mode === "join" ? "accept-invite" : mode;
            const payload =
              mode === "join"
                ? {
                    token,
                    name: text(d, "name"),
                    password: text(d, "password"),
                  }
                : Object.fromEntries(d);
            const session = await request<{ token: string; user: User }>(
              `/auth/${endpoint}`,
              json(payload),
            );
            sessionStorage.setItem("episode-token", session.token);
            history.replaceState(null, "", "/");
            onLogin(session.user);
          }}
        >
          {mode !== "login" && <Field name="name" label="Your name" required />}
          {mode === "register" && (
            <Field name="workspaceName" label="Studio name" required />
          )}
          {mode !== "join" && (
            <Field name="email" label="Email address" type="email" required />
          )}
          <Field
            name="password"
            label="Password (at least 10 characters)"
            type="password"
            required
          />
          <button className="wide">
            {mode === "join"
              ? "Accept invitation"
              : mode === "register"
                ? "Create studio"
                : "Sign in"}
          </button>
        </AsyncForm>
        {mode !== "join" && (
          <button
            className="link"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login"
              ? "New here? Create a studio"
              : "Already have an account? Sign in"}
          </button>
        )}
        <p className="note">
          Clients join with a private invitation from their editor.
        </p>
      </section>
    </main>
  );
}
function Invite({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState(""),
    [copied, setCopied] = useState(false);
  return (
    <section className="card">
      <div className="heading">
        <h2>Invite a client</h2>
        <button className="link" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="note">
        Create a private, one-use invitation. Copy the link and share it with
        your client. No email is sent automatically.
      </p>
      <AsyncForm
        submit={async (d) => {
          const invite = await request<{ token: string }>(
            "/invites",
            json({ email: text(d, "email") }),
          );
          setUrl(
            `${location.origin}/join?token=${encodeURIComponent(invite.token)}`,
          );
          setCopied(false);
        }}
      >
        <Field name="email" label="Client email" type="email" required />
        <button>Create invitation</button>
      </AsyncForm>
      {url && (
        <>
          <label>
            Invitation link
            <input readOnly value={url} onFocus={(e) => e.target.select()} />
          </label>
          <AsyncForm
            submit={async () => {
              await navigator.clipboard.writeText(url);
              setCopied(true);
            }}
          >
            <button className="secondary">
              {copied ? "Copied" : "Copy invitation"}
            </button>
          </AsyncForm>
        </>
      )}
    </section>
  );
}
function briefData(d: FormData) {
  return {
    title: text(d, "title"),
    brand: text(d, "brand"),
    source: text(d, "source"),
    duration: Number(d.get("duration")),
    notes: text(d, "notes"),
    glossary: text(d, "glossary"),
    prohibitedClaims: text(d, "prohibitedClaims"),
  };
}
function BriefFields({ ep }: { ep?: Episode }) {
  return (
    <>
      <Field name="title" label="Episode title" value={ep?.title} required />
      <div className="two">
        <Field name="brand" label="Brand / show" value={ep?.brand} required />
        <Field
          name="duration"
          label="Source duration (seconds, 90–3600)"
          type="number"
          min={90}
          max={3600}
          value={ep?.duration || 1800}
          required
        />
      </div>
      <Field
        name="source"
        label="Source URL or delivery reference"
        value={ep?.source}
        required
      />
      <Field
        name="notes"
        label="Editorial goals & audience"
        multiline
        value={ep?.notes}
      />
      <Field
        name="glossary"
        label="Names & pronunciation / glossary"
        multiline
        value={ep?.glossary}
      />
      <Field
        name="prohibitedClaims"
        label="Prohibited claims (write None if none)"
        multiline
        value={ep?.prohibitedClaims}
        required
      />
    </>
  );
}
function EpisodeView({
  episode: ep,
  user,
  update,
  reload,
}: {
  episode: Episode;
  user: User;
  update: (e: Episode) => void;
  reload: () => void;
}) {
  const { dirty } = useDirty();
  const [project, setProject] = useState("B04");
  const [tab, setTab] = useState("Brief");
  const editor = user.role === "editor";
  const mutate = async (path: string, body: unknown, method = "POST") =>
    update(
      await request<Episode>(
        `/episodes/${ep.id}/${path}`,
        json({ revision: ep.revision, ...(body as object) }, method),
      ),
    );
  const action = (name: string, body: unknown = {}) =>
    mutate(`actions/${name}`, body);
  const current = ep.assets.filter((a) => a.version === ep.version);
  return (
    <>
      <div className="heading">
        <div>
          <div className="eyebrow">{ep.brand} / EPISODE WORKSPACE</div>
          <h1>{ep.title}</h1>
          <p className="subtitle">
            Version {ep.version} · {ep.duration / 60} min source · Due{" "}
            {date(ep.dueAt)}
          </p>
        </div>
        <span className={`state ${ep.phase}`}>{ep.phase}</span>
      </div>
      <ol className="progress">
        {phases.map((p, i) => (
          <li key={p} className={i <= phases.indexOf(ep.phase) ? "done" : ""}>
            <span>{i + 1}</span>
            {p}
          </li>
        ))}
      </ol>
      <nav aria-label="Episode sections">
        {["Brief", "Clips & review", "Files & delivery", "History"].map((t) => (
          <button
            key={t}
            className={tab === t ? "selected" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      <div hidden={tab !== "Brief"}>
        <div className="grid">
          <section className="card">
            <h2>The editorial brief</h2>
            <AsyncForm
              key={`${ep.version}-${ep.phase}-brief`}
              reload={reload}
              disabled={editor || ep.phase !== "draft"}
              submit={(d) =>
                mutate(
                  "brief",
                  { ...briefData(d), rights: d.has("rights") },
                  "PUT",
                )
              }
            >
              <BriefFields ep={ep} />
              <Check name="rights" checked={ep.rights}>
                I have permission to use this recording and authorize editing
                and publication of the agreed clips.
              </Check>
              {!editor && ep.phase === "draft" && <button>Save brief</button>}
            </AsyncForm>
            {!editor && ep.phase === "draft" && (
              <AsyncForm reload={reload} submit={() => action("submit")}>
                <p className="note">
                  Save your brief and source authorization before submitting.
                </p>
                <button disabled={dirty}>Submit brief</button>
                {dirty && (
                  <p className="note">
                    Save all changed fields before submitting.
                  </p>
                )}
              </AsyncForm>
            )}
          </section>
          <aside>
            <section className="card">
              <div className="eyebrow">PRODUCTION NOTES</div>
              <h2>Ready means ready.</h2>
              <p className="note">
                The seven-day delivery clock starts when your editor has
                verified the source, scope, qualification and payment
                arrangement.
              </p>
              <div className="row">
                <span>Source authorized</span>
                <strong>{ep.rights ? "Yes" : "Not yet"}</strong>
              </div>
              <div className="row">
                <span>Started</span>
                <strong>{date(ep.readyAt)}</strong>
              </div>
              <div className="row">
                <span>Delivery target</span>
                <strong>{date(ep.dueAt)}</strong>
              </div>
            </section>
            {editor && ep.phase === "submitted" && (
              <section className="card">
                <h2>Confirm readiness</h2>
                <AsyncForm
                  reload={reload}
                  submit={(d) =>
                    action("ready", {
                      sourceUsable: d.has("sourceUsable"),
                      scopeConfirmed: d.has("scopeConfirmed"),
                      editorQualified: d.has("editorQualified"),
                      paymentPathConfirmed: d.has("paymentPathConfirmed"),
                    })
                  }
                >
                  <Check name="sourceUsable">
                    I reviewed the source; picture and audio are usable.
                  </Check>
                  <Check name="scopeConfirmed">
                    The three-clip scope and editorial boundaries are agreed.
                  </Check>
                  <Check name="editorQualified">
                    A qualified editor is assigned.
                  </Check>
                  <Check name="paymentPathConfirmed">
                    The payment arrangement is confirmed manually.
                  </Check>
                  <button>Confirm ready & start</button>
                </AsyncForm>
              </section>
            )}
            <section className="card">
              <h2>Source recording</h2>
              <p className="note">{ep.source}</p>
              {current
                .filter((a) => a.kind === "source")
                .map((a) => (
                  <AssetItem
                    key={a.id}
                    asset={a}
                    ep={ep}
                    editor={editor}
                    mutate={mutate}
                    reload={reload}
                  />
                ))}
              {((!editor && ["draft", "submitted"].includes(ep.phase)) ||
                (editor && ["submitted", "editing"].includes(ep.phase))) && (
                <Upload ep={ep} kind="source" update={update} reload={reload} />
              )}
            </section>
          </aside>
        </div>
      </div>
      <div hidden={tab !== "Clips & review"}>
        {ep.clips.map((clip, index) => (
          <ClipCard
            key={`${ep.version}-${clip.id}`}
            clip={clip}
            index={index}
            ep={ep}
            editor={editor}
            mutate={mutate}
            action={action}
            reload={reload}
          />
        ))}
        <section className="card">
          <h2>Episode chapters</h2>
          {editor && ep.phase === "editing" ? (
            <AsyncForm
              reload={reload}
              submit={(d) =>
                mutate("chapters", { chapters: text(d, "chapters") }, "PUT")
              }
            >
              <Field
                name="chapters"
                label="Timestamped chapter list"
                value={ep.chapters}
                multiline
                required
              />
              <button>Save chapters</button>
            </AsyncForm>
          ) : (
            <pre className="copy">
              {ep.chapters ||
                "Chapters will appear when your editor prepares the package."}
            </pre>
          )}
        </section>
        {editor && ep.phase === "editing" && (
          <section className="card">
            <h2>Ready for the client?</h2>
            <p className="note">
              Save all three clips, check each clip, and save the chapter list.
              Sending freezes this version for review.
            </p>
            <AsyncForm reload={reload} submit={() => action("send")}>
              <button disabled={dirty}>
                Send version {ep.version} for review
              </button>
              {dirty && (
                <p className="note">
                  Save all changed clips and chapters before sending.
                </p>
              )}
            </AsyncForm>
          </section>
        )}
        {ep.phase === "approved" && (
          <div className="notice">
            All three clips are approved. The editor can now upload and check
            the final files.
          </div>
        )}
      </div>
      <div hidden={tab !== "Files & delivery"}>
        <section className="card">
          <h2>Version {ep.version} · Delivery package</h2>
          <p className="note">
            Each clip needs a vertical MP4 with audio and an SRT subtitle file.
            All six files and the editable project must pass validation and
            editorial QC before delivery.
          </p>
          <div className="actions">
            <AsyncForm
              submit={() =>
                download(`/episodes/${ep.id}/export`, `${ep.title}-review.json`)
              }
            >
              <button className="secondary">Export review manifest</button>
            </AsyncForm>
            <AsyncForm
              submit={() =>
                download(`/episodes/${ep.id}/copy`, `${ep.title}-copy.txt`)
              }
            >
              <button className="secondary">Download copy & chapters</button>
            </AsyncForm>
          </div>
        </section>
        {ep.clips.map((clip, i) => (
          <section className="card" key={clip.id}>
            <h2>
              0{i + 1} · {clip.title || `Clip ${i + 1}`}
            </h2>
            {["video", "subtitle"].map((kind) => (
              <div key={kind}>
                <h3>
                  {kind === "video"
                    ? "Final video · MP4, 9:16"
                    : "Subtitles · SRT"}
                </h3>
                {current
                  .filter((a) => a.kind === kind && a.clipId === clip.id)
                  .map((a) => (
                    <AssetItem
                      key={a.id}
                      asset={a}
                      ep={ep}
                      editor={editor}
                      mutate={mutate}
                      reload={reload}
                    />
                  ))}
                {!current.some(
                  (a) => a.kind === kind && a.clipId === clip.id,
                ) && <p className="note">No current-version file uploaded.</p>}
                {editor && ep.phase === "approved" && (
                  <Upload
                    ep={ep}
                    kind={kind}
                    clipId={clip.id}
                    update={update}
                    reload={reload}
                  />
                )}
              </div>
            ))}
          </section>
        ))}
        <section className="card">
          <h2>Editable project</h2>
          {current
            .filter((a) => a.kind === "project")
            .map((a) => (
              <AssetItem
                key={a.id}
                asset={a}
                ep={ep}
                editor={editor}
                mutate={mutate}
                reload={reload}
              />
            ))}
          {editor && ep.phase === "approved" && (
            <Upload ep={ep} kind="project" update={update} reload={reload} />
          )}
        </section>
        {editor && ep.phase === "approved" && (
          <section className="card">
            <h2>Deliver the finished package</h2>
            <p className="note">
              Delivery becomes available once the actual files pass server
              validation and you have marked every file as checked.
            </p>
            <AsyncForm reload={reload} submit={() => action("deliver")}>
              <button>Deliver package</button>
            </AsyncForm>
          </section>
        )}
        {!editor && ep.phase === "delivered" && (
          <section className="card">
            <h2>Your package is ready</h2>
            <AsyncForm
              reload={reload}
              submit={(d) => action("accept", { read: d.has("read") })}
            >
              <Check name="read">
                I downloaded and checked the package, and confirm receipt.
              </Check>
              <button>Accept delivery</button>
            </AsyncForm>
          </section>
        )}
        {ep.phase === "accepted" && (
          <div className="notice">
            Delivery accepted. The receipt is recorded in the activity history.
          </div>
        )}
      </div>
      <div hidden={tab !== "History"}>
        <section className="card">
          <h2>Frozen review versions</h2>
          {!ep.history.length && (
            <p className="empty">
              A version snapshot is saved each time the package is sent for
              review.
            </p>
          )}
          {ep.history.map((h, i) => (
            <details key={`${h.version}-${i}`}>
              <summary>
                Version {h.version} · {date(h.sentAt)}
              </summary>
              <p>
                {h.title} · {h.source}
              </p>
              {h.clips.map((c) => (
                <div key={c.id} className="history-clip">
                  <strong>{c.title}</strong>
                  <p>
                    {c.start}–{c.end}s · {c.status}
                  </p>
                  <p>{c.quote}</p>
                  <p>{c.context}</p>
                  <pre className="copy">{c.post}</pre>
                </div>
              ))}
              <pre className="copy">{h.chapters}</pre>
              {h.decisions?.map((d, i) => (
                <p key={i} className="note">
                  {d.clipId} · {d.status} · {d.feedback} · {date(d.time)}
                </p>
              ))}
            </details>
          ))}
        </section>
        <section className="card">
          <h2>Activity log</h2>
          {[...ep.events].reverse().map((event) => (
            <div className="row" key={event.id}>
              <div>
                <strong>{event.action.replaceAll("_", " ")}</strong>
                <p className="note">
                  {event.actorName} · Version {event.version}
                </p>
              </div>
              <time className="note">{date(event.time)}</time>
            </div>
          ))}
        </section>
        <section className="card">
          <h2>Historical files</h2>
          {ep.assets
            .filter((a) => a.version !== ep.version)
            .map((a) => (
              <AssetItem
                key={a.id}
                asset={a}
                ep={ep}
                editor={editor}
                mutate={mutate}
                reload={reload}
              />
            ))}
          {!ep.assets.some((a) => a.version !== ep.version) && (
            <p className="note">No files from previous versions.</p>
          )}
        </section>
      </div>
      {ep.phase !== "accepted" && (
        <details className="card">
          <summary>Revision & source changes</summary>
          {editor && ["review", "approved", "delivered"].includes(ep.phase) && (
            <AsyncForm
              reload={reload}
              submit={(d) => action("revise", { reason: text(d, "reason") })}
            >
              <h3>Revise the package</h3>
              <p className="note">
                Starts a new version and clears all approvals and current-file
                eligibility. The existing deadline remains.
              </p>
              <Field name="reason" label="Revision reason" multiline required />
              <button className="secondary">Start new revision</button>
            </AsyncForm>
          )}
          <AsyncForm
            reload={reload}
            submit={(d) => action("reopen", { reason: text(d, "reason") })}
          >
            <h3>Replace the source / reopen brief</h3>
            <p className="note">
              Reopens the brief, clears approvals and source authorization, and
              resets the production clock. The client must authorize and submit
              again.
            </p>
            <Field
              name="reason"
              label="Source change reason"
              multiline
              required
            />
            <button className="secondary">Reopen brief</button>
          </AsyncForm>
        </details>
      )}
    </>
  );
}
type Mutate = (path: string, body: unknown, method?: string) => Promise<void>;
function ClipCard({
  clip: c,
  index,
  ep,
  editor,
  mutate,
  action,
  reload,
}: {
  clip: Clip;
  index: number;
  ep: Episode;
  editor: boolean;
  mutate: Mutate;
  action: (n: string, b?: unknown) => Promise<void>;
  reload: () => void;
}) {
  return (
    <section className="card clip-card">
      <div className="heading">
        <div>
          <div className="eyebrow">
            CLIP 0{index + 1} / VERSION {ep.version}
          </div>
          <h2>{c.title || "A story waiting to be shaped"}</h2>
        </div>
        <span className={`state ${c.status}`}>{c.status}</span>
      </div>
      {editor && ep.phase === "editing" ? (
        <AsyncForm
          reload={reload}
          submit={(d) =>
            mutate(
              `clips/${c.id}`,
              {
                title: text(d, "title"),
                start: Number(d.get("start")),
                end: Number(d.get("end")),
                quote: text(d, "quote"),
                context: text(d, "context"),
                post: text(d, "post"),
                checked: d.has("checked"),
              },
              "PUT",
            )
          }
        >
          <Field
            name="title"
            label={`Clip ${index + 1} title`}
            value={c.title}
            required
          />
          <div className="two">
            <Field
              name="start"
              label="Start (seconds)"
              type="number"
              min={0}
              max={ep.duration - 30}
              value={c.start}
              required
            />
            <Field
              name="end"
              label="End (seconds, 30–90 second clip)"
              type="number"
              min={30}
              max={ep.duration}
              value={c.end}
              required
            />
          </div>
          <Field
            name="quote"
            label="Exact quote / subtitle copy"
            value={c.quote}
            multiline
            required
          />
          <Field
            name="context"
            label="Context & editorial rationale"
            value={c.context}
            multiline
            required
          />
          <Field
            name="post"
            label="Post copy"
            value={c.post}
            multiline
            required
          />
          <Check name="checked" checked={c.checked} required={false}>
            I checked the quote, timing, context and prohibited claims against
            the source.
          </Check>
          <button>Save clip {index + 1}</button>
        </AsyncForm>
      ) : (
        <div className="grid">
          <div>
            <p className="note">
              SOURCE RANGE · {c.start}s — {c.end}s · {c.end - c.start}s
            </p>
            <blockquote>
              {c.quote ||
                "Your editor will prepare this clip after readiness is confirmed."}
            </blockquote>
            <h3>Context</h3>
            <p className="context">{c.context || "Not prepared yet"}</p>
            <h3>Post copy</h3>
            <pre className="copy">{c.post || "Not prepared yet"}</pre>
          </div>
          <aside className="review-panel">
            <div className="eyebrow">EDITORIAL REVIEW</div>
            <p className="note">
              Review the wording and source context. Final media files are
              checked separately before delivery.
            </p>
            {c.feedback && (
              <p className="feedback">Requested changes: {c.feedback}</p>
            )}
            {!editor && ep.phase === "review" && c.status === "pending" && (
              <>
                <AsyncForm
                  reload={reload}
                  submit={(d) =>
                    action("approve", {
                      version: ep.version,
                      clipId: c.id,
                      read: d.has("read"),
                    })
                  }
                >
                  <Check name="read">
                    I read this clip and checked its context.
                  </Check>
                  <button>Approve clip {index + 1}</button>
                </AsyncForm>
                <AsyncForm
                  reload={reload}
                  submit={(d) =>
                    action("changes", {
                      version: ep.version,
                      clipId: c.id,
                      feedback: text(d, "feedback"),
                    })
                  }
                >
                  <Field
                    name="feedback"
                    label={`Changes for clip ${index + 1}`}
                    multiline
                    required
                  />
                  <button className="secondary">
                    Request changes for clip {index + 1}
                  </button>
                </AsyncForm>
              </>
            )}
            {c.status === "approved" && (
              <p className="success">Approved for this version.</p>
            )}
            {c.status === "changes" && (
              <p className="note">
                The editor must start a new revision before further approval.
              </p>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}
function Upload({
  ep,
  kind,
  clipId,
  update,
  reload,
}: {
  ep: Episode;
  kind: string;
  clipId?: string;
  update: (e: Episode) => void;
  reload: () => void;
}) {
  return (
    <AsyncForm
      reload={reload}
      submit={async (d) => {
        d.set("revision", String(ep.revision));
        d.set("kind", kind);
        if (clipId) d.set("clipId", clipId);
        update(
          await request<Episode>(`/episodes/${ep.id}/assets`, {
            method: "POST",
            body: d,
          }),
        );
      }}
    >
      <label>
        Upload {kind}
        {clipId ? ` for ${clipId}` : ""}
        <input
          name="file"
          type="file"
          required
          accept={
            kind === "source"
              ? ".mp4,.mov,.webm"
              : kind === "video"
                ? ".mp4"
                : kind === "subtitle"
                  ? ".srt"
                  : ".json,.txt"
          }
        />
      </label>
      <p className="note">
        Maximum 100 MB. Replacing a file clears its QC check.
      </p>
      <button className="secondary">Upload {kind}</button>
    </AsyncForm>
  );
}
function AssetItem({
  asset: a,
  ep,
  editor,
  mutate,
  reload,
}: {
  asset: Asset;
  ep: Episode;
  editor: boolean;
  mutate: Mutate;
  reload: () => void;
}) {
  const [preview, setPreview] = useState("");
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  const allowed =
    editor ||
    (a.version === ep.version &&
      (a.kind === "source" ||
        ["approved", "delivered", "accepted"].includes(ep.phase)));
  return (
    <div className="asset">
      <div className="asset-top">
        <div>
          <strong>{a.filename}</strong>
          <p className="note">
            V{a.version} · {(a.size / 1024 / 1024).toFixed(2)} MB ·{" "}
            {a.checked ? "QC checked" : "Awaiting QC"}
          </p>
        </div>
        {allowed && (
          <AsyncForm
            submit={() =>
              download(`/episodes/${ep.id}/assets/${a.id}/download`, a.filename)
            }
          >
            <button className="secondary">Download {a.kind}</button>
          </AsyncForm>
        )}
      </div>
      {allowed && (a.kind === "source" || a.kind === "video") && (
        <>
          <AsyncForm
            submit={async () => {
              const r = await response(
                `/episodes/${ep.id}/assets/${a.id}/download`,
              );
              if (preview) URL.revokeObjectURL(preview);
              setPreview(URL.createObjectURL(await r.blob()));
            }}
          >
            <button className="link">Preview {a.kind}</button>
          </AsyncForm>
          {preview && (
            <video
              src={preview}
              controls
              aria-label={`${a.filename} preview`}
            />
          )}
        </>
      )}
      {editor &&
        ep.phase === "approved" &&
        a.version === ep.version &&
        a.kind !== "source" &&
        !a.checked && (
          <AsyncForm
            reload={reload}
            submit={(d) =>
              mutate(`assets/${a.id}/check`, { checked: d.has("checked") })
            }
          >
            <Check name="checked">
              I viewed this file and checked content, playback / readability and
              completeness.
            </Check>
            <button className="secondary">Mark {a.kind} QC checked</button>
          </AsyncForm>
        )}
    </div>
  );
}
