import { useState } from 'react';
import { fetchCritique } from '../../api/client';

export function CritiqueTab() {
  const [argument, setArgument] = useState('');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!argument.trim()) return;
    setLoading(true);
    try {
      const data = await fetchCritique(argument);
      setResult(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-full flex flex-col gap-6 max-w-4xl mx-auto w-full">
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 shadow-lg flex-shrink-0">
        <h2 className="text-xl font-bold mb-4 text-slate-100">Adversarial Argument Critique</h2>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <textarea 
            value={argument}
            onChange={(e) => setArgument(e.target.value)}
            placeholder="Draft your legal argument here. The AI acting as opposing counsel will attempt to destroy it using precedents..."
            className="w-full bg-slate-950 border border-slate-800 rounded-lg p-4 text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-red-500 resize-none h-32"
          />
          <button 
            type="submit"
            disabled={!argument.trim() || loading}
            className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white px-6 py-3 rounded-lg font-bold self-end transition-colors"
          >
            {loading ? 'Opposing Counsel is Analyzing...' : 'Critique My Argument'}
          </button>
        </form>
      </div>

      {result && (
        <div className="flex-1 bg-slate-900 border border-slate-700 p-6 rounded-xl overflow-y-auto custom-scrollbar flex flex-col gap-6">
          <div className="flex items-center gap-6">
            <div className={`shrink-0 w-24 h-24 rounded-full border-4 flex items-center justify-center text-3xl font-black ${
               result.strike_score > 75 ? 'border-red-500 text-red-500' : 
               result.strike_score > 40 ? 'border-amber-500 text-amber-500' : 
               'border-emerald-500 text-emerald-500'
            }`}>
              {result.strike_score}
            </div>
            <div>
              <h3 className="text-xl font-bold text-slate-100 mb-1">Argument Vulnerability Score</h3>
              <p className="text-sm text-slate-400">
                A score of 100 means your argument is easily destroyed by existing precedent. A low score means it is novel or well-supported.
              </p>
            </div>
          </div>

          <div className="bg-slate-800 p-5 rounded-lg border border-slate-700">
            <h4 className="text-red-400 font-bold mb-3 uppercase tracking-wider text-sm">Identified Weaknesses</h4>
            <ul className="list-disc pl-5 space-y-2 text-slate-300">
              {result.weaknesses?.map((w: string, i: number) => <li key={i}>{w}</li>)}
            </ul>
          </div>

          <div className="bg-indigo-900/20 p-5 rounded-lg border border-indigo-500/30">
            <h4 className="text-indigo-400 font-bold mb-3 uppercase tracking-wider text-sm">Counter-Precedents</h4>
            <ul className="list-disc pl-5 space-y-2 text-slate-300">
              {result.counter_cases?.map((c: string, i: number) => <li key={i}>{c}</li>)}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
