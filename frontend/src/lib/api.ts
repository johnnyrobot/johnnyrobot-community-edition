import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1'

console.log('🔧 API Base URL:', API_BASE_URL)

export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Add auth token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle auth errors.
//
// Only a 401 means the credential itself is bad. A 503 means the identity
// provider could not be reached, and clearing the token there would sign
// every Student out over a transient blip. A 429 is throttling, not a bad
// credential.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

// Mirrors api/models/user.py's UserProfile, which is what both GET /auth/me
// and the login response actually put on the wire. FastAPI's response_model
// strips anything the model does not declare, so a field named anything else
// here is silently always undefined.
export interface User {
  id: string
  email: string
  name: string | null
  created_at: string
}

export interface SessionResponse {
  access_token: string
  refresh_token: string
  expires_at: number | string
  token_type: string
}

export interface AuthResponse {
  user: User
  session: SessionResponse
}

export interface LoginData {
  email: string
  password: string
}

export interface LiveKitTokenResponse {
  token: string
  room_name: string
  url: string
}

export interface Document {
  id: string
  user_id: string
  filename: string
  file_type: string
  file_size: number
  processing_status: string
  created_at: string
  processed_at: string | null
}

// Mirrors CanvasTokenResponse in api/routers/canvas.py. `disconnected` marks a
// Disconnected Source: the record outlives the credential, so a source that
// was connected once is distinguishable from one that never was.
export interface CanvasToken {
  id: string
  canvas_url: string
  last_sync: string | null
  created_at: string
  disconnected: boolean
}

export interface CanvasData {
  id: string
  data_type: 'calendar' | 'assignment' | 'discussion' | 'announcement' | 'page'
  canvas_id: string
  course_id: string | null
  course_name: string | null
  title: string
  content: string
  due_date: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface CanvasStats {
  configured: boolean
  canvas_url?: string
  last_sync: string | null
  total_items: number
  by_type: Record<string, number>
}

// Auth APIs.
//
// There is no signup call. A Deployment Operator provisions every Student
// (the reset-only demo profile) and PocketBase keeps its users create rule locked.
export const authApi = {
  login: (data: LoginData) =>
    api.post<AuthResponse>('/auth/login', data),

  logout: () =>
    api.post('/auth/logout'),

  getProfile: () =>
    api.get<User>('/auth/me'),
}

// Session APIs.
//
// Ending a Tutor Session takes no argument: the backend closes the caller's own
// most recent open Tutor Session, so the browser never names a room it might
// not own.
export const sessionApi = {
  createToken: () =>
    api.post<LiveKitTokenResponse>('/session/token'),

  endSession: () =>
    api.post('/session/end', {}),
}

// Document APIs
export const documentApi = {
  upload: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post<{ document_id: string; message: string }>('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  
  list: () => 
    api.get<{ documents: Document[] }>('/documents/'),
  
  delete: (documentId: string) => 
    api.delete(`/documents/${documentId}`),
}

// Chat APIs
export interface ChatMessage {
  role: 'user' | 'model'
  content: string
}

export interface ChatRequest {
  message: string
  history: ChatMessage[]
  textbook_id?: string
}

export interface ChatResponse {
  response: string
  history: ChatMessage[]
}

export const chatApi = {
  sendMessage: (data: ChatRequest) => 
    api.post<ChatResponse>('/chat/message', data),
}

// Textbook APIs
//
// Mirrors a raw `course_materials` record, which is what GET /textbooks/ puts
// on the wire — the router returns repository records unmapped, so every field
// name here is PocketBase's, not a response model's. TypeScript cannot catch a
// wrong name in this file: `api.get<...>` asserts a shape rather than checking
// one, so a field spelled the old way is silently `undefined` at every render
// site. These names were verified against pb_migrations/1755000000_collections.js.
export interface Textbook {
  id: string
  student: string
  title: string
  status: 'processing' | 'ready' | 'failed'
  source_identity: string
  material_source: string
  provider_file_name: string
  provider_uri: string
  provider_store_name: string
  provider_document_name: string
  created: string
  updated: string
}

/**
 * What this deployment can actually do.
 *
 * Community Edition is operator-configured and several capabilities are
 * optional: Student Memory degrades to a no-op without a Mem0 key, Canvas
 * answers 503 with no instance configured, and a deployment with no LiveKit
 * project cannot start a voice Tutor Session. Degrading is correct;
 * advertising the degraded feature anyway is not.
 *
 * Any claim the interface makes about a capability reads from here, so a
 * promise cannot outlive the thing it describes.
 */
export interface Capabilities {
  student_memory: boolean
  canvas: boolean
  voice: boolean
  upload_formats: string[]
  max_upload_bytes: number
}

export const capabilitiesApi = {
  read: () => api.get<Capabilities>('/capabilities'),
}

/** Render an allow-list of suffixes the way a Student reads it: "PDF, TXT, MD". */
export function formatUploadFormats(formats: string[]): string {
  return formats.map((f) => f.replace(/^\./, '').toUpperCase()).join(', ')
}

/** Render a byte limit as the megabytes shown on screen. */
export function formatMaxUploadSize(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))}MB`
}

export const textbookApi = {
  upload: (file: File, title: string) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('title', title)
    return api.post<{ id: string; message: string }>('/textbooks/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  
  list: () => 
    api.get<{ textbooks: Textbook[] }>('/textbooks/'),
  
  delete: (textbookId: string) => 
    api.delete(`/textbooks/${textbookId}`),
}

// Canvas APIs
export const canvasApi = {
  saveToken: (api_token: string, canvas_url?: string) =>
    api.post<CanvasToken>('/canvas/token', { api_token, canvas_url }),
  
  getToken: () =>
    api.get<CanvasToken>('/canvas/token'),
  
  deleteToken: () =>
    api.delete('/canvas/token'),
  
  syncData: () =>
    api.post<{ success: boolean; message: string; counts: Record<string, number> }>('/canvas/sync'),
  
  getData: (data_type?: string, course_id?: string) => {
    const params = new URLSearchParams()
    if (data_type) params.append('data_type', data_type)
    if (course_id) params.append('course_id', course_id)
    return api.get<{ items: CanvasData[]; total: number; by_type: Record<string, number> }>(`/canvas/data?${params}`)
  },
  
  getCourses: () =>
    api.get<{ courses: Array<{ course_id: string; course_name: string }> }>('/canvas/courses'),
  
  getStats: () =>
    api.get<CanvasStats>('/canvas/stats'),
}
