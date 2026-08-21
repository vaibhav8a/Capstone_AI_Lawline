import { useEffect, useMemo, useState } from 'react';
import { fetchSummary } from '../../api/client';
import { useAppStore } from '../../store/useAppStore';
import { apiClient } from '../../api/client';
// import ReactMarkdown from 'react-markdown';

export function SummaryTab() {
  const { contextChunks } = useAppStore();
  const [summary, setSummary] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [glossary, setGlossary] = useState<Record<string, string>>({});

  // Use all chunks from the same top case so cheat-sheet is meaningful.
  const topChunk = contextChunks[0];
  const caseBundleText = useMemo(() => {
    if (!topChunk?.case_title) return '';
    const sameCaseChunks = contextChunks.filter((c: any) => c.case_title === topChunk.case_title);
    const joined = sameCaseChunks.map((c: any) => c.text || '').filter(Boolean).join('\n\n');
    return joined.slice(0, 30000);
  }, [contextChunks, topChunk?.case_title]);

  const handleGenerate = async () => {
    if (!topChunk || !caseBundleText) return;
    setLoading(true);
    try {
      const data = await fetchSummary(caseBundleText);
      setSummary(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const loadGlossary = async () => {
      try {
        const res = await apiClient.get('/summary/dictionary');
        const maxims = res.data.maxims || [];
        // Convert array of {term, definition} to object {term: definition}
        const glossaryObj: Record<string, string> = {};
        if (Array.isArray(maxims)) {
          maxims.forEach((item: any) => {
            if (item.term && item.definition) {
              glossaryObj[item.term] = item.definition;
            }
          });
        }
        setGlossary(glossaryObj);
      } catch {
        setGlossary({});
      }
    };
    loadGlossary();
  }, []);

  useEffect(() => {
    setSummary(null);
  }, [topChunk?.chunk_id]);

  if (!topChunk) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-slate-500">
        <p>Run a query first to summarize the top relevant case.</p>
      </div>
    );
  }

  return (
    <div className="h-full max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div className="bg-slate-900 border border-slate-700 p-6 rounded-xl shadow-lg">
        <h2 className="text-xl font-bold mb-2">Top Context: {topChunk.case_title}</h2>
        <p className="text-slate-400 mb-4 line-clamp-3 italic">"{topChunk.text}"</p>
        <button 
          onClick={handleGenerate}
          disabled={loading}
          className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-2 rounded shadow-md disabled:opacity-50"
        >
          {loading ? 'Analyzing Case...' : 'Generate Cheat Sheet'}
        </button>
      </div>

      {summary && (
        <div className="flex-1 bg-slate-900/80 border border-slate-700 p-6 rounded-xl overflow-y-auto custom-scrollbar">
          <h3 className="text-2xl font-bold text-slate-100 mb-6">{summary.case_title}</h3>
          
          <div className="grid grid-cols-2 gap-6 mb-6">
            <div className="bg-slate-800 p-4 rounded-lg">
              <h4 className="text-indigo-400 font-bold mb-2 uppercase text-sm tracking-wider">Facts</h4>
              <ul className="list-disc pl-5 text-sm text-slate-300 space-y-1">
                {summary.facts?.map((f: string, i: number) => <li key={i}>{f}</li>)}
              </ul>
            </div>
            <div className="bg-slate-800 p-4 rounded-lg">
              <h4 className="text-red-400 font-bold mb-2 uppercase text-sm tracking-wider">Issues</h4>
              <ul className="list-disc pl-5 text-sm text-slate-300 space-y-1">
                {summary.issues?.map((i: string, idx: number) => <li key={idx}>{i}</li>)}
              </ul>
            </div>
          </div>

          <div className="space-y-6">
            <div className="border-l-4 border-amber-500 pl-4">
              <h4 className="text-amber-500 font-bold mb-1 uppercase text-sm tracking-wider">Ratio Decidendi</h4>
              <p className="text-slate-300 bg-amber-500/10 p-3 rounded">{summary.ratio_decidendi}</p>
            </div>
            
            <div className="border-l-4 border-emerald-500 pl-4">
              <h4 className="text-emerald-500 font-bold mb-1 uppercase text-sm tracking-wider">Holding</h4>
              <p className="text-slate-300 bg-emerald-500/10 p-3 rounded">{summary.holding}</p>
            </div>
            
            {summary.obiter_dicta && (
              <div className="border-l-4 border-slate-500 pl-4">
                <h4 className="text-slate-500 font-bold mb-1 uppercase text-sm tracking-wider">Obiter Dicta</h4>
                <p className="text-slate-400 italic bg-slate-800/50 p-3 rounded">{summary.obiter_dicta}</p>
              </div>
            )}
          </div>

          {Object.keys(glossary).length > 0 && (
            <div className="mt-8 border-t border-slate-700 pt-4">
              <h4 className="text-sm font-semibold text-slate-300 mb-2">Legal Glossary</h4>
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(glossary).slice(0, 12).map(([term, definition]) => (
                  <div key={term} className="text-xs bg-slate-800 p-2 rounded border border-slate-700">
                    <div className="text-indigo-300">{term}</div>
                    <div className="text-slate-400">{definition}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
