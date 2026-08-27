"""
Canvas LMS tools for the AI agent.
Provides on-demand access to Canvas data - fetches directly from Canvas API when requested.
"""
import logging
from typing import Optional
from datetime import datetime, timedelta
import re
from agent_context import get_user_id

logger = logging.getLogger(__name__)


def get_user_id_from_context(context: dict) -> str:
    """Extract user_id from context - looks in session.current_agent."""
    # The context has a 'session' attribute with 'current_agent'
    if 'session' in context and context['session']:
        if 'current_agent' in context['session'] and context['session']['current_agent']:
            if 'user_id' in context['session']['current_agent']:
                return context['session']['current_agent']['user_id']
    
    # Fallback: try direct agent access (for compatibility)
    if 'agent' in context and context['agent']:
        if 'user_id' in context['agent']:
            return context['agent']['user_id']
    
    # Debug logging if we fail
    logger.error(f"Failed to get user_id. Context attrs: {[a for a in context if not a.startswith('_')]}")
    if 'session' in context:
        logger.error(f"Session attrs: {[a for a in context['session'] if not a.startswith('_')]}")
        if 'current_agent' in context['session']:
            logger.error(f"Current agent attrs: {[a for a in context['session']['current_agent'] if not a.startswith('_')]}")
    raise AttributeError("Unable to access user_id from context")



async def get_calendar_events(
    days_ahead: int = 7,
    event_type: Optional[str] = None
) -> str:
    """
    Get calendar events (assignments, tests, personal events) from Canvas.
    
    Use when user asks "what's on my calendar" or "do I have any exams".
    
    Args:
        days_ahead: Number of days to look ahead (default: 7)
        event_type: Optional type filter ('assignment', 'event', 'exam'). Default is all.
    
    Returns:
        List of calendar events
    """
    try:
        user_id = get_user_id()
        from api.services.canvas_service import get_canvas_service
        canvas_service = await get_canvas_service(user_id)
        
        if not canvas_service:
            return "You need to connect your Canvas account first."
            
        end_date = datetime.now() + timedelta(days=days_ahead)
        events = await canvas_service.get_calendar_events(
            start_date=datetime.now(),
            end_date=end_date,
            type=event_type
        )
        
        if not events:
            return f"No events found for the next {days_ahead} days."
            
        event_list = []
        for event in events:
            title = event.get('title', 'Untitled')
            start_at = event.get('start_at') or event.get('created_at')
            context = event.get('context_name', 'Unknown Context')
            
            date_str = "No date"
            if start_at:
                try:
                    dt = datetime.fromisoformat(start_at.replace('Z', '+00:00'))
                    date_str = dt.strftime('%B %d at %I:%M %p')
                except:
                    pass
            
            event_list.append(f"- {title} ({context}): {date_str}")
            
        return f"Calendar events for next {days_ahead} days:\n\n" + "\n".join(event_list)
        
    except Exception as e:
        logger.error(f"Error fetching calendar: {e}")
        return f"Error fetching calendar: {str(e)}"



async def get_upcoming_assignments(
    days_ahead: int = 7
) -> str:
    """
    Get upcoming assignments, quizzes, and tests from Canvas LMS in real-time.
    
    Use this when the student asks about deadlines or what they need to work on.
    
    Args:
        days_ahead: Number of days to look ahead (default: 7)
    
    Returns:
        Formatted list of upcoming assignments with course names and due dates
    """
    try:
        user_id = get_user_id()
        logger.info(f"Fetching Canvas assignments for user {user_id}")
        
        from api.services.canvas_service import get_canvas_service
        canvas_service = await get_canvas_service(user_id)
        
        if not canvas_service:
            return "You need to connect your Canvas account first. Go to Course Materials and add your Canvas API token."
        
        # Fetch calendar events from Canvas API
        end_date = datetime.now() + timedelta(days=days_ahead)
        events = await canvas_service.get_calendar_events(
            start_date=datetime.now(),
            end_date=end_date
        )
        
        logger.info(f"Canvas API returned {len(events) if events else 0} calendar events")
        logger.info(f"Events sample: {events[:2] if events else 'None'}")
        
        if not events:
            return f"I checked your Canvas calendar and found no assignments due in the next {days_ahead} days."
        
        # Parse and format assignments
        assignments = []
        for event in events:
            if event.get('assignment'):
                due_at = event.get('assignment', {}).get('due_at')
                if due_at:
                    try:
                        due_date = datetime.fromisoformat(due_at.replace('Z', '+00:00'))
                        course_name = event.get('context_name', 'Unknown Course')
                        title = event.get('title', 'Untitled Assignment')
                        assignments.append(
                            f"- {title} ({course_name}): Due {due_date.strftime('%B %d at %I:%M %p')}"
                        )
                    except:
                        pass
        
        logger.info(f"Parsed {len(assignments)} assignments with due dates")
        
        if not assignments:
            return f"I found {len(events)} calendar events, but none have assignment due dates in the next {days_ahead} days."
        
        return f"Here are your upcoming assignments in the next {days_ahead} days:\n\n" + "\n".join(assignments)
        
    except Exception as e:
        logger.error(f"Error getting upcoming assignments: {e}")
        return f"I couldn't retrieve your assignments from Canvas. Error: {str(e)}"



