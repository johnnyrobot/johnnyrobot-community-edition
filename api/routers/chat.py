"""
Text Chat API routes.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from api.dependencies import get_current_user_id
from api.config import get_settings
from api.services.gemini_service import GeminiService
from api.services.student_memory import get_memory_client
from prompts import AGENT_INSTRUCTION, memory_section
import google.genai.types as types
from google.genai import Client
import logging
import re

# Provider file names are `cm-<material id>` (GeminiService.upload_textbook),
# which is what makes a material identity recoverable from the file name alone.
_PROVIDER_FILE_NAME = re.compile(r"(?:^|/)cm-([A-Za-z0-9]+)$")

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()

class ChatMessage(BaseModel):
    role: str # "user" or "model"
    content: str

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []
    textbook_id: Optional[str] = None
    
class MaterialEvidence(BaseModel):
    """An excerpt from a Ready Course Material, with where it came from.

    Evidence supports a tutor response but is not itself the response. It
    travels beside `response` instead of being spliced into it so a Student
    can weigh the answer against its source.
    """
    excerpt: str
    material_id: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    history: List[ChatMessage]
    evidence: List[MaterialEvidence] = []

def _custom_metadata(context, key: str) -> Optional[str]:
    """Read one custom metadata value attached at import, if it came back."""
    for entry in getattr(context, "custom_metadata", None) or []:
        if entry.key == key and entry.string_value:
            return entry.string_value
    return None


def _material_identity(context) -> Optional[str]:
    """Name the Course Material a chunk came from.

    The import attaches `textbook_id` as custom metadata, so that is the
    direct answer. When it does not come back, the provider file name is
    `cm-<material id>` by construction (see `GeminiService.upload_textbook`),
    which recovers the same identity.
    """
    stored = _custom_metadata(context, "textbook_id")
    if stored:
        return stored
    match = _PROVIDER_FILE_NAME.search(getattr(context, "document_name", None) or "")
    return match.group(1) if match else None


def _material_title(context) -> Optional[str]:
    """The title the Student gave the material, not the provider's file name.

    `retrieved_context.title` comes back as `cm-<material id>` -- an
    identifier the Student has never seen. The title they typed is the `title`
    custom metadata attached at import, so that is preferred and the
    provider's value is only a fallback.
    """
    return _custom_metadata(context, "title") or getattr(context, "title", None)


def _source_location(context) -> Optional[str]:
    """Where in the material the excerpt sits, in the most locatable form available."""
    page = getattr(context, "page_number", None)
    if page is not None:
        return f"page {page}"
    return getattr(context, "uri", None) or None


def _evidence_from(response) -> List[MaterialEvidence]:
    """Read the excerpts the answer was grounded on.

    Gemini omits grounding metadata entirely for an ungrounded turn, and omits
    individual fields within it, so every step tolerates absence. No evidence
    is reported as no evidence and never invented: a Student reading this field
    is entitled to conclude from an empty list that nothing was retrieved.
    """
    found: List[MaterialEvidence] = []
    for candidate in getattr(response, "candidates", None) or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            context = getattr(chunk, "retrieved_context", None)
            if context is None or not getattr(context, "text", None):
                continue
            found.append(
                MaterialEvidence(
                    excerpt=context.text,
                    material_id=_material_identity(context),
                    title=_material_title(context),
                    location=_source_location(context),
                )
            )
    return found


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    Send a message to the AI Tutor (Text Mode).
    Uses Gemini, Mem0, and Tools.
    """
    try:
        if not settings.google_api_key:
            raise HTTPException(status_code=500, detail="Google API Key not configured")

        client = Client(api_key=settings.google_api_key)
        
        # 1. Retrieve/Update Memory. Student Memory is optional: the chat
        # still answers without it, it just does not remember. A key that is
        # absent, wrong, expired, or unreachable all end at the same no-op --
        # decided once for the process, not once per message (process-wide memory client construction).
        mem0 = await get_memory_client()
        remembered = []

        try:
            # Add user message to memory (Mem0 expects list of message dicts)
            messages = [{"role": "user", "content": request.message}]
            await mem0.add(messages, user_id=user_id)

            # Retrieve relevant memories
            memories = await mem0.search(request.message, user_id=user_id, limit=5)
            remembered = [m["memory"] for m in memories or [] if m.get("memory")]
        except Exception as mem_err:
            logger.error(f"Mem0 error (non-fatal): {mem_err}")
            # Continue without memory context

        # 2. Prepare Tools
        tools = []
        gemini_service = GeminiService()
        
        # File Search Tool, scoped to this Student's Library. The store is
        # this Library's own (the per-Library search boundary, the per-Library provider-store boundary); with no textbook_id the
        # search still covers only their own materials.
        #
        # A Student who has never uploaded has no store, and gets no search
        # tool rather than a search of someone else's store. Resolving here
        # also keeps store creation off the search path entirely: only
        # uploading provisions a Library (per-Library store isolation).
        library_store = await gemini_service.resolve_library_store(user_id)
        if library_store:
            try:
                tools.append(
                    gemini_service.get_search_tool_config(
                        user_id, library_store, request.textbook_id
                    )
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid course material selection")

        # TODO: Add Canvas tools if needed (requires refactoring Canvas tools to be compatible with GenAI SDK types or function calling)
        # For this prototype, we'll focus on RAG + Chat + Memory.

        # 3. Construct System Prompt
        #
        # Composed from AGENT_INSTRUCTION, never restated. The two Tutor
        # surfaces differ in the tools they can reach, never in what the tutor
        # is allowed to do, so the Academic Integrity Policy has exactly one
        # source. This route used to paraphrase it -- and the paraphrase
        # carried no policy, so the text tutor wrote graded work on request
        # while the dashboard promised it would not (the academic-integrity constraint).
        #
        # Remembered context is prepended to the policy, never substituted for
        # it: a Student who has used the tutor before is governed the same as
        # one who has not.
        #
        # Unconditionally, including when nothing is remembered. An empty
        # memory used to contribute nothing at all here, which is what left the
        # model to invent a history (the empty-memory case).
        #
        # complete=False: `remembered` comes from a relevance `search` against
        # The current message, not a full read of this Student's memory. Empty
        # here is routine -- a returning Student changing topic -- not proof
        # they have never spoken, so the block must not claim that.
        system_prompt = f"{memory_section(remembered, complete=False)}{AGENT_INSTRUCTION}"
        
        # 4. Convert History to Gemini Format
        contents = []
        # Add system instruction as first part? Gemini 1.5/2.0 supports system_instruction arg
        
        for msg in request.history:
            contents.append(types.Content(
                role=msg.role,
                parts=[types.Part(text=msg.content)]
            ))
        
        # Add current message
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=request.message)]
        ))

        # 5. Generate Response
        config = types.GenerateContentConfig(
            temperature=0.7,
            tools=tools,
            system_instruction=system_prompt
        )
        
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite", # Cost-effective text model
            contents=contents,
            config=config
        )
        
        # 6. Process Response
        # File Search retrieval is automatic (grounding), and the chunks it
        # retrieved come back as grounding metadata. Reading only `.text` here
        # is what used to discard the Material Evidence behind the answer.
        ai_text = response.text
        evidence = _evidence_from(response)
        
        # 7. Update History & Memory
        # Add AI response to memory (Mem0 expects list of message dicts)
        if ai_text:
            try:
                assistant_messages = [{"role": "assistant", "content": ai_text}]
                await mem0.add(assistant_messages, user_id=user_id)
            except Exception as mem_err:
                logger.error(f"Mem0 add error (non-fatal): {mem_err}")
        
        # Return
        new_history = request.history + [
            ChatMessage(role="user", content=request.message),
            ChatMessage(role="model", content=ai_text or "I'm sorry, I couldn't generate a response.")
        ]
        
        return ChatResponse(
            response=ai_text or "I'm sorry, I couldn't generate a response.",
            history=new_history,
            evidence=evidence,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate a response")
