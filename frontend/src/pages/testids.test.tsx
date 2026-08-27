/**
 * The test hooks the smoke harness selects on.
 *
 * `evals/smoke/legs.py` drives a real browser and finds these eight attributes
 * by name. They are load-bearing for a check run minutes before a demo, so
 * their disappearance has to fail here -- in the suite that owns them --
 * rather than as a red smoke run that looks like a backend fault.
 *
 * The voice status pill is deliberately NOT in this list. It is asserted by its
 * visible words, because that label is the contract the voice-status contract established: a
 * pill reading "Connected" while nothing is connected must turn the run red,
 * and a testid would let the words regress while the harness stayed green.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const PAGES = dirname(fileURLToPath(import.meta.url))

function source(file: string): string {
  return readFileSync(join(PAGES, file), 'utf8')
}

const CONTRACT: Array<[string, string]> = [
  ['chat-input', 'TextChatPage.tsx'],
  ['chat-send', 'TextChatPage.tsx'],
  ['chat-message-assistant', 'TextChatPage.tsx'],
  ['document-title-input', 'DocumentsPage.tsx'],
  ['document-upload-input', 'DocumentsPage.tsx'],
  ['document-upload-submit', 'DocumentsPage.tsx'],
  ['document-row', 'DocumentsPage.tsx'],
  ['agent-audio', 'VoiceCallPage.tsx'],
]

describe('the smoke harness test-hook contract', () => {
  // This asserts on raw source text, so a testid appearing only in a comment
  // -- never rendered, never selectable -- would satisfy it too. That is a
  // known, accepted gap: this suite's job is to catch a hook disappearing
  // (the failure that turns a red smoke run into a false backend-fault
  // diagnosis), not to prove the testid is correctly placed on a live
  // element. A misplaced-but-present testid is a smoke-run problem to find,
  // not this suite's.
  it.each(CONTRACT)('still exposes %s in %s', (testid, file) => {
    expect(source(file)).toContain(testid)
  })

  it('leaves the voice status pill without a testid', () => {
    // The pill renders `status.label`. If a testid ever appears on that span,
    // The harness would stop asserting the words and the voice-status contract could come back
    // silently.
    const voice = source('VoiceCallPage.tsx')
    const pill = voice.slice(voice.indexOf('STATUS_CLASSES[status.tone].pill'))

    expect(pill.slice(0, 400)).not.toContain('data-testid')
  })
})
