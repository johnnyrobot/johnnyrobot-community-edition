import { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';

interface Language {
  code: string;
  name: string;
  flag: string;
}

const SUPPORTED_LANGUAGES: Language[] = [
  { code: 'en-US', name: 'English', flag: '🇺🇸' },
  { code: 'es-ES', name: 'Español', flag: '🇪🇸' },
  { code: 'es-MX', name: 'Español (MX)', flag: '🇲🇽' },
  { code: 'vi-VN', name: 'Tiếng Việt', flag: '🇻🇳' },
  { code: 'fr-FR', name: 'Français', flag: '🇫🇷' },
  { code: 'de-DE', name: 'Deutsch', flag: '🇩🇪' },
  { code: 'ja-JP', name: '日本語', flag: '🇯🇵' },
  { code: 'ko-KR', name: '한국어', flag: '🇰🇷' },
  { code: 'zh-CN', name: '中文', flag: '🇨🇳' },
];

interface LanguageSelectorProps {
  variant?: 'dropdown' | 'compact';
  className?: string;
}

export function LanguageSelector({ variant = 'dropdown', className = '' }: LanguageSelectorProps) {
  const { user, getAuthToken } = useAuth();
  const [currentLanguage, setCurrentLanguage] = useState<string>('en-US');
  const [loading, setLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

  // Load current language preference
  useEffect(() => {
    if (user) {
      loadLanguagePreference();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadLanguagePreference is redefined every render and depends on the unmemoized getAuthToken; stabilizing that chain is a memoization refactor this file does not own
  }, [user]);

  const loadLanguagePreference = async () => {
    try {
      const token = await getAuthToken();
      const response = await fetch(`${import.meta.env.VITE_API_URL}/users/me/language`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setCurrentLanguage(data.language || 'en-US');
      }
    } catch (error) {
      console.error('Failed to load language preference:', error);
    }
  };

  const updateLanguagePreference = async (languageCode: string) => {
    setLoading(true);
    setMessage(null);

    try {
      const token = await getAuthToken();
      const response = await fetch(`${import.meta.env.VITE_API_URL}/users/me/language`, {
        method: 'PATCH',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ language: languageCode }),
      });

      if (response.ok) {
        setCurrentLanguage(languageCode);
        setMessage({ type: 'success', text: 'Language updated! Reconnect to apply changes.' });
        
        // Auto-hide success message after 3 seconds
        setTimeout(() => setMessage(null), 3000);
      } else {
        throw new Error('Failed to update language');
      }
    } catch (error) {
      console.error('Failed to update language preference:', error);
      setMessage({ type: 'error', text: 'Failed to update language preference' });
    } finally {
      setLoading(false);
      setIsOpen(false);
    }
  };

  const currentLangData = SUPPORTED_LANGUAGES.find(lang => lang.code === currentLanguage);

  if (variant === 'compact') {
    return (
      <div className={`relative ${className}`}>
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-700 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
          disabled={loading}
        >
          <span className="text-xl">{currentLangData?.flag}</span>
          <span>{currentLangData?.name}</span>
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {isOpen && (
          <>
            <div 
              className="fixed inset-0 z-10" 
              onClick={() => setIsOpen(false)}
            />
            <div className="absolute right-0 mt-2 w-56 rounded-lg shadow-lg bg-white ring-1 ring-black ring-opacity-5 z-20">
              <div className="py-1">
                {SUPPORTED_LANGUAGES.map((lang) => (
                  <button
                    key={lang.code}
                    onClick={() => updateLanguagePreference(lang.code)}
                    className={`flex items-center gap-3 w-full px-4 py-2 text-sm hover:bg-gray-100 transition-colors ${
                      currentLanguage === lang.code ? 'bg-blue-50 text-blue-700' : 'text-gray-700'
                    }`}
                  >
                    <span className="text-xl">{lang.flag}</span>
                    <span>{lang.name}</span>
                    {currentLanguage === lang.code && (
                      <svg className="w-4 h-4 ml-auto" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        {message && (
          <div 
            className={`absolute right-0 mt-2 px-4 py-2 rounded-lg shadow-lg text-sm z-30 ${
              message.type === 'success' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
            }`}
          >
            {message.text}
          </div>
        )}
      </div>
    );
  }

  // Dropdown variant (default)
  return (
    <div className={`w-full ${className}`}>
      <label className="block text-sm font-medium text-gray-700 mb-2">
        Language Preference
      </label>
      
      <select
        value={currentLanguage}
        onChange={(e) => updateLanguagePreference(e.target.value)}
        disabled={loading}
        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {SUPPORTED_LANGUAGES.map((lang) => (
          <option key={lang.code} value={lang.code}>
            {lang.flag} {lang.name}
          </option>
        ))}
      </select>

      {message && (
        <div 
          className={`mt-2 px-4 py-2 rounded-lg text-sm ${
            message.type === 'success' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
          }`}
        >
          {message.text}
        </div>
      )}

            <div className="flex items-start gap-3 p-3 bg-blue-50 text-blue-700 rounded-lg text-sm">
              <span className="text-lg flex-shrink-0">🌐</span>
              <p>
                Your language preference will be used when you connect to Johnny Robot Community Edition.
                The interface language will update where supported.
              </p>
            </div>
    </div>
  );
}
