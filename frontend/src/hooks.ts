import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

// Subscribe to the backend SSE stream and invalidate affected queries on each
// "update" event, so any page using this hook reflects job/account changes live.
export function useLiveEvents() {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/events/stream", { withCredentials: true } as any);
    const invalidate = () => {
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["library"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    };
    es.addEventListener("update", invalidate);
    es.onerror = () => {}; // browser auto-reconnects
    return () => es.close();
  }, [qc]);
}
