import React, { createContext, useContext, useState, useEffect } from 'react'
import type { AxiosError } from 'axios'
import { authApi, User, LoginData } from '../lib/api'

/**
 * Authentication state.
 *
 * The browser never talks to PocketBase directly (the private persistence boundary): it posts
 * credentials to FastAPI, which relays to PocketBase and returns a bearer
 * token. Accounts are provisioned by the Deployment Operator (the reset-only demo profile), so
 * there is no signup path here.
 */

const TOKEN_KEY = 'access_token'

/**
 * Three states, not two.
 *
 * `degraded` means the Student holds a token that has not been rejected, but
 * The profile could not be fetched -- a PocketBase outage answers 503 for
 * exactly this reason. Collapsing it into "no user" is what sent a signed-in
 * Student to the login screen during an outage, where the only thing they
 * could do was type a password that could not possibly work. See lifespan-based startup wiring.
 */
export type AuthStatus = 'authenticated' | 'unauthenticated' | 'degraded'

interface AuthContextType {
  user: User | null
  status: AuthStatus
  loading: boolean
  login: (data: LoginData) => Promise<void>
  logout: () => Promise<void>
  getAuthToken: () => Promise<string>
  retry: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<AuthStatus>('unauthenticated')
  const [loading, setLoading] = useState(true)

  // Restore the session from a stored token.
  //
  // A failure to restore is not proof the token is bad: only a 401 says that.
  // Clearing on a 503 would sign a Student out because the provider blinked --
  // and so, less obviously, would reporting them as having no user at all,
  // because that is indistinguishable from signed-out at the routing layer.
  // Keeping the token but returning `degraded` is what keeps them where they
  // were (lifespan-based startup wiring).
  const restore = React.useCallback(async () => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (!token) {
      setUser(null)
      setStatus('unauthenticated')
      setLoading(false)
      return
    }

    try {
      const response = await authApi.getProfile()
      setUser(response.data)
      setStatus('authenticated')
    } catch (error) {
      if ((error as AxiosError | undefined)?.response?.status === 401) {
        localStorage.removeItem(TOKEN_KEY)
        setUser(null)
        setStatus('unauthenticated')
      } else {
        console.warn('Could not restore session; keeping the token', error)
        setUser(null)
        setStatus('degraded')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    restore()
  }, [restore])

  const login = async (data: LoginData) => {
    const response = await authApi.login(data)
    localStorage.setItem(TOKEN_KEY, response.data.session.access_token)
    setUser(response.data.user)
    setStatus('authenticated')
  }

  const logout = async () => {
    try {
      await authApi.logout()
    } catch (error) {
      // Logging out locally must succeed even if the server call does not.
      console.warn('Server logout failed', error)
    }
    localStorage.removeItem(TOKEN_KEY)
    setUser(null)
    setStatus('unauthenticated')
  }

  const getAuthToken = async () => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (!token) {
      throw new Error('No authentication token found')
    }
    return token
  }

  return (
    <AuthContext.Provider
      value={{ user, status, loading, login, logout, getAuthToken, retry: restore }}
    >
      {children}
    </AuthContext.Provider>
  )
}

// A Context provider and the hook that reads it are the standard React pairing;
// splitting them across two files to satisfy fast refresh buys nothing here.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
