import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useLiveEvents } from "../hooks";
import { Spinner, StatCard } from "../components/ui";

function fmtBytes(n: number | null): string {
  if (!n && n !== 0) return "—";
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

export default function Dashboard() {
  useLiveEvents();
  const { data: stats, isLoading } = useQuery({
    queryKey: ["stats"],
    queryFn: api.stats,
    refetchInterval: 15000,
  });
  const { data: events } = useQuery({ queryKey: ["events"], queryFn: api.recentEvents });

  if (isLoading || !stats) return <Spinner />;

  const connected = stats.accounts_linked;
  const disconnected = stats.accounts_total - stats.accounts_linked;
  const usedPct =
    stats.library_total_bytes && stats.library_free_bytes != null
      ? Math.round(
          ((stats.library_total_bytes - stats.library_free_bytes) / stats.library_total_bytes) * 100
        )
      : null;

  return (
    <div className="space-y-6">
      {stats.library_warning && (
        <div className="card border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
          <span className="font-semibold">Library storage problem:</span> {stats.library_warning}
        </div>
      )}
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Dashboard</h1>
        <p className="text-sm text-slate-500">Live overview of your linked accounts and library.</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Accounts connected" value={connected} accent="#34d399" hint={`${disconnected} disconnected`} />
        <StatCard label="Books in library" value={stats.books_total} />
        <StatCard label="Downloaded" value={stats.downloaded} accent="#34d399" />
        <StatCard label="In progress" value={stats.in_progress} accent="#F8991C" />
        <StatCard label="Failed" value={stats.failed} accent="#f87171" />
        <StatCard label="Excluded" value={stats.excluded} accent="#94a3b8" />
        <StatCard label="Pending" value={stats.pending} accent="#38bdf8" />
        <StatCard label="Version" value={stats.current_version} />
      </div>

      <div className="card p-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wide text-slate-400">Library storage</div>
            <div className="mt-1 text-sm text-slate-300">
              {fmtBytes(stats.downloaded_bytes)} backed up across {stats.downloaded} title(s)
            </div>
            {stats.library_path && (
              <div className={`mt-1 break-all text-xs ${stats.library_ok ? "text-slate-500" : "text-red-400"}`}>
                → {stats.library_path}
              </div>
            )}
          </div>
          {usedPct != null && (
            <div className="w-full sm:w-56">
              <div className="flex justify-between text-xs text-slate-500">
                <span>{fmtBytes(stats.library_free_bytes)} free</span>
                <span>{usedPct}% used</span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-ink-800">
                <div className="h-full bg-audible-500" style={{ width: `${usedPct}%` }} />
              </div>
              <div className="mt-1 text-right text-xs text-slate-500">
                {fmtBytes(stats.library_total_bytes)} total
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card p-4">
        <h2 className="text-sm font-semibold text-slate-300 mb-3">Recent activity</h2>
        <div className="max-h-80 space-y-1.5 overflow-y-auto overflow-x-hidden">
          {(events || []).length === 0 && <div className="text-sm text-slate-500">No activity yet.</div>}
          {(events || []).map((e) => (
            <div key={e.id} className="flex min-w-0 items-baseline gap-2 text-xs">
              <span className="w-28 shrink-0 tabular-nums text-slate-600 sm:w-36">
                {new Date(e.ts).toLocaleString()}
              </span>
              <span className={`min-w-0 break-words ${e.level === "error" ? "text-red-400" : "text-slate-300"}`}>
                {e.message}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
