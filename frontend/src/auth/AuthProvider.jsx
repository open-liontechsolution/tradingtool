import { useCallback, useEffect, useRef, useState } from 'react'
import { UserManager, WebStorageStateStore } from 'oidc-client-ts'
import { buildOidcConfig, fetchAuthConfig } from './authConfig'
import { setAccessToken } from './apiFetch'
import { AuthContext } from './AuthContext'

// ---------------------------------------------------------------------------
// Mock user for local dev (AUTH_ENABLED=false)
// ---------------------------------------------------------------------------
const MOCK_USER = {
  profile: {
    sub: 'dev-local-user',
    email: 'dev@localhost',
    preferred_username: 'dev',
    resource_access: {
      'tradingtool-api': { roles: ['app_user', 'app_admin'] },
    },
  },
}

function extractRoles(user, audience = 'tradingtool-api') {
  try {
    // Check client roles under resource_access first
    const clientRoles = user?.profile?.resource_access?.[audience]?.roles ?? []
    if (clientRoles.length > 0) return clientRoles
    // Fallback to realm roles
    return user?.profile?.realm_access?.roles ?? []
  } catch {
    return []
  }
}

// ---------------------------------------------------------------------------
// Top-level provider: fetches runtime config then delegates
// ---------------------------------------------------------------------------

export function AuthProvider({ children }) {
  const [cfg, setCfg] = useState(null) // null = still loading

  useEffect(() => {
    fetchAuthConfig().then(setCfg)
  }, [])

  if (cfg === null) {
    // Loading runtime config — render nothing (App shows its own spinner)
    return (
      <AuthContext.Provider value={{ isLoading: true, isAuthenticated: false, roles: [], isAdmin: false, login: () => {}, logout: () => {} }}>
        {children}
      </AuthContext.Provider>
    )
  }

  if (!cfg.auth_enabled) {
    return <MockAuthProvider>{children}</MockAuthProvider>
  }

  return <KeycloakAuthProvider oidcConfig={buildOidcConfig(cfg)} audience={cfg.keycloak_audience}>{children}</KeycloakAuthProvider>
}

// ---------------------------------------------------------------------------
// Mock provider (local dev)
// ---------------------------------------------------------------------------

function MockAuthProvider({ children }) {
  useEffect(() => {
    setAccessToken('mock-dev-token')
    return () => setAccessToken(null)
  }, [])

  const roles = extractRoles(MOCK_USER)

  const value = {
    user: MOCK_USER,
    isAuthenticated: true,
    isLoading: false,
    accessToken: 'mock-dev-token',
    roles,
    isAdmin: roles.includes('app_admin'),
    login: () => {},
    logout: () => {},
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// ---------------------------------------------------------------------------
// Real Keycloak provider
// ---------------------------------------------------------------------------

function KeycloakAuthProvider({ oidcConfig, audience, children }) {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const mgr = useRef(null)

  useEffect(() => {
    const userManager = new UserManager({
      ...oidcConfig,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    })
    mgr.current = userManager

    const onUserLoaded = (u) => {
      setUser(u)
      setAccessToken(u?.access_token ?? null)
    }
    const onUserUnloaded = () => {
      setUser(null)
      setAccessToken(null)
    }

    userManager.events.addUserLoaded(onUserLoaded)
    userManager.events.addUserUnloaded(onUserUnloaded)

    const init = async () => {
      try {
        if (window.location.search.includes('code=')) {
          const u = await userManager.signinRedirectCallback()
          window.history.replaceState({}, document.title, window.location.pathname)
          setUser(u)
          setAccessToken(u?.access_token ?? null)
        } else {
          const u = await userManager.getUser()
          if (u && !u.expired) {
            setUser(u)
            setAccessToken(u?.access_token ?? null)
          }
        }
      } catch (err) {
        console.error('[Auth] init error:', err)
      } finally {
        setIsLoading(false)
      }
    }

    init()

    return () => {
      userManager.events.removeUserLoaded(onUserLoaded)
      userManager.events.removeUserUnloaded(onUserUnloaded)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(() => {
    mgr.current?.signinRedirect()
  }, [])

  const logout = useCallback(() => {
    mgr.current?.signoutRedirect()
  }, [])

  const roles = extractRoles(user, audience)

  const value = {
    user,
    isAuthenticated: !!user && !user.expired,
    isLoading,
    accessToken: user?.access_token ?? null,
    roles,
    isAdmin: roles.includes('app_admin'),
    login,
    logout,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
