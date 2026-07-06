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

function Shell({ children, onLogout }: { children: React.ReactNode; onLogout: () => void }) {
  return (
    <div className="min-h-screen">
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
          <nav className="-mx-1 flex gap-1 overflow-x-auto pb-2 sm:hidden">
            <NavLinks />
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
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
