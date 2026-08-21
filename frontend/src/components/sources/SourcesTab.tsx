import { useCallback } from 'react';
import { useAppStore } from '../../store/useAppStore';

/** Splits raw text into individually-addressable paragraph spans */
function ParagraphRenderer({ text, chunkId }: { text: string; chunkId: string }) {
  const paragraphs = text.split(/\n+/).filter((p) => p.trim().length > 0);
  return (
    <div className="space-y-2 mt-3">
      {paragraphs.map((para, idx) => (
        <p
          key={idx}
          id={`para-${chunkId}-${idx}`}
          className="text-slate-300 text-sm leading-relaxed border-l-2 border-transparent pl-3 py-0.5 rounded-r transition-all duration-300"
        >
          {para}
        </p>
      ))}
    </div>
  );
}

/** Scrolls to a paragraph and flashes it */
function useScrollToFragment() {
  return useCallback((chunkId: string, paraIdx: number) => {
    const el = document.getElementById(`para-${chunkId}-${paraIdx}`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    // Flash animation
    el.classList.remove('border-transparent');
    el.classList.add('border-indigo-500', 'bg-indigo-500/10', 'text-slate-100');
    setTimeout(() => {
      el.classList.remove('border-indigo-500', 'bg-indigo-500/10', 'text-slate-100');
      el.classList.add('border-transparent');
    }, 2500);
  }, []);
}

export function SourcesTab() {
  const { contextChunks, bookmarkChunk, bookmarkedChunks, removeBookmark } = useAppStore();
  const scrollTo = useScrollToFragment();

  if (!contextChunks || contextChunks.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-slate-500">
        <div className="text-4xl mb-3">📚</div>
        <p>No sources yet. Run a query to retrieve legal authorities.</p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col gap-4 max-w-5xl mx-auto w-full overflow-y-auto custom-scrollbar pr-2">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-xl font-semibold">
          Retrieved Legal Authorities <span className="text-slate-500 font-normal text-base">({contextChunks.length})</span>
        </h2>
      </div>

      {contextChunks.map((chunk, idx) => {
        const isBookmarked = bookmarkedChunks.some((c) => c.chunk_id === chunk.chunk_id);
        const paraCount = (chunk.text || '').split(/\n+/).filter((p: string) => p.trim()).length;
        const score = chunk._reranker_score ?? chunk._retrieval_score;

        return (
          <div key={idx} className="bg-slate-900 border border-slate-700/60 p-5 rounded-xl shadow-md hover:border-slate-500 transition-colors group">
            {/* Header */}
            <div className="flex justify-between items-start mb-3">
              <div className="flex items-center gap-3 flex-wrap">
                <h3 className="font-medium text-slate-100 text-lg group-hover:text-indigo-300 transition-colors">
                  {chunk.case_title}
                </h3>
                <span className={`text-xs px-2 py-1 rounded-md font-medium uppercase tracking-wider border ${
                  chunk.court?.toUpperCase().includes('SUPREME')
                    ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                    : 'bg-slate-700/50 text-slate-300 border-slate-600'
                }`}>
                  {chunk.court}
                </span>
                {chunk.date && (
                  <span className="text-xs px-2 py-0.5 bg-slate-800/50 text-slate-400 rounded border border-slate-700/50">
                    {chunk.date}
                  </span>
                )}
                {chunk.precedent_status?.status && (
                  <span className={`text-xs px-2 py-0.5 rounded border ${
                    ["overruled", "reversed", "per_incuriam"].includes(chunk.precedent_status.status)
                      ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                      : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  }`}>
                    {chunk.precedent_status.status.replace("_", " ")}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-3">
                {score !== undefined && (
                  <span className="text-xs text-slate-500 font-mono">
                    {score.toFixed(3)}
                  </span>
                )}
                <button
                  onClick={() => isBookmarked ? removeBookmark(chunk.chunk_id) : bookmarkChunk(chunk)}
                  title={isBookmarked ? 'Remove bookmark' : 'Bookmark this case'}
                  className={`text-lg transition-colors ${isBookmarked ? 'text-amber-400' : 'text-slate-600 hover:text-amber-400'}`}
                >
                  {isBookmarked ? '⭐' : '☆'}
                </button>
              </div>
            </div>

            {/* Type Badge */}
            <div className="flex flex-wrap gap-2 mb-3">
              <span className={`text-xs px-2 py-0.5 rounded border ${
                chunk.section_type === 'ratio' ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30' :
                chunk.section_type === 'facts' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' :
                chunk.section_type === 'obiter' ? 'bg-slate-600/20 text-slate-400 border-slate-600/30' :
                'bg-slate-800 text-slate-400 border-slate-700'
              }`}>
                {chunk.section_type || 'unclassified'}
              </span>
            </div>

            {/* Paragraph-addressable content */}
            <ParagraphRenderer text={chunk.text || ''} chunkId={chunk.chunk_id || `chunk-${idx}`} />

            {/* Paragraph navigation pills */}
            {paraCount > 1 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                <span className="text-xs text-slate-500 mr-1">Jump to ¶:</span>
                {Array.from({ length: paraCount }, (_, i) => (
                  <button
                    key={i}
                    onClick={() => scrollTo(chunk.chunk_id || `chunk-${idx}`, i)}
                    className="text-xs px-2 py-0.5 bg-slate-800 hover:bg-indigo-500/20 text-slate-400 hover:text-indigo-300 border border-slate-700 hover:border-indigo-500/40 rounded transition-all"
                  >
                    ¶{i + 1}
                  </button>
                ))}
              </div>
            )}

            {/* Retrieval Explanation */}
            <details className="mt-4 group/details">
              <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-300 transition-colors select-none">
                ▶ Why was this retrieved?
              </summary>
              <div className="mt-2 text-xs text-slate-400 bg-slate-950/60 p-3 rounded border border-slate-800 space-y-1">
                <div><span className="text-slate-500">Source file:</span> {chunk.source_file || 'N/A'}</div>
                <div><span className="text-slate-500">Section:</span> {chunk.section || 'N/A'}</div>
                <div><span className="text-slate-500">Para refs:</span> {chunk.para_numbers?.join(', ') || 'N/A'}</div>
                {score !== undefined && (
                  <div>
                    <span className="text-slate-500">Fusion+Reranker score:</span>{' '}
                    <span className="text-indigo-400 font-mono">{score.toFixed(4)}</span>
                    {chunk._retrieval_score && chunk._reranker_score && (
                      <span className="text-slate-600 ml-1">
                        (retrieval: {chunk._retrieval_score.toFixed(4)}, reranker: {chunk._reranker_score.toFixed(4)})
                      </span>
                    )}
                  </div>
                )}
              </div>
            </details>
          </div>
        );
      })}
    </div>
  );
}

