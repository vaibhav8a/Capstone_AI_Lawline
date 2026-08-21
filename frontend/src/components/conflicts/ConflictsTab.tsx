import { useEffect, useState } from 'react';
import { useAppStore } from '../../store/useAppStore';
import { fetchConflicts } from '../../api/client';

export function ConflictsTab() {
  const { searchQuery } = useAppStore();
  const [conflicts, setConflicts] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  useEffect(() => {
    setConflicts([]);
    setHasSearched(false);
  }, [searchQuery]);

  const handleDetect = async () => {
    if (!searchQuery) return;
    setLoading(true);
    try {
      const data = await fetchConflicts(searchQuery);
      setConflicts(data.conflicts || []);
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
         <p>Type a legal concept in the Query tab first (e.g. "Right to privacy").</p>
       </div>
    );
  }

  return (
    <div className="h-full max-w-5xl mx-auto w-full flex flex-col gap-6">
      <div className="bg-slate-900 border border-slate-700 p-6 rounded-xl flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold mb-1 text-slate-100">Jurisdictional Conflict Detector</h2>
          <p className="text-slate-400">Target Topic: <span className="font-mono text-indigo-400">{searchQuery}</span></p>
        </div>
        <button 
          onClick={handleDetect}
          disabled={loading}
          className="bg-red-600 hover:bg-red-500 text-white px-6 py-2 rounded shadow-md disabled:opacity-50"
        >
          {loading ? 'Scanning High Courts...' : 'Detect Conflicts'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar space-y-6">
        {hasSearched && conflicts.length === 0 && (
          <div className="p-8 text-center bg-emerald-900/20 border border-emerald-500/30 rounded-xl">
             <h3 className="text-emerald-400 font-bold text-lg mb-2">No Conflicts Detected</h3>
             <p className="text-emerald-500/80">The system found unified consensus across courts for this specific issue.</p>
          </div>
        )}

        {conflicts.map((c, idx) => (
          <div key={idx} className="bg-slate-900 border border-red-500/50 rounded-xl p-6 shadow-xl relative overflow-hidden">
            <div className="absolute top-0 right-0 bg-red-600/20 px-3 py-1 text-xs text-red-500 font-bold border-l border-b border-red-500/30 rounded-bl">
              DIVERGENCE 
              ({(100 - c.similarity * 100).toFixed(1)}% split)
            </div>
            
            <div className="grid grid-cols-2 gap-8 relative mt-4">
              <div className="absolute top-0 bottom-0 left-1/2 w-px bg-slate-700 flex items-center justify-center translate-x-[-1px]">
                  <div className="bg-slate-900 text-slate-500 w-8 h-8 rounded-full border border-slate-700 flex items-center justify-center text-xs font-bold z-10">VS</div>
              </div>
              
              <div className="pr-4">
                <h4 className="text-md font-bold text-slate-200 mb-1">{c.case_a}</h4>
                <div className="text-xs bg-slate-800 text-slate-400 uppercase px-2 py-1 rounded inline-block mb-3 border border-slate-700">{c.court_a}</div>
                <p className="text-sm text-slate-300 italic bg-red-500/5 border-l-2 border-red-500/30 p-3 rounded">
                  {c.held_a}
                </p>
              </div>

              <div className="pl-4">
                <h4 className="text-md font-bold text-slate-200 mb-1">{c.case_b}</h4>
                <div className="text-xs bg-slate-800 text-slate-400 uppercase px-2 py-1 rounded inline-block mb-3 border border-slate-700">{c.court_b}</div>
                <p className="text-sm text-slate-300 italic bg-amber-500/5 border-l-2 border-amber-500/30 p-3 rounded">
                  {c.held_b}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
