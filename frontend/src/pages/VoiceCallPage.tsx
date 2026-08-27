import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { 
  LiveKitRoom, 
  RoomAudioRenderer, 
  useVoiceAssistant, 
  BarVisualizer,
  useLocalParticipant,
  useTracks,
  VideoTrack,
  useRoomContext,
  useConnectionState
} from '@livekit/components-react'
import { voiceStatus, STATUS_CLASSES } from '../lib/voiceStatus'
import { Track, RoomEvent } from 'livekit-client'
import type { Participant, TranscriptionSegment } from 'livekit-client'
import { sessionApi, textbookApi, canvasApi } from '../lib/api'
import { Phone, ArrowLeft, Video, VideoOff, Monitor, MonitorOff, Mic, MicOff, Settings, FileText, BookOpen } from 'lucide-react'
import { RocketIcon } from '../components/RocketIcon'

interface TranscriptMessage {
  id: string
  speaker: 'user' | 'agent'
  text: string
  timestamp: Date
}

export default function VoiceCallPage() {
  const navigate = useNavigate()
  const [token, setToken] = useState<string>('')
  const [serverUrl, setServerUrl] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    // Get LiveKit token - backend generates the actual room name
    sessionApi.createToken()
      .then(response => {
        setToken(response.data.token)
        setServerUrl(response.data.url)
        setLoading(false)
      })
      .catch(err => {
        setError(err.response?.data?.detail || 'Failed to create session')
        setLoading(false)
      })
  }, [])

  const handleEndCall = async () => {
    try {
      await sessionApi.endSession()
    } catch {
      // The backend closes the caller's own open Tutor Session; if that call
      // fails the Student still leaves the room.
    }
    navigate('/dashboard')
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-blue-600 border-t-transparent mb-4"></div>
          <p className="text-lg text-gray-700">Connecting to Johnny Robot Community Edition...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl shadow-xl p-8 max-w-md w-full">
          <div className="text-red-600 mb-4">
            <Phone size={48} className="mx-auto mb-4" />
            <h2 className="text-xl font-bold">Connection Failed</h2>
          </div>
          <p className="text-gray-600 mb-6">{error}</p>
          <button
            onClick={() => navigate('/dashboard')}
            className="w-full bg-blue-600 text-white py-3 rounded-lg font-semibold hover:bg-blue-700"
          >
            Back to Dashboard
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      {/* A token being issued says nothing about whether LiveKit accepted the
          connection. Without onError, a rejected connection ("could not
          establish signal connection: invalid API key") left this page showing
          a working session indefinitely (#4). */}
      <LiveKitRoom
        token={token}
        serverUrl={serverUrl}
        connect={true}
        audio={true}
        video={false}
        screen={false}
        onError={(err) =>
          setError(
            err?.message
              ? `Could not connect to the voice service: ${err.message}`
              : 'Could not connect to the voice service.'
          )
        }
      >
        <VoiceCallInterface onEndCall={handleEndCall} />
        <RoomAudioRenderer />
      </LiveKitRoom>
    </div>
  )
}

