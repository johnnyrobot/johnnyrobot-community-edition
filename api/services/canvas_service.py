"""
Canvas LMS API Service
Handles interactions with Canvas LMS API for retrieving course data.
"""
import logging
from typing import List, Dict, Optional
import aiohttp
from datetime import datetime, timedelta
from api.config import get_settings
from api.database.pocketbase_client import ProviderUnavailable
from api.database.repository import get_repository
from api.database.store import UnfilterableValue
from api.security.crypto import decrypt_canvas_token

logger = logging.getLogger(__name__)


class CanvasNotConfigured(RuntimeError):
    """No Canvas instance is configured, so there is nothing to connect to.

    Distinct from "the Student has not connected Canvas", which is a 404 and
    The Student's own to fix. This is the Operator's: `CANVAS_BASE_URL` is
    unset, and no request can supply what the deployment never chose.
    """


def default_canvas_url() -> str:
    """The Canvas instance this deployment syncs from.

    Read at call time rather than bound to a class attribute at import, so a
    change to the environment takes effect on the next request instead of
    freezing whatever was set when the module first loaded.
    """
    url = get_settings().canvas_base_url
    if not url:
        raise CanvasNotConfigured(
            "CANVAS_BASE_URL is unset; no Canvas instance is configured"
        )
    return url.rstrip('/')


