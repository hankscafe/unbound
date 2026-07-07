import { Fragment, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Book, api } from "../api";
import { useIsAdmin, useLiveEvents } from "../hooks";
import { AccountBadge, Empty, Spinner, StatusPill } from "../components/ui";

const PAGE_SIZE = 25;

function fmtRuntime(min: number | null): string {
  if (!min) return "—";
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}

function BookModal({
  book,
  isAdmin,
  onClose,
  onExclude,
  onDownload,
}: {
  book: Book;
  isAdmin: boolean;
  onClose: () => void;
  onExclude: (b: Book) => void;
  onDownload: (b: Book) => void;
}) {
  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-black/60 px-4 py-8" onClick={onClose}>
      <div
        className="card max-h-full w-full max-w-2xl overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-lg font-semibold text-slate-100">{book.title}</h2>
          <button className="shrink-0 text-slate-500 hover:text-slate-300" onClick={onClose}>
            ✕
          </button>
        </div>
        {book.subtitle && <p className="mt-0.5 text-sm text-slate-400">{book.subtitle}</p>}

        <div className="mt-4 flex gap-4">
          {book.cover_url && (
            <img
              src={book.cover_url}
              alt=""
              className="h-28 w-28 shrink-0 rounded object-cover"
            />
          )}
          <dl className="min-w-0 flex-1 space-y-1 text-sm">
            <Row label="Author" value={book.authors} />
            <Row label="Narrator" value={book.narrators} />
            <Row
              label="Series"
              value={book.series ? `${book.series}${book.series_sequence ? ` #${book.series_sequence}` : ""}` : null}
            />
            <Row label="Runtime" value={fmtRuntime(book.runtime_minutes)} />
            <Row
              label="Purchased"
              value={book.purchase_date ? new Date(book.purchase_date).toLocaleDateString() : null}
            />
            <Row label="ASIN" value={book.asin} />
          </dl>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {book.account_label && (
            <AccountBadge label={book.account_label} color={book.account_badge_color || undefined} />
          )}
          {book.excluded ? (
            <StatusPill status="excluded" />
          ) : book.job_state ? (
            <StatusPill status={book.job_state} />
          ) : (
            <StatusPill status="pending" />
          )}
          {book.abs_present && <AbsBadge auto={book.abs_auto_excluded} />}
        </div>

        {book.output_path && (
          <p className="mt-3 break-all rounded-lg bg-ink-850 p-2 text-xs text-slate-400">
            {book.output_path}
          </p>
        )}

        <div className="mt-4 flex items-center gap-2 overflow-x-auto">
          {book.audible_url && (
            <a className="btn-ghost whitespace-nowrap" href={book.audible_url} target="_blank" rel="noreferrer">
              Audible ↗
            </a>
          )}
          {book.abs_url && (
            <a className="btn-ghost whitespace-nowrap" href={book.abs_url} target="_blank" rel="noreferrer">
              AudiobookShelf ↗
            </a>
          )}
          <div className="flex-1" />
          {isAdmin && (
            <>
              <button className="btn-ghost whitespace-nowrap" onClick={() => onExclude(book)}>
                {book.excluded ? "Include" : "Exclude"}
              </button>
              {!book.excluded && (
                <button className="btn-primary whitespace-nowrap" onClick={() => onDownload(book)}>
                  Download
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-slate-500">{label}</dt>
      <dd className="min-w-0 break-words text-slate-200">{value}</dd>
    </div>
  );
}

const ACTIVE_STATES = ["queued", "downloading", "downloaded", "decrypting", "tagging", "moving"];

// Small badge for titles that already exist in AudiobookShelf.
function AbsBadge({ auto }: { auto?: boolean }) {
  return (
    <span
      className="pill bg-sky-500/15 text-sky-400"
      title={
        auto
          ? "Already in AudiobookShelf — auto-excluded from downloads. Use Include to download it anyway."
          : "This title already exists in your AudiobookShelf library."
      }
    >
      In ABS{auto ? " · skipped" : ""}
    </span>
  );
}

function bookStatus(b: Book): "excluded" | "downloaded" | "in_progress" | "failed" | "pending" {
  if (b.excluded) return "excluded";
  if (b.job_state === "completed") return "downloaded";
  if (b.job_state && ACTIVE_STATES.includes(b.job_state)) return "in_progress";
  if (b.job_state === "failed") return "failed";
  return "pending";
}

function BookRow({
  b,
  isAdmin,
  checked,
  onCheck,
  onOpen,
  onExclude,
  onDownload,
}: {
  b: Book;
  isAdmin: boolean;
  checked: boolean;
  onCheck: () => void;
  onOpen: () => void;
  onExclude: () => void;
  onDownload: () => void;
}) {
  return (
    <tr
      onClick={onOpen}
      className={`cursor-pointer border-t border-ink-800/70 hover:bg-ink-850/50 ${
        checked ? "bg-audible-500/5" : ""
      }`}
    >
      {isAdmin && (
        <td className="w-10 px-3 py-2" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" aria-label={`Select ${b.title}`} checked={checked} onChange={onCheck} />
        </td>
      )}
      <td className="px-4 py-2">
        <div className="flex items-center gap-3">
          {b.cover_url && <img src={b.cover_url} alt="" className="h-10 w-10 rounded object-cover" />}
          <div className="min-w-0">
            <div className="truncate text-slate-100">{b.title}</div>
            {b.series && (
              <div className="truncate text-xs text-slate-500">
                {b.series} {b.series_sequence && `#${b.series_sequence}`}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="hidden px-4 py-2 text-slate-400 md:table-cell">{b.authors}</td>
      <td className="px-4 py-2">
        {b.account_label && (
          <AccountBadge label={b.account_label} color={b.account_badge_color || undefined} />
        )}
      </td>
      <td className="px-4 py-2">
        {b.excluded ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusPill status="excluded" />
            {b.abs_present && <AbsBadge auto={b.abs_auto_excluded} />}
          </div>
        ) : b.job_state ? (
          <div>
            <div className="flex items-center gap-2">
              <StatusPill status={b.job_state} />
              {b.job_progress != null && b.job_progress > 0 && b.job_progress < 100 && (
                <span className="text-xs tabular-nums text-audible-400">
                  {Math.round(b.job_progress)}%
                </span>
              )}
            </div>
            {b.job_state === "downloading" && (
              <div className="mt-1 h-1 w-24 overflow-hidden rounded-full bg-ink-800">
                <div
                  className="h-full bg-audible-500 transition-all"
                  style={{ width: `${Math.max(3, b.job_progress || 0)}%` }}
                />
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusPill status="pending" />
            {b.abs_present && <AbsBadge />}
          </div>
        )}
      </td>
      <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
        {isAdmin && (
          <div className="flex justify-end gap-2">
            <button className="btn-ghost !px-2 !py-1 text-xs" onClick={onExclude}>
              {b.excluded ? "Include" : "Exclude"}
            </button>
            {!b.excluded && (
              <button className="btn-primary !px-2 !py-1 text-xs" onClick={onDownload}>
                Download
              </button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

export default function Library() {
  useLiveEvents(); // live job/status updates on this page
  const isAdmin = useIsAdmin() ?? false;
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [accountId, setAccountId] = useState<number | undefined>(undefined);
  const [selected, setSelected] = useState<Book | null>(null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [groupBySeries, setGroupBySeries] = useState(false);
  const [page, setPage] = useState(0);
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const { data: books, isLoading } = useQuery({
    queryKey: ["library", accountId, search],
    queryFn: () => api.library({ account_id: accountId, search: search || undefined }),
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["library"] });
  const clearSel = () => setChecked(new Set());
  const toggleExclude = (b: Book) => api.setExcluded(b.id, !b.excluded).then(refresh);
  const download = (b: Book) => api.download(b.id).then(refresh);

  const toggleCheck = (id: number) =>
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  const filtered = (books || []).filter((b) => !statusFilter || bookStatus(b) === statusFilter);

  // Reset to the first page when the filters/grouping change.
  useEffect(() => setPage(0), [search, accountId, statusFilter, groupBySeries]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  // Pagination applies to the flat list; grouped view shows everything grouped.
  const pageItems = groupBySeries
    ? filtered
    : filtered.slice(clampedPage * PAGE_SIZE, (clampedPage + 1) * PAGE_SIZE);

  // Group by series (Standalone bucket for series-less titles), sorted by sequence.
  const groups: { name: string; books: Book[] }[] = (() => {
    if (!groupBySeries) return [{ name: "", books: pageItems }];
    const map = new Map<string, Book[]>();
    for (const b of filtered) {
      const key = b.series || "Standalone";
      (map.get(key) || map.set(key, []).get(key)!).push(b);
    }
    return [...map.entries()]
      .sort((a, b) => (a[0] === "Standalone" ? 1 : b[0] === "Standalone" ? -1 : a[0].localeCompare(b[0])))
      .map(([name, list]) => ({
        name,
        books: list.sort(
          (x, y) => Number(x.series_sequence || 0) - Number(y.series_sequence || 0)
        ),
      }));
  })();

  const allVisibleIds = pageItems.map((b) => b.id);
  const allChecked = allVisibleIds.length > 0 && allVisibleIds.every((id) => checked.has(id));
  const toggleAll = () => setChecked(allChecked ? new Set() : new Set(allVisibleIds));

  const bulkDownload = () =>
    api.batchDownload([...checked]).then(() => {
      clearSel();
      refresh();
    });
  const bulkExclude = (excluded: boolean) =>
    api.batchExclude([...checked], excluded).then(() => {
      clearSel();
      refresh();
    });

  // Keep the open modal in sync with refreshed data.
  const selectedLive = selected ? books?.find((b) => b.id === selected.id) || selected : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Library</h1>
          <p className="text-sm text-slate-500">All owned titles across linked accounts.</p>
        </div>
        {isAdmin && (
          <button className="btn-ghost" onClick={() => api.downloadAll().then(refresh)}>
            Download all pending
          </button>
        )}
      </div>

      {checked.size > 0 && (
        <div className="card sticky top-16 z-[5] flex flex-wrap items-center gap-2 p-3">
          <span className="text-sm text-slate-300">{checked.size} selected</span>
          <div className="flex-1" />
          <button className="btn-ghost !py-1 text-xs" onClick={clearSel}>
            Clear
          </button>
          <button className="btn-ghost !py-1 text-xs" onClick={() => bulkExclude(true)}>
            Exclude
          </button>
          <button className="btn-ghost !py-1 text-xs" onClick={() => bulkExclude(false)}>
            Include
          </button>
          <button className="btn-primary !py-1 text-xs" onClick={bulkDownload}>
            Download selected
          </button>
        </div>
      )}

      <div className="card flex flex-wrap gap-3 p-3">
        <input
          className="input min-w-[200px] flex-1"
          placeholder="Search title, author, series…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="input w-full sm:w-40"
          value={accountId ?? ""}
          onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : undefined)}
        >
          <option value="">All accounts</option>
          {(accounts || []).map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </select>
        <select
          className="input w-full sm:w-40"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">Any status</option>
          <option value="downloaded">Downloaded</option>
          <option value="pending">Pending</option>
          <option value="in_progress">In progress</option>
          <option value="failed">Failed</option>
          <option value="excluded">Excluded</option>
        </select>
        <label className="flex select-none items-center gap-2 whitespace-nowrap text-sm text-slate-400">
          <input
            type="checkbox"
            checked={groupBySeries}
            onChange={(e) => setGroupBySeries(e.target.checked)}
          />
          Group by series
        </label>
      </div>

      {isLoading ? (
        <Spinner />
      ) : filtered.length === 0 ? (
        <Empty>
          {(books || []).length === 0
            ? "No books found. Link an account and run a sync."
            : "No titles match the current filters."}
        </Empty>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-ink-850 text-slate-400">
              <tr>
                {isAdmin && (
                  <th className="w-10 px-3 py-2">
                    <input
                      type="checkbox"
                      aria-label="Select all"
                      checked={allChecked}
                      onChange={toggleAll}
                    />
                  </th>
                )}
                <th className="px-4 py-2 text-left font-medium">Title</th>
                <th className="hidden px-4 py-2 text-left font-medium md:table-cell">Author</th>
                <th className="px-4 py-2 text-left font-medium">Account</th>
                <th className="px-4 py-2 text-left font-medium">Status</th>
                <th className="px-4 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <Fragment key={g.name || "all"}>
                  {groupBySeries && (
                    <tr className="border-t border-ink-800 bg-ink-850/60">
                      <td colSpan={isAdmin ? 6 : 5} className="px-4 py-1.5 text-xs font-semibold text-audible-400">
                        {g.name}{" "}
                        <span className="font-normal text-slate-500">({g.books.length})</span>
                      </td>
                    </tr>
                  )}
                  {g.books.map((b) => (
                    <BookRow
                      key={b.id}
                      b={b}
                      isAdmin={isAdmin}
                      checked={checked.has(b.id)}
                      onCheck={() => toggleCheck(b.id)}
                      onOpen={() => setSelected(b)}
                      onExclude={() => toggleExclude(b)}
                      onDownload={() => download(b)}
                    />
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!groupBySeries && filtered.length > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-slate-400">
          <span>
            {clampedPage * PAGE_SIZE + 1}–{Math.min((clampedPage + 1) * PAGE_SIZE, filtered.length)} of{" "}
            {filtered.length}
          </span>
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={clampedPage === 0}
              onClick={() => setPage(clampedPage - 1)}
            >
              Prev
            </button>
            <span className="text-xs text-slate-500">
              Page {clampedPage + 1} / {pageCount}
            </span>
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={clampedPage >= pageCount - 1}
              onClick={() => setPage(clampedPage + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {selectedLive && (
        <BookModal
          book={selectedLive}
          isAdmin={isAdmin}
          onClose={() => setSelected(null)}
          onExclude={(b) => toggleExclude(b)}
          onDownload={(b) => download(b)}
        />
      )}
    </div>
  );
}
