import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Book, Job } from "./api";

// States that end a job (also refresh stats/library once, not just the bar).
const TERMINAL = ["completed", "failed", "excluded", "cancelled"];

// Subscribe to the backend SSE stream. Job progress events patch the react-query
// caches in place (real-time percent with no refetch); everything else falls back
// to debounced query invalidation.
export function useLiveEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/events/stream", { withCredentials: true } as any);
    let timer: number | null = null;
    const invalidateSoon = () => {
      if (timer != null) return;
      timer = window.setTimeout(() => {
        timer = null;
        qc.invalidateQueries({ queryKey: ["stats"] });
        qc.invalidateQueries({ queryKey: ["jobs"] });
        qc.invalidateQueries({ queryKey: ["library"] });
        qc.invalidateQueries({ queryKey: ["accounts"] });
      }, 400);
    };

    es.addEventListener("update", (e) => {
      let ev: any;
      try {
        ev = JSON.parse((e as MessageEvent).data);
      } catch {
        return;
      }
      if (ev?.type !== "job") {
        invalidateSoon();
        return;
      }

      // Patch the job in the jobs list.
      let known = false;
      qc.setQueriesData<Job[]>({ queryKey: ["jobs"] }, (old) => {
        if (!old) return old;
        return old.map((j) => {
          if (j.id !== ev.job_id) return j;
          known = true;
          return {
            ...j,
            state: ev.state,
            progress: ev.progress ?? j.progress,
            bytes_done: ev.bytes_done ?? j.bytes_done,
            bytes_total: ev.bytes_total ?? j.bytes_total,
          };
        });
      });
      // Patch the owning book's status in any cached library view.
      qc.setQueriesData<Book[]>({ queryKey: ["library"] }, (old) => {
        if (!old) return old;
        return old.map((b) =>
          b.id === ev.book_id
            ? { ...b, job_state: ev.state, job_progress: ev.progress ?? b.job_progress }
            : b
        );
      });
      // New job we haven't fetched yet, or a terminal transition (touches stats,
      // output paths, ABS links…) → do a full refresh.
      if (!known || TERMINAL.includes(ev.state)) invalidateSoon();
    });
    es.onerror = () => {}; // browser auto-reconnects
    return () => {
      if (timer != null) window.clearTimeout(timer);
      es.close();
    };
  }, [qc]);
}
