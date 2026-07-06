import { useState } from "react";
import { api, ApiError } from "../api";
import { Logo } from "../components/ui";

export default function Setup({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.setup({ username, password, email: email || undefined, consent });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Setup failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen grid place-items-center px-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <Logo size={36} />
        </div>
        <form onSubmit={submit} className="card p-6 space-y-4">
          <div>
            <h1 className="text-lg font-semibold text-slate-100">Welcome — first-run setup</h1>
            <p className="text-sm text-slate-400 mt-1">Create the admin account for Unbound.</p>
          </div>
          <div>
            <label className="label">Admin username</label>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} required minLength={3} />
          </div>
          <div>
            <label className="label">Email (optional)</label>
            <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="label">Password</label>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
          </div>
          <label className="flex items-start gap-2 text-sm text-slate-400">
            <input type="checkbox" className="mt-1" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>
              I will use Unbound only to back up audiobooks I have purchased on my own Audible
              account, for personal use. I will not redistribute decrypted files.
            </span>
          </label>
          {error && <div className="text-sm text-red-400">{error}</div>}
          <button className="btn-primary w-full" disabled={busy || !consent}>
            {busy ? "Creating…" : "Create admin & continue"}
          </button>
        </form>
      </div>
    </div>
  );
}
