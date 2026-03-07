/**
 * OIDC / Keycloak configuration built from Vite environment variables.
 *
 * Env vars (set in .env.development.local or build-time):
 *   VITE_AUTH_ENABLED      – "true" to enable Keycloak login (default: "false")
 *   VITE_KEYCLOAK_URL      – Keycloak base URL
 *   VITE_KEYCLOAK_REALM    – Keycloak realm name (default: "tradingtool-dev")
 *   VITE_KEYCLOAK_CLIENT_ID – OIDC client ID (default: "tradingtool-web")
 */

export const AUTH_ENABLED =
  (import.meta.env.VITE_AUTH_ENABLED ?? 'false').toLowerCase() === 'true'

const KEYCLOAK_URL = import.meta.env.VITE_KEYCLOAK_URL ?? ''
const KEYCLOAK_REALM = import.meta.env.VITE_KEYCLOAK_REALM ?? 'tradingtool-dev'
const KEYCLOAK_CLIENT_ID =
  import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? 'tradingtool-web'

const authority = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}`

/** Settings object for oidc-client-ts UserManager */
export const oidcConfig = {
  authority,
  client_id: KEYCLOAK_CLIENT_ID,
  redirect_uri: `${window.location.origin}/`,
  post_logout_redirect_uri: `${window.location.origin}/`,
  response_type: 'code',
  scope: 'openid profile email',
  automaticSilentRenew: true,
  // PKCE S256 is the default in oidc-client-ts for response_type=code
}
