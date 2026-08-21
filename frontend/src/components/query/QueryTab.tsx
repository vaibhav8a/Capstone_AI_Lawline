import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useQueryStream } from '../../hooks/useQueryStream';
import { useAppStore } from '../../store/useAppStore';
import { LegalTooltip, parseTooltipMarkers } from '../LegalTooltip';

// ── Custom ReactMarkdown text renderer ────────────────────────────────────────
// Intercepts every inline text node and replaces [[term||def]] markers with
// live tooltip components.
function TooltipText({ node: _node, children }: any) {
  const raw = typeof children === 'string' ? children : String(children ?? '');
  const segments = parseTooltipMarkers(raw);
  // If no markers at all, render as plain span to avoid extra DOM noise
  const hasTooltips = segments.some((s) => s.type === 'tooltip');
  if (!hasTooltips) return <>{children}</>;
  return (
    <>
      {segments.map((seg, i) =>
        seg.type === 'tooltip' ? (
          <LegalTooltip key={i} term={seg.text} definition={seg.definition!}>
            {seg.text}
          </LegalTooltip>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </>
  );
}

const MD_COMPONENTS = {
  // Override paragraph to support tooltip injection
  p: ({ node, children, ...props }: any) => (
    <p {...props}>
      <TooltipText>{typeof children === 'string' ? children : children}</TooltipText>
    </p>
  ),
  // Override list items too
  li: ({ node, children, ...props }: any) => (
    <li {...props}>
      <TooltipText>{typeof children === 'string' ? children : children}</TooltipText>
    </li>
  ),
};

// ── QueryTab ──────────────────────────────────────────────────────────────────
export function QueryTab() {
  const { searchQuery, setSearchQuery, useHyDE, setUseHyDE, useSelfRAG, setUseSelfRAG, saveQuery } = useAppStore();
  const { streamQuery, isStreaming, response, error } = useQueryStream();
  // Annotated response (sent by backend after stream ends if legal terms found)
  const [annotatedResponse, setAnnotatedResponse] = useState<string | null>(null);
  const [chatHistory, setChatHistory] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([]);

  const handleQuery = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim() || isStreaming) return;
    const currentQuery = searchQuery.trim();
    setAnnotatedResponse(null);
    streamQuery(currentQuery, (annotated: string) => setAnnotatedResponse(annotated), chatHistory);
    saveQuery(currentQuery);
    setChatHistory((prev) => [...prev, { role: 'user', content: currentQuery }]);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (searchQuery.trim() && !isStreaming) handleQuery(e as any);
    }
  };

  // The displayed text: prefer annotated (with tooltips) over raw stream
  const displayText = annotatedResponse ?? response;

  useEffect(() => {
    if (isStreaming || !displayText) return;
    setChatHistory((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role !== 'user') return prev;
      return [...prev, { role: 'assistant', content: displayText.slice(0, 3000) }];
    });
  }, [isStreaming, displayText]);

  return (
    <div className="h-full flex flex-col gap-5 max-w-4xl mx-auto w-full">
      {/* Input */}
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-4 shadow-lg flex-shrink-0">
        <form onSubmit={handleQuery} className="flex flex-col gap-3">
          <textarea
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a complex legal question... (E.g. How does the Supreme Court view strict liability after the Oleum Gas Leak case?)"
            className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none h-24"
          />
          <div className="flex justify-between items-center">
            <div className="flex gap-4 text-sm text-slate-400">
              <label className="flex items-center gap-2 cursor-pointer hover:text-slate-200">
                <input
                  type="checkbox"
                  checked={useHyDE}
                  onChange={(e) => setUseHyDE(e.target.checked)}
                  className="rounded bg-slate-800 border-slate-600 text-indigo-500 focus:ring-indigo-500"
                />
                Enable HyDE
              </label>
              <label className="flex items-center gap-2 cursor-pointer hover:text-slate-200">
                <input
                  type="checkbox"
                  checked={useSelfRAG}
                  onChange={(e) => setUseSelfRAG(e.target.checked)}
                  className="rounded bg-slate-800 border-slate-600 text-indigo-500 focus:ring-indigo-500"
                />
                Self-RAG Critic
              </label>
            </div>
            <button
              type="submit"
              disabled={!searchQuery.trim() || isStreaming}
              className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2 rounded-lg font-medium transition-colors"
            >
              {isStreaming ? 'Searching...' : 'Search'}
            </button>
          </div>
        </form>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto custom-scrollbar bg-slate-900 border border-slate-700 rounded-xl p-6 shadow-lg leading-relaxed text-slate-300 relative">
        {!displayText && !error && !isStreaming && (
          <div className="h-full flex flex-col items-center justify-center text-slate-500 gap-4">
            <div className="text-5xl">⚖️</div>
            <p className="text-base">Enter a legal query to begin research.</p>
            <p className="text-xs text-slate-600 max-w-xs text-center">
              Latin legal terms will be <span className="italic text-indigo-500/70 underline decoration-dotted">highlighted</span> with hover definitions.
            </p>
          </div>
        )}

        {error && (
          <div className="p-4 bg-red-900/20 border border-red-500/50 rounded-lg text-red-200">
            <strong>Error:</strong> {error}
          </div>
        )}

        {annotatedResponse && (
          <div className="flex items-center gap-2 mb-4 text-xs text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-3 py-2 rounded-lg">
            <span>✦</span>
            <span>Latin maxims detected — hover the <span className="italic underline decoration-dotted">highlighted terms</span> for definitions.</span>
          </div>
        )}

        <div className="prose prose-invert prose-indigo max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={MD_COMPONENTS}
          >
            {displayText}
          </ReactMarkdown>
          {isStreaming && (
            <span className="inline-block w-2 h-5 bg-indigo-500 animate-pulse ml-1 align-middle rounded-sm" />
          )}
        </div>
      </div>
    </div>
  );
}
