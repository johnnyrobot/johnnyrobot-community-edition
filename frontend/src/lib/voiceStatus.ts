/**
 * What the voice session status indicator should say.
 *
 * The indicator used to be a hardcoded green pill whose label fell back to
 * "Connected" whenever no assistant state was available -- which is exactly the
 * situation where nothing is connected. Observed live: minutes after LiveKit
 * had rejected the connection outright ("could not establish signal
 * connection: invalid API key"), the page still showed a green pill, a live
 * microphone, and "Waiting for audio...". A Student would wait forever.
 *
 * Three states have to be distinguishable, because the remedy differs:
 *
 *   - not connected to the room      -> credentials or network
 *   - connected, but no tutor joined -> the agent is down
 *   - connected with a tutor         -> working
 *
 * The status reflects the room and agent state.
 */
export type StatusTone = 'ok' | 'warn' | 'bad'

export interface VoiceStatus {
  tone: StatusTone
  label: string
  /** Whether the session can carry audio right now. */
  live: boolean
}

/** LiveKit's ConnectionState values, as strings. */
export type ConnectionState =
  | 'disconnected'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'signalReconnecting'

export function voiceStatus(
  connection: ConnectionState | string,
  hasAgent: boolean,
  assistantState?: string
): VoiceStatus {
  if (connection === 'connecting') {
    return { tone: 'warn', label: 'Connecting…', live: false }
  }

  if (connection === 'reconnecting' || connection === 'signalReconnecting') {
    return { tone: 'warn', label: 'Reconnecting…', live: false }
  }

  if (connection !== 'connected') {
    return { tone: 'bad', label: 'Not connected', live: false }
  }

  // Connected to the room, but the tutor never arrived. Distinct from a failed
  // connection because the thing to fix is different -- and distinct from
  // working, which is what it used to be reported as.
  if (!hasAgent) {
    return { tone: 'warn', label: 'Waiting for the tutor to join…', live: false }
  }

  switch (assistantState) {
    case 'listening':
      return { tone: 'ok', label: 'Listening…', live: true }
    case 'thinking':
      return { tone: 'ok', label: 'Thinking…', live: true }
    case 'speaking':
      return { tone: 'ok', label: 'Speaking…', live: true }
    default:
      return { tone: 'ok', label: 'Ready', live: true }
  }
}

/** Tailwind classes for a tone, kept beside the tone so they cannot drift apart. */
export const STATUS_CLASSES: Record<StatusTone, { pill: string; dot: string }> = {
  ok: { pill: 'bg-green-100 text-green-800', dot: 'bg-green-600' },
  warn: { pill: 'bg-amber-100 text-amber-900', dot: 'bg-amber-500' },
  bad: { pill: 'bg-red-100 text-red-800', dot: 'bg-red-600' },
}
