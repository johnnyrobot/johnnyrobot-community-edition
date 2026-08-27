/**
 * The interceptor's one job.
 *
 * A 401 means the credential is bad and the token should go. A 503 means the
 * identity provider could not be reached — clearing the token there signs every
 * Student out over a blip.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, sessionApi } from './api'

type RejectedHandler = (error: unknown) => unknown

describe('the auth interceptor', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('access_token', 'a-valid-token')
    vi.restoreAllMocks()
  })

  const reject = (status: number) => {
    // axios exposes the registered handlers only at runtime; the public
    // AxiosInterceptorManager type declares use/eject/clear and nothing else.
    const handlers = (
      api.interceptors.response as unknown as {
        handlers: Array<{ rejected?: RejectedHandler } | null>
      }
    ).handlers

    return handlers
      .filter((handler): handler is { rejected?: RejectedHandler } => Boolean(handler))
      .map((handler) => handler.rejected)
      .reduce(
        (chain: Promise<unknown>, rejected) =>
          rejected ? chain.catch((error) => rejected(error)) : chain,
        Promise.reject({ response: { status } }) as Promise<unknown>
      )
      .catch(() => undefined)
  }

  it('clears the token on 401', async () => {
    await reject(401)

    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('keeps the token on 503', async () => {
    await reject(503)

    expect(localStorage.getItem('access_token')).toBe('a-valid-token')
  })

  it('keeps the token on 500', async () => {
    await reject(500)

    expect(localStorage.getItem('access_token')).toBe('a-valid-token')
  })

  it('keeps the token on 429', async () => {
    await reject(429)

    expect(localStorage.getItem('access_token')).toBe('a-valid-token')
  })
})

describe('the Tutor Session API', () => {
  it('sends the empty request object required to end a session', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({} as never)

    await sessionApi.endSession()

    expect(post).toHaveBeenCalledWith('/session/end', {})
  })
})
