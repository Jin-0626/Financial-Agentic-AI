import { useEffect, useState, ReactNode } from "react";
import { User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { api, Identity, setAccessToken } from "./api";

import { isTauri } from "@tauri-apps/api/core";

type Config = {
  required: boolean;
  configured: boolean;
  authority: string;
  client_id: string;
  scope: string;
  audience: string;
};
let manager: UserManager | null = null;
let callback: Promise<User> | null = null;
export function Session({
  children,
}: {
  children: (identity: Identity | null) => ReactNode;
}) {
  const [ready, setReady] = useState(false);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [error, setError] = useState("");
  const [login, setLogin] = useState(false);
  useEffect(() => {
    if (isTauri() && !import.meta.env.VITE_BACKEND_URL) {
      setError(
        "This desktop build has no trusted backend configured. Ask your administrator for a configured build.",
      );
      return;
    }
    let active = true;
    async function initialize() {
      const config = await api<Config>("/auth/config");
      if (
        typeof config.required !== "boolean" ||
        typeof config.configured !== "boolean"
      )
        throw new Error("The server returned invalid login configuration.");
      if (!config.required) {
        if (active) setReady(true);
        return;
      }
      if (!config.configured)
        throw new Error(
          "Production login is not configured. Contact the server administrator.",
        );
      manager ??= new UserManager({
        authority: config.authority,
        client_id: config.client_id,
        redirect_uri:
          import.meta.env.VITE_OIDC_REDIRECT_URI ||
          `${window.location.origin}/auth/callback`,
        post_logout_redirect_uri: window.location.origin,
        response_type: "code",
        scope: config.scope,
        userStore: new WebStorageStateStore({ store: window.sessionStorage }),
        automaticSilentRenew: false,
      });
      const user =
        window.location.pathname === "/auth/callback"
          ? await (callback ??= manager.signinRedirectCallback())
          : await manager.getUser();
      if (window.location.pathname === "/auth/callback")
        history.replaceState(null, "", "/portfolio");
      if (!user || user.expired) {
        if (active) setLogin(true);
        return;
      }
      setAccessToken(user.access_token);
      const me = await api<Identity>("/auth/me");
      if (active) {
        setIdentity(me);
        setReady(true);
      }
    }
    void initialize().catch((e) => {
      if (active) setError(e.message);
    });
    return () => {
      active = false;
    };
  }, []);
  if (ready) return <>{children(identity)}</>;
  return (
    <main className="session panel">
      <h1>Financial Terminal</h1>
      {error ? (
        <p role="alert">{error}</p>
      ) : (
        <p>
          {login ? "Sign in to your workspace" : "Connecting to workspace…"}
        </p>
      )}
      {login && (
        <button
          onClick={() => {
            void manager?.signinRedirect().catch((e) => setError(e.message));
          }}
        >
          Sign in
        </button>
      )}
      <button onClick={() => window.location.reload()}>Retry connection</button>
    </main>
  );
}
export async function logout() {
  setAccessToken(null);
  if (manager) {
    await manager.removeUser();
    await manager.signoutRedirect();
  }
}
