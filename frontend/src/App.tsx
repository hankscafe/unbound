import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "./api";
import { Logo, Spinner } from "./components/ui";
import Setup from "./pages/Setup";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Accounts from "./pages/Accounts";
import Library from "./pages/Library";
import Jobs from "./pages/Jobs";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/accounts", label: "Accounts" },
  { to: "/library", label: "Library" },
  { to: "/jobs", label: "Jobs" },
  { to: "/settings", label: "Settings" },
];

function NavLinks() {
  return (
    <>
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.end}
          className={({ isActive }) =>
            `whitespace-nowrap rounded-lg px-3 py-1.5 text-sm ${
              isActive
                ? "bg-audible-500/15 text-audible-400"
                : "text-slate-400 hover:bg-ink-800 hover:text-slate-200"
            }`
          }
        >
          {n.label}
        </NavLink>
      ))}
    </>
  );
}

function GitHubIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

// App-wide "new version" banner (dismissible per version, per browser).
function UpdateBanner() {
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status });
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: api.stats,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  });
  const [dismissed, setDismissed] = useState<string | null>(() =>
    localStorage.getItem("unbound.update.dismissed")
  );
  if (!stats?.update_available || !stats.latest_version) return null;
  if (dismissed === stats.latest_version) return null;
  const releaseUrl = `${status?.github_url || "https://github.com"}/releases/latest`;
  return (
    <div className="mx-auto max-w-7xl px-4 pt-4">
      <div className="card flex flex-wrap items-center gap-2 border-audible-500/40 bg-audible-500/10 p-3 text-sm">
        <span className="text-audible-400">
          <span className="font-semibold">Update available:</span> {stats.latest_version} (you are
          on v{stats.current_version})
        </span>
        <div className="flex-1" />
        <a className="btn-ghost !px-2 !py-1 text-xs" href={releaseUrl} target="_blank" rel="noreferrer">
          View release ↗
        </a>
        <button
          className="text-slate-500 hover:text-slate-300"
          aria-label="Dismiss update notice"
          onClick={() => {
            localStorage.setItem("unbound.update.dismissed", stats.latest_version!);
            setDismissed(stats.latest_version);
          }}
        >
          ✕
        </button>
      </div>
    </div>
  );
}

function Shell({ children, onLogout }: { children: React.ReactNode; onLogout: () => void }) {
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status });
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-10 border-b border-ink-800 bg-ink-900/70 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4">
          <div className="flex h-14 items-center justify-between gap-2">
            <Logo />
            <nav className="hidden items-center gap-1 sm:flex">
              <NavLinks />
            </nav>
            <button className="btn-ghost shrink-0" onClick={onLogout}>
              Sign out
            </button>
          </div>
          {/* Mobile: horizontally scrollable nav row */}
          <nav className="no-scrollbar -mx-1 flex gap-1 overflow-x-auto pb-2 sm:hidden">
            <NavLinks />
          </nav>
        </div>
      </header>
      <UpdateBanner />
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
      <footer className="mx-auto w-full max-w-7xl px-4 pb-5 pt-8">
        <div className="flex items-center justify-center gap-3 text-xs text-slate-600">
          <span>Unbound {status ? `v${status.version}` : ""}</span>
          <span aria-hidden="true">·</span>
          <a
            className="inline-flex items-center gap-1.5 hover:text-slate-400"
            href={status?.github_url || "https://github.com"}
            target="_blank"
            rel="noreferrer"
          >
            <GitHubIcon /> GitHub
          </a>
        </div>
      </footer>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  const { data: status, isLoading, refetch } = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
  });

  if (isLoading || !status) {
    return (
      <div className="min-h-screen grid place-items-center">
        <Spinner />
      </div>
    );
  }

  if (status.setup_required) {
    return <Setup onDone={() => refetch()} />;
  }

  if (!status.authenticated) {
    return <Login onDone={() => refetch()} />;
  }

  const logout = async () => {
    await api.logout();
    await refetch();
    navigate("/");
  };

  return (
    <Shell onLogout={logout}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/library" element={<Library />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