function VoiceCallInterface({ onEndCall }: { onEndCall: () => void }) {
  const { state, audioTrack, agent } = useVoiceAssistant()

  // The room's real connection state, and whether a tutor actually joined.
  // Both are needed: connected-with-no-agent and failed-to-connect are
  // different problems with different fixes, and neither is a working session.
  const connectionState = useConnectionState()
  const status = voiceStatus(String(connectionState), Boolean(agent), String(state))
  const { isCameraEnabled: isCameraOn, localParticipant } = useLocalParticipant()
  const room = useRoomContext()
  const [isScreenSharing, setIsScreenSharing] = useState(false)
  const [isMicMuted, setIsMicMuted] = useState(false)
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([])
  const [availableCameras, setAvailableCameras] = useState<MediaDeviceInfo[]>([])
  const [selectedCameraId, setSelectedCameraId] = useState<string>('')
  const [showCameraSelector, setShowCameraSelector] = useState(false)
  const transcriptEndRef = useRef<HTMLDivElement>(null)

  // Get video and screen share tracks
  const tracks = useTracks([Track.Source.Camera, Track.Source.ScreenShare])
  const cameraTrack = tracks.find(t => t.source === Track.Source.Camera)
  const screenTrack = tracks.find(t => t.source === Track.Source.ScreenShare)

  // Screen sharing unpublishes its track when stopped, so publication
  // presence is authoritative for that control. Camera tracks are different:
  // LiveKit mutes an existing publication, and useLocalParticipant owns that
  // enabled state above.
  useEffect(() => {
    const hasScreenTrack = screenTrack?.publication?.track !== undefined

    if (hasScreenTrack !== isScreenSharing) {
      console.log('Syncing screen share state:', hasScreenTrack)
      setIsScreenSharing(hasScreenTrack)
    }
  }, [screenTrack, isScreenSharing])

  // Load available cameras on mount
  useEffect(() => {
    const loadCameras = async () => {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices()
        const cameras = devices.filter(device => device.kind === 'videoinput')
        setAvailableCameras(cameras)
        // Set first camera as default
        if (cameras.length > 0 && !selectedCameraId) {
          setSelectedCameraId(cameras[0].deviceId)
        }
      } catch (error) {
        console.error('Failed to enumerate cameras:', error)
      }
    }
    loadCameras()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only on purpose: re-running on selectedCameraId would re-enumerate devices every time a Student picks a camera
  }, [])

  // Auto-scroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript])

  // Listen for agent speech and user speech separately
  useEffect(() => {
    if (!room) return

    // Track the last added text to avoid duplicates
    let lastAddedText = ''
    let lastAddedTime = 0

    const handleTranscription = (segments: TranscriptionSegment[], participant?: Participant) => {
      if (segments.length === 0) return
      
      // Only process final transcriptions
      const finalSegments = segments.filter(s => s.final === true)
      if (finalSegments.length === 0) return
      
      const text = finalSegments.map(s => s.text).join(' ').trim()
      
      // Skip empty or very short text
      if (!text || text.length < 5) return
      
      const isAgent = participant?.identity?.includes('agent') || participant?.identity?.includes('assistant')
      
      // Debounce - avoid adding same text within 2 seconds
      const now = Date.now()
      if (text === lastAddedText && (now - lastAddedTime) < 2000) {
        return
      }
      
      lastAddedText = text
      lastAddedTime = now
      
      setTranscript(prev => {
        // Check last 3 messages for duplicates
        const recentDuplicates = prev.slice(-3).some(msg => 
          msg.text === text && Math.abs(msg.timestamp.getTime() - now) < 3000
        )
        
        if (recentDuplicates) return prev
        
        return [...prev, {
          id: `${now}-${Math.random().toString(36).substr(2, 9)}`,
          speaker: isAgent ? 'agent' : 'user',
          text,
          timestamp: new Date(now)
        }]
      })
    }

    room.on(RoomEvent.TranscriptionReceived, handleTranscription)
    return () => {
      room.off(RoomEvent.TranscriptionReceived, handleTranscription)
    }
  }, [room])

  const toggleCamera = async () => {
    if (!localParticipant) {
      console.error('No local participant available')
      return
    }

    try {
      const nextEnabled = !isCameraOn
      const captureOptions = nextEnabled && selectedCameraId
        ? { deviceId: selectedCameraId }
        : undefined

      console.log(`${nextEnabled ? 'Enabling' : 'Disabling'} camera...`)
      await localParticipant.setCameraEnabled(nextEnabled, captureOptions)
      console.log(`Camera ${nextEnabled ? 'enabled' : 'disabled'} successfully`)
    } catch (error) {
      console.error('Failed to toggle camera:', error)
      alert(`Camera error: ${error instanceof Error ? error.message : 'Unknown error'}`)
    }
  }

  const changeCamera = async (deviceId: string) => {
    setShowCameraSelector(false)

    // Switch the existing publication even while it is muted. Re-enabling a
    // muted publication only unmutes it; capture options passed at that point
    // do not replace its device.
    try {
      const switched = await room.switchActiveDevice('videoinput', deviceId)
      if (!switched) {
        console.error('Failed to change camera: LiveKit refused the device switch')
        return
      }
      setSelectedCameraId(deviceId)
    } catch (error) {
      console.error('Failed to change camera:', error)
    }
  }

  const toggleScreenShare = async () => {
    if (!localParticipant) {
      console.error('No local participant available')
      return
    }

    try {
      const newState = !isScreenSharing
      console.log(`${newState ? 'Enabling' : 'Disabling'} screen share...`)
      
      await localParticipant.setScreenShareEnabled(newState)
      setIsScreenSharing(newState)
      
      console.log(`Screen share ${newState ? 'enabled' : 'disabled'} successfully`)
    } catch (error) {
      console.error('Failed to toggle screen share:', error)
      alert(`Screen share error: ${error instanceof Error ? error.message : 'Unknown error'}`)
    }
  }

  const toggleMicrophone = async () => {
    if (localParticipant) {
      try {
        const newMutedState = !isMicMuted
        await localParticipant.setMicrophoneEnabled(!newMutedState)
        setIsMicMuted(newMutedState)
      } catch (error) {
        console.error('Failed to toggle microphone:', error)
      }
    }
  }

  const visibleCameraTrack = isCameraOn ? cameraTrack : undefined
  const hasVideo = visibleCameraTrack || screenTrack

  return (
    <div className="min-h-screen flex flex-col lg:flex-row p-4 gap-4">
      {/* Left Side: Video Display Area */}
      <div className="flex-1 flex flex-col gap-4 min-h-0">
        {hasVideo && (
          <div className="flex gap-4 w-full">
            {/* Screen Share Display */}
            {screenTrack && (
              <div className="relative bg-black rounded-xl overflow-hidden shadow-2xl flex-1" style={{ height: '500px' }}>
                <VideoTrack trackRef={screenTrack} className="w-full h-full object-contain" />
                <div className="absolute top-4 left-4 bg-red-600 text-white px-3 py-1.5 rounded-full text-sm font-semibold flex items-center gap-2">
                  <Monitor size={16} />
                  Screen
                </div>
                <button
                  onClick={toggleScreenShare}
                  className="absolute top-4 right-4 p-2 rounded-full bg-black/50 hover:bg-black/70 text-white transition-colors backdrop-blur-sm"
                  title="Stop sharing"
                >
                  <MonitorOff size={20} />
                </button>
              </div>
            )}

            {/* Camera Display */}
            {visibleCameraTrack && (
              <div className={`relative bg-black rounded-xl overflow-hidden shadow-2xl ${screenTrack ? 'w-80' : 'flex-1'}`} style={{ height: '500px' }}>
                <VideoTrack trackRef={visibleCameraTrack} className="w-full h-full object-cover" />
                <div className="absolute top-4 left-4 bg-blue-600 text-white px-3 py-1.5 rounded-full text-sm font-semibold flex items-center gap-2">
                  <Video size={16} />
                  Camera
                </div>
                <button
                  onClick={toggleCamera}
                  className="absolute top-4 right-4 p-2 rounded-full bg-black/50 hover:bg-black/70 text-white transition-colors backdrop-blur-sm"
                  title="Turn off camera"
                >
                  <VideoOff size={20} />
                </button>
              </div>
            )}
          </div>
        )}

        {/* Voice Interface */}
        <div className="bg-white rounded-2xl shadow-2xl p-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center">
                <RocketIcon size={24} className="text-blue-600" />
              </div>
              <div>
                <h2 className="font-bold text-gray-900">Johnny Robot Community Edition</h2>
                <p className="text-xs text-gray-600">AI Learning Assistant</p>
              </div>
            </div>
          </div>

          {/* Voice Visualizer */}
          <div className="mb-4 h-24 flex items-center justify-center bg-gray-50 rounded-xl">
            {audioTrack && (
              /* The smoke harness reads this to answer "did the tutor publish
                 audio". A wrapper rather than a prop on BarVisualizer, which
                 is third-party and need not forward unknown attributes. */
              <div data-testid="agent-audio">
                <BarVisualizer
                  state={state}
                  trackRef={audioTrack}
                  barCount={7}
                  options={{
                    minHeight: 15,
                    maxHeight: 80,
                  }}
                />
              </div>
            )}
            {!audioTrack && (
              // "Waiting for audio..." reads the same whether the tutor is
              // about to speak or will never arrive, which is what let a dead
              // session look like a slow one. Say which it is.
              <p className="text-gray-500 text-sm">
                {status.live ? 'Waiting for audio…' : status.label}
              </p>
            )}
          </div>

          {/* Status. Driven by the room's real connection state and whether a
              tutor actually joined -- this was a hardcoded green pill whose
              label fell back to "Connected" precisely when nothing was (#4). */}
          <div className="mb-4 text-center">
            <div
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm ${
                STATUS_CLASSES[status.tone].pill
              }`}
            >
              <div
                className={`w-2 h-2 rounded-full ${STATUS_CLASSES[status.tone].dot} ${
                  status.live ? 'animate-pulse' : ''
                }`}
              ></div>
              <span className="font-medium">{status.label}</span>
            </div>
          </div>

          {/* Controls */}
          <div className="flex items-center justify-center gap-3 flex-wrap">
            {/* Microphone Toggle */}
            <button
              onClick={toggleMicrophone}
              className={`p-3 rounded-full font-semibold transition-colors ${
                isMicMuted
                  ? 'bg-gray-200 text-gray-600 hover:bg-gray-300'
                  : 'bg-green-600 text-white hover:bg-green-700'
              }`}
              title={isMicMuted ? 'Unmute microphone' : 'Mute microphone'}
            >
              {isMicMuted ? <MicOff size={20} /> : <Mic size={20} />}
            </button>

            {/* Camera Toggle */}
            <div className="relative">
              <button
                onClick={toggleCamera}
                className={`p-3 rounded-full font-semibold transition-colors ${
                  isCameraOn
                    ? 'bg-blue-600 text-white hover:bg-blue-700'
                    : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
                }`}
                title={isCameraOn ? 'Turn off camera' : 'Turn on camera'}
              >
                {isCameraOn ? <Video size={20} /> : <VideoOff size={20} />}
              </button>
              
              {/* Camera Selector Button */}
              {availableCameras.length > 1 && (
                <button
                  onClick={() => setShowCameraSelector(!showCameraSelector)}
                  className="absolute -top-1 -right-1 p-1 rounded-full bg-white border border-gray-300 hover:bg-gray-50 shadow-sm"
                  title="Select camera"
                >
                  <Settings size={12} />
                </button>
              )}
              
              {/* Camera Selector Dropdown */}
              {showCameraSelector && availableCameras.length > 1 && (
                <div className="absolute bottom-full mb-2 left-0 bg-white rounded-lg shadow-xl border border-gray-200 p-2 min-w-[200px] z-50">
                  <p className="text-xs font-semibold text-gray-600 mb-2 px-2">Select Camera</p>
                  {availableCameras.map((camera) => (
                    <button
                      key={camera.deviceId}
                      onClick={() => changeCamera(camera.deviceId)}
                      className={`w-full text-left px-3 py-2 rounded text-sm hover:bg-blue-50 transition-colors ${
                        selectedCameraId === camera.deviceId
                          ? 'bg-blue-100 text-blue-900 font-medium'
                          : 'text-gray-700'
                      }`}
                    >
                      {camera.label || `Camera ${availableCameras.indexOf(camera) + 1}`}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Screen Share Toggle */}
            <button
              onClick={toggleScreenShare}
              className={`p-3 rounded-full font-semibold transition-colors ${
                isScreenSharing
                  ? 'bg-purple-600 text-white hover:bg-purple-700'
                  : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
              }`}
              title={isScreenSharing ? 'Stop sharing' : 'Share screen'}
            >
              {isScreenSharing ? <Monitor size={20} /> : <MonitorOff size={20} />}
            </button>

            {/* End Call */}
            <button
              onClick={onEndCall}
              className="px-6 py-3 bg-red-600 text-white rounded-full font-semibold hover:bg-red-700 transition-colors flex items-center gap-2"
            >
              <Phone size={18} />
              End Session
            </button>

            {/* Back to Dashboard */}
            <button
              onClick={() => window.location.href = '/dashboard'}
              className="p-3 rounded-full bg-gray-200 text-gray-600 hover:bg-gray-300 transition-colors"
              title="Back to Dashboard"
            >
              <ArrowLeft size={20} />
            </button>
          </div>
        </div>
      </div>

      {/* Right Side: Transcript & Materials */}
      <div className="w-full lg:w-96 flex flex-col gap-4 h-[500px] lg:max-h-screen">
        {/* Course Materials */}
        <CourseMaterialsPanel />
        
        {/* Transcript */}
        <div className="bg-white rounded-2xl shadow-2xl p-6 flex flex-col flex-1 min-h-0">
          <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2 flex-shrink-0">
            <span>💬</span>
            Conversation
          </h3>
          
          {/* Transcript Messages - Scrollable */}
          <div className="flex-1 overflow-y-auto space-y-3 mb-4 min-h-0 pr-2 scrollbar-thin">
            {transcript.length === 0 ? (
              <div className="text-center text-gray-500 text-sm mt-8">
                <p>Your conversation will appear here</p>
              </div>
            ) : (
              transcript.map((message) => (
                <div
                  key={message.id}
                  className={`flex ${message.speaker === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-2 ${
                      message.speaker === 'user'
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-100 text-gray-900'
                    }`}
                  >
                    <p className="text-sm font-medium mb-1">
                      {message.speaker === 'user' ? 'You' : 'Johnny Robot Community Edition'}
                    </p>
                    <p className="text-sm">{message.text}</p>
                    <p className="text-xs opacity-70 mt-1">
                      {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </p>
                  </div>
                </div>
              ))
            )}
            <div ref={transcriptEndRef} />
          </div>
        </div>
      </div>
    </div>
  )
}

function CourseMaterialsPanel() {
  const { data: textbooks } = useQuery({
    queryKey: ['textbooks'],
    queryFn: async () => {
      const response = await textbookApi.list()
      return response.data.textbooks
    },
  })

  const { data: canvasStats } = useQuery({
    queryKey: ['canvas-stats'],
    queryFn: async () => {
      const response = await canvasApi.getStats()
      return response.data
    },
  })

  const totalTextbooks = textbooks?.length || 0
  const canvasConnected = canvasStats?.configured || false

  if (totalTextbooks === 0 && !canvasConnected) {
    return null
  }

  return (
    <div className="bg-white rounded-2xl shadow-2xl p-4">
      <h3 className="text-sm font-bold text-gray-900 mb-3 flex items-center gap-2">
        <BookOpen size={18} />
        Available Resources
      </h3>

      <div className="space-y-2">
        {totalTextbooks > 0 && (
          <div className="flex items-center justify-between bg-blue-50 p-2 rounded-lg">
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-blue-600" />
              <span className="text-sm font-medium text-blue-900">Textbooks</span>
            </div>
            <span className="text-sm font-bold text-blue-700">{totalTextbooks}</span>
          </div>
        )}

        {canvasConnected && (
          <div className="bg-green-50 p-2 rounded-lg">
            <div className="flex items-center gap-2">
              <span className="text-green-600">✅</span>
              <span className="text-sm font-medium text-green-900">Canvas Connected</span>
            </div>
            <p className="text-xs text-green-700 mt-1">
              Real-time access to assignments, announcements & materials
            </p>
          </div>
        )}
      </div>

      <p className="text-xs text-gray-500 mt-3">
        Johnny Robot Community Edition can access these resources during your conversation
      </p>
    </div>
  )
}
