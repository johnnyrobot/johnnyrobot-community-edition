import { Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import VoiceCallPage from './pages/VoiceCallPage'
import TextChatPage from './pages/TextChatPage'
import DocumentsPage from './pages/DocumentsPage'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { status, loading, retry } = useAuth()

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>
  }

  // A Student whose token has not been rejected is still signed in, even when
  // The profile cannot be fetched right now. Sending them to /login during an
  // outage offered a password field as the remedy for a problem a password
  // cannot fix, and taught re-entering a credential in response to a backend
  // fault. Stay on the route and say what is actually wrong (lifespan-based startup wiring).
  if (status === 'degraded') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl shadow-xl p-8 max-w-md w-full text-center">
          <h2 className="text-xl font-bold text-gray-900 mb-2">
            Johnny Robot Community Edition is temporarily unavailable
          </h2>
          <p className="text-gray-600 mb-6">
            You are still signed in. This usually clears on its own in a moment —
            there is no need to sign in again.
          </p>
          <button
            onClick={() => retry()}
            className="w-full bg-blue-600 text-white py-3 rounded-lg font-semibold hover:bg-blue-700"
          >
            Try again
          </button>
        </div>
      </div>
    )
  }

  if (status !== 'authenticated') {
    return <Navigate to="/login" />
  }

  return <>{children}</>
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route 
          path="/dashboard" 
          element={
            <PrivateRoute>
              <DashboardPage />
            </PrivateRoute>
          } 
        />
        <Route 
          path="/session" 
          element={
            <PrivateRoute>
              <VoiceCallPage />
            </PrivateRoute>
          } 
        />
        <Route 
          path="/chat" 
          element={
            <PrivateRoute>
              <TextChatPage />
            </PrivateRoute>
          } 
        />
        <Route 
          path="/documents" 
          element={
            <PrivateRoute>
              <DocumentsPage />
            </PrivateRoute>
          } 
        />
        <Route path="/" element={<Navigate to="/dashboard" />} />
      </Routes>
    </AuthProvider>
  )
}
