import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import VoiceCallPage from './VoiceCallPage'

const livekit = vi.hoisted(() => ({
  createLocalVideoTrack: vi.fn(async () => ({})),
  publishTrack: vi.fn(async () => undefined),
  setCameraEnabled: vi
    .fn<(enabled: boolean, options?: unknown) => Promise<void>>()
    .mockResolvedValue(undefined),
  setMicrophoneEnabled: vi.fn(async () => undefined),
  setScreenShareEnabled: vi.fn(async () => undefined),
  switchActiveDevice: vi
    .fn<(kind: string, deviceId: string) => Promise<boolean>>()
    .mockResolvedValue(true),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: undefined }),
}))

vi.mock('../lib/api', () => ({
  canvasApi: {},
  textbookApi: {},
  sessionApi: {
    createToken: vi.fn(async () => ({
      data: {
        token: 'test-token',
        room_name: 'test-room',
        url: 'wss://test.livekit.cloud',
      },
    })),
    endSession: vi.fn(async () => undefined),
  },
}))

vi.mock('livekit-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('livekit-client')>()
  return {
    ...actual,
    createLocalVideoTrack: livekit.createLocalVideoTrack,
  }
})

vi.mock('@livekit/components-react', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  const { Track } = await vi.importActual<typeof import('livekit-client')>('livekit-client')
  const participant = { identity: 'student' }
  const cameraTrack = {
    participant,
    publication: {
      isMuted: false,
      track: {},
    },
    source: Track.Source.Camera,
  }
  const room = {
    off: vi.fn(),
    on: vi.fn(),
    switchActiveDevice: livekit.switchActiveDevice,
  }

  return {
    BarVisualizer: () => React.createElement('div'),
    LiveKitRoom: ({ children }: { children: React.ReactNode }) =>
      React.createElement(React.Fragment, null, children),
    RoomAudioRenderer: () => null,
    VideoTrack: () => React.createElement('div', { 'data-testid': 'camera-preview' }),
    useConnectionState: () => 'connected',
    useLocalParticipant: () => {
      const [isCameraEnabled, setIsCameraEnabled] = React.useState(true)
      const localParticipant = React.useMemo(
        () => ({
          identity: participant.identity,
          publishTrack: livekit.publishTrack,
          setCameraEnabled: async (enabled: boolean, options?: unknown) => {
            await livekit.setCameraEnabled(enabled, options)
            setIsCameraEnabled(enabled)
          },
          setMicrophoneEnabled: livekit.setMicrophoneEnabled,
          setScreenShareEnabled: livekit.setScreenShareEnabled,
        }),
        [],
      )

      return {
        cameraTrack: cameraTrack.publication,
        isCameraEnabled,
        isMicrophoneEnabled: true,
        isScreenShareEnabled: false,
        lastCameraError: undefined,
        lastMicrophoneError: undefined,
        localParticipant,
        microphoneTrack: undefined,
      }
    },
    useRoomContext: () => room,
    useTracks: () => [cameraTrack],
    useVoiceAssistant: () => ({ agent: { identity: 'agent' }, audioTrack: undefined, state: 'listening' }),
  }
})

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <VoiceCallPage />
    </MemoryRouter>,
  )
}

function cameraControl(title: 'Turn off camera' | 'Turn on camera') {
  const matches = screen.getAllByTitle(title)
  return matches[matches.length - 1]
}

describe('Voice Tutor camera controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollIntoView = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn(async () => []),
      },
    })
  })

  it('can turn a muted camera back on while its publication still exists', async () => {
    renderPage()

    await screen.findAllByTitle('Turn off camera')
    expect(cameraControl('Turn off camera')).toBeTruthy()
    expect(screen.getByTestId('camera-preview')).toBeTruthy()

    fireEvent.click(cameraControl('Turn off camera'))

    await waitFor(() => expect(screen.getByTitle('Turn on camera')).toBeTruthy())
    expect(screen.queryByTestId('camera-preview')).toBeNull()

    fireEvent.click(cameraControl('Turn on camera'))

    await waitFor(() => expect(cameraControl('Turn off camera')).toBeTruthy())
    expect(screen.getByTestId('camera-preview')).toBeTruthy()
  })

  it('switches a muted camera through the room device API before re-enabling it', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn(async () => [
          { deviceId: 'front-camera', groupId: 'front', kind: 'videoinput', label: 'Front Camera' },
          { deviceId: 'rear-camera', groupId: 'rear', kind: 'videoinput', label: 'Rear Camera' },
        ]),
      },
    })
    renderPage()

    await screen.findAllByTitle('Turn off camera')
    fireEvent.click(cameraControl('Turn off camera'))
    await waitFor(() => expect(cameraControl('Turn on camera')).toBeTruthy())

    fireEvent.click(await screen.findByTitle('Select camera'))
    fireEvent.click(screen.getByRole('button', { name: 'Rear Camera' }))

    await waitFor(() =>
      expect(livekit.switchActiveDevice).toHaveBeenCalledWith('videoinput', 'rear-camera'),
    )
    expect(livekit.createLocalVideoTrack).not.toHaveBeenCalled()
    expect(livekit.publishTrack).not.toHaveBeenCalled()

    fireEvent.click(cameraControl('Turn on camera'))
    await waitFor(() => expect(cameraControl('Turn off camera')).toBeTruthy())
    expect(screen.getByTestId('camera-preview')).toBeTruthy()
  })

  it('keeps the previous selection when LiveKit refuses a device switch', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn(async () => [
          { deviceId: 'front-camera', groupId: 'front', kind: 'videoinput', label: 'Front Camera' },
          { deviceId: 'rear-camera', groupId: 'rear', kind: 'videoinput', label: 'Rear Camera' },
        ]),
      },
    })
    livekit.switchActiveDevice.mockResolvedValueOnce(false)
    renderPage()

    fireEvent.click(await screen.findByTitle('Select camera'))
    fireEvent.click(screen.getByRole('button', { name: 'Rear Camera' }))

    await waitFor(() =>
      expect(livekit.switchActiveDevice).toHaveBeenCalledWith('videoinput', 'rear-camera'),
    )
    fireEvent.click(screen.getByTitle('Select camera'))

    expect(screen.getByRole('button', { name: 'Front Camera' }).className).toContain('bg-blue-100')
    expect(screen.getByRole('button', { name: 'Rear Camera' }).className).not.toContain('bg-blue-100')
  })
})
