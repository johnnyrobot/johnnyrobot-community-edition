/**
 * Signing in and restoring a session.
 *
 * Accounts are provisioned by a Deployment Operator (the reset-only demo profile), so there is no
 * signup path here to test — its absence is the test.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'
import { authApi } from '../lib/api'
import type { AuthResponse, User } from '../lib/api'

vi.mock('../lib/api', () => ({
  authApi: {
    login: vi.fn(),
    logout: vi.fn(),
    getProfile: vi.fn(),
  },
}))

// Typed against the real interfaces on purpose. The `as never` below only
// papers over the AxiosResponse wrapper; the payload itself has to match what
// api.ts says the backend sends, so a field renamed on the wire breaks `tsc`
// here instead of silently reading undefined in a page.
const student: User = {
  id: 'aaaaaaaaaaaaaaa',
  email: 'alice@example.test',
  name: null,
  created_at: '',
}

const signedIn: AuthResponse = {
  user: student,
  session: { access_token: 'fresh-token', refresh_token: '', expires_at: '', token_type: 'bearer' },
}

function Probe() {
  const { user, status, loading, login, logout, retry } = useAuth()
  if (loading) return <span>loading</span>
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="who">{user ? user.email : 'signed out'}</span>
      <button onClick={() => login({ email: 'alice@example.test', password: 'pw' })}>
        sign in
      </button>
      <button onClick={() => logout()}>sign out</button>
      <button onClick={() => retry()}>try again</button>
    </div>
  )
}

const renderProbe = () =>
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>
  )

describe('AuthContext', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('starts signed out when there is no token', async () => {
    renderProbe()

    await waitFor(() => expect(screen.getByTestId('who').textContent).toBe('signed out'))
    expect(authApi.getProfile).not.toHaveBeenCalled()
  })

  it('restores the session from a stored token', async () => {
    localStorage.setItem('access_token', 'a-valid-token')
    vi.mocked(authApi.getProfile).mockResolvedValue({ data: student } as never)

    renderProbe()

    await waitFor(() => expect(screen.getByTestId('who').textContent).toBe('alice@example.test'))
  })

  it('keeps the token when restore fails with a provider outage', async () => {
    localStorage.setItem('access_token', 'a-valid-token')
    vi.mocked(authApi.getProfile).mockRejectedValue({ response: { status: 503 } } as never)

    renderProbe()

    await waitFor(() => expect(screen.getByTestId('who').textContent).toBe('signed out'))
    expect(localStorage.getItem('access_token')).toBe('a-valid-token')
  })

  it('clears the token when restore fails with a rejected credential', async () => {
    localStorage.setItem('access_token', 'a-stale-token')
    vi.mocked(authApi.getProfile).mockRejectedValue({ response: { status: 401 } } as never)

    renderProbe()

    await waitFor(() => expect(localStorage.getItem('access_token')).toBeNull())
  })

  it('stores the token returned by login', async () => {
    vi.mocked(authApi.login).mockResolvedValue({ data: signedIn } as never)

    renderProbe()
    await waitFor(() => screen.getByText('sign in'))
    screen.getByText('sign in').click()

    await waitFor(() => expect(localStorage.getItem('access_token')).toBe('fresh-token'))
  })

  it('signs out locally even when the server call fails', async () => {
    localStorage.setItem('access_token', 'a-valid-token')
    vi.mocked(authApi.getProfile).mockResolvedValue({ data: student } as never)
    vi.mocked(authApi.logout).mockRejectedValue(new Error('network down') as never)

    renderProbe()
    await waitFor(() => expect(screen.getByTestId('who').textContent).toBe('alice@example.test'))

    screen.getByText('sign out').click()

    // A Student signing out on a shared lab machine must not be left signed in
    // because the network blipped.
    await waitFor(() => expect(localStorage.getItem('access_token')).toBeNull())
    await waitFor(() => expect(screen.getByTestId('who').textContent).toBe('signed out'))
    expect(authApi.logout).toHaveBeenCalled()
  })

  // -- outages are not sign-outs -------------------------------------------

  it('reports a provider outage as degraded, not as signed out', async () => {
    // A PocketBase outage answers 503 by design (the private persistence boundary) so that a blip is
    // never mistaken for a bad credential. Reporting it as "no user" undid
    // that at the routing layer, because `!user` and "signed out" are the same
    // thing to PrivateRoute. See lifespan-based startup wiring.
    localStorage.setItem('access_token', 'a-valid-token')
    vi.mocked(authApi.getProfile).mockRejectedValue({ response: { status: 503 } } as never)

    renderProbe()

    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('degraded'))
    expect(localStorage.getItem('access_token')).toBe('a-valid-token')
  })

  it('keeps the token through an outage so recovery needs no new password', async () => {
    localStorage.setItem('access_token', 'a-valid-token')
    vi.mocked(authApi.getProfile).mockRejectedValue({ response: { status: 503 } } as never)

    renderProbe()
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('degraded'))

    expect(localStorage.getItem('access_token')).toBe('a-valid-token')
  })

  it('still treats a 401 as signed out', async () => {
    // The distinction has to survive: only a definitive rejection clears the
    // token, or an expired session would linger behind a "try again" button.
    localStorage.setItem('access_token', 'a-stale-token')
    vi.mocked(authApi.getProfile).mockRejectedValue({ response: { status: 401 } } as never)

    renderProbe()

    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('unauthenticated'))
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('recovers to authenticated when the provider returns', async () => {
    localStorage.setItem('access_token', 'a-valid-token')
    vi.mocked(authApi.getProfile).mockRejectedValueOnce({ response: { status: 503 } } as never)

    renderProbe()
    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('degraded'))

    vi.mocked(authApi.getProfile).mockResolvedValue({ data: student } as never)
    screen.getByText('try again').click()

    await waitFor(() => expect(screen.getByTestId('status').textContent).toBe('authenticated'))
    await waitFor(() => expect(screen.getByTestId('who').textContent).toBe('alice@example.test'))
  })
})
