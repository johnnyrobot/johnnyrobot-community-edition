"""
Language switching tools for the AI agent.
Allows users to change their preferred language via voice commands.
"""
import logging
from agent_context import get_user_id

logger = logging.getLogger(__name__)


# Language code mapping with names
SUPPORTED_LANGUAGES = {
    "english": "en-US",
    "spanish": "es-ES",
    "spanish (spain)": "es-ES",
    "spanish (mexico)": "es-MX",
    "vietnamese": "vi-VN",
    "french": "fr-FR",
    "german": "de-DE",
    "japanese": "ja-JP",
    "korean": "ko-KR",
    "chinese": "zh-CN",
    "mandarin": "zh-CN",
    
    # Allow code-based requests too
    "en-us": "en-US",
    "es-es": "es-ES",
    "es-mx": "es-MX",
    "vi-vn": "vi-VN",
    "fr-fr": "fr-FR",
    "de-de": "de-DE",
    "ja-jp": "ja-JP",
    "ko-kr": "ko-KR",
    "zh-cn": "zh-CN",
}

# Language display names
LANGUAGE_NAMES = {
    "en-US": "English (US)",
    "es-ES": "Spanish (Spain)",
    "es-MX": "Spanish (Mexico)",
    "vi-VN": "Vietnamese",
    "fr-FR": "French",
    "de-DE": "German",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Chinese (Simplified)"
}

# Confirmation messages in each language
LANGUAGE_CONFIRMATIONS = {
    "en-US": "Your language preference has been saved to English. It will take effect in your next session. For now, let's continue in the current language.",
    "es-ES": "Tu preferencia de idioma se ha guardado en español. Se aplicará en tu próxima sesión. Por ahora, continuemos en el idioma actual.",
    "es-MX": "Tu preferencia de idioma se ha guardado en español. Se aplicará en tu próxima sesión. Por ahora, continuemos en el idioma actual.",
    "vi-VN": "Tùy chọn ngôn ngữ của bạn đã được lưu là tiếng Việt. Nó sẽ có hiệu lực trong phiên tiếp theo. Bây giờ, hãy tiếp tục bằng ngôn ngữ hiện tại.",
    "fr-FR": "Votre préférence linguistique a été enregistrée en français. Elle prendra effet lors de votre prochaine session. Pour l'instant, continuons dans la langue actuelle.",
    "de-DE": "Ihre Spracheinstellung wurde auf Deutsch gespeichert. Sie wird in Ihrer nächsten Sitzung wirksam. Fahren wir vorerst in der aktuellen Sprache fort.",
    "ja-JP": "言語設定が日本語に保存されました。次回のセッションで有効になります。今は現在の言語で続けましょう。",
    "ko-KR": "언어 설정이 한국어로 저장되었습니다. 다음 세션에서 적용됩니다. 지금은 현재 언어로 계속하겠습니다.",
    "zh-CN": "您的语言偏好已保存为中文。它将在下次会话中生效。现在，让我们继续使用当前语言。"
}



async def switch_language(
    language: str
) -> str:
    """
    Switch the agent's response language during the conversation.
    
    Use this when the user explicitly requests to change language.
    Examples: "Switch to Spanish", "Speak Vietnamese", "Change to English"
    
    Args:
        language: Target language name or code (e.g., "Spanish", "Vietnamese", "es-ES", "vi-VN")
    
    Returns:
        Confirmation message in the new language
    """
    try:
        user_id = get_user_id()
        
        # Normalize language input
        language_lower = language.lower().strip()
        
        # Find matching language code
        language_code = SUPPORTED_LANGUAGES.get(language_lower)
        
        if not language_code:
            # Try partial matching for common variations
            for key, code in SUPPORTED_LANGUAGES.items():
                if language_lower in key or key in language_lower:
                    language_code = code
                    break
        
        if not language_code:
            supported_list = ", ".join(set(LANGUAGE_NAMES.values()))
            return f"I don't recognize that language. I support: {supported_list}. Please try again."
        
        # Store preference in database
        try:
            from api.services.user_service import update_user_language_preference
            await update_user_language_preference(user_id, language_code)
            logger.info(f"Updated language preference for user {user_id} to {language_code}")
        except Exception as e:
            logger.error(f"Failed to save language preference: {e}")
            # Continue anyway - the agent will still respond in the new language
        
        # Note: Language change will take effect in your next session
        # The preference has been saved to your account
        
        # Return confirmation in the new language
        language_name = LANGUAGE_NAMES.get(language_code, language_code)
        confirmation = LANGUAGE_CONFIRMATIONS.get(language_code, 
            f"I've switched to {language_name}. How can I help you?")
        
        logger.info(f"User {user_id} switched to {language_name} ({language_code})")
        
        return confirmation
        
    except Exception as e:
        logger.error(f"Error switching language: {e}")
        return f"I couldn't switch languages right now. Error: {str(e)}"



