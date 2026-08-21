import { useAppStore } from '../store/useAppStore';

const COURTS = ["Supreme Court", "Delhi High Court", "Bombay High Court", "Madras High Court", "Allahabad High Court"];
const TOPICS = ["Privacy", "Free Speech", "Personal Liberty", "Equal Protection", "Environmental Law", "Criminal Procedure", "Constitutional Law"];
const STATUSES = ["good_law", "overruled", "reversed", "distinguished", "followed", "per_incuriam"];

export function FiltersPanel() {
  const { searchFilters, setSearchFilters } = useAppStore();

  const toggleArray = (key: string, value: string) => {
    const current: string[] = (searchFilters as any)[key] || [];
    const updated = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value];
    setSearchFilters({ ...searchFilters, [key]: updated });
  };

  const setYear = (key: 'yearFrom' | 'yearTo', val: number) => {
    setSearchFilters({ ...searchFilters, [key]: val });
  };

  return (
    <div className="flex flex-col gap-6 p-4 bg-slate-900 border-r border-slate-800 w-64 h-full overflow-y-auto custom-scrollbar shrink-0">
      <div>
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Filters</h3>

        {/* Court Filter */}
        <div className="mb-5">
          <p className="text-xs font-medium text-slate-400 mb-2">Court</p>
          <div className="space-y-1.5">
            {COURTS.map((court) => (
              <label key={court} className="flex items-center gap-2 cursor-pointer group">
                <input
                  type="checkbox"
                  checked={(searchFilters.courts || []).includes(court)}
                  onChange={() => toggleArray('courts', court)}
                  className="rounded bg-slate-800 border-slate-600 text-indigo-500 focus:ring-indigo-500"
                />
                <span className="text-xs text-slate-400 group-hover:text-slate-200 transition-colors leading-tight">{court}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Year Range */}
        <div className="mb-5">
          <p className="text-xs font-medium text-slate-400 mb-2">
            Year Range: <span className="text-indigo-400">{searchFilters.yearFrom ?? 1990} – {searchFilters.yearTo ?? 2025}</span>
          </p>
          <div className="space-y-2">
            <div>
              <label className="text-xs text-slate-500">From</label>
              <input
                type="range" min={1950} max={2025}
                value={searchFilters.yearFrom ?? 1990}
                onChange={(e) => setYear('yearFrom', Number(e.target.value))}
                className="w-full accent-indigo-500"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">To</label>
              <input
                type="range" min={1950} max={2025}
                value={searchFilters.yearTo ?? 2025}
                onChange={(e) => setYear('yearTo', Number(e.target.value))}
                className="w-full accent-indigo-500"
              />
            </div>
          </div>
        </div>

        {/* Precedent Status */}
        <div className="mb-5">
          <p className="text-xs font-medium text-slate-400 mb-2">Precedent Status</p>
          <div className="space-y-1.5">
            {STATUSES.map((s) => {
              const color = s === 'overruled' ? 'text-red-400' : s === 'good_law' || s === 'followed' ? 'text-emerald-400' : 'text-amber-400';
              return (
                <label key={s} className="flex items-center gap-2 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={(searchFilters.precedentStatus || []).includes(s)}
                    onChange={() => toggleArray('precedentStatus', s)}
                    className="rounded bg-slate-800 border-slate-600 text-indigo-500 focus:ring-indigo-500"
                  />
                  <span className={`text-xs capitalize ${color} group-hover:brightness-125 transition-all`}>{s.replace('_', ' ')}</span>
                </label>
              );
            })}
          </div>
        </div>

        {/* Legal Topic */}
        <div className="mb-5">
          <p className="text-xs font-medium text-slate-400 mb-2">Legal Topic</p>
          <div className="flex flex-wrap gap-1.5">
            {TOPICS.map((topic) => {
              const active = (searchFilters.topics || []).includes(topic);
              return (
                <button
                  key={topic}
                  onClick={() => toggleArray('topics', topic)}
                  className={`text-xs px-2 py-1 rounded border transition-all ${
                    active
                      ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40'
                      : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-500'
                  }`}
                >
                  {topic}
                </button>
              );
            })}
          </div>
        </div>

        {/* Reset */}
        <button
          onClick={() => setSearchFilters({})}
          className="w-full text-xs text-slate-500 hover:text-red-400 transition-colors py-1.5 border border-slate-700 rounded hover:border-red-400/30"
        >
          ✕ Clear Filters
        </button>
      </div>
    </div>
  );
}
