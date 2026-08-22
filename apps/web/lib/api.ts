/**
 * API client.
 *
 * The access token lives in module memory, never localStorage — so an XSS cannot
 * read it and API calls carry no ambient credential, which is what makes them
 * structurally CSRF-immune. The refresh token is an httpOnly cookie the browser
 * sends only to /api/v1/auth/*.
 *
 * A 401 triggers exactly one refresh attempt, with concurrent callers sharing
 * that single in-flight refresh rather than each firing their own.
 */

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** RFC 9457 Problem Details. `code` is the stable contract to switch on. */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  request_id?: string;
  errors?: { field: string; message: string }[];
  duplicate_of?: string;
}

export class ApiError extends Error {
  constructor(
    readonly problem: ProblemDetail,
    readonly status: number,
  ) {
    super(problem.detail || problem.title);
    this.name = 'ApiError';
  }

  get code(): string {
    return this.problem.code;
  }
}

async function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'include',
    })
      .then(async (res) => {
        if (!res.ok) {
          accessToken = null;
          return false;
        }
        const body = (await res.json()) as { access_token: string };
        accessToken = body.access_token;
        return true;
      })
      .catch(() => {
        accessToken = null;
        return false;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  retryOn401?: boolean;
  /** Read headers off a successful response — used where a token is not in the body. */
  onResponse?: (response: Response) => void;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, retryOn401 = true, headers, onResponse, ...rest } = options;

  const isFormData = body instanceof FormData;
  const requestHeaders: Record<string, string> = {
    ...(isFormData ? {} : body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    ...((headers as Record<string, string>) ?? {}),
  };

  const response = await fetch(path, {
    ...rest,
    headers: requestHeaders,
    credentials: 'include',
    body: isFormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (response.status === 401 && retryOn401 && !path.includes('/auth/')) {
    if (await refreshSession()) {
      return apiFetch<T>(path, { ...options, retryOn401: false });
    }
  }

  if (response.ok) onResponse?.(response);

  if (response.status === 204) {
    return undefined as T;
  }

  if (!response.ok) {
    let problem: ProblemDetail;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      problem = {
        type: 'about:blank',
        title: 'Request failed',
        status: response.status,
        detail: `The server returned ${response.status}.`,
        code: 'UNKNOWN_ERROR',
      };
    }
    throw new ApiError(problem, response.status);
  }

  return (await response.json()) as T;
}

// --- auth -----------------------------------------------------------------

export interface SessionResponse {
  user: {
    id: string;
    email: string;
    full_name: string | null;
    timezone: string;
  };
  workspace: { id: string; name: string; slug: string };
  role: string;
  permissions: string[];
}

export async function login(email: string, password: string): Promise<void> {
  const body = await apiFetch<{ access_token: string }>('/api/v1/auth/login', {
    method: 'POST',
    body: { email, password },
  });
  setAccessToken(body.access_token);
}

export async function register(
  email: string,
  password: string,
  fullName?: string,
): Promise<SessionResponse> {
  return apiFetch<SessionResponse>('/api/v1/auth/register', {
    method: 'POST',
    body: { email, password, full_name: fullName },
    // Register already issues a token — it just rides in a header instead of
    // the body. Reading it here saves a second password hash through /login.
    onResponse: (response) => {
      const token = response.headers.get('X-Access-Token');
      if (token) setAccessToken(token);
    },
  });
}

export async function logout(): Promise<void> {
  await apiFetch<void>('/api/v1/auth/logout', { method: 'POST' });
  setAccessToken(null);
}

export async function getSession(): Promise<SessionResponse> {
  return apiFetch<SessionResponse>('/api/v1/auth/me');
}

/** Restore a session on page load from the httpOnly refresh cookie. */
export async function bootstrapSession(): Promise<SessionResponse | null> {
  if (!(await refreshSession())) return null;
  try {
    return await getSession();
  } catch {
    return null;
  }
}

/**
 * Fetch a binary resource with the access token attached.
 *
 * pdf.js fetches its URL itself and cannot attach our in-memory bearer token,
 * so pointing it at an authenticated endpoint yields a 401 and no viewer at
 * all. Instead we fetch here — following the 302 to the presigned object URL,
 * which carries its own signature — and hand pdf.js a blob: URL.
 *
 * The 25 MB upload cap bounds the memory cost. If that limit ever rises,
 * switch to a signed URL the viewer can request directly and stream.
 */
export async function fetchBlobUrl(path: string): Promise<string> {
  const request = async (): Promise<Response> =>
    fetch(path, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      credentials: 'include',
    });

  let response = await request();
  if (response.status === 401 && (await refreshSession())) {
    response = await request();
  }
  if (!response.ok) {
    throw new Error(`Could not load the document (${response.status}).`);
  }
  return URL.createObjectURL(await response.blob());
}

/** Exchange a verified Google ID token for a DocFlow session. */
export async function loginWithGoogle(idToken: string): Promise<void> {
  const body = await apiFetch<{ access_token: string }>('/api/v1/auth/google', {
    method: 'POST',
    body: { id_token: idToken },
  });
  setAccessToken(body.access_token);
}

/** Ask for a reset link. Always succeeds, whether or not the account exists. */
export async function requestPasswordReset(email: string): Promise<void> {
  await apiFetch<{ detail: string }>('/api/v1/auth/forgot-password', {
    method: 'POST',
    body: { email },
  });
}

/** Set a new password from a reset link, and sign in with the result. */
export async function completePasswordReset(token: string, password: string): Promise<void> {
  const body = await apiFetch<{ access_token: string }>('/api/v1/auth/reset-password', {
    method: 'POST',
    body: { token, password },
  });
  setAccessToken(body.access_token);
}