async def get_assignment_details(
    course_name: str,
    assignment_name: str
) -> str:
    """
    Get detailed information about a specific assignment from Canvas.
    
    Use when student asks for help understanding assignment requirements
    or wants details about what they need to do.
    
    Args:
        course_name: Name of the course
        assignment_name: Name or partial name of the assignment
    
    Returns:
        Detailed assignment information including description and due date
    """
    try:
        user_id = get_user_id()
        
        from api.services.canvas_service import get_canvas_service
        canvas_service = await get_canvas_service(user_id)
        
        if not canvas_service:
            return "You need to connect your Canvas account first."
        
        # Get all courses
        courses = await canvas_service.get_courses()
        
        # Find matching course
        matching_course = None
        for course in courses:
            if course_name.lower() in course.get('name', '').lower():
                matching_course = course
                break
        
        if not matching_course:
            course_list = ', '.join([c.get('name', '')[:30] for c in courses[:5]])
            return f"I couldn't find a course matching '{course_name}'. Available courses: {course_list}"
        
        # Get assignments for this course
        assignments = await canvas_service.get_assignments(matching_course['id'])
        
        logger.info(f"Canvas API returned {len(assignments) if assignments else 0} assignments for {matching_course['name']}")
        
        # Find matching assignment
        matching_assignment = None
        for assignment in assignments:
            if assignment_name.lower() in assignment.get('name', '').lower():
                matching_assignment = assignment
                break
        
        if not matching_assignment:
            available = [a.get('name', 'Unnamed')[:30] for a in assignments[:5]]
            return f"I couldn't find an assignment matching '{assignment_name}' in {matching_course['name']}. Available assignments: {', '.join(available) if available else 'None found'}"
        
        # Format assignment details
        details = [
            f"Assignment: {matching_assignment['name']}",
            f"Course: {matching_course['name']}",
        ]
        
        if matching_assignment.get('due_at'):
            try:
                due_date = datetime.fromisoformat(matching_assignment['due_at'].replace('Z', '+00:00'))
                details.append(f"Due: {due_date.strftime('%B %d, %Y at %I:%M %p')}")
            except:
                pass
        
        if matching_assignment.get('points_possible'):
            details.append(f"Points: {matching_assignment['points_possible']}")
            
        # Add Rubric Information
        if matching_assignment.get('rubric'):
            details.append("\nRubric:")
            for criterion in matching_assignment['rubric']:
                desc = criterion.get('description', 'No description')
                points = criterion.get('points', 0)
                details.append(f"- {desc} ({points} pts)")
                if criterion.get('long_description'):
                    details.append(f"  Note: {criterion['long_description']}")
        
        if matching_assignment.get('description'):
            desc = matching_assignment['description']
            # Strip HTML tags
            desc = re.sub('<[^<]+?>', '', desc)
            details.append(f"\nDescription:\n{desc[:500]}")
        
        return "\n".join(details)
        
    except Exception as e:
        logger.error(f"Error getting assignment details: {e}")
        return f"I couldn't retrieve assignment details. Error: {str(e)}"



