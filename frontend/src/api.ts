let accessToken: string | null = null;
export const setAccessToken = (value: string | null) => {
  accessToken = value;
};
export const backendUrl = (path: string) =>
  (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "") + "/api" + path;
const authHeaders = (): Record<string, string> =>
  accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
export type Identity = { org: string; user: string };
export const scope = (
  identity: Identity,
  portfolioId?: string,
  revision?: number,
) =>
  new URLSearchParams({
    org_id: identity.org,
    user_id: identity.user,
    ...(portfolioId ? { portfolio_id: portfolioId } : {}),
    ...(revision !== undefined ? { revision: String(revision) } : {}),
  }).toString();
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(backendUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...init?.headers,
    },
  });
  if (!response.ok) throw await responseError(response);
  try {
    return await response.json();
  } catch {
    throw new Error("The server returned an invalid response. Please retry.");
  }
}
async function responseError(response: Response): Promise<Error> {
  const fallback = `Request failed (${response.status}). Please retry.`;
  try {
    const data = await response.json();
    return new Error(typeof data?.detail === "string" ? data.detail : fallback);
  } catch {
    return new Error(fallback);
  }
}

export const send = (value: unknown, method = "POST") => ({
  method,
  body: JSON.stringify(value),
});
export const number = (value: unknown) =>
  value === null || value === undefined ? null : Number(value);
export function amount(value: unknown, currency = "USD") {
  const n = number(value);
  return n === null || !Number.isFinite(n)
    ? "—"
    : new Intl.NumberFormat("en", {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
      }).format(n);
}
export type Activity = {
  id: string;
  name: string;
  agent?: string;
  status?: string;
  output?: string;
};
export type Statements = {
  symbol: string;
  currency?: string;
  source: string;
  frequency: string;
  income_statement: Record<string, Record<string, unknown>>;
  balance_sheet: Record<string, Record<string, unknown>>;
  cash_flow: Record<string, Record<string, unknown>>;
};
export async function researchStream(
  body: unknown,
  onEvent: (event: Record<string, unknown>) => void,
  signal: AbortSignal,
) {
  const response = await fetch(backendUrl("/chat/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new Error("Streaming is unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = /\r\n\r\n|\n\n|\r\r/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const data = frame
          .split(/\r\n|\n|\r/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (!data) continue;
        let event;
        try {
          event = JSON.parse(data);
        } catch {
          throw new Error(
            "The research stream returned invalid data. Please retry.",
          );
        }
        if (!event || typeof event !== "object" || Array.isArray(event))
          throw new Error(
            "The research stream returned invalid data. Please retry.",
          );
        onEvent(event);
      }
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
