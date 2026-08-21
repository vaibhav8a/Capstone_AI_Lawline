import axios from 'axios';
import { useAppStore } from '../store/useAppStore';

export const apiClient = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add interceptor to inject auth token
apiClient.interceptors.request.use((config) => {
  const token = useAppStore.getState().authToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// For SSE Streams, we just use raw fetch or EventSource in hooks,
// but we provide wrappers here for standard async calls.

export const fetchSummary = async (documentText: string) => {
  const res = await apiClient.post('/summary/generate', { document_text: documentText });
  return res.data;
};

export const fetchConflicts = async (query: string) => {
  const res = await apiClient.post('/compare/detect', { query });
  return res.data;
};

export const fetchCritique = async (argument: string) => {
  const res = await apiClient.post('/critique/score', { argument });
  return res.data;
};

export const fetchLineage = async (caseTitle: string) => {
  const res = await apiClient.get(`/graph/lineage/${encodeURIComponent(caseTitle)}`);
  return res.data;
};
