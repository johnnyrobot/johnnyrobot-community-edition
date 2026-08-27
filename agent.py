from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import Agent, AgentSession, RoomInputOptions, function_tool
from livekit.plugins import openai, google
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION, memory_section
from tools import search_web, query_documents
from agent_context import set_user_context

# Import language tools
from language_tools import (
    switch_language,
    get_current_language,
    list_supported_languages
)
from api.services.student_memory import get_memory_client
import logging
from api.config import get_settings
load_dotenv()

# The agent holds the same database authority as the API and reaches records
# through the same repository layer, so Student Library isolation is enforced
# in one place rather than duplicated across two access paths (the private persistence boundary).
from api.database.pocketbase_client import PocketBaseClient
from api.database.pocketbase_store import PocketBaseStore
from api.database.store import NotConfiguredStore, get_store, set_store

settings = get_settings()
logger = logging.getLogger(__name__)


def install_store() -> None:
    """Give this process the PocketBase store the agent's tools read through.

    Called as a job starts, not at import. The store is a process-wide global,
    and installing one is not a decision a module should make simply because
    somebody imported it -- a test collecting this file, or a tool reading it,
    would silently reach for the Deployment Operator's credentials and point
    The whole process at whatever `.env` happened to say. The API makes the
    same install from its lifespan (lifespan-based startup wiring); this is the agent's half.

    A store that is already installed is left alone. Whoever put it there --
    The suite's fake, or a process that embeds the agent -- chose it more
    deliberately than this default does.
    """
    if not isinstance(get_store(), NotConfiguredStore):
        return

    if not settings.pocketbase_superuser_password:
        # Same reasoning as the API: loud, and no store that pretends to
        # persist. `NotConfiguredStore` fails on first use instead.
        logger.error(
            "POCKETBASE_SUPERUSER_PASSWORD is unset; persistence stays unconfigured"
        )
        return

    set_store(PocketBaseStore(PocketBaseClient(
        base_url=settings.pocketbase_url,
        superuser_email=settings.pocketbase_superuser_email,
        superuser_password=settings.pocketbase_superuser_password,
        timeout=settings.pocketbase_timeout_seconds,
    )))
    logger.info(f"PocketBase configured at {settings.pocketbase_url}")


# Language configuration mapping
LANGUAGE_CONFIGS = {
    "openai": {
        "en-US": {"voice": "alloy", "name": "English (US)"},
        "es-ES": {"voice": "nova", "name": "Spanish"},
        "es-MX": {"voice": "nova", "name": "Spanish (Mexico)"},
        "vi-VN": {"voice": "shimmer", "name": "Vietnamese"},
        "fr-FR": {"voice": "onyx", "name": "French"},
        "de-DE": {"voice": "echo", "name": "German"},
        "ja-JP": {"voice": "shimmer", "name": "Japanese"},
        "ko-KR": {"voice": "shimmer", "name": "Korean"},
        "zh-CN": {"voice": "shimmer", "name": "Chinese"}
    },
    "google": {
        "en-US": {"voice": "Puck", "name": "English (US)"},
        "es-ES": {"voice": "Charon", "name": "Spanish"},
        "es-MX": {"voice": "Charon", "name": "Spanish (Mexico)"},
        "vi-VN": {"voice": "Aoede", "name": "Vietnamese"},
        "fr-FR": {"voice": "Fenrir", "name": "French"},
        "de-DE": {"voice": "Kore", "name": "German"},
        "ja-JP": {"voice": "Aoede", "name": "Japanese"},
        "ko-KR": {"voice": "Aoede", "name": "Korean"},
        "zh-CN": {"voice": "Aoede", "name": "Chinese"}
    }
}


class Assistant(Agent):
    """Custom Agent class with tools and instructions"""
    def __init__(self, instructions: str, user_id: str = None, language: str = None):
        # Collect all tools and wrap with function_tool for LiveKit 1.2
        tools = [
            function_tool(search_web),
            function_tool(query_documents)
        ]

        # Canvas tools - Use legacy direct tools
        logger.info("📚 Using direct Canvas tools")
        from canvas_tools import (
            get_upcoming_assignments,
            get_assignment_details,
            get_course_announcements,
            get_discussion_questions,
            get_course_materials,
            get_calendar_events
        )
        tools.extend([
            function_tool(get_upcoming_assignments),
            function_tool(get_assignment_details),
            function_tool(get_course_announcements),
            function_tool(get_discussion_questions),
            function_tool(get_course_materials),
            function_tool(get_calendar_events)
        ])
        # Language tools
        tools.extend([
            function_tool(switch_language),
            function_tool(get_current_language),
            function_tool(list_supported_languages)
        ])

        super().__init__(instructions=instructions, tools=tools)

        # Store user context
        self.user_id = user_id
        self.current_language = language


