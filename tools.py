import logging
from langchain_community.tools import DuckDuckGoSearchRun
from agent_context import get_user_id



async def search_web(query: str) -> str:
    """
    Search the web using DuckDuckGo.

    Args:
        query: The search query

    Returns:
        Search results from DuckDuckGo
    """
    try:
        results = DuckDuckGoSearchRun().run(tool_input=query)
        logging.info(f"Search results for '{query}': {results}")
        return results
    except Exception as e:
        logging.error(f"Error searching the web for '{query}': {e}")
        return f"An error occurred while searching the web for '{query}'."


async def query_documents(question: str) -> str:


    """


    Query the user's uploaded documents for information.





    Use this when the user asks about their documents, uploaded files,


    or information they've previously shared via document upload.





    Args:


        question: The question to ask about the documents





    Returns:


        Answer with source citations


    """


    try:


        # Get user_id from shared context


        user_id = get_user_id()


        logging.info(f"RAG query from user {user_id}: {question}")


        


        import asyncio


        from api.services.gemini_service import GeminiService


        


        # Instantiate service (make sure API key is set)


        service = GeminiService()


        


        # Which store holds this Student's Library. Resolved here, out of the
        # worker thread, because the record read is async — and passed in as an
        # argument so the search itself cannot pick its own store (per-Library store isolation).


        store_name = await service.resolve_library_store(user_id)


        if not store_name:


            # No uploads, so no store. Say so: searching another Student's
            # store to have something to answer with is the failure the per-Library search boundary
            # exists to prevent.


            return (


                "There are no course materials in your library yet. "


                "Upload one and I can look through it with you."


            )


        # Run sync call in thread


        answer = await asyncio.to_thread(
            service.query_textbook, question, user_id, store_name
        )


        


        return answer


    


    except Exception as e:


        logging.error(f"RAG query failed: {str(e)}")


        return "I'm sorry, I encountered an issue accessing the documents."