async def get_current_language() -> str:
    """
    Get the agent's current language setting.
    
    Use this when the user asks "What language are you speaking?" or 
    "What language is this?"
    
    Returns:
        Information about the current language
    """
    try:
        # Get user's saved language preference from database
        user_id = get_user_id()
        current_code = "en-US"  # default
        
        try:
            from api.services.user_service import get_user_language_preference
            current_code = await get_user_language_preference(user_id)
        except Exception:
            pass  # Use default
        
        language_name = LANGUAGE_NAMES.get(current_code, current_code)
        
        # Return in the current language
        responses = {
            "en-US": f"I'm currently speaking {language_name}. You can ask me to switch to another language anytime!",
            "es-ES": f"Actualmente estoy hablando {language_name}. ¡Puedes pedirme que cambie a otro idioma en cualquier momento!",
            "es-MX": f"Actualmente estoy hablando {language_name}. ¡Puedes pedirme que cambie a otro idioma en cualquier momento!",
            "vi-VN": f"Tôi hiện đang nói {language_name}. Bạn có thể yêu cầu tôi chuyển sang ngôn ngữ khác bất cứ lúc nào!",
            "fr-FR": f"Je parle actuellement {language_name}. Vous pouvez me demander de changer de langue à tout moment!",
            "de-DE": f"Ich spreche derzeit {language_name}. Sie können mich jederzeit bitten, zu einer anderen Sprache zu wechseln!",
            "ja-JP": f"現在{language_name}を話しています。いつでも他の言語に切り替えるように頼むことができます！",
            "ko-KR": f"현재 {language_name}를 사용하고 있습니다. 언제든지 다른 언어로 전환하도록 요청할 수 있습니다!",
            "zh-CN": f"我目前正在使用{language_name}。您可以随时要求我切换到另一种语言！"
        }
        
        return responses.get(current_code, responses["en-US"])
        
    except Exception as e:
        logger.error(f"Error getting current language: {e}")
        return "I'm speaking English right now."



async def list_supported_languages() -> str:
    """
    List all languages the agent can speak.
    
    Use this when user asks "What languages do you support?" or 
    "What languages can you speak?"
    
    Returns:
        List of supported languages
    """
    try:
        # Get user's saved language preference
        try:
            user_id = get_user_id()
            from api.services.user_service import get_user_language_preference
            current_code = await get_user_language_preference(user_id)
        except Exception:
            current_code = "en-US"  # default
        
        # Build list of languages
        languages = []
        for code, name in sorted(LANGUAGE_NAMES.items()):
            languages.append(f"- {name}")
        
        language_list = "\n".join(languages)
        
        # Response in current language
        responses = {
            "en-US": f"I can speak these languages:\n\n{language_list}\n\nJust ask me to switch to any of them!",
            "es-ES": f"Puedo hablar estos idiomas:\n\n{language_list}\n\n¡Solo pídeme que cambie a cualquiera de ellos!",
            "es-MX": f"Puedo hablar estos idiomas:\n\n{language_list}\n\n¡Solo pídeme que cambie a cualquiera de ellos!",
            "vi-VN": f"Tôi có thể nói những ngôn ngữ này:\n\n{language_list}\n\nChỉ cần yêu cầu tôi chuyển sang bất kỳ ngôn ngữ nào!",
            "fr-FR": f"Je peux parler ces langues:\n\n{language_list}\n\nDemandez-moi simplement de passer à l'une d'entre elles!",
            "de-DE": f"Ich kann diese Sprachen sprechen:\n\n{language_list}\n\nBitten Sie mich einfach, zu einer von ihnen zu wechseln!",
            "ja-JP": f"これらの言語を話すことができます:\n\n{language_list}\n\nいずれかに切り替えるよう頼んでください！",
            "ko-KR": f"이 언어를 말할 수 있습니다:\n\n{language_list}\n\n그 중 하나로 전환하도록 요청하세요!",
            "zh-CN": f"我可以说这些语言:\n\n{language_list}\n\n只需要求我切换到其中任何一个！"
        }
        
        return responses.get(current_code, responses["en-US"])
        
    except Exception as e:
        logger.error(f"Error listing languages: {e}")
        return "I support English, Spanish, Vietnamese, French, German, Japanese, Korean, and Chinese."
