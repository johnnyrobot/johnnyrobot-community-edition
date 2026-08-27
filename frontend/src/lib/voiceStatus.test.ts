import { describe, it, expect } from 'vitest'
import { voiceStatus } from './voiceStatus'

/**
 * Observed live before this existed: LiveKit rejected the connection with
 * "could not establish signal connection: invalid API key", and minutes later
 * The page still showed a green pill, a live microphone, and "Waiting for
 * audio...". These tests are that screenshot, turned into assertions.
 *
 * The status must reflect the room and agent state.
 */
describe('voiceStatus', () => {
  it('never reports a failed connection as working', () => {
    const status = voiceStatus('disconnected', false)

    expect(status.tone).toBe('bad')
    expect(status.live).toBe(false)
    // Not a substring check: "Not connected" rightly contains "connected".
    // What must never happen is the label reading as a working session.
    expect(['Ready', 'Connected', 'Listening…', 'Thinking…', 'Speaking…']).not.toContain(
      status.label
    )
  })

  it('does not present the microphone as live while connecting', () => {
    expect(voiceStatus('connecting', false).live).toBe(false)
  })

  it('distinguishes a lost connection from a failed one', () => {
    expect(voiceStatus('reconnecting', true).label).toBe('Reconnecting…')
    expect(voiceStatus('signalReconnecting', true).tone).toBe('warn')
  })

  it('distinguishes "the agent is down" from "the connection failed"', () => {
    const noAgent = voiceStatus('connected', false)
    const noConnection = voiceStatus('disconnected', false)

    expect(noAgent.label).toMatch(/tutor/i)
    expect(noAgent.tone).not.toBe(noConnection.tone)
  })

  it('does not claim a session is live when no tutor has joined', () => {
    // The precise regression: the old indicator fell back to "Connected"
    // whenever assistant state was absent, which is this case exactly.
    const status = voiceStatus('connected', false)

    expect(status.live).toBe(false)
    expect(status.label).not.toBe('Connected')
  })

  it('reports a working session as working', () => {
    expect(voiceStatus('connected', true, 'listening')).toEqual({
      tone: 'ok',
      label: 'Listening…',
      live: true,
    })
  })

  it('falls back to Ready only once a tutor is actually present', () => {
    const status = voiceStatus('connected', true, undefined)

    expect(status).toEqual({ tone: 'ok', label: 'Ready', live: true })
  })

  it('treats an unknown connection value as not connected', () => {
    // Failing closed matters here: an unrecognised state showing green is how
    // The original bug reads to a Student.
    expect(voiceStatus('something-new' as never, true).tone).toBe('bad')
  })
})
