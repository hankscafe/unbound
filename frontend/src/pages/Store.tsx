import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { StoreItem, api } from "../api";
import { useMe } from "../hooks";
import { Empty, Spinner } from "../components/ui";

function fmtRuntime(min: number | null): string | null {
  if (!min) return null;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}

// Typed-title confirmation before a credit is spent — no accidental purchases.
function PurchaseModal({
  item,
  accountLabel,
  onClose,
  onConfirm,
  busy,
  error,
}: {
  item: StoreItem;
  accountLabel: string;
  onClose: () => void;
  onConfirm: () => void;
  busy: boolean;
  error: string | null;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed.trim().toLowerCase() === item.title.trim().toLowerCase();
  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-black/60 px-4 py-8" onClick={onClose}>
      <div className="card w-full max-w-md space-y-4 p-5" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-slate-100">Buy with 1 credit</h2>
        <div className="flex gap-3">
          {item.cover_url && (
            <img src={item.cover_url} alt="" className="h-16 w-16 shrink-0 rounded object-cover" />
          )}
          <div className="min-w-0 text-sm">
            <div className="font-medium text-slate-100">{item.title}</div>
            {item.authors && <div className="text-slate-400">{item.authors}</div>}
            <div className="mt-1 text-xs text-slate-500">Account: {accountLabel}</div>
          </div>
        </div>
        <p className="rounded-lg bg-ink-850 p-3 text-xs text-slate-400">
          This spends <span className="text-audible-400">one Audible credit</span> from this
          account — monthly plan credits count. Unbound never charges your card: with no credits
          available the purchase simply fails. After buying, the book is synced and its download
          starts automatically.
        </p>
        <div>
          <label className="label">Type the title to confirm</label>
          <input
            className="input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={item.title}
            autoFocus
          />
        </div>
        {error && <div className="text-sm text-red-400">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn-primary" disabled={!matches || busy} onClick={onConfirm}>
            {busy ? "Purchasing…" : "Buy · 1 credit"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ItemCard({
  item,
  wishlisted,
  busy,
  canBuy,
  onWishlist,
  onBuy,
}: {
  item: StoreItem;
  wishlisted: boolean;
  busy: boolean;
  canBuy: boolean;
  onWishlist: (add: boolean) => void;
  onBuy: () => void;
}) {
  const runtime = fmtRuntime(item.runtime_minutes);
  return (
    <div className="card flex gap-3 p-3">
      {item.cover_url ? (
        <img src={item.cover_url} alt="" className="h-20 w-20 shrink-0 rounded object-cover" />
      ) : (
        <div className="h-20 w-20 shrink-0 rounded bg-ink-850" />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-slate-100">{item.title}</div>
        {item.subtitle && <div className="truncate text-xs text-slate-500">{item.subtitle}</div>}
        <div className="mt-0.5 truncate text-xs text-slate-400">
          {item.authors}
          {item.narrators && <span className="text-slate-600"> · read by {item.narrators}</span>}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
          {item.series && (
            <span>
              {item.series}
              {item.series_sequence && ` #${item.series_sequence}`}
            </span>
          )}
          {runtime && <span>{runtime}</span>}
          {item.release_date && <span>{item.release_date.slice(0, 10)}</span>}
          {item.price_display && <span className="text-slate-400">{item.price_display}</span>}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end justify-between gap-2">
        {item.in_library ? (
          <span className="pill bg-emerald-500/15 text-emerald-400">In library</span>
        ) : wishlisted ? (
          <span className="pill bg-audible-500/15 text-audible-400">Wishlisted</span>
        ) : (
          <span />
        )}
        {!item.in_library && (
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost !px-2 !py-1 text-xs"
              disabled={busy}
              onClick={() => onWishlist(!wishlisted)}
            >
              {wishlisted ? "Remove" : "+ Wishlist"}
            </button>
            {canBuy && (
              <button className="btn-primary !px-2 !py-1 text-xs" disabled={busy} onClick={onBuy}>
                Buy · 1 credit
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Store() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const [accountId, setAccountId] = useState<number | undefined>(undefined);
  const [view, setView] = useState<"search" | "wishlist">("search");
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [buying, setBuying] = useState<StoreItem | null>(null);
  const [purchaseMsg, setPurchaseMsg] = useState<string | null>(null);

  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const { data: storeConfig } = useQuery({ queryKey: ["store-config"], queryFn: api.storeConfig });
  const canBuy = Boolean(storeConfig?.purchases_enabled && me?.can_spend_credits);
  const linked = useMemo(() => (accounts || []).filter((a) => a.status === "linked"), [accounts]);
  useEffect(() => {
    if (accountId == null && linked.length > 0) setAccountId(linked[0].id);
  }, [linked, accountId]);

  const search = useQuery({
    queryKey: ["store-search", accountId, query, page],
    queryFn: () => api.storeSearch(accountId!, query, page),
    enabled: accountId != null && query.length > 0,
    staleTime: 5 * 60 * 1000,
  });
  const wishlist = useQuery({
    queryKey: ["store-wishlist", accountId],
    queryFn: () => api.storeWishlist(accountId!),
    enabled: accountId != null,
    staleTime: 60 * 1000,
  });
  const wishlistAsins = new Set((wishlist.data || []).map((i) => i.asin));

  const doPurchase = useMutation({
    mutationFn: (item: StoreItem) => api.purchase(accountId!, item.asin, item.title),
    onSuccess: (r, item) => {
      setBuying(null);
      setPurchaseMsg(`"${item.title}" — ${r.message}`);
      qc.invalidateQueries({ queryKey: ["library"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["store-search"] });
      qc.invalidateQueries({ queryKey: ["store-wishlist", accountId] });
    },
  });

  const toggleWishlist = useMutation({
    mutationFn: ({ asin, add }: { asin: string; add: boolean }) =>
      add ? api.wishlistAdd(accountId!, asin).then(() => {}) : api.wishlistRemove(accountId!, asin),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["store-wishlist", accountId] });
    },
    onError: (e: any) => setError(e?.message || "Wishlist update failed"),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(0);
    setQuery(input.trim());
    setView("search");
  };

  if (linked.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-slate-100">Store</h1>
        <Empty>Link an Audible account first — search runs against the account’s marketplace.</Empty>
      </div>
    );
  }

  const results = search.data?.items || [];
  const pageCount = search.data ? Math.max(1, Math.ceil(Math.min(search.data.total, 500) / 20)) : 1;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Store</h1>
        <p className="text-sm text-slate-500">
          {canBuy
            ? "Search Audible, manage wishlists, and buy with credits (never your card)."
            : "Search Audible and manage the account wishlist."}
        </p>
      </div>

      <div className="card flex flex-wrap items-center gap-3 p-3">
        <select
          className="input w-full sm:w-52"
          value={accountId ?? ""}
          onChange={(e) => {
            setAccountId(Number(e.target.value));
            setPage(0);
          }}
        >
          {linked.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label} ({a.marketplace})
            </option>
          ))}
        </select>
        <form onSubmit={submit} className="flex min-w-[220px] flex-1 gap-2">
          <input
            className="input flex-1"
            placeholder="Search titles, authors, narrators…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <button className="btn-primary" disabled={!input.trim()}>
            Search
          </button>
        </form>
        <div className="no-scrollbar flex gap-2 overflow-x-auto">
          <button
            className={`pill whitespace-nowrap ${view === "search" ? "bg-audible-500/15 text-audible-400" : "bg-ink-800 text-slate-400 hover:text-slate-200"}`}
            onClick={() => setView("search")}
          >
            Results
          </button>
          <button
            className={`pill whitespace-nowrap ${view === "wishlist" ? "bg-audible-500/15 text-audible-400" : "bg-ink-800 text-slate-400 hover:text-slate-200"}`}
            onClick={() => setView("wishlist")}
          >
            Wishlist · {wishlist.data?.length ?? "…"}
          </button>
        </div>
      </div>

      {error && <div className="text-sm text-red-400">{error}</div>}
      {purchaseMsg && (
        <div className="card border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">
          {purchaseMsg}
        </div>
      )}

      {view === "search" ? (
        search.isFetching ? (
          <Spinner />
        ) : !query ? (
          <Empty>Search Audible’s catalog — results match the selected account’s marketplace.</Empty>
        ) : results.length === 0 ? (
          <Empty>No results for “{query}”.</Empty>
        ) : (
          <>
            <div className="grid gap-2">
              {results.map((item) => (
                <ItemCard
                  key={item.asin}
                  item={item}
                  wishlisted={wishlistAsins.has(item.asin)}
                  busy={toggleWishlist.isPending}
                  canBuy={canBuy}
                  onWishlist={(add) => toggleWishlist.mutate({ asin: item.asin, add })}
                  onBuy={() => {
                    setPurchaseMsg(null);
                    doPurchase.reset();
                    setBuying(item);
                  }}
                />
              ))}
            </div>
            {pageCount > 1 && (
              <div className="flex items-center justify-between text-sm text-slate-400">
                <span>
                  {search.data!.total} result{search.data!.total === 1 ? "" : "s"}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    className="btn-ghost !py-1 text-xs"
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                  >
                    Prev
                  </button>
                  <span className="text-xs text-slate-500">
                    Page {page + 1} / {pageCount}
                  </span>
                  <button
                    className="btn-ghost !py-1 text-xs"
                    disabled={page >= pageCount - 1}
                    onClick={() => setPage(page + 1)}
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )
      ) : wishlist.isLoading ? (
        <Spinner />
      ) : (wishlist.data || []).length === 0 ? (
        <Empty>The wishlist for this account is empty.</Empty>
      ) : (
        <div className="grid gap-2">
          {(wishlist.data || []).map((item) => (
            <ItemCard
              key={item.asin}
              item={item}
              wishlisted
              busy={toggleWishlist.isPending}
              canBuy={canBuy}
              onWishlist={() => toggleWishlist.mutate({ asin: item.asin, add: false })}
              onBuy={() => {
                setPurchaseMsg(null);
                doPurchase.reset();
                setBuying(item);
              }}
            />
          ))}
        </div>
      )}

      {buying && (
        <PurchaseModal
          item={buying}
          accountLabel={linked.find((a) => a.id === accountId)?.label || "account"}
          busy={doPurchase.isPending}
          error={doPurchase.error ? (doPurchase.error as any)?.message || "Purchase failed" : null}
          onClose={() => setBuying(null)}
          onConfirm={() => doPurchase.mutate(buying)}
        />
      )}
    </div>
  );
}
