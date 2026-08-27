"""
Shared context for agent tools.
Provides thread-local storage for user_id that can be accessed by all tools.
"""
import threading

# Thread-local storage for user context
_context = threading.local()


def set_user_context(user_id: str):
    """Set the current user context for tool access."""
    _context.user_id = user_id


def get_user_id() -> str:
    """Get user_id from thread-local storage."""
    user_id = getattr(_context, 'user_id', None)
    if not user_id:
        raise AttributeError("Unable to access user_id from context")
    return user_id