async def get_course_announcements(
    course_name: Optional[str] = None
) -> str:
    """
    Get recent announcements from Canvas courses.
    
    Use when student asks about announcements or updates from instructors.
    
    Args:
        course_name: Optional - specific course name. If None, gets from all courses
    
    Returns:
        Formatted list of announcements with course, date, and summary
    """
    try:
        user_id = get_user_id()
        
        from api.services.canvas_service import get_canvas_service
        canvas_service = await get_canvas_service(user_id)
        
        if not canvas_service:
            return "You need to connect your Canvas account first."
        
        # Get courses
        courses = await canvas_service.get_courses()
        
        if course_name:
            # Filter to specific course
            courses = [c for c in courses if course_name.lower() in c.get('name', '').lower()]
            if not courses:
                return f"I couldn't find a course matching '{course_name}'."
        
        # Get announcements for courses
        all_announcements = []
        for course in courses[:5]:  # Limit to first 5 courses
            try:
                announcements = await canvas_service.get_announcements(course['id'])
                for ann in announcements[:3]:  # Get latest 3 per course
                    all_announcements.append({
                        'title': ann.get('title', 'Untitled'),
                        'course': course.get('name', 'Unknown'),
                        'message': ann.get('message', 'No content'),
                        'posted_at': ann.get('posted_at', '')
                    })
            except:
                pass  # Skip courses that error
        
        if not all_announcements:
            return "No recent announcements found."
        
        # Format announcements
        announcements_text = []
        for ann in all_announcements[:10]:  # Show max 10
            date_str = ""
            if ann['posted_at']:
                try:
                    date = datetime.fromisoformat(ann['posted_at'].replace('Z', '+00:00'))
                    date_str = date.strftime('%B %d')
                except:
                    pass
            
            # Strip HTML and limit length
            message = re.sub('<[^<]+?>', '', ann['message'])
            message = message[:200] + "..." if len(message) > 200 else message
            
            announcements_text.append(
                f"- {ann['title']} ({ann['course']}) - {date_str}\n  {message}"
            )
        
        return "Recent announcements:\n\n" + "\n\n".join(announcements_text)
        
    except Exception as e:
        logger.error(f"Error getting announcements: {e}")
        return f"I couldn't retrieve announcements. Error: {str(e)}"



async def get_discussion_questions(
    course_name: Optional[str] = None
) -> str:
    """
    Get discussion questions and topics from Canvas courses.
    
    Use when student asks about discussion forums, wants to see discussion prompts,
    or needs help with discussion participation.
    
    Args:
        course_name: Name of the course (optional)
    
    Returns:
        Discussion topics with prompts
    """
    try:
        user_id = get_user_id()
        
        from api.services.canvas_service import get_canvas_service
        canvas_service = await get_canvas_service(user_id)
        
        if not canvas_service:
            return "You need to connect your Canvas account first."
        
        # Get courses
        courses = await canvas_service.get_courses()
        
        if course_name:
            # Filter to specific course
            courses = [c for c in courses if course_name.lower() in c.get('name', '').lower()]
            if not courses:
                return f"I couldn't find a course matching '{course_name}'."
        
        # Get discussions for courses
        all_discussions = []
        for course in courses[:5]:
            try:
                discussions = await canvas_service.get_discussion_topics(course['id'])
                for disc in discussions[:3]:
                    all_discussions.append({
                        'title': disc.get('title', 'Untitled'),
                        'course': course.get('name', 'Unknown'),
                        'message': disc.get('message', 'No content')
                    })
            except:
                pass
        
        if not all_discussions:
            return "No discussion topics found."
        
        # Format discussions
        discussions_text = []
        for disc in all_discussions[:10]:
            # Strip HTML
            message = re.sub('<[^<]+?>', '', disc['message'])
            message = message[:200] + "..." if len(message) > 200 else message
            
            discussions_text.append(
                f"- {disc['title']} ({disc['course']})\n  {message}"
            )
        
        return "Discussion topics:\n\n" + "\n\n".join(discussions_text)
        
    except Exception as e:
        logger.error(f"Error getting discussions: {e}")
        return f"I couldn't retrieve discussions. Error: {str(e)}"



async def get_course_materials(
    course_name: str
) -> str:
    """
    Get files, modules, and materials from a Canvas course.
    
    Use when student wants to review class materials or find resources.
    
    Args:
        course_name: Name of the course
    
    Returns:
        List of course modules and files
    """
    try:
        user_id = get_user_id()
        
        from api.services.canvas_service import get_canvas_service
        canvas_service = await get_canvas_service(user_id)
        
        if not canvas_service:
            return "You need to connect your Canvas account first."
        
        # Get courses
        courses = await canvas_service.get_courses()
        
        # Find matching course
        matching_course = None
        for course in courses:
            if course_name.lower() in course.get('name', '').lower():
                matching_course = course
                break
        
        if not matching_course:
            return f"I couldn't find a course matching '{course_name}'."
        
        # Get pages for this course
        pages = await canvas_service.get_pages(matching_course['id'])
        
        if not pages:
            return f"No course materials/pages found for {matching_course['name']}."
        
        # Format materials
        materials = []
        for page in pages[:10]:  # Show max 10 pages
            title = page.get('title', 'Untitled')
            body = page.get('body', 'No content')
            
            # Strip HTML
            body = re.sub('<[^<]+?>', '', body)
            preview = body[:300] + "..." if len(body) > 300 else body
            
            materials.append(f"- {title}\n  {preview}")
        
        return f"Course materials for {matching_course['name']}:\n\n" + "\n\n".join(materials)
        
    except Exception as e:
        logger.error(f"Error getting course materials: {e}")
        return f"I couldn't retrieve course materials. Error: {str(e)}"