class CanvasService:
    """Service for interacting with Canvas LMS API."""

    def __init__(self, api_token: str, user_id: str, canvas_url: str = None):
        """
        Initialize Canvas service.

        Args:
            api_token: Canvas API token
            user_id: User ID for data association
            canvas_url: The Canvas instance this token belongs to. An already
                connected source passes the URL stored on its record, so
                repointing the deployment default never redirects an existing
                credential at a host it cannot authenticate against. Omitted,
                The configured instance is used, and `CanvasNotConfigured` is
                raised if the deployment has not chosen one.
        """
        self.canvas_url = (canvas_url or default_canvas_url()).rstrip('/')
        self.api_token = api_token
        self.user_id = user_id
        self.headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        }
    
    async def _make_request(self, endpoint: str, params: Optional[any] = None) -> Optional[Dict]:
        """
        Make an async request to Canvas API.

        Args:
            endpoint: API endpoint (e.g., '/api/v1/calendar_events')
            params: Query parameters (dict or list of tuples for repeated keys like include[])

        Returns:
            JSON response or None on error
        """
        url = f"{self.canvas_url}{endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params, timeout=30) as response:
                    response.raise_for_status()
                    return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Canvas API request failed for {endpoint}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in Canvas API request: {e}")
            return None
    
    async def get_calendar_events(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None, type: Optional[str] = 'assignment') -> List[Dict]:
        """
        Get calendar events (assignments, quizzes, tests).
        
        Args:
            start_date: Start date for events
            end_date: End date for events
            type: Event type (e.g., 'assignment', 'event', 'quiz'). None for all.
            
        Returns:
            List of calendar event dictionaries
        """
        if not start_date:
            start_date = datetime.now()
        if not end_date:
            end_date = start_date + timedelta(days=90)
        
        params = {
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'per_page': 100
        }
        
        if type:
            params['type'] = type
        
        events = await self._make_request('/api/v1/calendar_events', params)
        return events if isinstance(events, list) else []
    
    async def get_courses(self) -> List[Dict]:
        """
        Get user's active courses.
        
        Returns:
            List of course dictionaries
        """
        params = {
            'enrollment_state': 'active',
            'per_page': 100
        }
        
        courses = await self._make_request('/api/v1/courses', params)
        return courses if isinstance(courses, list) else []

    async def validate_token(self) -> bool:
        """Confirm the configured token actually authenticates against Canvas.

        Independent of course enrollment -- a Student enrolled in nothing is
        still a valid Student. `/api/v1/users/self` is Canvas's own canonical
        "is this credential good" check.

        This exists because `get_courses()` cannot answer the question: it
        turns every failure, a rejected token included, into `[]`, so a
        caller testing it for emptiness or for `None` learns nothing about
        The credential.
        """
        return await self._make_request('/api/v1/users/self') is not None

    async def get_assignments(self, course_id: str) -> List[Dict]:
        """
        Get assignments for a course, including rubric and submission info.

        Args:
            course_id: Canvas course ID

        Returns:
            List of assignment dictionaries
        """
        endpoint = f'/api/v1/courses/{course_id}/assignments'
        # Canvas API expects multiple include[] params, but aiohttp handles list values
        params = [
            ('per_page', 100),
            ('include[]', 'rubric'),
            ('include[]', 'submission'),
            ('include[]', 'score_statistics')
        ]

        assignments = await self._make_request(endpoint, params)
        return assignments if isinstance(assignments, list) else []
    
    async def get_assignment_details(self, course_id: str, assignment_id: str) -> Optional[Dict]:
        """
        Get detailed information about a specific assignment, including rubric.

        Args:
            course_id: Canvas course ID
            assignment_id: Canvas assignment ID

        Returns:
            Assignment details dictionary
        """
        endpoint = f'/api/v1/courses/{course_id}/assignments/{assignment_id}'
        # Canvas API expects multiple include[] params
        params = [
            ('include[]', 'rubric'),
            ('include[]', 'submission'),
            ('include[]', 'score_statistics')
        ]
        return await self._make_request(endpoint, params)
    
    async def get_discussion_topics(self, course_id: str) -> List[Dict]:
        """
        Get discussion topics for a course.
        
        Args:
            course_id: Canvas course ID
            
        Returns:
            List of discussion topic dictionaries
        """
        endpoint = f'/api/v1/courses/{course_id}/discussion_topics'
        params = {'per_page': 100}
        
        topics = await self._make_request(endpoint, params)
        return topics if isinstance(topics, list) else []
    
    async def get_announcements(self, context_codes: Optional[List[str]] = None) -> List[Dict]:
        """
        Get announcements.

        Args:
            context_codes: List of context codes (e.g., ['course_123'])

        Returns:
            List of announcement dictionaries
        """
        # Canvas API expects multiple context_codes[] params for each course
        params = [('per_page', 100)]
        if context_codes:
            for code in context_codes:
                params.append(('context_codes[]', code))

        announcements = await self._make_request('/api/v1/announcements', params)
        return announcements if isinstance(announcements, list) else []
    
    async def get_pages(self, course_id: str) -> List[Dict]:
        """
        Get course pages (content/instructional materials).
        
        Args:
            course_id: Canvas course ID
            
        Returns:
            List of page dictionaries
        """
        endpoint = f'/api/v1/courses/{course_id}/pages'
        params = {'per_page': 100}
        
        pages = await self._make_request(endpoint, params)
        return pages if isinstance(pages, list) else []
    
    async def get_page_content(self, course_id: str, page_url: str) -> Optional[Dict]:
        """
        Get full content of a specific page.
        
        Args:
            course_id: Canvas course ID
            page_url: Page URL or ID
            
        Returns:
            Page details with full content
        """
        endpoint = f'/api/v1/courses/{course_id}/pages/{page_url}'
        return await self._make_request(endpoint)
    
    async def sync_all_data(self) -> Dict[str, int]:
        """
        Sync all Canvas data to database and route to appropriate memory systems.

        - Educational content (pages) -> Google File Search API
        - Personal/temporal data (assignments, calendar, discussions, announcements) -> Mem0

        Returns:
            Dictionary with counts of synced items
        """
        from api.services.canvas_memory_service import CanvasMemoryService

        memory_service = CanvasMemoryService(self.user_id, self.canvas_url)

        counts = {
            'courses': 0,
            'calendar_events': 0,
            'assignments': 0,
            'discussions': 0,
            'announcements': 0,
            'pages': 0,
            'mem0_stored': 0,
            'file_search_stored': 0,
            'skipped': 0
        }

        def counted(kind: str, written: bool) -> None:
            """Record one write attempt under `kind`, or under `skipped`.

            Every record count passes through here, so none of them can drift
            back to a Canvas list length. `mem0_stored` and `file_search_stored`
            already worked this way -- they only ever counted an actual result
            -- and this is the rest of the counts catching up.

            `courses` is not counted here and has no write: it reports how many
            courses the sync walked, and claims nothing about storage.
            """
            counts[kind if written else 'skipped'] += 1

        try:
            # Get all courses
            courses = await self.get_courses()
            counts['courses'] = len(courses)

            # Get calendar events -> Mem0 (temporal/personal)
            calendar_events = await self.get_calendar_events()
            for event in calendar_events:
                course_name = event.get('context_name', 'Unknown Course')
                # Also retain the cached Canvas record
                counted('calendar_events', await self._save_canvas_data(
                    data_type='calendar',
                    canvas_id=str(event.get('id', '')),
                    course_id=event.get('context_code', '').replace('course_', ''),
                    course_name=course_name,
                    title=event.get('title', ''),
                    content=event.get('description', ''),
                    due_date=event.get('start_at'),
                    metadata=event
                ))
                # Route to Mem0 for personal/temporal data
                result = await memory_service.process_canvas_data(
                    'calendar', event, course_name, canvas_id=str(event.get('id', ''))
                )
                if result.get('mem0'):
                    counts['mem0_stored'] += 1

            # Process each course
            for course in courses:
                course_id = str(course.get('id', ''))
                course_name = course.get('name', '')

                # Get assignments -> Mem0 (personal: due dates, grades, submissions)
                assignments = await self.get_assignments(course_id)
                for assignment in assignments:
                    counted('assignments', await self._save_canvas_data(
                        data_type='assignment',
                        canvas_id=str(assignment.get('id', '')),
                        course_id=course_id,
                        course_name=course_name,
                        title=assignment.get('name', ''),
                        content=assignment.get('description', ''),
                        due_date=assignment.get('due_at'),
                        metadata=assignment
                    ))
                    # Route to Mem0 for personal/temporal data
                    result = await memory_service.process_canvas_data(
                        'assignment', assignment, course_name, canvas_id=str(assignment.get('id', ''))
                    )
                    if result.get('mem0'):
                        counts['mem0_stored'] += 1

                # Get discussions -> Mem0 (personal: discussion prompts and peer responses)
                discussions = await self.get_discussion_topics(course_id)
                for discussion in discussions:
                    counted('discussions', await self._save_canvas_data(
                        data_type='discussion',
                        canvas_id=str(discussion.get('id', '')),
                        course_id=course_id,
                        course_name=course_name,
                        title=discussion.get('title', ''),
                        content=discussion.get('message', ''),
                        due_date=discussion.get('posted_at'),
                        metadata=discussion
                    ))
                    # Route to Mem0 for personal data
                    result = await memory_service.process_canvas_data(
                        'discussion', discussion, course_name, canvas_id=str(discussion.get('id', ''))
                    )
                    if result.get('mem0'):
                        counts['mem0_stored'] += 1

                # Get pages -> Google File Search (educational content)
                pages = await self.get_pages(course_id)
                for page in pages:
                    # Get full page content
                    page_detail = await self.get_page_content(course_id, page.get('url', ''))
                    if page_detail:
                        counted('pages', await self._save_canvas_data(
                            data_type='page',
                            canvas_id=str(page.get('page_id', page.get('url', ''))),
                            course_id=course_id,
                            course_name=course_name,
                            title=page_detail.get('title', ''),
                            content=page_detail.get('body', ''),
                            due_date=None,
                            metadata=page_detail
                        ))
                        # Route to Google File Search for educational content
                        result = await memory_service.process_canvas_data(
                            'page', page_detail, course_name,
                            canvas_id=str(page.get('page_id', page.get('url', '')))
                        )
                        if result.get('file_search'):
                            counts['file_search_stored'] += 1

            # Get announcements -> Mem0 (temporal: time-sensitive updates)
            context_codes = [f"course_{course.get('id')}" for course in courses]
            announcements = await self.get_announcements(context_codes)
            for announcement in announcements:
                course_name = announcement.get('context_name', 'Unknown Course')
                counted('announcements', await self._save_canvas_data(
                    data_type='announcement',
                    canvas_id=str(announcement.get('id', '')),
                    course_id=announcement.get('context_code', '').replace('course_', ''),
                    course_name=course_name,
                    title=announcement.get('title', ''),
                    content=announcement.get('message', ''),
                    due_date=announcement.get('posted_at'),
                    metadata=announcement
                ))
                # Route to Mem0 for temporal data
                result = await memory_service.process_canvas_data(
                    'announcement', announcement, course_name,
                    canvas_id=str(announcement.get('id', ''))
                )
                if result.get('mem0'):
                    counts['mem0_stored'] += 1

            # Update last sync time. upsert_canvas_token is owner-scoped and
            # finds the record by owner -- self.user_id is the Student's
            # PocketBase record id, not the token record's own id.
            await get_repository().upsert_canvas_token(self.user_id, {
                'last_sync': datetime.now().isoformat()
            })

            logger.info(f"Canvas sync complete for user {self.user_id}: {counts}")
            logger.info(f"  - Mem0 stored: {counts['mem0_stored']} personal/temporal items")
            logger.info(f"  - File Search stored: {counts['file_search_stored']} educational pages")
            if counts['skipped']:
                # A skip is already visible to the Student in `counts`, but it
                # is the Operator who can act on it, and a refused record is
                # not routine.
                logger.warning(f"  - Skipped: {counts['skipped']} records the store refused")
            return counts

        except Exception as e:
            logger.error(f"Error syncing Canvas data: {e}")
            raise
    
    async def _save_canvas_data(
        self,
        data_type: str,
        canvas_id: str,
        course_id: str,
        course_name: str,
        title: str,
        content: str,
        due_date: Optional[str],
        metadata: Dict
    ) -> bool:
        """Cache one Canvas record. True if it was written.

        The answer is what the caller counts, so a sync reports records it
        actually persisted rather than records Canvas happened to list. This
        used to end in a catch-all that swallowed every failure while the
        counts came from the Canvas list lengths, so a store refusing half a
        Student's course load still reported all of it as synced -- the same
        untruth Material Removal takes such care to avoid when it stamps
        `failed` rather than claim a removal it did not complete (the immediate-removal contract).

        Only `UnfilterableValue` is caught, because it is the only failure
        confined to one record: `upsert_canvas_record` filters on `canvas_id`,
        which for a page is a Canvas-supplied URL slug, and the seam refuses a
        value it cannot render rather than escaping it (the persistence seam contract). Losing the
        other pages over one unrenderable slug would cost the Student their
        whole sync for data they did not author and cannot correct.

        Everything else propagates. `ProviderUnavailable` means the store is
        down, so every remaining write is doomed and each one costs a full
        timeout -- carrying on would leave the request hanging for as long as
        it takes to time out once per record before it could answer 503. An
        unexpected error is a bug, and reporting it as a successful sync is
        The same lie in a different suit.
        """
        try:
            await get_repository().upsert_canvas_record(
                self.user_id,
                data_type,
                canvas_id,
                {
                    'course_id': course_id,
                    'course_name': course_name,
                    'title': title,
                    'content': content,
                    'due_date': due_date,
                    'metadata': metadata or {},
                },
            )
            return True

        except UnfilterableValue as e:
            logger.error(
                f"Skipped Canvas {data_type} {canvas_id!r} for student "
                f"{self.user_id}: {e}"
            )
            return False


async def get_canvas_service(user_id: str) -> Optional[CanvasService]:
    """Build a Canvas client for a Student, or None if the source is unusable.

    The token is decrypted here and nowhere else: it exists in plaintext only
    for the duration of the calls that need it.
    """
    try:
        record = await get_repository().get_canvas_token(user_id)

        if not record or record.get("disconnected"):
            return None

        api_token = decrypt_canvas_token(
            record["api_token_ciphertext"], record.get("key_version", 0)
        )
        return CanvasService(
            api_token=api_token,
            user_id=user_id,
            canvas_url=record.get("canvas_url"),
        )

    except ProviderUnavailable:
        # "None" means the source is unusable, which the caller reports as
        # "Canvas is not connected". A storage outage is not that: it would
        # tell a Student to reconnect a source that is perfectly fine, and it
        # would report an outage as a 404. Let it through to the 503 handler.
        raise
    except Exception as e:
        logger.error(f"Could not build a Canvas client for student {user_id}: {e}")
        return None
