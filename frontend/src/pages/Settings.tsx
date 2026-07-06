import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Integrations, LibraryProfile, OIDCSettings, TwoFASetup } from "../api";
import { Spinner } from "../components/ui";

const H2 = "text-sm font-semibold uppercase tracking-wide text-slate-300";

// --- Security (2FA) --------------------------------------------------------

function SecuritySection() {
  const qc = useQueryClient();
  const { data: me, isLoading } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [setup, setSetup] = useState<TwoFASetup | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [disableCode, setDisableCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["me"] });

  if (isLoading || !me) return <Spinner />;

  const wrap = async (fn: () => Promise<void>) => {
    setErr(null);
    try {
      await fn();
    } catch (e: any) {
      setErr(e?.message || "Something went wrong");
    }
  };

  return (
    <section className="space-y-3">
      <h2 className={H2}>Two-factor authentication</h2>

      {me.totp_enabled ? (
        <div className="card space-y-3 p-4">
          <div className="text-sm text-emerald-400">✓ 2FA is enabled on your account.</div>
          <p className="text-sm text-slate-500">
            To turn it off, enter a current authenticator code or a recovery code.
          </p>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="label">Code</label>
              <input className="input" value={disableCode} onChange={(e) => setDisableCode(e.target.value)} />
            </div>
            <button
              className="btn-danger"
              onClick={() => wrap(async () => {
                await api.twofaDisable(disableCode.trim());
                setDisableCode("");
                refresh();
              })}
            >
              Disable 2FA
            </button>
          </div>
        </div>
      ) : recovery ? (
        <div className="card space-y-3 p-4">
          <div className="text-sm text-emerald-400">
            ✓ 2FA enabled. Save these recovery codes somewhere safe — each works once if you lose your
            authenticator.
          </div>
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-ink-850 p-3 font-mono text-sm text-slate-200 sm:grid-cols-2">
            {recovery.map((c) => (
              <div key={c}>{c}</div>
            ))}
          </div>
          <button className="btn-primary" onClick={() => setRecovery(null)}>
            I’ve saved them
          </button>
        </div>
      ) : setup ? (
        <div className="card space-y-3 p-4">
          <p className="text-sm text-slate-400">
            Scan this QR in an authenticator app (Google Authenticator, Authy, 1Password…), or enter the
            secret manually, then type the 6-digit code to confirm.
          </p>
          <img src={setup.qr} alt="2FA QR code" className="h-44 w-44 rounded bg-white p-2" />
          <div className="text-xs text-slate-500">
            Secret: <span className="select-all font-mono text-slate-300">{setup.secret}</span>
          </div>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="label">6-digit code</label>
              <input className="input tracking-widest" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
            </div>
            <button
              className="btn-primary"
              onClick={() => wrap(async () => {
                const r = await api.twofaEnable(code.trim());
                setRecovery(r.recovery_codes);
                setSetup(null);
                setCode("");
                refresh();
              })}
            >
              Confirm &amp; enable
            </button>
          </div>
        </div>
      ) : (
        <div className="card space-y-3 p-4">
          <p className="text-sm text-slate-500">
            Require a time-based one-time code (TOTP) at sign-in, in addition to your password.
          </p>
          <button className="btn-primary" onClick={() => wrap(async () => setSetup(await api.twofaSetup()))}>
            Enable 2FA
          </button>
        </div>
      )}

      {err && <div className="text-sm text-red-400">{err}</div>}
    </section>
  );
}

// --- OIDC single sign-on ----------------------------------------------------

