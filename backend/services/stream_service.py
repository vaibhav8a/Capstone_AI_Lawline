"""
stream_service.py
Helper for SSE formatting.
"""

from typing import AsyncGenerator

async def format_sse(generator: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """
    Wraps an async generator string stream into properly formatted 
    Server-Sent Events (SSE).
    """
    async for chunk in generator:
        # Repalce newlines to keep SSE protocol intact for data payload
        safe_chunk = chunk.replace('\n', '\\n')
        yield f"data: {safe_chunk}\n\n"
        
    yield "data: [DONE]\n\n"
