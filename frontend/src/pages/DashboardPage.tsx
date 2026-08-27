import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { Mic, FileText, LogOut, MessageSquare, Sparkles } from 'lucide-react'
import { LanguageSelector } from '../components/LanguageSelector'
import { RocketIcon } from '../components/RocketIcon'
import { capabilitiesApi } from '../lib/api'

export default function DashboardPage() {
  const { user, logout } = useAuth()

  // Claims on this page describe what this deployment can do, so they are read
  // from it rather than written here. See api/routers/capabilities.py.
  const { data: capabilities } = useQuery({
    queryKey: ['capabilities'],
    queryFn: async () => {
      const response = await capabilitiesApi.read()
      return response.data
    },
  })

  const handleLogout = async () => {
    await logout()
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      {/* Header */}
      <header className="bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
                Johnny Robot Community Edition <RocketIcon size={28} className="text-blue-600" />
              </h1>
              <p className="text-sm text-gray-600 mt-1">
                Welcome, {user?.name || user?.email}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <LanguageSelector variant="compact" />
              <button
                onClick={handleLogout}
                className="flex items-center gap-2 px-4 py-2 text-gray-700 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <LogOut size={18} />
                <span>Logout</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="mb-8">
          <h2 className="text-3xl font-bold text-gray-900 mb-2">
            Your AI Learning Hub
          </h2>
          <p className="text-lg text-gray-600">
            Get help with any subject, upload course materials, and track your progress
          </p>
        </div>

        {/* Action Cards */}
        <div className="grid md:grid-cols-3 gap-6">
          {/* Start Voice Session Card */}
          <Link
            to="/session"
            className="bg-white rounded-2xl shadow-lg p-8 hover:shadow-xl transition-shadow border-2 border-transparent hover:border-blue-500 group"
          >
            <div className="flex items-center gap-4 mb-4">
              <div className="bg-blue-100 p-4 rounded-full group-hover:bg-blue-200 transition-colors">
                <Mic size={32} className="text-blue-600" />
              </div>
              <h3 className="text-2xl font-bold text-gray-900">
                Voice Tutor
              </h3>
            </div>
            <p className="text-gray-600">
              Talk to Johnny Robot Community Edition and get help with your studies via natural voice conversation.
            </p>
            <div className="mt-4 text-blue-600 font-semibold group-hover:text-blue-700">
              Start Voice Session →
            </div>
          </Link>

          {/* Text Chat Session Card */}
          <Link
            to="/chat"
            className="bg-white rounded-2xl shadow-lg p-8 hover:shadow-xl transition-shadow border-2 border-transparent hover:border-green-500 group"
          >
            <div className="flex items-center gap-4 mb-4">
              <div className="bg-green-100 p-4 rounded-full group-hover:bg-green-200 transition-colors">
                <MessageSquare size={32} className="text-green-600" />
              </div>
              <h3 className="text-2xl font-bold text-gray-900">
                Text Tutor
              </h3>
            </div>
            <p className="text-gray-600">
              Chat with Johnny Robot Community Edition via text and view course materials side-by-side.
            </p>
            <div className="mt-4 text-green-600 font-semibold group-hover:text-green-700">
              Start Chat Session →
            </div>
          </Link>

          {/* Upload Documents Card */}
          <Link
            to="/documents"
            className="bg-white rounded-2xl shadow-lg p-8 hover:shadow-xl transition-shadow border-2 border-transparent hover:border-indigo-500 group"
          >
            <div className="flex items-center gap-4 mb-4">
              <div className="bg-indigo-100 p-4 rounded-full group-hover:bg-indigo-200 transition-colors">
                <FileText size={32} className="text-indigo-600" />
              </div>
              <h3 className="text-2xl font-bold text-gray-900">
                Course Materials
              </h3>
            </div>
            <p className="text-gray-600">
              Upload your lecture notes, textbooks, and study guides for the AI to learn.
            </p>
            <div className="mt-4 text-indigo-600 font-semibold group-hover:text-indigo-700">
              Manage Documents →
            </div>
          </Link>
        </div>

        {/* Tips Section */}
        <div className="mt-12 bg-white rounded-2xl shadow-sm p-8 border border-gray-100">
          <h2 className="text-xl font-bold text-gray-900 mb-6 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-yellow-500" />
            How Johnny Robot Community Edition Helps You Learn
          </h2>
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 mt-6">
            <div>
              <h4 className="font-semibold text-gray-900 mb-2">🎓 Socratic Method</h4>
              <p className="text-sm text-gray-600">
                Johnny Robot Community Edition asks guiding questions to help you discover answers yourself, building deep understanding
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-gray-900 mb-2">📚 Course Materials</h4>
              <p className="text-sm text-gray-600">
                Upload your notes and textbooks. Johnny Robot Community Edition references your materials to help you learn
              </p>
            </div>
            {/* Student Memory is optional and degrades to a no-op with
                no Mem0 key configured. Degrading is correct; promising it
                anyway is not -- this panel used to render unconditionally
                while the backend logged "Student Memory is a no-op" at
                startup, so a Student was told the tutor remembers them by a
                deployment that remembers nothing (#6). */}
            {capabilities?.student_memory && (
              <div>
                <h4 className="font-semibold text-gray-900 mb-2">💭 Remembers You</h4>
                <p className="text-sm text-gray-600">
                  Johnny Robot Community Edition tracks your learning progress and picks up where you left off in previous sessions
                </p>
              </div>
            )}
            <div>
              <h4 className="font-semibold text-gray-900 mb-2">🌍 Multi-Language</h4>
              <p className="text-sm text-gray-600">
                Speak in English, Spanish, Vietnamese, or 6 other languages. Change anytime by voice or settings
              </p>
            </div>
          </div>
          
          <div className="mt-8 p-4 bg-blue-50 rounded-lg">
            <p className="text-sm text-blue-900">
              <strong>Academic Integrity:</strong> Johnny Robot Community Edition won't write essays or complete assignments for you.
              Instead, it helps you learn how to approach problems and build your skills.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
