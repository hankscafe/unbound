import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Empty, Spinner, StatusPill } from "../components/ui";

const ACTIVE = ["queued", "downloading", "downloaded", "decrypting", "tagging", "moving"];

export default function Jobs() {
  const qc = useQueryClient();
  const { data: jobs, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    refetchInterval: 5000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["jobs"] });

  if (isLoading) return <Spinner />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Jobs</h1>
        <p className="text-sm text-slate-500">Download, decrypt, and move progress.</p>
      </div>

      {(jobs || []).length === 0 ? (
        <Empty>No jobs yet. Trigger a download from the Library.</Empty>
      ) : (
        <div className="grid gap-2">
          {jobs!.map((j) => (
            <div key={j.id} className="card p-3 overflow-hidden">
              <div className="flex flex-wrap items-start gap-x-3 gap-y-2 sm:flex-nowrap">
                <div className="min-w-0 flex-1 basis-full sm:basis-auto">
                  <div className="truncate text-sm text-slate-100">
                    {j.book_title || `Book #${j.book_id}`}
                  </div>
                  {j.error_message && (
                    <div className="mt-0.5 break-all text-xs text-red-400 line-clamp-2">
                      {j.error_message}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusPill status={j.state} />
                  {j.state === "failed" && (
                    <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => api.retryJob(j.id).then(refresh)}>
                      Retry
                    </button>
                  )}
                  {ACTIVE.includes(j.state) && (
                    <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => api.cancelJob(j.id).then(refresh)}>
                      Cancel
                    </button>
                  )}
                </div>
              </div>
              {ACTIVE.includes(j.state) && (
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-ink-800">
                  <div
                    className="h-full bg-audible-500 transition-all"
                    style={{ width: `${Math.max(3, j.progress)}%` }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
