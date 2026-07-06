import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { Logo } from "../components/ui";

function CoverBackdrop() {
  const [covers, setCovers] = useState<string[]>([]);
  useEffect(() => {
    api
      .covers()
      .then((r) => setCovers(r.covers))
      .catch(() => {});
  }, []);
  if (covers.length === 0) return null;
  // Tile the covers to fill the viewport; duplicate URLs are cached by the browser.
  const tiles = Array.from({ length: 96 }, (_, i) => covers[i % covers.length]);
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="flex scale-110 flex-wrap opacity-20 blur-[2px]">
        {tiles.map((url, i) => (
          <img key={i} src={url} alt="" className="h-32 w-24 object-cover sm:h-40 sm:w-28" />
        ))}
      </div>
      <div className="absolute inset-0 bg-gradient-to-b from-ink-950/85 via-ink-950/80 to-ink-950/95" />
    </div>
  );
}

export default function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status });

  // Failed OIDC callbacks land back here with the reason in the query string.
  useEffect(() => {
    const oidcError = new URLSearchParams(window.location.search).get("oidc_error");
    if (oidcError) {
      setError(oidcError);
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  const submitPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api.login({ username, password });
      if (res.mfa_required && res.mfa_token) {
        setMfaToken(res.mfa_token); // move to the code step
      } else {
        onDone();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.login2fa(mfaToken!, code.trim());
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <CoverBackdrop />
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <Logo size={36} />
        </div>
        {mfaToken ? (
          <form onSubmit={submitCode} className="card space-y-4 p-6 shadow-2xl">
            <h1 className="text-lg font-semibold text-slate-100">Two-factor authentication</h1>
            <p className="text-sm text-slate-400">
              Enter the 6-digit code from your authenticator app, or a recovery code.
            </p>
            <div>
              <label className="label">Code</label>
              <input
                className="input tracking-widest"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                autoFocus
                autoComplete="one-time-code"
                inputMode="numeric"
                required
              />
            </div>
            {error && <div className="text-sm text-red-400">{error}</div>}
            <button className="btn-primary w-full" disabled={busy}>
              {busy ? "Verifying…" : "Verify"}
            </button>
            <button
              type="button"
              className="w-full text-xs text-slate-500 hover:text-slate-300"
              onClick={() => {
                setMfaToken(null);
                setCode("");
                setError(null);
              }}
            >
              ← Back
            </button>
          </form>
        ) : (
          <form onSubmit={submitPassword} className="card space-y-4 p-6 shadow-2xl">
            <h1 className="text-lg font-semibold text-slate-100">Sign in</h1>
            <div>
              <label className="label">Username</label>
              <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
            </div>
            <div>
              <label className="label">Password</label>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            {error && <div className="text-sm text-red-400">{error}</div>}
            <button className="btn-primary w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
            {status?.oidc_enabled && (
              <>
                <div className="flex items-center gap-3 text-xs text-slate-600">
                  <div className="h-px flex-1 bg-ink-700" />
                  or
                  <div className="h-px flex-1 bg-ink-700" />
                </div>
                <a className="btn-ghost w-full" href="/api/auth/oidc/login">
                  {status.oidc_button_label || "Single sign-on"}
                </a>
              </>
            )}
          </form>
        )}
      </div>
    </div>
  );
}
