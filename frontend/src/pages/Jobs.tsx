import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Job, api } from "../api";
import { useLiveEvents } from "../hooks";
import { Empty, Spinner, StatusPill } from "../components/ui";

const ACTIVE = ["queued", "downloading", "downloaded", "decrypting", "tagging", "moving"];

function fmtBytes(n: number | null): string {
  if (n == null) return "?";
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString();
}

function JobCard({ job, onRefresh }: { job: Job; onRefresh: () => void }) {
  const active = ACTIVE.includes(job.state);
  const downloading = job.state === "downloading";
  return (
    <div className="card p-3">
      <div className="flex items-start gap-3">
        {job.cover_url ? (
          <img src={job.cover_url} alt="" className="h-12 w-12 shrink-0 rounded object-cover" />
        ) : (
          <div className="h-12 w-12 shrink-0 rounded bg-ink-850" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm text-slate-100">
            {job.book_title || `Book #${job.book_id}`}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
            {job.book_author && <span className="truncate">{job.book_author}</span>}
            <span>·</span>
            <span>{fmtWhen(job.finished_at || job.updated_at)}</span>
            {job.attempt_count > 1 && (
              <>
                <span>·</span>
                <span>attempt {job.attempt_count}</span>
              </>
            )}
          </div>
          {job.error_message && (
            <div className="mt-1 break-all text-xs text-red-400 line-clamp-2">{job.error_message}</div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusPill status={job.state} />
          {(job.state === "failed" || job.state === "cancelled") && (
            <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => api.retryJob(job.id).then(onRefresh)}>
              Retry
            </button>
          )}
          {active && (
            <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => api.cancelJob(job.id).then(onRefresh)}>
              Cancel
            </button>
          )}
        </div>
      </div>
      {active && (
        <div className="mt-2">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-800">
            <div
              className="h-full bg-audible-500 transition-all"
              style={{ width: `${Math.max(3, job.progress)}%` }}
            />
          </div>
          {downloading && (
            <div className="mt-1 flex justify-between text-xs tabular-nums text-slate-500">
              <span>
                {fmtBytes(job.bytes_done)}
                {job.bytes_total != null && <> / {fmtBytes(job.bytes_total)}</>}
              </span>
              <span className="text-audible-400">{job.progress.toFixed(0)}%</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Section({ title, jobs, onRefresh }: { title: string; jobs: Job[]; onRefresh: () => void }) {
  if (jobs.length === 0) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
        {title} <span className="font-normal text-slate-600">({jobs.length})</span>
      </h2>
      <div className="grid gap-2">
        {jobs.map((j) => (
          <JobCard key={j.id} job={j} onRefresh={onRefresh} />
        ))}
      </div>
    </section>
  );
}

const FILTERS = [
  { id: "", label: "All" },
  { id: "active", label: "Active" },
  { id: "failed", label: "Failed" },
  { id: "completed", label: "Completed" },
] as const;

export default function Jobs() {
  useLiveEvents(); // SSE patches progress in real time; polling is just a fallback
  const qc = useQueryClient();
  const [filter, setFilter] = useState<string>("");
  const { data: jobs, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    refetchInterval: 15000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["jobs"] });

  if (isLoading) return <Spinner />;

  const all = jobs || [];
  const active = all.filter((j) => ACTIVE.includes(j.state));
  const failed = all.filter((j) => j.state === "failed");
  const completed = all.filter((j) => j.state === "completed");
  const other = all.filter(
    (j) => !ACTIVE.includes(j.state) && !["failed", "completed"].includes(j.state)
  );
  const counts: Record<string, number> = {
    "": all.length,
    active: active.length,
    failed: failed.length,
    completed: completed.length,
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Jobs</h1>
          <p className="text-sm text-slate-500">Download, decrypt, and move progress.</p>
        </div>
        {failed.length > 0 && (
          <button
            className="btn-ghost"
            onClick={() => Promise.all(failed.map((j) => api.retryJob(j.id))).then(refresh)}
          >
            Retry all failed
          </button>
        )}
      </div>

      <div className="no-scrollbar flex gap-2 overflow-x-auto">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            className={`pill whitespace-nowrap ${
              filter === f.id
                ? "bg-audible-500/15 text-audible-400"
                : "bg-ink-800 text-slate-400 hover:text-slate-200"
            }`}
          >
            {f.label} · {counts[f.id]}
          </button>
        ))}
      </div>

      {all.length === 0 ? (
        <Empty>No jobs yet. Trigger a download from the Library.</Empty>
      ) : (
        <div className="space-y-6">
          {(filter === "" || filter === "active") && (
            <Section title="Active" jobs={active} onRefresh={refresh} />
          )}
          {(filter === "" || filter === "failed") && (
            <Section title="Failed" jobs={failed} onRefresh={refresh} />
          )}
          {(filter === "" || filter === "completed") && (
            <Section title="Completed" jobs={completed} onRefresh={refresh} />
          )}
          {filter === "" && <Section title="Other" jobs={other} onRefresh={refresh} />}
          {filter !== "" && counts[filter] === 0 && <Empty>Nothing here right now.</Empty>}
        </div>
      )}
    </div>
  );
}
