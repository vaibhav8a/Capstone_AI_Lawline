import { useEffect, useState } from 'react';
import { useAppStore } from '../../store/useAppStore';
import { apiClient } from '../../api/client';

export function CompareTab() {
  const { searchQuery, contextChunks } = useAppStore();
  const [timeline, setTimeline] = useState<any[]>([]);
  const [comparison, setComparison] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [leftCase, setLeftCase] = useState<string>('');
  const [rightCase, setRightCase] = useState<string>('');

  useEffect(() => {
    setTimeline([]);
    setHasSearched(false);
    setLeftCase('');
    setRightCase('');
    setComparison(null);
  }, [searchQuery]);

  const fetchTimeline = async () => {
    if (!searchQuery) return;
    setLoading(true);
    try {
      const res = await apiClient.post('/compare/timeline', { query: searchQuery });
      setTimeline(res.data.timeline || []);
      setHasSearched(true);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  if (!searchQuery) {
    return (
       <div className="h-full flex flex-col items-center justify-center text-slate-500">
         <p>Type a legal concept in the Query tab first to generate a Timeline.</p>
       </div>
    );
  }

  return (
    <div className="h-full max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div className="bg-slate-900 border border-slate-700 p-6 rounded-xl flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold mb-1 text-slate-100">Chronological Precedent Timeline</h2>
          <p className="text-slate-400 text-sm">Visualizing evolution of: <span className="font-mono text-indigo-400">{searchQuery}</span></p>
        </div>
        <button 
          onClick={fetchTimeline}
          disabled={loading}
          className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-2 rounded shadow-md disabled:opacity-50"
        >
          {loading ? 'Plotting Timeline...' : 'Generate Timeline'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar px-4 relative">
        {contextChunks.length > 1 && (
          <div className="mb-6 bg-slate-900 border border-slate-700 rounded-xl p-4">
            <h3 className="text-sm text-slate-300 mb-3">Side-by-side Case Comparison</h3>
            <div className="grid grid-cols-2 gap-3">
              <select className="bg-slate-800 border border-slate-700 rounded p-2 text-sm"
                value={leftCase} onChange={(e)=>setLeftCase(e.target.value)}>
                <option value="">Select Case A</option>
                {[...new Set(contextChunks.map((c:any)=>c.case_title))].map((t:string)=>(
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <select className="bg-slate-800 border border-slate-700 rounded p-2 text-sm"
                value={rightCase} onChange={(e)=>setRightCase(e.target.value)}>
                <option value="">Select Case B</option>
                {[...new Set(contextChunks.map((c:any)=>c.case_title))].map((t:string)=>(
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            {leftCase && rightCase && (
              <div className="grid grid-cols-2 gap-3 mt-3">
                <button
                  className="col-span-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded"
                  onClick={async () => {
                    setLoading(true);
                    try {
                      const res = await apiClient.post('/compare/compare', {
                        case1_title: leftCase,
                        case2_title: rightCase,
                        query: searchQuery,
                      });
                      setComparison(res.data);
                    } catch (e) {
                      console.error(e);
                    } finally {
                      setLoading(false);
                    }
                  }}
                >
                  {loading ? 'Comparing...' : 'Run Side-by-Side Comparison'}
                </button>
                {comparison && (
                  <>
                    <div className="bg-slate-800 border border-slate-700 rounded p-3">
                      <div className="text-indigo-300 text-sm font-semibold">{comparison.case1?.title}</div>
                      <div className="text-xs text-slate-400 mt-2"><strong>Facts:</strong></div>
                      <div className="text-xs text-slate-300 mt-1">{comparison.case1?.facts || 'N/A'}</div>
                      <div className="text-xs text-slate-400 mt-2"><strong>Ratio:</strong></div>
                      <div className="text-xs text-slate-300 mt-1">{comparison.case1?.ratio || 'N/A'}</div>
                      <div className="text-xs text-slate-500 mt-2">{comparison.case1?.court} {comparison.case1?.year ? `(${comparison.case1?.year})` : ''}</div>
                    </div>
                    <div className="bg-slate-800 border border-slate-700 rounded p-3">
                      <div className="text-indigo-300 text-sm font-semibold">{comparison.case2?.title}</div>
                      <div className="text-xs text-slate-400 mt-2"><strong>Facts:</strong></div>
                      <div className="text-xs text-slate-300 mt-1">{comparison.case2?.facts || 'N/A'}</div>
                      <div className="text-xs text-slate-400 mt-2"><strong>Ratio:</strong></div>
                      <div className="text-xs text-slate-300 mt-1">{comparison.case2?.ratio || 'N/A'}</div>
                      <div className="text-xs text-slate-500 mt-2">{comparison.case2?.court} {comparison.case2?.year ? `(${comparison.case2?.year})` : ''}</div>
                    </div>
                    <div className="col-span-2 bg-slate-950 border border-slate-700 rounded p-3">
                      <div className="text-xs text-slate-400"><strong>Precedential relationship:</strong> {comparison.analysis?.precedential_relationship || 'N/A'}</div>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {hasSearched && timeline.length === 0 && (
          <div className="text-center text-slate-500 mt-10">No clear timeline data found for this topic.</div>
        )}

        {timeline.length > 0 && (
          <div className="relative border-l-2 border-indigo-500/30 ml-6 py-6 space-y-12">
            {timeline.map((item, idx) => (
              <div key={idx} className="relative pl-8 group">
                <div className="absolute w-4 h-4 bg-indigo-500 rounded-full -left-[9px] top-1 group-hover:scale-125 transition-transform shadow-[0_0_10px_rgba(99,102,241,0.5)] border-2 border-slate-900"></div>
                <div className="bg-slate-900 border border-slate-700 p-6 rounded-xl shadow-lg hover:border-indigo-500/50 transition-colors">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-3xl font-black tracking-tighter text-indigo-500/20">{item.year || 'Unknown'}</span>
                    <h3 className="text-lg font-bold text-slate-100">{item.case}</h3>
                  </div>
                  <div className="text-xs bg-slate-800 text-slate-400 uppercase px-2 py-1 rounded inline-block mb-3 border border-slate-700">
                    {item.court}
                  </div>
                  <p className="text-slate-300 text-sm italic">{item.excerpt}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
