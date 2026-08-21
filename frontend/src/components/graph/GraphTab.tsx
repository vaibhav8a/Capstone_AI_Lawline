import { useState, useEffect, useCallback } from 'react';
import ReactFlow, { 
  Background, 
  Controls, 
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { apiClient } from '../../api/client';
import { useAppStore } from '../../store/useAppStore';

export function GraphTab() {
  const { searchQuery, contextChunks } = useAppStore();
  const [loading, setLoading] = useState(false);
  const [clusters, setClusters] = useState<Record<string, string[]>>({});
  const [selectedCase, setSelectedCase] = useState('');
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const fetchGraphData = useCallback(async (query: string) => {
    setLoading(true);
    try {
      const res = await apiClient.get(`/graph/lineage/${encodeURIComponent(query)}`);
      const { nodes: rawNodes, edges: rawEdges } = res.data;

      // Simple circular layout
      const formattedNodes = rawNodes.map((node: any, i: number) => {
        const angle = (i / rawNodes.length) * 2 * Math.PI;
        const radius = 250;
        return {
          id: node.id,
          data: { label: node.data.label },
          position: { 
            x: Math.cos(angle) * radius + 400, 
            y: Math.sin(angle) * radius + 300 
          },
          style: {
            background: node.id.startsWith('Case') ? '#4f46e5' : '#1e293b',
            color: '#fff',
            borderRadius: '8px',
            border: '1px solid #6366f1',
            fontSize: '10px',
            width: 150,
          },
        };
      });

      const formattedEdges = rawEdges.map((edge: any) => ({
        ...edge,
        animated: true,
        style: { stroke: '#6366f1' },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: '#6366f1',
        },
      }));

      setNodes(formattedNodes);
      setEdges(formattedEdges);
      const cRes = await apiClient.get('/graph/clusters');
      setClusters(cRes.data || {});
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [setNodes, setEdges]);

  useEffect(() => {
    const canonicalCase = selectedCase || contextChunks?.[0]?.case_title || searchQuery;
    if (canonicalCase) {
      fetchGraphData(canonicalCase);
    }
  }, [searchQuery, contextChunks, selectedCase, fetchGraphData]);

  return (
    <div className="h-full flex flex-col gap-4">
      <div className="bg-slate-900 border border-slate-700 p-4 rounded-xl flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-100">Knowledge Graph View</h2>
          <p className="text-slate-400 text-sm">Visualizing citation networks.</p>
        </div>
        <div className="flex items-center gap-3">
          {contextChunks.length > 0 && (
            <select
              className="bg-slate-800 border border-slate-700 rounded p-2 text-xs"
              value={selectedCase}
              onChange={(e) => setSelectedCase(e.target.value)}
            >
              <option value="">Top retrieved case</option>
              {[...new Set(contextChunks.map((c: any) => c.case_title).filter(Boolean))].slice(0, 12).map((title: string) => (
                <option key={title} value={title}>{title}</option>
              ))}
            </select>
          )}
          <div className="text-xs text-slate-400">
            Topics: {Object.keys(clusters).slice(0, 3).join(' • ')}
          </div>
        </div>
      </div>
      
      <div className="flex-1 bg-slate-900 border border-slate-700 rounded-xl relative overflow-hidden">
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-900/50 z-10">
            <div className="text-indigo-400 animate-pulse font-bold">Building Citation Network...</div>
          </div>
        ) : nodes.length === 0 ? (
          <div className="h-full flex items-center justify-center text-slate-500">
            Search for a case to view its citation lineage.
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            fitView
          >
            <Background color="#334155" gap={20} />
            <Controls />
            <MiniMap 
              style={{ background: '#0f172a' }}
              nodeColor={(n) => (n.id.startsWith('Case') ? '#4f46e5' : '#1e293b')}
            />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