def create_realtime_model(language: str = None):
    """Create Realtime model (OpenAI or Google) with vision support."""

    # Use config language or default
    language = language or settings.agent_language
    provider = settings.ai_provider.lower() # "openai" or "google"

    # Get language configuration
    provider_config = LANGUAGE_CONFIGS.get(provider, LANGUAGE_CONFIGS["openai"])
    lang_config = provider_config.get(language, {})
    
    # Fallback to English if language not supported
    if not lang_config:
        logger.warning(f"Language {language} not found for provider {provider}. Fallback to en-US.")
        lang_config = provider_config.get("en-US", {"voice": "alloy" if provider == "openai" else "Puck"})
        
    voice = lang_config.get("voice")

    # Add language-specific instructions for better pronunciation
    language_instructions = {
        "vi-VN": "Speak in Vietnamese (Tiếng Việt). Use proper Vietnamese pronunciation, tones, and natural phrasing. Nói tiếng Việt một cách tự nhiên với giọng điệu và ngữ điệu đúng. ",
        "es-ES": "Speak in Spanish (Español). Use natural Spanish pronunciation and intonation. Habla en español con pronunciación y entonación natural. ",
        "es-MX": "Speak in Mexican Spanish. Use natural Mexican Spanish pronunciation. Habla en español mexicano con pronunciación natural. ",
        "fr-FR": "Speak in French (Français). Use natural French pronunciation. Parlez français avec une prononciation naturelle. ",
        "de-DE": "Speak in German (Deutsch). Use natural German pronunciation. Sprechen Sie Deutsch mit natürlicher Aussprache. ",
        "ja-JP": "Speak in Japanese (日本語). Use natural Japanese pronunciation. 自然な日本語の発音で話してください。",
        "ko-KR": "Speak in Korean (한국어). Use natural Korean pronunciation. 자연스러운 한국어 발음으로 말하세요. ",
        "zh-CN": "Speak in Mandarin Chinese (中文). Use natural Mandarin pronunciation. 用自然的普通话发音说话。"
    }
    lang_instruction = language_instructions.get(language, "")

    model = None
    
    if provider == "google":
        # Google Gemini Realtime (Multimodal)
        # Uses livekit-plugins-google
        logger.info(f"🤖 Using Google Gemini Realtime API - voice: {voice} for language: {language}")
        # `gemini-2.0-flash-exp` was retired and no longer exists in the API at
        # all: every session died at connect with `1008 policy violation ...
        # not found for API version v1beta`. The voice tutor failed 100% of the
        # time and nothing reported it -- the worker registered, the container
        # was healthy, and the browser showed a connected session, so every
        # signal the operator and the Student had said the tutor was working.
        # The healthcheck of the worker-only healthcheck cannot see this: the worker is up, the
        # model is gone.
        #
        # This is the plugin's own default for the Gemini API
        # (livekit-plugins-google 1.6.10, `realtime/realtime_api.py:301`),
        # which is the version the plugin is tested against.
        #
        # Deliberately not `gemini-3.1-flash-live-preview`. LiveKit documents
        # `generate_reply()`, `update_instructions()` and `update_chat_ctx()`
        # as incompatible with 3.1 -- the call is ignored with a warning rather
        # than failing loudly. Nothing here calls them today, which is exactly
        # The danger: 3.1 would look fine until something did, and then break
        # silently. See
        # https://docs.livekit.io/agents/models/realtime/plugins/gemini.md
        model = google.realtime.RealtimeModel(
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            voice=voice,
            temperature=0.8
        )
    else:
        # OpenAI Realtime (gpt-realtime)
        # LiveKit 1.2+ automatically handles video frames when video_enabled=True
        logger.info(f"🤖 Using OpenAI Realtime API (gpt-realtime) - voice: {voice} for language: {language}")
        model = openai.realtime.RealtimeModel(
            model="gpt-4o-mini-realtime-preview",
            voice=voice,
            temperature=0.8,
            modalities=["text", "audio"]
        )

    return model, lang_instruction