function OIDCSection() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["oidc"], queryFn: api.oidcSettings });
  const [form, setForm] = useState<OIDCSettings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  if (isLoading || !form) return <Spinner />;
  const set = (patch: Partial<OIDCSettings>) => {
    setForm({ ...form, ...patch });
    setMsg(null);
  };
  const save = async () => {
    try {
      const saved = await api.updateOidcSettings(form);
      qc.setQueryData(["oidc"], saved);
      setForm(saved);
      setMsg("Saved");
    } catch (e: any) {
      setMsg(e?.message || "Could not save");
    }
  };

  return (
    <section className="space-y-3">
      <h2 className={H2}>Single sign-on (OIDC)</h2>
      <div className="card space-y-3 p-4">
        <p className="text-sm text-slate-500">
          Sign in through an OpenID Connect provider (Authentik, Keycloak, Authelia, Google…). The
          identity must match an existing Unbound user by email or username — accounts are never
          created automatically. When SSO is used, your provider is responsible for MFA.
        </p>
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => set({ enabled: e.target.checked })}
          />
          Enable OIDC sign-on
        </label>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <label className="label">Issuer URL</label>
            <input
              className="input"
              placeholder="https://auth.example.com/application/o/unbound/"
              value={form.issuer || ""}
              onChange={(e) => set({ issuer: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Client ID</label>
            <input
              className="input"
              value={form.client_id || ""}
              onChange={(e) => set({ client_id: e.target.value })}
            />
          </div>
          <div>
            <label className="label">
              Client secret{" "}
              {form.client_secret_set && (
                <span className="text-emerald-400">(set — leave blank to keep)</span>
              )}
            </label>
            <input
              className="input font-mono text-xs"
              type="password"
              placeholder={form.client_secret_set ? "•••••••• (unchanged)" : "paste client secret"}
              value={form.client_secret || ""}
              onChange={(e) => set({ client_secret: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Login button label (optional)</label>
            <input
              className="input"
              placeholder="Single sign-on"
              value={form.button_label || ""}
              onChange={(e) => set({ button_label: e.target.value })}
            />
          </div>
          <div>
            <label className="label">Public URL of this Unbound instance</label>
            <input
              className="input"
              placeholder="https://unbound.example.com"
              value={form.public_base_url || ""}
              onChange={(e) => set({ public_base_url: e.target.value })}
            />
          </div>
        </div>
        {form.redirect_uri && (
          <div className="rounded-lg bg-ink-850 p-2 text-xs text-slate-400">
            Register this redirect URI at your provider:{" "}
            <code className="select-all break-all text-audible-400">{form.redirect_uri}</code>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary" onClick={save}>
            Save
          </button>
          <button
            className="btn-ghost"
            onClick={async () => {
              try {
                await api.updateOidcSettings(form);
                qc.invalidateQueries({ queryKey: ["oidc"] });
                const r = await api.testOidc();
                setMsg(`Provider reachable — auth endpoint: ${r.authorization_endpoint}`);
              } catch (e: any) {
                setMsg(e?.message || "Test failed");
              }
            }}
          >
            Save &amp; test provider
          </button>
          {msg && <span className="text-sm text-slate-400">{msg}</span>}
        </div>
      </div>
    </section>
  );
}

// --- Integrations (automation / AudiobookShelf / notifications) ------------

function IntegrationsSection({ tab }: { tab: "automation" | "abs" | "notifications" }) {
  const { data, isLoading } = useQuery({ queryKey: ["integrations"], queryFn: api.integrations });
  const [form, setForm] = useState<Integrations | null>(null);
  const [saved, setSaved] = useState(false);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [absMsg, setAbsMsg] = useState<string | null>(null);
  const [dlMsg, setDlMsg] = useState<string | null>(null);
  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  if (isLoading || !form) return <Spinner />;
  const set = (patch: Partial<Integrations>) => {
    setForm({ ...form, ...patch });
    setSaved(false);
  };
  const save = async () => {
    await api.updateIntegrations(form);
    setSaved(true);
  };

  return (
    <section className="space-y-3">
      {tab === "automation" && (
        <>
          <h2 className={H2}>Scheduling &amp; automation</h2>
          <div className="card space-y-4 p-4">
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={form.schedule_enabled}
                onChange={(e) => set({ schedule_enabled: e.target.checked })}
              />
              Automatically check Audible on a schedule
            </label>
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <span>Check every</span>
              <input
                type="number"
                min={1}
                max={168}
                className="input w-20"
                value={form.schedule_interval_hours}
                onChange={(e) => set({ schedule_interval_hours: Number(e.target.value) })}
              />
              <span>hours</span>
            </div>
            <label className="flex items-start gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                className="mt-1"
                checked={form.auto_download_new}
                onChange={(e) => set({ auto_download_new: e.target.checked })}
              />
              <span>
                Auto-download items that haven’t been downloaded
                <span className="block text-xs text-slate-500">
                  On each scheduled check, queues a download for any owned, non-excluded title with
                  no download yet. Leave off if your files already exist elsewhere.
                </span>
              </span>
            </label>
            <div className="flex flex-wrap items-center gap-2 border-t border-ink-800 pt-3">
              <button
                className="btn-ghost"
                onClick={async () => {
                  const r = await api.downloadAll();
                  setDlMsg(`Queued ${r.queued} download(s).`);
                }}
              >
                Download all missing now
              </button>
              {dlMsg && <span className="text-sm text-slate-400">{dlMsg}</span>}
            </div>
          </div>
        </>
      )}

      {tab === "abs" && (
        <>
          <h2 className={H2}>AudiobookShelf</h2>
          <div className="card space-y-3 p-4">
            <p className="text-sm text-slate-500">
              Link your AudiobookShelf server so completed downloads deep-link to the exact item. Add
              an API token (ABS → Settings → Users → your user → API Token). Token is stored encrypted.
            </p>
            <div>
              <label className="label">Server URL</label>
              <input
                className="input"
                placeholder="https://abs.example.com"
                value={form.abs_url || ""}
                onChange={(e) => set({ abs_url: e.target.value })}
              />
            </div>
            <div>
              <label className="label">
                API token{" "}
                {form.abs_token_set && (
                  <span className="text-emerald-400">(set — leave blank to keep)</span>
                )}
              </label>
              <input
                className="input font-mono text-xs"
                type="password"
                placeholder={form.abs_token_set ? "•••••••• (unchanged)" : "paste API token"}
                value={form.abs_token || ""}
                onChange={(e) => set({ abs_token: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Library ID (optional — restrict matching to one library)</label>
              <input
                className="input"
                placeholder="auto-detected if blank"
                value={form.abs_library_id || ""}
                onChange={(e) => set({ abs_library_id: e.target.value })}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                className="btn-ghost"
                onClick={async () => {
                  await save();
                  try {
                    const r = await api.testAudiobookshelf();
                    setAbsMsg(`Connected — ${r.libraries.length} librar${r.libraries.length === 1 ? "y" : "ies"}.`);
                  } catch (e: any) {
                    setAbsMsg(e?.message || "Connection failed");
                  }
                }}
              >
                Save &amp; test connection
              </button>
              <button
                className="btn-ghost"
                onClick={async () => {
                  try {
                    const r = await api.absMatch();
                    setAbsMsg(`Matched ${r.matched} of ${r.checked} downloaded title(s).`);
                  } catch (e: any) {
                    setAbsMsg(e?.message || "Match failed");
                  }
                }}
              >
                Match downloaded books
              </button>
              {absMsg && <span className="text-sm text-slate-400">{absMsg}</span>}
            </div>
          </div>
        </>
      )}

      {tab === "notifications" && (
        <>
          <h2 className={H2}>Notifications</h2>
          <div className="card space-y-3 p-4">
            <p className="text-sm text-slate-500">
              Uses{" "}
              <a className="text-audible-400 underline" href="https://github.com/caronc/apprise#supported-notifications" target="_blank" rel="noreferrer">
                Apprise
              </a>{" "}
              URLs (ntfy, Discord, Telegram, email, webhooks…). One per line. Stored encrypted.
            </p>
            <textarea
              className="input h-24 font-mono text-xs"
              placeholder={"ntfy://ntfy.sh/my-topic\ndiscord://webhook_id/webhook_token"}
              value={form.notify_urls || ""}
              onChange={(e) => set({ notify_urls: e.target.value })}
            />
            <div className="grid gap-2 sm:grid-cols-3">
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={form.notify_on_new_books} onChange={(e) => set({ notify_on_new_books: e.target.checked })} />
                New books
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={form.notify_on_complete} onChange={(e) => set({ notify_on_complete: e.target.checked })} />
                Download complete
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={form.notify_on_failure} onChange={(e) => set({ notify_on_failure: e.target.checked })} />
                Download failed
              </label>
            </div>
            <div>
              <button
                className="btn-ghost"
                onClick={async () => {
                  await save();
                  try {
                    const r = await api.testNotification();
                    setTestMsg(`Sent to ${r.sent_to} target(s).`);
                  } catch (e: any) {
                    setTestMsg(e?.message || "Failed to send");
                  }
                }}
              >
                Save &amp; send test
              </button>
              {testMsg && <span className="ml-2 text-sm text-slate-400">{testMsg}</span>}
            </div>
          </div>
        </>
      )}

      <div className="flex items-center gap-3">
        <button className="btn-primary" onClick={save}>
          Save settings
        </button>
        {saved && <span className="text-sm text-emerald-400">Saved</span>}
      </div>
    </section>
  );
}

// --- Library profiles ------------------------------------------------------

function ProfileForm({ onSaved }: { onSaved: () => void }) {
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("/data/library");
  const [folder, setFolder] = useState("{author}/{series}");
  const [file, setFile] = useState("{title}");
  const [isDefault, setIsDefault] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.createProfile({
        name,
        root_path: rootPath,
        folder_template: folder,
        filename_template: file,
        is_default: isDefault,
        audio_format: "m4b",
      } as Partial<LibraryProfile>);
      setName("");
      onSaved();
    } catch (e: any) {
      setErr(e?.message || "Could not save profile");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card space-y-3 p-4">
      <h3 className="text-sm font-semibold text-slate-300">New library profile</h3>
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <label className="label">Name</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label">Root path (under /data/library)</label>
          <input className="input" value={rootPath} onChange={(e) => setRootPath(e.target.value)} />
        </div>
        <div>
          <label className="label">Folder template</label>
          <input className="input" value={folder} onChange={(e) => setFolder(e.target.value)} />
        </div>
        <div>
          <label className="label">Filename template</label>
          <input className="input" value={file} onChange={(e) => setFile(e.target.value)} />
        </div>
      </div>
      <p className="text-xs text-slate-500">
        Tokens: {"{author} {title} {series} {series_seq} {narrator} {asin} {year}"}
      </p>
      <label className="flex items-center gap-2 text-sm text-slate-400">
        <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
        Use as default profile
      </label>
      {err && <div className="text-sm text-red-400">{err}</div>}
      <button className="btn-primary" disabled={!name || busy} onClick={save}>
        Save profile
      </button>
    </div>
  );
}

function LibraryProfilesSection() {
  const qc = useQueryClient();
  const { data: profiles, isLoading } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const refresh = () => qc.invalidateQueries({ queryKey: ["profiles"] });
  if (isLoading) return <Spinner />;
  return (
    <section className="space-y-3">
      <h2 className={H2}>Library profiles</h2>
      <div className="grid gap-2">
        {(profiles || []).map((p) => (
          <div key={p.id} className="card flex items-center gap-3 p-3">
            <div className="min-w-0 flex-1">
              <div className="text-sm text-slate-100">
                {p.name} {p.is_default && <span className="text-xs text-audible-400">(default)</span>}
              </div>
              <div className="truncate text-xs text-slate-500">
                {p.root_path}/{p.folder_template}/{p.filename_template}.{p.audio_format}
              </div>
            </div>
            <button className="btn-danger !px-2 !py-1 text-xs" onClick={() => api.deleteProfile(p.id).then(refresh)}>
              Delete
            </button>
          </div>
        ))}
      </div>
      <ProfileForm onSaved={refresh} />
    </section>
  );
}

// --- API keys --------------------------------------------------------------

function ApiKeysSection() {
  const qc = useQueryClient();
  const { data: keys, isLoading } = useQuery({ queryKey: ["apiKeys"], queryFn: api.apiKeys });
  const [newKeyName, setNewKeyName] = useState("");
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["apiKeys"] });
  const createKey = async () => {
    const res = await api.createApiKey(newKeyName || "widget");
    setCreatedKey(res.key);
    setNewKeyName("");
    refresh();
  };
  if (isLoading) return <Spinner />;
  return (
    <section className="space-y-3">
      <h2 className={H2}>API keys (Homepage widget)</h2>
      <p className="text-sm text-slate-500">
        Read-only keys let dashboards poll <code className="text-audible-400">/api/stats</code> without a session.
      </p>
      {createdKey && (
        <div className="card border-audible-500/40 bg-audible-500/10 p-3">
          <div className="mb-1 text-xs text-slate-400">Copy this key now — it won’t be shown again:</div>
          <code className="break-all text-sm text-audible-400">{createdKey}</code>
        </div>
      )}
      <div className="grid gap-2">
        {(keys || []).map((k) => (
          <div key={k.id} className="card flex items-center gap-3 p-3">
            <div className="flex-1 text-sm text-slate-200">
              {k.name} <span className="text-xs text-slate-500">· {k.scope}</span>
            </div>
            <button className="btn-danger !px-2 !py-1 text-xs" onClick={() => api.deleteApiKey(k.id).then(refresh)}>
              Revoke
            </button>
          </div>
        ))}
      </div>
      <div className="card flex items-end gap-3 p-3">
        <div className="flex-1">
          <label className="label">New key name</label>
          <input className="input" value={newKeyName} onChange={(e) => setNewKeyName(e.target.value)} placeholder="homepage" />
        </div>
        <button className="btn-primary" onClick={createKey}>
          Create key
        </button>
      </div>
    </section>
  );
}

// --- Tabbed settings shell -------------------------------------------------

const TABS = [
  { id: "automation", label: "Automation" },
  { id: "abs", label: "AudiobookShelf" },
  { id: "notifications", label: "Notifications" },
  { id: "library", label: "Library profiles" },
  { id: "security", label: "Security" },
  { id: "apikeys", label: "API keys" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Settings() {
  const [tab, setTab] = useState<TabId>("automation");
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
      <div className="no-scrollbar flex gap-1 overflow-x-auto border-b border-ink-800">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${
              tab === t.id
                ? "border-audible-500 text-audible-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {(tab === "automation" || tab === "abs" || tab === "notifications") && (
        <IntegrationsSection tab={tab} />
      )}
      {tab === "library" && <LibraryProfilesSection />}
      {tab === "security" && (
        <div className="space-y-6">
          <SecuritySection />
          <OIDCSection />
        </div>
      )}
      {tab === "apikeys" && <ApiKeysSection />}
    </div>
  );
}
