import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface SearchFilters {
  courts?: string[];
  yearFrom?: number;
  yearTo?: number;
  precedentStatus?: string[];
  topics?: string[];
}

export interface SavedQuery {
  id: string;
  query: string;
  timestamp: string;
}

interface RAGState {
  searchQuery: string;
  setSearchQuery: (q: string) => void;

  isSettingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;

  // Toggles
  useHyDE: boolean;
  setUseHyDE: (v: boolean) => void;
  useSelfRAG: boolean;
  setUseSelfRAG: (v: boolean) => void;

  // Context chunks
  contextChunks: any[];
  setContextChunks: (chunks: any[]) => void;

  // Tab State (0-6)
  activeTab: number;
  setActiveTab: (t: number) => void;

  // Filters
  searchFilters: SearchFilters;
  setSearchFilters: (f: SearchFilters) => void;

  // Saved Queries & Bookmarks
  savedQueries: SavedQuery[];
  saveQuery: (q: string) => void;
  removeSavedQuery: (id: string) => void;

  bookmarkedChunks: any[];
  bookmarkChunk: (chunk: any) => void;
  removeBookmark: (chunkId: string) => void;

  // Auth
  authToken: string | null;
  authRole: string | null;
  setAuth: (token: string, role: string) => void;
  clearAuth: () => void;
}

export const useAppStore = create<RAGState>()(
  persist(
    (set, get) => ({
      searchQuery: '',
      setSearchQuery: (q) => set({ searchQuery: q }),

      isSettingsOpen: false,
      setSettingsOpen: (open) => set({ isSettingsOpen: open }),

      useHyDE: true,
      setUseHyDE: (v) => set({ useHyDE: v }),

      useSelfRAG: true,
      setUseSelfRAG: (v) => set({ useSelfRAG: v }),

      contextChunks: [],
      setContextChunks: (chunks) => set({ contextChunks: chunks }),

      activeTab: 0,
      setActiveTab: (t) => set({ activeTab: t }),

      searchFilters: {},
      setSearchFilters: (f) => set({ searchFilters: f }),

      savedQueries: [],
      saveQuery: (q) => {
        const existing = get().savedQueries;
        const newEntry: SavedQuery = {
          id: Date.now().toString(),
          query: q,
          timestamp: new Date().toISOString(),
        };
        set({ savedQueries: [newEntry, ...existing].slice(0, 50) });
      },
      removeSavedQuery: (id) =>
        set({ savedQueries: get().savedQueries.filter((q) => q.id !== id) }),

      bookmarkedChunks: [],
      bookmarkChunk: (chunk) => {
        const existing = get().bookmarkedChunks;
        if (!existing.find((c) => c.chunk_id === chunk.chunk_id)) {
          set({ bookmarkedChunks: [chunk, ...existing].slice(0, 100) });
        }
      },
      removeBookmark: (chunkId) =>
        set({ bookmarkedChunks: get().bookmarkedChunks.filter((c) => c.chunk_id !== chunkId) }),

      authToken: null,
      authRole: null,
      setAuth: (token, role) => set({ authToken: token, authRole: role }),
      clearAuth: () => set({ authToken: null, authRole: null }),
    }),
    {
      name: 'legal-rag-store',
      partialize: (state) => ({
        savedQueries: state.savedQueries,
        bookmarkedChunks: state.bookmarkedChunks,
        authToken: state.authToken,
        authRole: state.authRole,
        useHyDE: state.useHyDE,
        useSelfRAG: state.useSelfRAG,
      }),
    }
  )
);

