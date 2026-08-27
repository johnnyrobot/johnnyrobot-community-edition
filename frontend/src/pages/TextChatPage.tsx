import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Send, ArrowLeft, BookOpen, MessageSquare } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { chatApi, ChatMessage, textbookApi, Textbook } from '../lib/api'

export default function TextChatPage() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedTextbook, setSelectedTextbook] = useState<string | undefined>()
  const [textbooks, setTextbooks] = useState<Textbook[]>([])
  const [showMaterials, setShowMaterials] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (messages.length === 0) {
      setMessages([{ role: 'model', content: `Hello ${user?.name?.split(' ')[0] || 'there'}! I'm Johnny Robot Community Edition. How can I help you with your studies today?` }])
    }
  }, [user, messages.length])

  useEffect(() => {
    loadTextbooks()
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const loadTextbooks = async () => {
    try {
      const response = await textbookApi.list()
      setTextbooks(response.data.textbooks)
    } catch (error) {
      console.error('Failed to load textbooks', error)
    }
  }

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!input.trim() || loading) return

    const userMsg: ChatMessage = { role: 'user', content: input }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      // Exclude the just-added user message from history sent to API to avoid duplication if API handles it,
      // BUT our API expects history so far. 
      // Actually, our API adds the new message to history. 
      // Let's send current messages (excluding the one we just added locally for optimistic UI? 
      // No, `messages` state hasn't updated in closure yet.
      // Better to send `messages` (history) + `input` (current message).
      
      const response = await chatApi.sendMessage({
        message: userMsg.content,
        history: messages, // History before this message
        textbook_id: selectedTextbook
      })
      
      // Update with history returned from server (includes AI response)
      setMessages(response.data.history)
    } catch (error) {
      console.error('Chat error', error)
      setMessages(prev => [...prev, { role: 'model', content: "I'm having trouble connecting right now. Please try again." }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="h-screen flex flex-col bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b px-6 py-4 flex items-center justify-between shadow-sm z-10">
        <div className="flex items-center gap-4">
          <button 
            onClick={() => navigate('/dashboard')}
            className="p-2 hover:bg-gray-100 rounded-full transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-gray-600" />
          </button>
          <h1 className="text-xl font-semibold text-gray-800 flex items-center gap-2">
            <MessageSquare className="w-6 h-6 text-blue-600" />
            Tutor Chat
          </h1>
        </div>
        
        <div className="flex items-center gap-4">
           <select 
             value={selectedTextbook || ''}
             onChange={(e) => setSelectedTextbook(e.target.value || undefined)}
             className="px-3 py-2 border rounded-lg text-sm max-w-xs truncate"
           >
             <option value="">Select a Textbook for Context...</option>
             {textbooks.map(tb => (
               <option key={tb.id} value={tb.id}>{tb.title}</option>
             ))}
           </select>
           
           <button
             onClick={() => setShowMaterials(!showMaterials)}
             className={`p-2 rounded-lg transition-colors ${showMaterials ? 'bg-blue-100 text-blue-700' : 'hover:bg-gray-100 text-gray-600'}`}
             title="Toggle Materials View"
           >
             <BookOpen className="w-5 h-5" />
           </button>
        </div>
      </header>

      {/* Main Content - Split Layout */}
      <div className="flex-1 flex overflow-hidden">
        
        {/* Chat Pane */}
        <div className={`flex-1 flex flex-col transition-all duration-300 ${showMaterials ? 'w-1/2 border-r' : 'w-full'}`}>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.map((msg, idx) => (
              <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div data-testid={msg.role === 'user' ? 'chat-message-user' : 'chat-message-assistant'} className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                  msg.role === 'user' 
                    ? 'bg-blue-600 text-white rounded-br-none' 
                    : 'bg-white border shadow-sm text-gray-800 rounded-bl-none'
                }`}>
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="bg-white border shadow-sm text-gray-500 rounded-2xl rounded-bl-none px-4 py-3">
                  <div className="flex gap-2">
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></span>
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-100"></span>
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-200"></span>
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <form onSubmit={handleSend} className="p-4 bg-white border-t">
            <div className="flex gap-2">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask your tutor something..."
                className="flex-1 px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
                disabled={loading}
                data-testid="chat-input"
              />
              <button
                type="submit"
                disabled={!input.trim() || loading}
                className="p-3 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
                data-testid="chat-send"
              >
                <Send className="w-5 h-5" />
              </button>
            </div>
          </form>
        </div>

        {/* Materials/Canvas Pane (Split Window) */}
        {showMaterials && (
          <div className="w-1/2 flex flex-col bg-gray-100">
            <div className="p-4 bg-white border-b flex justify-between items-center">
              <h2 className="font-semibold text-gray-700 flex items-center gap-2">
                <BookOpen className="w-4 h-4" />
                Materials & Canvas
              </h2>
              {/* Could add tabs here for "Textbook" vs "Canvas" */}
            </div>
            
            <div className="flex-1 p-8 flex items-center justify-center text-gray-400 flex-col gap-4">
              {selectedTextbook ? (
                <div className="text-center">
                  <BookOpen className="w-16 h-16 mx-auto mb-4 opacity-50" />
                  <p className="text-lg font-medium text-gray-600">
                    {textbooks.find(t => t.id === selectedTextbook)?.title}
                  </p>
                  <p className="text-sm">Context loaded for AI Tutor.</p>
                  <p className="text-xs mt-4 opacity-75">
                    (Document viewer not implemented in prototype. <br/> 
                    The AI has read this book and can answer questions about it.)
                  </p>
                </div>
              ) : (
                <div className="text-center">
                   <p>Select a textbook to enable context.</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