async def entrypoint(ctx: agents.JobContext):
    # Storage first: the language preference read below, and every tool the
    # session registers, goes through the store this installs.
    install_store()

    # Connect to the room first (required in LiveKit 1.2)
    await ctx.connect()
    logging.info("🔌 Connected to room")

    # Explicitly subscribe to all video tracks (Camera & Screen Share)
    # This ensures the agent "sees" the video for both Google and OpenAI models
    def subscribe_to_video(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if publication.kind == rtc.TrackKind.KIND_VIDEO and not publication.subscribed:
            logger.info(f"📹 Subscribing to video track {publication.sid} ({publication.source}) from {participant.identity}")
            publication.set_subscribed(True)

    @ctx.room.on("track_published")
    def on_track_published(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        subscribe_to_video(publication, participant)

    # Subscribe to existing tracks
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            subscribe_to_video(publication, participant)

    # Wait for participant and extract user info
    participant = await ctx.wait_for_participant()
    user_id = participant.identity
    user_name = participant.name or user_id

    logging.info(f"🎙️ Agent starting for user: {user_name} ({user_id})")

    # Load user's language preference from database
    user_language = settings.agent_language  # default from config
    try:
        from api.services.user_service import get_user_language_preference
        user_language = await get_user_language_preference(user_id)
        logging.info(f"📚 Loaded language preference for {user_name}: {user_language}")
    except Exception as e:
        logging.warning(f"Could not load language preference, using default: {e}")

    # Student Memory is optional: a key that is absent, wrong, expired,
    # or simply unreachable from the lab may not take a Tutor Session down --
    # The session runs against a no-op and does not remember. The client is
    # built once for this agent process, so only the first Tutor Session it
    # serves waits on Mem0 at all (process-wide memory client construction).
    #
    # The key comes from settings rather than `os.getenv`: pydantic-settings
    # already reads MEM0_API_KEY from the environment ahead of `.env`, so the
    # two agree, and the seam has one place to read it from.
    mem0 = await get_memory_client()

    # Load memories for this user
    memories = []
    canvas_memories = []
    try:
        results = await mem0.get_all(user_id=user_id)
        logging.info(f"Mem0 get_all response type: {type(results)}, content: {results[:2] if results else 'empty'}")

        if results:
            for result in results:
                if isinstance(result, dict) and "memory" in result:
                    metadata = result.get("metadata") or {}
                    memory_item = {
                        "memory": result["memory"],
                        "updated_at": result.get("updated_at", "")
                    }

                    # Separate Canvas memories from general memories
                    if metadata.get("source") == "canvas":
                        canvas_memories.append({
                            **memory_item,
                            "data_type": metadata.get("data_type", "unknown"),
                            "course_name": metadata.get("course_name", "Unknown"),
                            "due_at": metadata.get("due_at")
                        })
                    else:
                        memories.append(memory_item)

            logging.info(f"✅ Loaded {len(memories)} general memories for user {user_name}")
            logging.info(f"📚 Loaded {len(canvas_memories)} Canvas memories for user {user_name}")
    except Exception as e:
        logging.error(f"❌ Failed to load memories from Mem0: {e}")

    # Create the realtime model (also returns language-specific instruction prefix)
    model, lang_instruction = create_realtime_model(language=user_language)

    # Prepare initial instructions with language prefix, memories, and Canvas context
    instructions = lang_instruction + AGENT_INSTRUCTION

    # Build context sections
    context_parts = [f"# User Context\nThe user's account name is {user_name}."]

    # Unconditionally, including when nothing is remembered (the empty-memory case). This
    # used to be `if memories:`, so a Student with an empty memory got no
    # memory block at all while AGENT_INSTRUCTION told the agent it remembered
    # them.
    context_parts.append(memory_section([m["memory"] for m in memories]))
    if memories:
        context_parts.append(
            "IMPORTANT: If the memories contain the student's preferred name, use "
            "that name when greeting them. The account name may just be a username."
        )
        logging.info(f"📝 Injected {len(memories)} general memories into agent instructions")

    # Add Canvas memories if available (assignments, calendar, discussions, announcements)
    if canvas_memories:
        # Sort by due date (upcoming first)
        sorted_canvas = sorted(
            canvas_memories,
            key=lambda x: x.get('due_at') or '9999',
            reverse=False
        )[:20]  # Limit to 20 most relevant

        # Group by type for better organization
        assignments = [m for m in sorted_canvas if m.get('data_type') == 'assignment']
        calendar = [m for m in sorted_canvas if m.get('data_type') == 'calendar']
        discussions = [m for m in sorted_canvas if m.get('data_type') == 'discussion']
        announcements = [m for m in sorted_canvas if m.get('data_type') == 'announcement']

        canvas_context = "## Canvas LMS Data (from student's account)\n"
        canvas_context += "This is personal academic data from the student's Canvas account. Use it to help with their questions.\n\n"

        if assignments:
            canvas_context += "### Assignments & Deadlines\n"
            for a in assignments[:8]:
                canvas_context += f"- {a['memory']}\n"

        if calendar:
            canvas_context += "\n### Upcoming Calendar Events\n"
            for c in calendar[:5]:
                canvas_context += f"- {c['memory']}\n"

        if discussions:
            canvas_context += "\n### Discussion Topics\n"
            for d in discussions[:5]:
                canvas_context += f"- {d['memory']}\n"

        if announcements:
            canvas_context += "\n### Recent Announcements\n"
            for ann in announcements[:3]:
                canvas_context += f"- {ann['memory']}\n"

        context_parts.append(canvas_context)
        logging.info(f"📚 Injected {len(sorted_canvas)} Canvas memories into agent instructions")

    # Combine all context with base instructions. context_parts always has at
    # least two entries here -- the user name plus the memory section appended
    # unconditionally above -- so there is no longer a case where it holds
    # only the user name.
    instructions = "\n\n".join(context_parts) + f"\n\n{lang_instruction}{AGENT_INSTRUCTION}"

    # Create the agent session with the model
    session = AgentSession(llm=model)

    # Set user context for tools to access
    set_user_context(user_id)

    # Create assistant with instructions and tools
    assistant = Assistant(
        instructions=instructions,
        user_id=user_id,
        language=user_language
    )

    # Start the session with video enabled
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_input_options=RoomInputOptions(
            video_enabled=True,  # This enables snapshot-based vision for both OpenAI and Gemini
        ),
    )

    logging.info(f"✅ Agent session started with video enabled (snapshot-based vision)")

    # Open the conversation, rather than waiting to be spoken to.
    #
    # `SESSION_INSTRUCTION` has described this greeting since the repo was
    # written and nothing called it, so a Student who joined met silence until
    # they spoke first -- which is indistinguishable, from their side, from a
    # tutor that failed to connect.
    #
    # After `start`, never before: a reply generated first would be spoken into
    # a room the Student is not yet connected to. Wrapped, because this reaches
    # The realtime model and can fail for every reason a model call can; a
    # Student who is already connected must not lose the session to a failed
    # hello, and can simply speak first as they did before.
    try:
        await session.generate_reply(instructions=SESSION_INSTRUCTION)
    except Exception as greeting_err:
        logging.warning(
            f"The Tutor could not open the conversation; the session is unaffected "
            f"and the Student may speak first: {greeting_err}"
        )

    # Add shutdown hook to save chat context to Mem0
    async def shutdown_callback():
        logging.info(f"Shutting down for user {user_id}, saving chat context...")

        try:
            # Get chat history from session
            history = session.history
            if history and hasattr(history, 'items') and history.items:
                # Convert history to messages format for Mem0
                messages = []
                for item in history.items:
                    if item.type == "message" and item.text_content:
                        role = "user" if item.role == "user" else "assistant"
                        messages.append({
                            "role": role,
                            "content": item.text_content
                        })

                if messages:
                    # Save conversation to Mem0
                    await mem0.add(messages, user_id=user_id)
                    logging.info(f"Saved {len(messages)} messages to Mem0 for user {user_id}")
                else:
                    logging.info("No messages to save")
            else:
                logging.info("No chat history available to save")
        except Exception as e:
            logging.error(f"Error saving chat context: {e}")

    ctx.add_shutdown_callback(shutdown_callback)

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="johnnyrobot-community-edition-voice-agent",
    ))
