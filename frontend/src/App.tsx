import { useState } from 'react';
import { 
  Search, 
  Book, 
  FileText, 
  Calendar, 
  Scale, 
  Sword, 
  Share2, 
  Filter, 
  Download, 
  RefreshCcw,
  Plus,
  Loader2
} from 'lucide-react';
import { StatuteTab }   from './components/statute/StatuteTab';
import { QueryTab }     from './components/query/QueryTab';
import { SourcesTab }   from './components/sources/SourcesTab';
import { SummaryTab }   from './components/summary/SummaryTab';
import { CompareTab }   from './components/compare/CompareTab';
import { ConflictsTab } from './components/conflicts/ConflictsTab';
import { CritiqueTab }  from './components/critique/CritiqueTab';
import { GraphTab }     from './components/graph/GraphTab';
import { FiltersPanel } from './components/FiltersPanel';
import { useAppStore }  from './store/useAppStore';

/* ── Tab definitions ─────────────────────────────────────────────────────────── */
const TABS = [
  { name: 'Statute QA',        icon: <Scale className="w-4 h-4" />,    component: <StatuteTab />,   color: 'indigo' },
  { name: 'Case Law Chat',     icon: <Search className="w-4 h-4" />,   component: <QueryTab />,     color: 'indigo' },
  { name: 'Sources',           icon: <Book className="w-4 h-4" />,     component: <SourcesTab />,   color: 'blue' },
  { name: 'Case Summary',      icon: <FileText className="w-4 h-4" />, component: <SummaryTab />,   color: 'violet' },
  { name: 'Compare Timeline',  icon: <Calendar className="w-4 h-4" />, component: <CompareTab />,   color: 'cyan' },
  { name: 'Conflicts',         icon: <Scale className="w-4 h-4" />,    component: <ConflictsTab />, color: 'rose' },
  { name: 'Critique',          icon: <Sword className="w-4 h-4" />,    component: <CritiqueTab />,  color: 'amber' },
  { name: 'Knowledge Graph',   icon: <Share2 className="w-4 h-4" />,  component: <GraphTab />,     color: 'emerald' },
];

