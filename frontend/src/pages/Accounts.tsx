import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Account, api, ApiError, LinkStep } from "../api";
import { AccountBadge, Empty, Spinner, StatusPill } from "../components/ui";

const MARKETS = ["us", "uk", "de", "fr", "ca", "au", "jp", "it", "es", "in", "br"];

function LinkDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"choose" | "guided" | "external">("choose");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [step, setStep] = useState<LinkStep | null>(null);
  const [input, setInput] = useState("");
  const [responseUrl, setResponseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const done = () => {
    qc.invalidateQueries({ queryKey: ["accounts"] });
    onClose();
  };

  const handle = async (fn: () => Promise<LinkStep>) => {
    setBusy(true);
    setError(null);
    try {
      const s = await fn();
      setStep(s);
      setInput("");
      if (s.status === "linked") done();
      if (s.status === "error") setError(s.message || "Linking failed");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const startGuided = () => handle(() => api.linkGuided(account.id, { email, password }));
  const submitOtp = () => handle(() => api.linkOtp(account.id, step!.flow_id!, input));
  const submitCaptcha = () => handle(() => api.linkCaptcha(account.id, step!.flow_id!, input));
  const startExternal = () => handle(() => api.linkExternalStart(account.id));
  const completeExternal = () =>
    handle(() =>
      api.linkExternalComplete(account.id, { flow_id: step!.flow_id!, response_url: responseUrl })
    );

  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-black/60 px-4" onClick={onClose}>
      <div className="card w-full max-w-md p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-100">Link “{account.label}”</h2>
          <button className="text-slate-500 hover:text-slate-300" onClick={onClose}>✕</button>
        </div>

        {mode === "choose" && (
          <div className="space-y-3">
            <p className="text-sm text-slate-400">Choose how to sign in to Audible ({account.marketplace.toUpperCase()}).</p>
            <button className="btn-primary w-full" onClick={() => setMode("guided")}>
              Guided sign-in (email + password)
            </button>
            <button
              className="btn-ghost w-full"
              onClick={() => {
                setMode("external");
                startExternal();
              }}
            >
              External browser sign-in
            </button>
          </div>
        )}

        {mode === "guided" && !step && (
          <div className="space-y-3">
            <div>
              <label className="label">Audible email</label>
              <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div>
              <label className="label">Password</label>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            <p className="text-xs text-slate-500">
              Credentials are used once to register a device, then discarded. Only an encrypted
              device token is stored.
            </p>
            <button className="btn-primary w-full" onClick={startGuided} disabled={busy}>
              {busy ? "Connecting…" : "Sign in"}
            </button>
          </div>
        )}

        {step?.status === "needs_otp" && (
          <div className="space-y-3">
            <label className="label">One-time passcode (OTP)</label>
            <input className="input" value={input} onChange={(e) => setInput(e.target.value)} autoFocus />
            <button className="btn-primary w-full" onClick={submitOtp} disabled={busy}>Submit code</button>
          </div>
        )}

        {step?.status === "needs_captcha" && (
          <div className="space-y-3">
            {step.captcha_image_url && (
              <img src={step.captcha_image_url} alt="CAPTCHA" className="rounded border border-ink-700" />
            )}
            <label className="label">Type the characters shown</label>
            <input className="input" value={input} onChange={(e) => setInput(e.target.value)} autoFocus />
            <button className="btn-primary w-full" onClick={submitCaptcha} disabled={busy}>Submit</button>
          </div>
        )}

        {mode === "external" && (
          <div className="space-y-3">
            {busy && !step && <Spinner />}
            {step?.status === "needs_response_url" && (
              <>
                <p className="text-sm text-slate-400">
                  1. Open this URL in your browser and sign in to Audible:
                </p>
                <a
                  className="block text-xs text-audible-400 break-all underline"
                  href={step.message || "#"}
                  target="_blank"
                  rel="noreferrer"
                >
                  {step.message}
                </a>
                <p className="text-sm text-slate-400">
                  2. After signing in you’ll reach a page that may not load — copy that page’s full
                  URL and paste it here:
                </p>
                <input
                  className="input"
                  placeholder="https://www.amazon.com/ap/maplanding?..."
                  value={responseUrl}
                  onChange={(e) => setResponseUrl(e.target.value)}
                />
                <button className="btn-primary w-full" onClick={completeExternal} disabled={busy}>
                  Complete linking
                </button>
              </>
            )}
          </div>
        )}

        {error && <div className="text-sm text-red-400">{error}</div>}
      </div>
    </div>
  );
}

export default function Accounts() {
  const qc = useQueryClient();
  const { data: accounts, isLoading } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const [linking, setLinking] = useState<Account | null>(null);
  const [label, setLabel] = useState("");
  const [marketplace, setMarketplace] = useState("us");

  const create = useMutation({
    mutationFn: () => api.createAccount({ label, marketplace }),
    onSuccess: () => {
      setLabel("");
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  const act = (fn: () => Promise<unknown>) =>
    fn().then(() => qc.invalidateQueries({ queryKey: ["accounts"] }));

  if (isLoading) return <Spinner />;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Audible accounts</h1>
          <p className="text-sm text-slate-500">Link one or more Audible accounts. Each gets its own badge.</p>
        </div>
      </div>

      <div className="card p-4 flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[160px]">
          <label className="label">New account label</label>
          <input className="input" placeholder="e.g. Personal" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
        <div>
          <label className="label">Marketplace</label>
          <select className="input" value={marketplace} onChange={(e) => setMarketplace(e.target.value)}>
            {MARKETS.map((m) => (
              <option key={m} value={m}>{m.toUpperCase()}</option>
            ))}
          </select>
        </div>
        <button className="btn-primary" disabled={!label || create.isPending} onClick={() => create.mutate()}>
          Add account
        </button>
      </div>

      {(accounts || []).length === 0 ? (
        <Empty>No accounts yet. Add one above to get started.</Empty>
      ) : (
        <div className="grid gap-3">
          {accounts!.map((a) => (
            <div key={a.id} className="card p-4 flex flex-wrap items-center gap-3">
              <AccountBadge label={a.label} color={a.badge_color} />
              <div className="flex-1 min-w-[160px]">
                <div className="text-sm text-slate-200">
                  {a.account_owner_email ||
                    (a.status === "linked" ? "Linked account" : "Not linked")}{" "}
                  <span className="text-slate-500">· {a.marketplace.toUpperCase()}</span>
                </div>
                {a.last_error && <div className="text-xs text-red-400">{a.last_error}</div>}
                {a.last_sync_at && (
                  <div className="text-xs text-slate-500">
                    Last sync {new Date(a.last_sync_at).toLocaleString()}
                  </div>
                )}
              </div>
              <StatusPill status={a.status} />
              <div className="flex gap-2">
                {a.status === "linked" ? (
                  <>
                    <button className="btn-ghost" onClick={() => act(() => api.sync(a.id))}>Sync</button>
                    <button className="btn-ghost" onClick={() => act(() => api.unlink(a.id))}>Unlink</button>
                  </>
                ) : (
                  <button className="btn-primary" onClick={() => setLinking(a)}>Link</button>
                )}
                <button className="btn-danger" onClick={() => act(() => api.deleteAccount(a.id))}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {linking && <LinkDialog account={linking} onClose={() => setLinking(null)} />}
    </div>
  );
}
