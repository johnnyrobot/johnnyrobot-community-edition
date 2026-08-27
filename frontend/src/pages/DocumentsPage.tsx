import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { AxiosError } from 'axios'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  textbookApi,
  canvasApi,
  capabilitiesApi,
  formatUploadFormats,
  formatMaxUploadSize,
  Textbook,
} from '../lib/api'
import { Upload, FileText, Trash2, ArrowLeft, Link2, Book } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'

export default function DocumentsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [uploadError, setUploadError] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)

  // Canvas State
  const [canvasToken, setCanvasToken] = useState('')
  const [showCanvasForm, setShowCanvasForm] = useState(false)
  const [canvasError, setCanvasError] = useState('')

  // Fetch Textbooks
  const { data: textbooksData, isLoading } = useQuery({
    queryKey: ['textbooks'],
    queryFn: async () => {
      const response = await textbookApi.list()
      return response.data.textbooks
    },
  })

  // What this deployment actually accepts. Every format shown on this page
  // reads from here: the three lists below used to be written by hand and one
  // of them advertised DOCX long after the server started rejecting it (#3).
  const { data: capabilities } = useQuery({
    queryKey: ['capabilities'],
    queryFn: async () => {
      const response = await capabilitiesApi.read()
      return response.data
    },
  })

  const uploadFormats = capabilities?.upload_formats ?? []

  // Upload mutation
  const uploadMutation = useMutation({
    mutationFn: (data: { file: File; title: string }) => textbookApi.upload(data.file, data.title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['textbooks'] })
      setUploadError('')
      setTitle('')
      setFile(null)
    },
    onError: (error: AxiosError<{ detail?: string }>) => {
      setUploadError(error.response?.data?.detail || 'Upload failed')
    },
  })

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (textbookId: string) => textbookApi.delete(textbookId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['textbooks'] })
    },
  })

  // Canvas stats query
  const { data: canvasStats } = useQuery({
    queryKey: ['canvas-stats'],
    queryFn: async () => {
      const response = await canvasApi.getStats()
      return response.data
    },
  })

  // Canvas token mutation
  const saveCanvasMutation = useMutation({
    mutationFn: (token: string) =>
      canvasApi.saveToken(token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['canvas-stats'] })
      setShowCanvasForm(false)
      setCanvasToken('')
      setCanvasError('')
    },
    onError: (error: AxiosError<{ detail?: string }>) => {
      setCanvasError(error.response?.data?.detail || 'Failed to save Canvas token')
    },
  })

  // Canvas sync mutation
  const syncCanvasMutation = useMutation({
    mutationFn: () => canvasApi.syncData(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['canvas-stats'] })
    },
    onError: (error: AxiosError<{ detail?: string }>) => {
      setCanvasError(error.response?.data?.detail || 'Failed to sync Canvas data')
    },
  })

  // Canvas submit handler
  const handleCanvasSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!canvasToken) {
      setCanvasError('Please enter your Canvas API token')
      return
    }
    saveCanvasMutation.mutate(canvasToken)
  }

  const handleUpload = (e: React.FormEvent) => {
    e.preventDefault()
    if (!file || !title) {
      setUploadError('Please select a file and enter a title')
      return
    }
    uploadMutation.mutate({ file, title })
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      {/* Header */}
      <header className="bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/dashboard')}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <ArrowLeft size={24} />
            </button>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">
                Course Materials
              </h1>
              <p className="text-sm text-gray-600 mt-1">
                Upload your study materials for Johnny Robot Community Edition to reference
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* Upload Area */}
        <div className="mb-8">
          <div className="bg-white rounded-2xl p-8 shadow-sm">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Upload size={20} className="text-blue-600" />
              Upload Textbook
            </h3>
            
            <form onSubmit={handleUpload} className="space-y-4">
               <div>
                 <label className="block text-sm font-medium text-gray-700 mb-1">Textbook Title</label>
                 <input
                   type="text"
                   value={title}
                   onChange={(e) => setTitle(e.target.value)}
                   className="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                   placeholder="e.g. Introduction to Biology"
                   required
                   data-testid="document-title-input"
                 />
               </div>
               
               <div>
                 <label className="block text-sm font-medium text-gray-700 mb-1">File</label>
                 <input
                   type="file"
                   onChange={(e) => setFile(e.target.files ? e.target.files[0] : null)}
                   className="w-full border rounded-lg p-2"
                   accept={uploadFormats.join(',')}
                   required
                   data-testid="document-upload-input"
                 />
                 <p className="text-xs text-gray-500 mt-1">
                   Supported: {formatUploadFormats(uploadFormats)}
                 </p>
               </div>

               {uploadError && (
                 <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">
                   {uploadError}
                 </div>
               )}

               <button
                 type="submit"
                 disabled={uploadMutation.isPending}
                 className="bg-blue-600 text-white px-6 py-2 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
                 data-testid="document-upload-submit"
               >
                 {uploadMutation.isPending ? 'Uploading...' : 'Upload'}
               </button>
            </form>
          </div>
        </div>

        {/* Textbooks List */}
        <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
          <div className="p-6 border-b border-gray-200">
            <h2 className="text-xl font-bold text-gray-900">
              Your Textbooks
            </h2>
            <p className="text-sm text-gray-600 mt-1">
              {textbooksData?.length || 0} textbook{textbooksData?.length !== 1 ? 's' : ''}
            </p>
          </div>

          {isLoading ? (
            <div className="p-12 text-center">
              <div className="inline-block animate-spin rounded-full h-8 w-8 border-4 border-blue-600 border-t-transparent mb-4"></div>
              <p className="text-gray-600">Loading textbooks...</p>
            </div>
          ) : textbooksData && textbooksData.length > 0 ? (
            <div className="divide-y divide-gray-200">
              {textbooksData.map((tb: Textbook) => (
                <div key={tb.id} data-testid="document-row" className="p-6 hover:bg-gray-50 transition-colors">
                  <div className="flex items-start justify-between">
                    <div className="flex items-start gap-4 flex-1">
                      <div className="p-3 bg-blue-100 rounded-lg">
                        <Book size={24} className="text-blue-600" />
                      </div>
                      <div className="flex-1">
                        <h3 className="font-semibold text-gray-900 mb-1 flex items-center gap-2">
                          {tb.title}
                          {/* Material Status. A half-finished or failed import
                              stays listed but is not searchable, so it has to
                              read differently from one that worked. */}
                          {tb.status === 'processing' && (
                            <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs font-medium">
                              Processing
                            </span>
                          )}
                          {tb.status === 'failed' && (
                            <span className="px-2 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-medium">
                              Failed — not searchable
                            </span>
                          )}
                        </h3>
                        <div className="flex items-center gap-4 text-sm text-gray-600">
                          {/* A Processing or Failed material was never indexed,
                              so it has no provider file to name yet. */}
                          {tb.provider_file_name && (
                            <>
                              <span>File: {tb.provider_file_name}</span>
                              <span>•</span>
                            </>
                          )}
                          <span>
                            {tb.created
                              ? formatDistanceToNow(new Date(tb.created), { addSuffix: true })
                              : 'Just now'}
                          </span>
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={() => deleteMutation.mutate(tb.id)}
                      disabled={deleteMutation.isPending}
                      className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50"
                      title="Delete textbook"
                    >
                      <Trash2 size={20} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-12 text-center">
              <FileText size={48} className="mx-auto mb-4 text-gray-300" />
              <h3 className="text-lg font-semibold text-gray-900 mb-2">
                No textbooks yet
              </h3>
              <p className="text-gray-600">
                Upload your textbooks to get started
              </p>
            </div>
          )}
        </div>

        <div className="mt-8 bg-white rounded-2xl shadow-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
                <Link2 size={24} className="text-blue-600" />
                Canvas LMS Integration
              </h2>
              <p className="text-sm text-gray-600 mt-1">
                Connect your Canvas account to sync assignments, announcements, and course materials
              </p>
            </div>
          </div>

          {!canvasStats?.configured ? (
            <>
              {!showCanvasForm ? (
                <button
                  onClick={() => setShowCanvasForm(true)}
                  className="w-full bg-blue-50 text-blue-700 py-4 rounded-lg font-semibold hover:bg-blue-100 transition-colors"
                >
                  + Connect Canvas Account
                </button>
              ) : (
                <form onSubmit={handleCanvasSubmit} className="space-y-4">
                  <div className="bg-blue-50 p-3 rounded-lg border border-blue-200 mb-4">
                    <p className="text-sm font-medium text-blue-900">
                      Connecting to: {canvasStats?.canvas_url || 'your Canvas instance'}
                    </p>
                    <p className="text-xs text-blue-700 mt-1">
                      This is the Canvas instance this deployment is configured for
                    </p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Canvas API Token
                    </label>
                    <input
                      type="password"
                      value={canvasToken}
                      onChange={(e) => setCanvasToken(e.target.value)}
                      placeholder="Your Canvas API token"
                      className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                      required
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      Generate a token from Canvas → Account → Settings → Approved Integrations → New Access Token
                    </p>
                  </div>
                  {canvasError && (
                    <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">
                      {canvasError}
                    </div>
                  )}
                  <div className="flex gap-3">
                    <button
                      type="submit"
                      disabled={saveCanvasMutation.isPending}
                      className="flex-1 bg-blue-600 text-white py-2 rounded-lg font-semibold hover:bg-blue-700 transition-colors disabled:opacity-50"
                    >
                      {saveCanvasMutation.isPending ? 'Connecting...' : 'Connect Canvas'}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setShowCanvasForm(false)
                        setCanvasError('')
                      }}
                      className="px-6 bg-gray-200 text-gray-700 py-2 rounded-lg font-semibold hover:bg-gray-300 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </>
          ) : (
            <div className="space-y-4">
              <div className="bg-green-50 p-4 rounded-lg border border-green-200">
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <p className="text-sm font-medium text-green-900">
                      Connected to {canvasStats?.canvas_url || 'Canvas'}
                    </p>
                    <p className="text-xs text-green-700 mt-1">
                      {canvasStats?.last_sync
                        ? `Last synced: ${formatDistanceToNow(new Date(canvasStats.last_sync), { addSuffix: true })}`
                        : 'Not synced yet - click Sync to import your Canvas data'}
                    </p>
                  </div>
                  <button
                    onClick={() => canvasApi.deleteToken().then(() => queryClient.invalidateQueries({ queryKey: ['canvas-stats'] }))}
                    className="text-red-600 hover:text-red-700 text-sm font-medium ml-4"
                  >
                    Disconnect
                  </button>
                </div>
              </div>

              {/* Sync Button */}
              <button
                onClick={() => syncCanvasMutation.mutate()}
                disabled={syncCanvasMutation.isPending}
                className="w-full bg-blue-600 text-white py-3 rounded-lg font-semibold hover:bg-blue-700 transition-colors disabled:opacity-50"
              >
                {syncCanvasMutation.isPending ? 'Syncing Canvas Data...' : 'Sync Canvas Data'}
              </button>

              {/* Sync Stats */}
              {canvasStats?.total_items > 0 && (
                <div className="bg-gray-50 p-4 rounded-lg">
                  <p className="text-sm font-medium text-gray-900 mb-2">Synced Data:</p>
                  <div className="grid grid-cols-2 gap-2 text-xs text-gray-600">
                    {canvasStats.by_type && Object.entries(canvasStats.by_type).map(([type, count]) => (
                      <div key={type} className="flex justify-between">
                        <span className="capitalize">{type}s:</span>
                        <span className="font-medium">{count as number}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="bg-blue-50 p-4 rounded-lg border border-blue-200">
                <p className="text-sm font-bold text-blue-900 mb-2">How it works:</p>
                <ul className="text-xs text-blue-800 space-y-1">
                  <li>• <strong>Course pages</strong> are stored in your knowledge base for RAG queries</li>
                  <li>• <strong>Assignments & deadlines</strong> are stored in your personal memory</li>
                  <li>• <strong>Announcements & discussions</strong> are accessible during voice sessions</li>
                  <li>• Your personal Canvas data is never shared with other users</li>
                </ul>
              </div>
            </div>
          )}
        </div>

        {/* Info */}
        <div className="mt-8 bg-white rounded-2xl shadow-lg p-6">
          <h3 className="font-semibold text-gray-900 mb-3">
            📚 How it works
          </h3>
            <ul className="space-y-2 text-gray-600">
              <li>• Supported formats: {formatUploadFormats(uploadFormats)}</li>
              <li>
                • Max file size:{' '}
                {capabilities ? formatMaxUploadSize(capabilities.max_upload_bytes) : '—'} per file
              </li>
              <li>• Johnny Robot Community Edition will process your documents and can fetch Canvas data when needed</li>
            </ul>
        </div>
      </main>
    </div>
  )
}
