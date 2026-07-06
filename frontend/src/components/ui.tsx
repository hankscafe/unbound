import React from "react";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <div className="flex items-center gap-2 select-none">
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
        <circle cx="16" cy="16" r="15" stroke="#F8991C" strokeWidth="2" />
        <path
          d="M10 20c2-6 4-9 6-9s4 3 6 9"
          stroke="#F8991C"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <circle cx="16" cy="16" r="2.4" fill="#F8991C" />
      </svg>
      <span className="font-display text-xl tracking-wide text-slate-100">UNBOUND</span>
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  linked: "bg-emerald-500/15 text-emerald-300",
  completed: "bg-emerald-500/15 text-emerald-300",
  unlinked: "bg-slate-500/15 text-slate-300",
  linking: "bg-amber-500/15 text-amber-300",
  needs_reauth: "bg-amber-500/15 text-amber-300",
  error: "bg-red-500/15 text-red-300",
  failed: "bg-red-500/15 text-red-300",
  excluded: "bg-slate-600/20 text-slate-400",
  downloading: "bg-audible-500/15 text-audible-400",
  decrypting: "bg-audible-500/15 text-audible-400",
  tagging: "bg-audible-500/15 text-audible-400",
  moving: "bg-audible-500/15 text-audible-400",
  queued: "bg-sky-500/15 text-sky-300",
  pending: "bg-slate-500/15 text-slate-300",
};

export function StatusPill({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] || "bg-slate-500/15 text-slate-300";
  return <span className={`pill ${cls}`}>{status.replace(/_/g, " ")}</span>;
}

export function AccountBadge({ label, color }: { label: string; color?: string }) {
  return (
    <span
      className="pill border"
      style={{
        color: color || "#F8991C",
        borderColor: (color || "#F8991C") + "66",
        background: (color || "#F8991C") + "1a",
      }}
    >
      {label}
    </span>
  );
}

export function StatCard({
  label,
  value,
  accent,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  accent?: string;
  hint?: string;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 text-3xl font-semibold" style={{ color: accent || "#e2e8f0" }}>
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-10 text-slate-500">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-ink-600 border-t-audible-500" />
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="card p-10 text-center text-slate-500">{children}</div>;
}
