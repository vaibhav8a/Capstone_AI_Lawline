import { useState, useCallback } from 'react';
import { useAppStore } from '../store/useAppStore';
import { apiClient } from '../api/client';

export const useQueryStream = () => {
  const [isStreaming, setIsStreaming] = useState(false);
  const [response, setResponse] = useState('');
  const [error, setError] = useState<string | null>(null);
  const { setContextChunks } = useAppStore();

  /**
   * @param query          The legal question to send
   * @param onAnnotated    Optional callback — called with the tooltip-annotated
   *                       version of the full response once the backend finishes.
   */
  const streamQuery = useCallback(async (
    query: string,
    onAnnotated?: (annotated: string) => void,
    chatHistory: Array<{ role: 'user' | 'assistant'; content: string }> = []
  ) => {
    setIsStreaming(true);
    setResponse('');
    setError(null);
    setContextChunks([]);

    const authToken = useAppStore.getState().authToken;
    const { searchFilters, useHyDE, useSelfRAG } = useAppStore.getState();

    // 1. Parallel retrieval — fetch context chunks first
    let retrievedChunks: any[] = [];
    try {
      const execRes = await apiClient.post('/query/execute', {
        query,
        stream: false,
        filters: searchFilters,
        use_hyde: useHyDE,
        use_self_rag: useSelfRAG,
      });
      if (execRes.data.context_chunks) {
        retrievedChunks = execRes.data.context_chunks;
        setContextChunks(retrievedChunks);
      }
    } catch (e: any) {
      console.warn('[useQueryStream] /execute failed:', e.message);
    }

    // 2. SSE streaming generation
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
      }

      const res = await fetch('/api/query/stream', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          query,
          stream: true,
          filters: searchFilters,
          use_hyde: useHyDE,
          use_self_rag: useSelfRAG,
          context_chunks: retrievedChunks,
          chat_history: chatHistory,
        }),
      });

      if (!res.ok) throw new Error(`Server error: ${res.status}`);

      const reader = res.body?.getReader();
      const decoder = new TextDecoder('utf-8');
      if (!reader) throw new Error('No reader');

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') { setIsStreaming(false); return; }

          // Backend sends __ANNOTATED__:{json_string} after streaming ends
          // when it finds Latin legal terms in the response.
          if (data.startsWith('__ANNOTATED__:')) {
            try {
              const jsonStr = data.slice('__ANNOTATED__:'.length);
              const annotated: string = JSON.parse(jsonStr);
              if (onAnnotated) onAnnotated(annotated);
            } catch {
              // Silently ignore parse errors — raw response still shown
            }
            continue;
          }

          // Normal token — restore literal newlines and append
          const text = data.replace(/\\n/g, '\n');
          setResponse(prev => prev + text);
        }
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsStreaming(false);
    }
  }, [setContextChunks]);

  return { streamQuery, isStreaming, response, error };
}