/* ── App ────────────────────────────────────────────────────────────────────── */
export default function App() {
  const {
    activeTab, setActiveTab,
    savedQueries,
    bookmarkedChunks,
    contextChunks,
    searchQuery,
    authToken,
    clearAuth,
  } = useAppStore();

  const [showFilters, setShowFilters] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<{ type: 'success' | 'error' | null, message: string }>({ type: null, message: '' });

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setUploadStatus({ type: 'error', message: 'Only PDF files are allowed' });
      return;
    }

    setUploading(true);
    setUploadStatus({ type: null, message: '' });

    try {
      const formData = new FormData();
      formData.append('file', file);

      const { apiClient } = await import('./api/client');
      await apiClient.post('/index/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      setUploadStatus({ type: 'success', message: `${file.name} uploaded! Indexing started.` });
      // Reset after 3 seconds
      setTimeout(() => setUploadStatus({ type: null, message: '' }), 3000);
    } catch (error: any) {
      console.error('Upload failed:', error);
      setUploadStatus({ type: 'error', message: error.response?.data?.detail || 'Upload failed' });
    } finally {
      setUploading(false);
      // Clear input
      event.target.value = '';
    }
  };

  const handleExport = async (format: 'pdf' | 'docx') => {
    const { apiClient } = await import('./api/client');
    const res = await apiClient.post('/export/download', {
      format, query: searchQuery,
      chunks: contextChunks.slice(0, 5),
      title: 'Legal Research Export',
    }, { responseType: 'blob' });
    const url = URL.createObjectURL(res.data);
    const a = document.createElement('a');
    a.href = url; a.download = `legal_research.${format}`; a.click();
  };

  // const activeColor = TABS[activeTab]?.color ?? 'indigo';

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground font-sans h-screen overflow-hidden">

      {/* ── Top Bar ──────────────────────────────────────────────────────────── */}
      <header
        className="h-16 shrink-0 z-50 flex items-center px-6 justify-between border-b"
        style={{
          background: 'linear-gradient(180deg, hsl(222,47%,9%) 0%, hsl(222,47%,7%) 100%)',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.6)',
        }}
      >
        {/* Logo + Brand */}
        <div className="flex items-center gap-4 min-w-0">
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="shrink-0 w-9 h-9 rounded-lg flex items-center justify-center text-slate-400 hover:text-slate-100 hover:bg-white/10 active:bg-white/15 transition-all duration-150"
            title="Toggle sidebar"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
              <path fillRule="evenodd" d="M2 4.75A.75.75 0 012.75 4h14.5a.75.75 0 010 1.5H2.75A.75.75 0 012 4.75zM2 10a.75.75 0 01.75-.75h14.5a.75.75 0 010 1.5H2.75A.75.75 0 012 10zm0 5.25a.75.75 0 01.75-.75h14.5a.75.75 0 010 1.5H2.75a.75.75 0 01-.75-.75z" clipRule="evenodd"/>
            </svg>
          </button>

          <div className="flex items-center justify-between gap-3 min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <div
                className="w-9 h-9 shrink-0 rounded-lg flex items-center justify-center font-bold text-white text-sm select-none"
                style={{ background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)', boxShadow: '0 0 20px rgba(99,102,241,0.4)' }}
              >
                LA
              </div>
              <div className="min-w-0">
                <div className="text-base font-bold text-slate-50 leading-none">
                  LawLine <span className="bg-gradient-to-r from-indigo-300 to-purple-300 bg-clip-text text-transparent">AI</span>
                </div>
                <div className="text-xs text-slate-500 leading-none mt-1">Conversational Assistant for Legal Support</div>
              </div>
            </div>
            <button className="p-2 hover:bg-white/5 rounded-lg text-slate-500 hover:text-slate-300 transition-colors mr-2" title="Refresh">
              <RefreshCcw className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Centre breadcrumb */}
        <div className="hidden md:flex items-center gap-2 text-xs text-slate-500 flex-1 px-6 justify-center">
          <span>Workspace</span>
          <svg className="w-3 h-3 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
          <span className="text-slate-300 font-medium max-w-xs truncate">{TABS[activeTab].name}</span>
        </div>

        {/* Right controls */}
        <div className="flex items-center gap-2.5 shrink-0">
          {contextChunks.length > 0 && (
            <div className="flex gap-2 border-r border-white/10 pr-2.5">
              <button
                onClick={() => handleExport('pdf')}
                className="flex items-center gap-2 px-3 py-2 text-xs font-medium text-slate-300 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg transition-all duration-150 active:bg-white/15"
                title="Export as PDF"
              >
                <Download className="w-4 h-4" /> PDF
              </button>
              <button
                onClick={() => handleExport('docx')}
                className="flex items-center gap-2 px-3 py-2 text-xs font-medium text-slate-300 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg transition-all duration-150 active:bg-white/15"
                title="Export as Word"
              >
                <Download className="w-4 h-4" /> Word
              </button>
            </div>
          )}

          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`flex items-center gap-2 px-3.5 py-2 text-xs font-medium rounded-lg border transition-all duration-200 ${
              showFilters
                ? 'bg-indigo-500/20 text-indigo-200 border-indigo-500/40 shadow-lg'
                : 'bg-white/5 text-slate-300 border-white/15 hover:bg-white/10 hover:border-white/25'
            }`}
            title="Toggle filters panel"
          >
            <Filter className="w-4 h-4" />
            Filters
          </button>

          {authToken && (
            <button onClick={clearAuth} className="px-3 py-2 text-xs font-medium text-rose-300 hover:text-rose-200 hover:bg-rose-500/10 rounded-lg transition-all duration-150" title="Sign out">
              Sign out
            </button>
          )}

          {/* Status indicator */}
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-emerald-500/15 border border-emerald-500/30 ml-1">
            <span className="status-dot" />
            <span className="text-xs font-semibold text-emerald-300">Live</span>
          </div>
        </div>
      </header>

      {/* ── Body ─────────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Sidebar ──────────────────────────────────────────────────────── */}
        <aside
          className={`flex flex-col border-r border-white/8 shrink-0 overflow-hidden transition-all duration-300 ${
            sidebarCollapsed ? 'w-0 opacity-0 pointer-events-none' : 'w-64 opacity-100'
          }`}
          style={{ background: 'linear-gradient(180deg, hsl(222,47%,8%) 0%, hsl(222,47%,6%) 100%)' }}
        >
          {/* Navigation Section */}
          <div className="flex-1 overflow-y-auto custom-scrollbar py-5 px-3 flex flex-col gap-2">
            
            {/* Upload Case Button */}
            <div className="px-3 mb-6">
              <label className={`
                w-full flex items-center justify-center gap-3 px-4 py-3 rounded-xl 
                text-sm font-bold transition-all duration-300 cursor-pointer
                ${uploading 
                  ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 cursor-not-allowed' 
                  : 'bg-gradient-to-r from-indigo-600 to-violet-600 text-white hover:from-indigo-500 hover:to-violet-500 shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:shadow-[0_0_25px_rgba(99,102,241,0.5)] active:scale-[0.98]'
                }
              `}>
                {uploading ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Plus className="w-5 h-5" />
                )}
                <span>{uploading ? 'Processing...' : 'Add New Case'}</span>
                <input 
                  type="file" 
                  className="hidden" 
                  accept=".pdf" 
                  onChange={handleFileUpload} 
                  disabled={uploading}
                />
              </label>
              
              {uploadStatus.type && (
                <div className={`mt-3 px-3 py-2 rounded-lg text-[10px] font-medium animate-fade-in border ${
                  uploadStatus.type === 'success' 
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
                    : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                }`}>
                  <div className="flex items-center gap-2">
                    {uploadStatus.type === 'success' ? <div className="w-1 h-1 rounded-full bg-emerald-400" /> : <div className="w-1 h-1 rounded-full bg-rose-400" />}
                    {uploadStatus.message}
                  </div>
                </div>
              )}
            </div>

            <div className="px-3 mb-4">
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-3">Navigation</div>
              <div className="space-y-1">
                {TABS.map((tab, idx) => (
                  <button
                    key={idx}
                    id={`tab-${idx}`}
                    onClick={() => setActiveTab(idx)}
                    className={`w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
                      activeTab === idx
                        ? 'bg-indigo-500/20 text-indigo-200 border border-indigo-500/40 shadow-md'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-white/5 border border-transparent'
                    }`}
                  >
                    <span className="text-lg shrink-0">{tab.icon}</span>
                    <span className="truncate text-left flex-1">{tab.name}</span>
                    {activeTab === idx && (
                      <span className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-300 shrink-0 animate-pulse" />
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* Saved Queries */}
            {savedQueries.length > 0 && (
              <div className="mt-6 pt-6 border-t border-white/10">
                <div className="px-3 mb-3">
                  <div className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Recent Queries</div>
                </div>
                <div className="space-y-1 px-2">
                  {savedQueries.slice(0, 6).map((sq) => (
                    <button
                      key={sq.id}
                      onClick={() => setActiveTab(0)}
                      className="w-full text-left flex items-start gap-2.5 px-3 py-2 rounded-lg hover:bg-white/5 transition-colors group text-xs"
                    >
                      <span className="text-indigo-400 mt-0.5 shrink-0 text-sm">
                        <Search className="w-4 h-4" />
                      </span>
                      <span className="text-slate-400 group-hover:text-slate-300 truncate transition-colors leading-relaxed flex-1">
                        {sq.query.slice(0, 40)}{sq.query.length > 40 ? '…' : ''}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Bookmarks */}
            {bookmarkedChunks.length > 0 && (
              <div className="mt-4 pt-6 border-t border-white/10">
                <div className="px-3 mb-3">
                  <div className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Bookmarks</div>
                </div>
                <div className="space-y-1.5 px-2">
                  {bookmarkedChunks.slice(0, 4).map((c) => (
                    <div key={c.chunk_id} className="flex items-start gap-2.5 px-3 py-2 rounded-lg hover:bg-white/5 transition-colors text-xs group">
                      <span className="text-amber-400 mt-0.5 shrink-0 text-sm">★</span>
                      <span className="text-amber-300/80 truncate flex-1 group-hover:text-amber-200">
                        {c.case_title?.slice(0, 28)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Sidebar Footer */}
          <div className="px-3 py-4 border-t border-white/10 bg-slate-950/40">
            <div className="flex items-center gap-3 px-3 py-3 rounded-lg bg-indigo-500/15 border border-indigo-500/25 hover:bg-indigo-500/20 transition-colors">
              <div className="w-6 h-6 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center shrink-0">
                <span className="text-[10px] text-white font-bold">AI</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs text-slate-200 font-semibold truncate">Legal RAG Engine</div>
                <div className="text-[10px] text-slate-500">HyDE + Self-RAG</div>
              </div>
            </div>
          </div>
        </aside>

        {/* ── Filters Panel ─────────────────────────────────────────────────── */}
        {showFilters && (
          <div className="w-64 border-r border-white/5 shrink-0 animate-slide-in overflow-y-auto custom-scrollbar"
            style={{ background: 'hsl(222,47%,7%)' }}>
            <FiltersPanel />
          </div>
        )}

        {/* ── Main Content ──────────────────────────────────────────────────── */}
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* Professional Tab Navigation Bar */}
          <div className="shrink-0 border-b border-white/8 bg-gradient-to-b from-slate-900/80 to-slate-900/40">
            {/* Tabs */}
            <div className="flex items-center overflow-x-auto custom-scrollbar px-4">
              {TABS.map((tab, idx) => (
                <button
                  key={idx}
                  onClick={() => setActiveTab(idx)}
                  className={`px-4 py-3.5 text-sm font-medium whitespace-nowrap border-b-2 transition-all duration-200 flex items-center gap-2 ${
                    activeTab === idx
                      ? 'border-indigo-500 text-indigo-300'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span className="text-base">{tab.icon}</span>
                  <span>{tab.name}</span>
                </button>
              ))}
            </div>

            {/* Bottom info bar */}
            <div className="flex items-center justify-between px-4 py-2.5 bg-slate-950/30 border-t border-white/5">
              <div className="flex items-center gap-3">
                <span className={`text-indigo-400`}>{TABS[activeTab].icon}</span>
                <span className="text-xs text-slate-400">
                  <span className="font-semibold text-slate-300">{TABS[activeTab].name}</span>
                  {contextChunks.length > 0 && (
                    <span className="ml-3">• {contextChunks.length} sources retrieved</span>
                  )}
                </span>
              </div>
              <div className="flex items-center gap-2">
                {contextChunks.length > 0 && (
                  <span className="badge badge-indigo text-xs">
                    {contextChunks.length} chunks loaded
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Content area */}
          <div className="flex-1 overflow-y-auto custom-scrollbar p-6 animate-fade-in">
            {TABS[activeTab].component}
          </div>
        </main>
      </div>
    </div>
  );
}
