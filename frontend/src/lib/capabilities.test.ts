import { describe, it, expect } from 'vitest'
import { formatUploadFormats, formatMaxUploadSize } from './api'

/**
 * The Documents page advertised "PDF, DOCX, TXT, MD" in one panel while the
 * server rejected DOCX with 415, and said "PDF, TXT, MD" forty lines above.
 * Both lists were hand-written, so both could drift and one did.
 *
 * These helpers exist so the rendered list is a function of the server's
 * allow-list rather than a literal a reader has to keep in sync. See #3.
 */
describe('formatUploadFormats', () => {
  it('renders the server allow-list the way a Student reads it', () => {
    expect(formatUploadFormats(['.md', '.pdf', '.txt'])).toBe('MD, PDF, TXT')
  })

  it('cannot produce DOCX from an allow-list that does not contain it', () => {
    expect(formatUploadFormats(['.pdf', '.txt', '.md'])).not.toContain('DOCX')
  })

  it('renders an empty allow-list as nothing rather than inventing a default', () => {
    expect(formatUploadFormats([])).toBe('')
  })
})

describe('formatMaxUploadSize', () => {
  it('reports the limit the server actually enforces', () => {
    expect(formatMaxUploadSize(100 * 1024 * 1024)).toBe('100MB')
  })

  it('tracks a changed limit instead of staying at the written-down one', () => {
    expect(formatMaxUploadSize(250 * 1024 * 1024)).toBe('250MB')
  })
})
