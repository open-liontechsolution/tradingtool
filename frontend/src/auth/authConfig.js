/**
 * Auth configuration fetched at runtime from the backend.
 *
 * The backend exposes GET /api/auth/config (no auth required) which returns
 * the values of AUTH_ENABLED, KEYCLOAK_URL, KEYCLOAK_REALM and
 * KEYCLOAK_FRONTEND_CLIENT_ID from its environment.
 *
 * VITE_* env vars are only used as fallbacks for local `npm run dev` without
 * a running backend.
 */

/**
 * Fetch auth config from the backend.
 * Returns a plain object: { auth_enabled, keycloak_url, keycloak_realm, keycloak_client_id }
 */
export async function fetchAuthConfig() {
  try {
    const res = await fetch('/api/auth/config')
    if (res.ok) {
      return await res.json()
    }
  } catch {
    // backend unreachable — fall through to VITE_ defaults (local dev)
  }

  return {
    auth_enabled: (import.meta.env.VITE_AUTH_ENABLED ?? 'false').toLowerCase() === 'true',
    keycloak_url: import.meta.env.VITE_KEYCLOAK_URL ?? '',
    keycloak_realm: import.meta.env.VITE_KEYCLOAK_REALM ?? 'tradingtool-dev',
    keycloak_client_id: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? 'tradingtool-web',
  }
}

/** Build an oidc-client-ts config object from a fetched auth config. */
export function buildOidcConfig({ keycloak_url, keycloak_realm, keycloak_client_id }) {
  const authority = `${keycloak_url}/realms/${keycloak_realm}`
  return {
    authority,
    client_id: keycloak_client_id,
    redirect_uri: `${window.location.origin}/`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: 'code',
    scope: 'openid profile email',
    automaticSilentRenew: true,
  }
}
