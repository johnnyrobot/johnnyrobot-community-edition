"""
Canvas Memory Service - Routes Canvas data to appropriate storage systems.

Educational content (pages, course materials) -> Google File Search API
Personal/temporal data (assignments, calendar, grades, discussions) -> Mem0

This ensures:
1. General educational content is searchable via RAG but shared knowledge
2. Personal information is isolated per-user in Mem0 for privacy
"""
import logging
import os
import re
import tempfile
from typing import List, Dict, Any
from datetime import datetime
from urllib.parse import urlparse
from api.database.repository import get_repository
from api.services.gemini_service import GeminiService
from api.services.student_memory import get_memory_client

logger = logging.getLogger(__name__)


def source_identity_for(canvas_url: str, data_type: str, canvas_id: str) -> str:
    """The stable identity of an imported source resource in one Library.

    Derived rather than stored so the same upstream resource resolves to the
    same value on every sync.
    """
    host = urlparse(canvas_url).netloc or canvas_url.strip("/")
    return f"canvas:{host}:{data_type}:{canvas_id}"


# Data types that contain personal/temporal information -> Mem0
PERSONAL_DATA_TYPES = [
    'assignment',      # Due dates, grades, rubric scores, submission status
    'calendar',        # Personal calendar events, deadlines
    'announcement',    # Course announcements (temporal, may reference students)
    'discussion',      # Discussion posts, other students' responses
    'grade',           # Student grades and feedback
    'submission',      # Assignment submissions and comments
]

# Data types that contain general educational content -> Google File Search
EDUCATIONAL_DATA_TYPES = [
    'page',            # Course pages, instructional materials
    'module_item',     # Module content items
    'file',            # Course files (PDFs, documents)
]


