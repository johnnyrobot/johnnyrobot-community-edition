"""
Memory management routes: list, delete, clear.
"""
from fastapi import APIRouter, HTTPException, Depends, status
from api.models.memory import MemoryListResponse, MemoryDeleteResponse, MemoryItem
from api.dependencies import get_current_user_id
from api.services.student_memory import get_memory_client
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/", response_model=MemoryListResponse)
async def list_memories(
    user_id: str = Depends(get_current_user_id),
    limit: int = 50
):
    """
    List all memories for the current user.
    
    Returns memories sorted by most recently updated.
    """
    try:
        # Student Memory is optional: with no usable key the list is
        # simply empty rather than an error.
        mem0 = await get_memory_client()
        results = await mem0.get_all(user_id=user_id)
        
        if not results:
            return MemoryListResponse(memories=[], total=0)
        
        # Format memories
        memories = []
        for result in results[:limit]:
            memories.append(MemoryItem(
                id=result.get("id", ""),
                memory=result.get("memory", ""),
                user_id=user_id,
                created_at=result.get("created_at"),
                updated_at=result.get("updated_at"),
                metadata=result.get("metadata") or {}
            ))
        
        logger.info(f"Retrieved {len(memories)} memories for user {user_id}")
        
        return MemoryListResponse(
            memories=memories,
            total=len(memories)
        )
    
    except Exception as e:
        logger.error(f"Failed to list memories: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve memories"
        )


@router.delete("/{memory_id}", response_model=MemoryDeleteResponse)
async def delete_memory(
    memory_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """
    Delete a specific memory by ID.
    
    Only the memory owner can delete their memories.
    """
    try:
        # Student Memory is optional. With no usable key nothing was ever
        # remembered, so the ownership check below answers 404.
        mem0 = await get_memory_client()

        # Verify memory belongs to user (security check)
        all_memories = await mem0.get_all(user_id=user_id)
        memory_ids = [m.get("id") for m in all_memories]
        
        if memory_id not in memory_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory not found or does not belong to you"
            )
        
        # Delete the memory
        await mem0.delete(memory_id=memory_id)
        
        logger.info(f"Deleted memory {memory_id} for user {user_id}")
        
        return MemoryDeleteResponse(
            message="Memory deleted successfully",
            memory_id=memory_id
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete memory {memory_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete memory"
        )


@router.delete("/", response_model=MemoryDeleteResponse)
async def clear_all_memories(
    user_id: str = Depends(get_current_user_id),
    confirm: bool = False
):
    """
    Clear all memories for the current user.
    
    Requires confirm=true query parameter as safety measure.
    """
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must set confirm=true to clear all memories"
        )
    
    try:
        # Student Memory is optional. With no usable key there is nothing
        # to clear, so the clear reports zero rather than failing.
        mem0 = await get_memory_client()

        # Get all memories
        all_memories = await mem0.get_all(user_id=user_id)
        
        if not all_memories:
            return MemoryDeleteResponse(
                message="No memories to delete",
                deleted_count=0
            )
        
        # Delete each memory
        deleted_count = 0
        for memory in all_memories:
            memory_id = memory.get("id")
            if memory_id:
                try:
                    await mem0.delete(memory_id=memory_id)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete memory {memory_id}: {str(e)}")
        
        logger.info(f"Cleared {deleted_count} memories for user {user_id}")
        
        return MemoryDeleteResponse(
            message=f"Successfully cleared {deleted_count} memories",
            deleted_count=deleted_count
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clear memories: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear memories"
        )
