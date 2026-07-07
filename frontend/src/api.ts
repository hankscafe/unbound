// Typed client for the Unbound REST API. Cookies carry the session, so all
// requests use credentials: "include".

export interface AppStatus {
  setup_required: boolean;
  authenticated: boolean;
  consent_acknowledged: boolean;
  secret_key_rotated: boolean;
  version: string;
  github_url: string;
  oidc_enabled: boolean;
  oidc_button_label: string | null;
}

export interface User {
  id: number;
  username: string;
  email: string | null;
  role: string;
  totp_enabled: boolean;
  can_spend_credits: boolean;
}

export interface AdminUser {
  id: number;
  username: string;
  email: string | null;
  role: string;
  is_active: boolean;
  totp_enabled: boolean;
  can_spend_credits: boolean;
  account_ids: number[];
  created_at: string;
  last_login_at: string | null;
}

export interface AdminUserUpdate {
  email?: string | null;
  role?: string;
  is_active?: boolean;
  can_spend_credits?: boolean;
  password?: string;
  account_ids?: number[];
}

export interface LoginResult {
  mfa_required: boolean;
  mfa_token: string | null;
  user: User | null;
}

export interface TwoFASetup {
  secret: string;
  otpauth_uri: string;
  qr: string;
}

export interface Account {
  id: number;
  label: string;
  marketplace: string;
  account_owner_email: string | null;
  status: string;
  badge_color: string;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface Book {
  id: number;
  audible_account_id: number;
  account_label: string | null;
  account_badge_color: string | null;
  asin: string;
  title: string;
  subtitle: string | null;
  authors: string | null;
  narrators: string | null;
  series: string | null;
  series_sequence: string | null;
  runtime_minutes: number | null;
  cover_url: string | null;
  is_aax_available: boolean;
  excluded: boolean;
  purchase_date: string | null;
  audible_url: string | null;
  abs_url: string | null;
  abs_present: boolean;
  abs_auto_excluded: boolean;
  output_path: string | null;
  job_state: string | null;
  job_progress: number | null;
}

export interface Integrations {
  schedule_enabled: boolean;
  schedule_interval_hours: number;
  auto_download_new: boolean;
  abs_url: string | null;
  abs_library_id: string | null;
  abs_token: string | null;
  abs_token_set: boolean;
  abs_auto_exclude: boolean;
  notify_urls: string | null;
  notify_on_new_books: boolean;
  notify_on_complete: boolean;
  notify_on_failure: boolean;
  notify_on_update: boolean;
  notify_on_purchase: boolean;
  purchases_enabled: boolean;
}

export interface StoreItem {
  asin: string;
  title: string;
  subtitle: string | null;
  authors: string | null;
  narrators: string | null;
  series: string | null;
  series_sequence: string | null;
  runtime_minutes: number | null;
  cover_url: string | null;
  price_display: string | null;
  release_date: string | null;
  in_library: boolean;
}

export interface StoreSearchResult {
  items: StoreItem[];
  total: number;
  page: number;
}

export interface Job {
  id: number;
  book_id: number;
  book_title: string | null;
  book_author: string | null;
  cover_url: string | null;
  state: string;
  progress: number;
  bytes_done: number | null;
  bytes_total: number | null;
  format: string | null;
  error_message: string | null;
  attempt_count: number;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface OIDCSettings {
  enabled: boolean;
  issuer: string | null;
  client_id: string | null;
  client_secret: string | null;
  client_secret_set: boolean;
  button_label: string | null;
  public_base_url: string | null;
  redirect_uri: string | null;
}

export interface Stats {
  accounts_linked: number;
  accounts_total: number;
  books_total: number;
  downloaded: number;
  in_progress: number;
  failed: number;
  excluded: number;
  pending: number;
  update_available: boolean;
  current_version: string;
  latest_version: string | null;
  downloaded_bytes: number;
  library_path: string | null;
  library_ok: boolean;
  library_total_bytes: number | null;
  library_free_bytes: number | null;
  library_warning: string | null;
  network_online: boolean;
}

export interface LinkStep {
  status: string;
  flow_id: string | null;
  captcha_image_url: string | null;
  message: string | null;
}

export interface LibraryProfile {
  id: number;
  name: string;
  root_path: string;
  folder_template: string;
  filename_template: string;
  audio_format: string;
  embed_cover: boolean;
  embed_chapters: boolean;
  is_default: boolean;
  audible_account_id: number | null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const body = (data: unknown) => ({ body: JSON.stringify(data) });

export const api = {
  status: () => req<AppStatus>("/auth/status"),
  setup: (d: { username: string; password: string; email?: string; consent: boolean }) =>
    req<User>("/auth/setup", { method: "POST", ...body(d) }),
  login: (d: { username: string; password: string }) =>
    req<LoginResult>("/auth/login", { method: "POST", ...body(d) }),
  login2fa: (mfa_token: string, code: string) =>
    req<LoginResult>("/auth/login/2fa", { method: "POST", ...body({ mfa_token, code }) }),
  logout: () => req<void>("/auth/logout", { method: "POST" }),
  refreshSession: () => req<void>("/auth/refresh", { method: "POST" }),
  me: () => req<User>("/auth/me"),
  twofaSetup: () => req<TwoFASetup>("/auth/2fa/setup", { method: "POST" }),
  twofaEnable: (code: string) =>
    req<{ recovery_codes: string[] }>("/auth/2fa/enable", { method: "POST", ...body({ code }) }),
  twofaDisable: (code: string) =>
    req<void>("/auth/2fa/disable", { method: "POST", ...body({ code }) }),

  stats: () => req<Stats>("/stats"),
  covers: () => req<{ covers: string[] }>("/covers"),
  recentEvents: () => req<any[]>("/settings/events"),

  accounts: () => req<Account[]>("/accounts"),
  createAccount: (d: { label: string; marketplace: string; badge_color?: string }) =>
    req<Account>("/accounts", { method: "POST", ...body(d) }),
  deleteAccount: (id: number) => req<void>(`/accounts/${id}`, { method: "DELETE" }),
  linkGuided: (id: number, d: { email: string; password: string }) =>
    req<LinkStep>(`/accounts/${id}/link/guided`, { method: "POST", ...body(d) }),
  linkOtp: (id: number, flowId: string, otp: string) =>
    req<LinkStep>(`/accounts/${id}/link/otp?flow_id=${flowId}`, { method: "POST", ...body({ otp }) }),
  linkCaptcha: (id: number, flowId: string, answer: string) =>
    req<LinkStep>(`/accounts/${id}/link/captcha?flow_id=${flowId}`, {
      method: "POST",
      ...body({ captcha_answer: answer }),
    }),
  linkExternalStart: (id: number) =>
    req<LinkStep>(`/accounts/${id}/link/external/start`, { method: "POST" }),
  linkExternalComplete: (id: number, d: { flow_id: string; response_url: string }) =>
    req<LinkStep>(`/accounts/${id}/link/external/complete`, { method: "POST", ...body(d) }),
  unlink: (id: number) => req<Account>(`/accounts/${id}/unlink`, { method: "POST" }),
  sync: (id: number) => req<Account>(`/accounts/${id}/sync`, { method: "POST" }),

  library: (params: { account_id?: number; excluded?: boolean; search?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.account_id != null) q.set("account_id", String(params.account_id));
    if (params.excluded != null) q.set("excluded", String(params.excluded));
    if (params.search) q.set("search", params.search);
    q.set("limit", "1000"); // load full library; pagination is applied client-side
    return req<Book[]>(`/library?${q.toString()}`);
  },
  setExcluded: (bookId: number, excluded: boolean) =>
    req<Book>(`/library/${bookId}/exclude`, { method: "POST", ...body({ excluded }) }),
  download: (bookId: number) => req<any>(`/library/${bookId}/download`, { method: "POST" }),
  downloadAll: () => req<any>(`/library/download-all`, { method: "POST" }),
  batchExclude: (book_ids: number[], excluded: boolean) =>
    req<{ updated: number }>(`/library/batch/exclude`, { method: "POST", ...body({ book_ids, excluded }) }),
  batchDownload: (book_ids: number[]) =>
    req<{ queued: number }>(`/library/batch/download`, { method: "POST", ...body({ book_ids }) }),
  absMatch: () =>
    req<{ checked: number; matched: number; auto_excluded: number }>(`/library/abs-match`, {
      method: "POST",
    }),

  users: () => req<AdminUser[]>("/users"),
  createUser: (d: { username: string; password: string; email?: string; role: string; account_ids: number[] }) =>
    req<AdminUser>("/users", { method: "POST", ...body(d) }),
  updateUser: (id: number, d: AdminUserUpdate) =>
    req<AdminUser>(`/users/${id}`, { method: "PUT", ...body(d) }),
  deleteUser: (id: number) => req<void>(`/users/${id}`, { method: "DELETE" }),

  storeSearch: (accountId: number, q: string, page = 0) =>
    req<StoreSearchResult>(
      `/store/search?account_id=${accountId}&q=${encodeURIComponent(q)}&page=${page}`
    ),
  storeWishlist: (accountId: number) => req<StoreItem[]>(`/store/wishlist?account_id=${accountId}`),
  storeConfig: () => req<{ purchases_enabled: boolean }>("/store/config"),
  purchase: (account_id: number, asin: string, title: string) =>
    req<{ ok: boolean; order_id: string | null; message: string }>("/store/purchase", {
      method: "POST",
      ...body({ account_id, asin, title }),
    }),
  wishlistAdd: (account_id: number, asin: string) =>
    req<{ ok: boolean }>("/store/wishlist", { method: "POST", ...body({ account_id, asin }) }),
  wishlistRemove: (accountId: number, asin: string) =>
    req<void>(`/store/wishlist/${asin}?account_id=${accountId}`, { method: "DELETE" }),

  jobs: () => req<Job[]>("/jobs"),
  retryJob: (id: number) => req<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  cancelJob: (id: number) => req<Job>(`/jobs/${id}/cancel`, { method: "POST" }),

  profiles: () => req<LibraryProfile[]>("/settings/library-profiles"),
  createProfile: (d: Partial<LibraryProfile>) =>
    req<LibraryProfile>("/settings/library-profiles", { method: "POST", ...body(d) }),
  deleteProfile: (id: number) =>
    req<void>(`/settings/library-profiles/${id}`, { method: "DELETE" }),
  apiKeys: () => req<any[]>("/settings/api-keys"),
  createApiKey: (name: string) =>
    req<any>("/settings/api-keys", { method: "POST", ...body({ name }) }),
  deleteApiKey: (id: number) => req<void>(`/settings/api-keys/${id}`, { method: "DELETE" }),

  integrations: () => req<Integrations>("/settings/integrations"),
  updateIntegrations: (d: Integrations) =>
    req<Integrations>("/settings/integrations", { method: "PUT", ...body(d) }),
  testNotification: () =>
    req<{ sent_to: number }>("/settings/integrations/test-notification", { method: "POST" }),
  oidcSettings: () => req<OIDCSettings>("/settings/oidc"),
  updateOidcSettings: (d: OIDCSettings) =>
    req<OIDCSettings>("/settings/oidc", { method: "PUT", ...body(d) }),
  testOidc: () =>
    req<{ ok: boolean; authorization_endpoint: string }>("/settings/oidc/test", { method: "POST" }),
  testAudiobookshelf: () =>
    req<{ ok: boolean; libraries: { id: string; name: string; mediaType: string }[] }>(
      "/settings/integrations/test-audiobookshelf",
      { method: "POST" }
    ),
};