class CanvasMemoryService:
    """
    Service for routing Canvas data to appropriate storage systems.

    - Educational content -> Google File Search (shared knowledge base)
    - Personal/temporal data -> Mem0 (per-user memory)

    Student Memory is optional: with no usable key a sync still imports
    educational content, it just remembers nothing personal. The Mem0 client is
    asked for where it is used rather than held from `__init__`, because a sync
    builds one of these services per Canvas resource and building one must not
    be what reaches Mem0 (process-wide memory client construction).
    """

    def __init__(self, user_id: str, canvas_url: str):
        """
        Args:
            user_id: the Student this import acts for
            canvas_url: the Canvas host, which scopes every Source Identity
        """
        self.user_id = user_id
        self.canvas_url = canvas_url
        self.gemini_service = GeminiService()

    async def process_canvas_data(
        self,
        data_type: str,
        data: Dict[str, Any],
        course_name: str = "Unknown Course",
        canvas_id: str = "",
    ) -> Dict[str, bool]:
        """
        Process Canvas data and route to appropriate storage.

        Args:
            data_type: Type of Canvas data (assignment, page, discussion, etc.)
            data: The Canvas data dictionary
            course_name: Name of the course
            canvas_id: The upstream Canvas resource id, used to derive this
                resource's Source Identity for educational content

        Returns:
            Dict with success status for each storage type
        """
        results = {'mem0': False, 'file_search': False}

        if data_type in PERSONAL_DATA_TYPES:
            # Route to Mem0 for personal/temporal data
            results['mem0'] = await self._store_in_mem0(data_type, data, course_name)
        elif data_type in EDUCATIONAL_DATA_TYPES:
            # Route to Google File Search for educational content
            results['file_search'] = await self._store_in_file_search(
                data_type, data, course_name, canvas_id
            )
        else:
            logger.warning(f"Unknown data type: {data_type}, defaulting to Mem0")
            results['mem0'] = await self._store_in_mem0(data_type, data, course_name)

        return results

    async def _store_in_mem0(
        self,
        data_type: str,
        data: Dict[str, Any],
        course_name: str
    ) -> bool:
        """
        Store personal/temporal Canvas data in Mem0.

        Args:
            data_type: Type of data
            data: Canvas data dictionary
            course_name: Name of the course

        Returns:
            True if successful
        """
        try:
            # Format the memory based on data type
            memory_text = self._format_memory_text(data_type, data, course_name)

            if not memory_text:
                logger.warning(f"Empty memory text for {data_type}")
                return False

            # Create metadata for the memory
            metadata = {
                'source': 'canvas',
                'data_type': data_type,
                'course_name': course_name,
                'canvas_id': str(data.get('id', '')),
                'synced_at': datetime.now().isoformat()
            }

            # Add due date if available for temporal relevance
            if data.get('due_at'):
                metadata['due_at'] = data['due_at']
            elif data.get('start_at'):
                metadata['due_at'] = data['start_at']

            # Store in Mem0 with structured message format
            messages = [
                {"role": "user", "content": f"Canvas {data_type} from {course_name}"},
                {"role": "assistant", "content": memory_text}
            ]

            mem0 = await get_memory_client()
            await mem0.add(
                messages,
                user_id=self.user_id,
                metadata=metadata
            )

            logger.info(f"Stored Canvas {data_type} in Mem0 for user {self.user_id}: {data.get('title', 'Untitled')[:50]}")
            return True

        except Exception as e:
            logger.error(f"Error storing Canvas data in Mem0: {e}")
            return False

    async def _store_in_file_search(
        self,
        data_type: str,
        data: Dict[str, Any],
        course_name: str,
        canvas_id: str = "",
    ) -> bool:
        """
        Store educational Canvas content in Google File Search.

        Args:
            data_type: Type of data (page, module_item, etc.)
            data: Canvas data dictionary
            course_name: Name of the course
            canvas_id: The upstream Canvas resource id, used to derive this
                resource's Source Identity

        Returns:
            True if successful
        """
        try:
            title = data.get('title', 'Untitled')
            content = data.get('body', data.get('content', ''))

            if not content:
                logger.warning(f"No content for {data_type}: {title}")
                return False

            # Strip HTML from content
            clean_content = self._strip_html(content)

            if len(clean_content) < 50:  # Skip very short content
                logger.info(f"Skipping short content for {title}")
                return False

            # Create a markdown file with the content
            markdown_content = f"""# {title}

**Course:** {course_name}
**Source:** Canvas LMS
**Type:** {data_type}
**Last Updated:** {data.get('updated_at', datetime.now().isoformat())}

---

{clean_content}
"""

            # Create temporary file
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.md',
                delete=False,
                encoding='utf-8'
            ) as f:
                f.write(markdown_content)
                temp_path = f.name

            try:
                canvas_title = f"[Canvas] {course_name} - {title}"
                identity = source_identity_for(self.canvas_url, data_type, canvas_id)
                existing = await get_repository().find_material_by_source(
                    self.user_id, identity
                )

                if existing:
                    # Re-importing the same source updates its Course Material
                    # rather than minting a duplicate (the source-identity contract).
                    await self.gemini_service.update_material_content(
                        existing["id"], temp_path, canvas_title, self.user_id
                    )
                else:
                    await self.gemini_service.upload_textbook(
                        file_path=temp_path,
                        title=canvas_title,
                        user_id=self.user_id,
                        source_identity=identity,
                        material_source="canvas",
                    )
                logger.info(f"Imported Canvas {data_type} into the Student Library: {title}")
                return True
            except Exception as e:
                # A failed update leaves the existing material untouched.
                logger.error(f"Could not import Canvas {data_type} '{title}': {e}")
                return False
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Error storing Canvas content in File Search: {e}")
            return False

    def _format_memory_text(
        self,
        data_type: str,
        data: Dict[str, Any],
        course_name: str
    ) -> str:
        """
        Format Canvas data into memory text for Mem0.

        Args:
            data_type: Type of Canvas data
            data: Canvas data dictionary
            course_name: Name of the course

        Returns:
            Formatted memory text
        """
        title = data.get('title', data.get('name', 'Untitled'))

        if data_type == 'assignment':
            return self._format_assignment_memory(data, course_name, title)
        elif data_type == 'calendar':
            return self._format_calendar_memory(data, course_name, title)
        elif data_type == 'announcement':
            return self._format_announcement_memory(data, course_name, title)
        elif data_type == 'discussion':
            return self._format_discussion_memory(data, course_name, title)
        elif data_type == 'grade':
            return self._format_grade_memory(data, course_name, title)
        else:
            # Generic format
            content = self._strip_html(data.get('content', data.get('message', '')))
            return f"[{course_name}] {title}: {content[:500]}"

    def _format_assignment_memory(
        self,
        data: Dict[str, Any],
        course_name: str,
        title: str
    ) -> str:
        """Format assignment data for memory."""
        parts = [f"Assignment in {course_name}: {title}"]

        # Due date
        if data.get('due_at'):
            try:
                due_date = datetime.fromisoformat(data['due_at'].replace('Z', '+00:00'))
                parts.append(f"Due: {due_date.strftime('%B %d, %Y at %I:%M %p')}")
            except:
                parts.append(f"Due: {data['due_at']}")

        # Points
        if data.get('points_possible'):
            parts.append(f"Points: {data['points_possible']}")

        # Submission status
        if data.get('submission'):
            sub = data['submission']
            if sub.get('submitted_at'):
                parts.append("Status: Submitted")
            if sub.get('grade'):
                parts.append(f"Grade: {sub['grade']}")
            if sub.get('score'):
                parts.append(f"Score: {sub['score']}/{data.get('points_possible', '?')}")

        # Rubric summary
        if data.get('rubric'):
            rubric_items = [r.get('description', '') for r in data['rubric'][:3]]
            parts.append(f"Rubric criteria: {', '.join(rubric_items)}")

        # Description (truncated)
        if data.get('description'):
            desc = self._strip_html(data['description'])[:300]
            parts.append(f"Description: {desc}")

        return ". ".join(parts)

    def _format_calendar_memory(
        self,
        data: Dict[str, Any],
        course_name: str,
        title: str
    ) -> str:
        """Format calendar event for memory."""
        parts = [f"Calendar event in {course_name}: {title}"]

        # Start time
        if data.get('start_at'):
            try:
                start = datetime.fromisoformat(data['start_at'].replace('Z', '+00:00'))
                parts.append(f"When: {start.strftime('%B %d, %Y at %I:%M %p')}")
            except:
                parts.append(f"When: {data['start_at']}")

        # Event type
        if data.get('assignment'):
            parts.append("Type: Assignment due date")
        else:
            parts.append("Type: Calendar event")

        return ". ".join(parts)

    def _format_announcement_memory(
        self,
        data: Dict[str, Any],
        course_name: str,
        title: str
    ) -> str:
        """Format announcement for memory."""
        parts = [f"Announcement in {course_name}: {title}"]

        # Posted date
        if data.get('posted_at'):
            try:
                posted = datetime.fromisoformat(data['posted_at'].replace('Z', '+00:00'))
                parts.append(f"Posted: {posted.strftime('%B %d, %Y')}")
            except:
                pass

        # Message content (truncated)
        if data.get('message'):
            msg = self._strip_html(data['message'])[:400]
            parts.append(f"Message: {msg}")

        return ". ".join(parts)

    def _format_discussion_memory(
        self,
        data: Dict[str, Any],
        course_name: str,
        title: str
    ) -> str:
        """Format discussion topic for memory."""
        parts = [f"Discussion topic in {course_name}: {title}"]

        # Discussion prompt
        if data.get('message'):
            msg = self._strip_html(data['message'])[:400]
            parts.append(f"Prompt: {msg}")

        # Due date if it's a graded discussion
        if data.get('due_at'):
            try:
                due = datetime.fromisoformat(data['due_at'].replace('Z', '+00:00'))
                parts.append(f"Due: {due.strftime('%B %d, %Y')}")
            except:
                pass

        return ". ".join(parts)

    def _format_grade_memory(
        self,
        data: Dict[str, Any],
        course_name: str,
        title: str
    ) -> str:
        """Format grade/feedback for memory."""
        parts = [f"Grade for {title} in {course_name}"]

        if data.get('score'):
            parts.append(f"Score: {data['score']}")
        if data.get('grade'):
            parts.append(f"Grade: {data['grade']}")
        if data.get('comments'):
            comments = self._strip_html(str(data['comments']))[:300]
            parts.append(f"Feedback: {comments}")

        return ". ".join(parts)

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        if not text:
            return ""
        clean = re.sub(r'<[^>]+>', ' ', text)
        clean = re.sub(r'\s+', ' ', clean)
        return clean.strip()

    async def get_canvas_memories(self, limit: int = 20) -> List[Dict]:
        """
        Retrieve Canvas-specific memories for the user from Mem0.

        Args:
            limit: Maximum number of memories to retrieve

        Returns:
            List of Canvas memory items
        """
        try:
            # Get all memories and filter for Canvas source
            mem0 = await get_memory_client()
            all_memories = await mem0.get_all(user_id=self.user_id)

            canvas_memories = []
            for mem in all_memories:
                metadata = mem.get('metadata') or {}
                if metadata.get('source') == 'canvas':
                    canvas_memories.append({
                        'memory': mem.get('memory', ''),
                        'data_type': metadata.get('data_type', 'unknown'),
                        'course_name': metadata.get('course_name', 'Unknown'),
                        'due_at': metadata.get('due_at'),
                        'synced_at': metadata.get('synced_at')
                    })

            # Sort by due_at or synced_at
            canvas_memories.sort(
                key=lambda x: x.get('due_at') or x.get('synced_at') or '',
                reverse=True
            )

            return canvas_memories[:limit]

        except Exception as e:
            logger.error(f"Error retrieving Canvas memories: {e}")
            return []

    async def search_canvas_memories(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Search Canvas memories using Mem0's semantic search.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of relevant Canvas memories
        """
        try:
            mem0 = await get_memory_client()
            results = await mem0.search(
                query,
                user_id=self.user_id,
                limit=limit
            )

            # Filter for Canvas source
            canvas_results = []
            for result in results:
                metadata = result.get('metadata') or {}
                if metadata.get('source') == 'canvas':
                    canvas_results.append({
                        'memory': result.get('memory', ''),
                        'score': result.get('score', 0),
                        'data_type': metadata.get('data_type', 'unknown'),
                        'course_name': metadata.get('course_name', 'Unknown')
                    })

            return canvas_results

        except Exception as e:
            logger.error(f"Error searching Canvas memories: {e}")
            return []
