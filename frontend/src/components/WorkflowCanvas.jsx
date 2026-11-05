import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background, Controls, Handle, MarkerType, Position, ReactFlow,
  ReactFlowProvider, useNodesInitialized, useReactFlow, useStore,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowLeftRight, LayoutGrid, Plus, Trash2, X } from 'lucide-react';
import { api, errorMessage } from '../utils.js';
import { useUnsavedForm } from './UnsavedChanges.jsx';
import { NodeConfig } from './WorkflowNodeConfig.jsx';
import {
  NODE_TYPES, TYPE_LABELS, defaultConfig, deriveEdges, hasEdge, newId,
  reaches, removeNodeAt, validateWorkflow,
} from './workflowModel.js';

/**
 * 工作流画布（React Flow）。
 *
 * 🎯 与表单视图共用同一份数据：
 *   节点仍是 `{id, type, name, config, next?, position?}` 数组，
 *   边不落库、每次从节点推导（deriveEdges）——避免「边和 next 各存一份、改了一边忘了另一边」。
 *   画布上连线 = 写回 next / config.branches；删边 = 清掉对应字段。
 *
 * 🧠 画布会自动额外启用「顺序视图」开关：
 *   执行器在节点没有显式 next 时会自动走数组下一个，所以默认工作流在画布上是连贯的一条链。
 *   这些兜底边画成虚线，不能删——删了也会按顺序跑，容易误导。
 */

const COLUMN_GAP = 260;
const ROW_GAP = 120;

const TYPE_COLORS = {
  Start: '#12b76a',
  Knowledge: '#2e90fa',
  Tool: '#7a5af8',
  LLM: '#f79009',
  Condition: '#ee46bc',
  HumanApproval: '#667085',
  Answer: '#0e9384',
  Agent: '#b54708',
};

function nodeStyle(type) {
  return { borderTop: `3px solid ${TYPE_COLORS[type] || '#98a2b3'}` };
}

/** 没有 position 的老数据按数组顺序自动排布 */
function layoutNodes(nodes) {
  return nodes.map((node, index) => ({
    ...node,
    position: node.position && typeof node.position.x === 'number'
      ? node.position
      : { x: (index % 4) * COLUMN_GAP, y: Math.floor(index / 4) * ROW_GAP },
  }));
}

function CanvasNode({ data, selected }) {
  return (
    <div className={`wf-node ${selected ? 'selected' : ''}`} style={nodeStyle(data.nodeType)}>
      <Handle type="target" position={Position.Top} className="wf-handle" />
      <div className="wf-node-title">{data.label}</div>
      <div className="wf-node-id">{data.id}</div>
      {data.detail && <div className="wf-node-detail">{data.detail}</div>}
      <Handle type="source" position={Position.Bottom} className="wf-handle" />
    </div>
  );
}

const nodeTypes = { workflowNode: CanvasNode };

function detailOf(node, agents = []) {
  if (node.type === 'Knowledge') {
    const filters = (node.config?.metadata_filter?.conditions || []).length;
    return `召回 ${node.config?.top_k ?? 4} 条${filters ? ` · ${filters} 个过滤条件` : ''}`;
  }
  if (node.type === 'VariableAggregate') return `→ ${node.config?.output_var || '未命名'}`;
  if (node.type === 'Loop' && Number(node.config?.parallel) > 1) return `并行 ${node.config.parallel}`;
  if (node.type === 'Condition') {
    const count = (node.config?.branches || []).length;
    return count ? `${count} 个条件分支` : '未设置条件';
  }
  if (node.type === 'Tool') {
    const count = (node.config?.tools || []).length;
    return count ? `限定 ${count} 个工具` : '全部已绑定工具';
  }
  if (node.type === 'HumanApproval') return node.config?.prompt || '';
  if (node.type === 'Agent') {
    const target = agents.find((agent) => agent.id === node.config?.agent_id);
    if (!node.config?.agent_id) return '未选择智能体';
    return target ? target.name : `智能体 #${node.config.agent_id}（已不可用）`;
  }
  return '';
}

function CanvasInner({
  agentId, token, canEdit, tools = [], agents = [], onCountChange, loadWorkflow, saveWorkflow, fullscreen = false,
}) {
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [connectError, setConnectError] = useState('');
  const generation = useRef(0);
  const savedNodes = useRef([]);
  const { fitView, screenToFlowPosition } = useReactFlow();
  const stageRef = useRef(null);

  useEffect(() => {
    const current = ++generation.current;
    setNodes([]); setError(''); setMessage(''); setLoading(true); setSelectedId(null);
    if (!agentId || !token) { setLoading(false); return undefined; }
    let live = true;
    const load = loadWorkflow || ((id) => api(`/api/agents/${id}/workflow`, { token }));
    load(agentId)
      .then((data) => {
        if (!live) return;
        const loaded = layoutNodes(Array.isArray(data.nodes) ? data.nodes : []);
        setNodes(loaded);
        savedNodes.current = loaded;
      })
      .catch((err) => live && setError(errorMessage(err)))
      .finally(() => live && setLoading(false));
    return () => { live = false; if (generation.current === current) generation.current++; };
  }, [agentId, token, loadWorkflow]);

  useEffect(() => {
    onCountChange?.(nodes.length);
  }, [nodes.length, onCountChange]);

  const problems = useMemo(() => validateWorkflow(nodes), [nodes]);

  // 画布常在不可见时挂载（折叠区 display:none、全屏切换），此时尺寸为 0，取景会落空。
  // 订阅 React Flow 自己量到的尺寸：它更新之后再取景，fitView 才读得到新尺寸（自建 ResizeObserver 会抢跑）。
  const nodesInitialized = useNodesInitialized();
  const canvasSize = useStore((state) => `${Math.round(state.width)}x${Math.round(state.height)}`);
  useEffect(() => {
    if (loading || !nodesInitialized || canvasSize.startsWith('0x') || canvasSize.endsWith('x0')) return undefined;
    const frame = requestAnimationFrame(() => fitView({ padding: 0.2, duration: 200 }));
    return () => cancelAnimationFrame(frame);
  }, [canvasSize, nodesInitialized, loading, fitView]);

  // React Flow 量出的节点尺寸单独存放：不进工作流数据（不触发未保存、不落库），
  // 但必须回传给画布——缺了它节点会一直是「未初始化」：被隐藏，fitView 也会忽略它们。
  const [measured, setMeasured] = useState({});
  const flowNodes = useMemo(() => nodes.map((node) => ({
    id: node.id,
    type: 'workflowNode',
    position: node.position,
    measured: measured[node.id],
    data: { label: TYPE_LABELS[node.type] || node.type, nodeType: node.type, id: node.id, detail: detailOf(node, agents) },
    selected: node.id === selectedId,
  })), [nodes, selectedId, measured, agents]);

  const flowEdges = useMemo(() => deriveEdges(nodes).map((edge) => ({
    id: edge.id,
    source: edge.from,
    target: edge.to,
    label: edge.label,
    animated: !edge.implicit,
    deletable: !edge.implicit,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: edge.implicit
      ? { stroke: '#d0d5dd', strokeDasharray: '4 4' }
      : { stroke: '#7a5af8', strokeWidth: 1.6 },
    labelStyle: { fontSize: 10, fill: edge.implicit ? '#98a2b3' : '#475467' },
  })), [nodes]);

  const selectedIndex = nodes.findIndex((node) => node.id === selectedId);
  const selectedNode = selectedIndex >= 0 ? nodes[selectedIndex] : null;

  function markDirty() {
    setMessage('');
  }

  function updateNode(index, patch) {
    setNodes((current) => current.map((node, position) => (
      position === index ? { ...node, ...patch } : node
    )));
    markDirty();
  }

  function updateConfig(index, patch) {
    setNodes((current) => current.map((node, position) => (
      position === index ? { ...node, config: { ...(node.config || {}), ...patch } } : node
    )));
    markDirty();
  }

  // 新节点放在用户此刻看到的画布中央；被占了就往下找空位，连续添加也不会叠在一起
  function freeSpotNearCenter(existing) {
    const box = stageRef.current?.getBoundingClientRect();
    const center = box?.width
      ? screenToFlowPosition({ x: box.left + box.width / 2, y: box.top + box.height / 2 })
      : { x: 130, y: 60 };
    const base = { x: center.x - 90, y: center.y - 30 };
    const taken = (spot) => existing.some((node) => (
      Math.abs((node.position?.x ?? 0) - spot.x) < 200 && Math.abs((node.position?.y ?? 0) - spot.y) < 90
    ));
    for (let step = 0; step < 40; step += 1) {
      const spot = { x: base.x + Math.floor(step / 8) * 220, y: base.y + (step % 8) * 100 };
      if (!taken(spot)) return spot;
    }
    return base;
  }

  function addNode(type) {
    const id = newId(type, nodes);
    setNodes((current) => {
      const node = {
        id,
        type,
        name: TYPE_LABELS[type],
        config: defaultConfig(type),
        position: freeSpotNearCenter(current),
      };
      // 与执行器保持一致：开始永远在最前、输出回答永远在最后
      if (type === 'Start') return [node, ...current];
      if (type === 'Answer') {
        const withoutAnswer = current.filter((item) => item.type !== 'Answer');
        return [...withoutAnswer, node];
      }
      const answerIndex = current.findIndex((item) => item.type === 'Answer');
      if (answerIndex === -1) return [...current, node];
      return [...current.slice(0, answerIndex), node, ...current.slice(answerIndex)];
    });
    setAddMenuOpen(false);
    setSelectedId(id);
    markDirty();
  }

  function removeNode(index) {
    setNodes((current) => removeNodeAt(current, index));
    setSelectedId(null);
    markDirty();
  }

  const onNodesChange = useCallback((changes) => {
    const sized = changes.filter((change) => change.type === 'dimensions' && change.dimensions);
    if (sized.length) {
      setMeasured((current) => {
        const next = { ...current };
        for (const change of sized) next[change.id] = change.dimensions;
        return next;
      });
    }
    setNodes((current) => {
      let next = current;
      let changed = false;
      for (const change of changes) {
        if (change.type === 'position' && change.position) {
          next = next.map((node) => (node.id === change.id ? { ...node, position: change.position } : node));
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, []);

  const onEdgesChange = useCallback((changes) => {
    const removed = changes.filter((change) => change.type === 'remove');
    if (!removed.length) return;
    setNodes((current) => {
      let next = current;
      for (const change of removed) {
        const [from, rest] = change.id.split('->');
        const target = rest.split('#')[0];
        const suffix = rest.split('#')[1] || '';
        next = next.map((node) => {
          if (node.id !== from) return node;
          if (suffix === 'default') {
            return { ...node, config: { ...(node.config || {}), default: '' } };
          }
          if (suffix.startsWith('b')) {
            const branchIndex = Number(suffix.slice(1));
            const config = { ...(node.config || {}) };
            config.branches = (config.branches || []).filter((_, position) => position !== branchIndex);
            return { ...node, config };
          }
          if (suffix === 'implicit') return node; // 顺序兜底边不允许删
          if (node.next === target) return { ...node, next: undefined };
          return node;
        });
      }
      return next;
    });
    markDirty();
  }, []);

  const onConnect = useCallback((connection) => {
    const fromId = connection.source;
    const toId = connection.target;
    setConnectError('');
    if (!fromId || !toId) return;
    if (fromId === toId) { setConnectError('不能连接到自身'); return; }

    const fromIndex = nodes.findIndex((node) => node.id === fromId);
    const from = nodes[fromIndex];
    if (!from) return;
    if (from.type === 'Answer') { setConnectError('「输出回答」是终点，不能再连出去'); return; }
    if (nodes.find((node) => node.id === toId)?.type === 'Start') {
      setConnectError('「开始」是起点，不能作为目标'); return;
    }
    // 执行器每个节点只跑一次，成环会让后半段永远执行不到
    if (reaches(nodes, toId, fromId)) { setConnectError('不能形成回路：执行器每个节点只运行一次'); return; }

    if (from.type === 'Condition') {
      // 条件节点的出边必须带条件：新增一条待配置的分支，目标先写进去
      setNodes((current) => current.map((node, position) => {
        if (position !== fromIndex) return node;
        const config = { ...(node.config || {}) };
        config.branches = [...(config.branches || []), { when: { field: '', gte: 1 }, next: toId }];
        config.default = config.default === toId ? '' : config.default;
        return { ...node, config };
      }));
      setSelectedId(fromId);
      markDirty();
      return;
    }

    if (hasEdge(nodes, fromId, toId)) {
      setConnectError('这两个节点之间已经有连线'); return;
    }
    setNodes((current) => current.map((node, position) => (
      position === fromIndex ? { ...node, next: toId } : node
    )));
    markDirty();
  }, [nodes]);

  async function save() {
    if (problems.length) { setError(problems[0]); return false; }
    const current = generation.current;
    setSaving(true); setError(''); setMessage('');
    try {
      const payload = nodes.map((node) => {
        const { id, type, name, config, next, position } = node;
        return {
          id, type, name: name || TYPE_LABELS[type] || type, config: config || {},
          ...(next ? { next } : {}),
          ...(position ? { position } : {}),
        };
      });
      const save = saveWorkflow || ((id, nodeList) => api(`/api/agents/${id}/workflow`, { token, method: 'PATCH', body: { nodes: nodeList } }));
      const response = await save(agentId, payload);
      if (generation.current !== current) return false;
      const saved = layoutNodes(response?.nodes || payload);
      setNodes(saved);
      savedNodes.current = saved;
      guard.markSaved(JSON.stringify(saved)); // 与 value 同格式；服务端（MySQL JSON）返回的键序会变，必须以返回值为基线
      setMessage('工作流已保存。');
      return true;
    } catch (err) {
      if (generation.current === current) setError(errorMessage(err));
      return false;
    } finally {
      if (generation.current === current) setSaving(false);
    }
  }

  const guard = useUnsavedForm({
    label: '工作流画布',
    enabled: canEdit && !loading,
    busy: saving,
    value: JSON.stringify(nodes),
    onSave: save,
    onDiscard: () => { setNodes(savedNodes.current); setMessage(''); },
  });

  if (loading) return <p className="workflow-editor-empty">正在读取工作流…</p>;

  return (
    <div className={`wf-canvas-wrap ${fullscreen ? 'fullscreen' : ''}`}>
      <div className="wf-canvas-toolbar">
        <div className="workflow-add">
          <button type="button" onClick={() => setAddMenuOpen((open) => !open)} disabled={!canEdit || saving}>
            <Plus size={14} />添加节点
          </button>
          {addMenuOpen && (
            <div className="workflow-add-menu">
              {NODE_TYPES.map((item) => (
                <button key={item.type} type="button" onClick={() => addNode(item.type)}>
                  <strong>{item.label}</strong>
                  <small>{item.hint}</small>
                </button>
              ))}
            </div>
          )}
        </div>
        <button type="button" title="缩放并居中，让所有节点都出现在画布里" onClick={() => fitView({ padding: 0.2, duration: 200 })}>
          <LayoutGrid size={14} />显示全部
        </button>
        <span className="wf-canvas-hint">
          <ArrowLeftRight size={13} />拖节点调整位置，拖圆点连线
        </span>
        {canEdit && (
          <button
            type="button" className="primary" disabled={saving || problems.length > 0} onClick={save}
          >
            {saving ? '保存中…' : '保存工作流'}
          </button>
        )}
      </div>

      <div className="wf-canvas-stage">
      <div className="wf-canvas" ref={stageRef}>
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={(_, node) => setSelectedId(node.id)}
          onPaneClick={() => setSelectedId(null)}
          nodesDraggable={canEdit}
          nodesConnectable={canEdit}
          edgesFocusable={canEdit}
          elementsSelectable
          fitView
          minZoom={0.3}
          maxZoom={1.6}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} size={1} color="#eaecf0" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {selectedNode && (
        <div className="wf-inspector">
          <header className="wf-inspector-head">
            <div>
              <strong>{TYPE_LABELS[selectedNode.type] || selectedNode.type}</strong>
              <span className="wf-node-id">{selectedNode.id}</span>
            </div>
            <div className="wf-inspector-actions">
              {canEdit && selectedNode.type !== 'Start' && selectedNode.type !== 'Answer' && (
                <button type="button" aria-label="删除节点" disabled={saving} onClick={() => removeNode(selectedIndex)}>
                  <Trash2 size={14} />
                </button>
              )}
              <button type="button" aria-label="关闭配置" onClick={() => setSelectedId(null)}><X size={14} /></button>
            </div>
          </header>
          <NodeConfig
            node={selectedNode}
            index={selectedIndex}
            nodes={nodes}
            nodeIds={nodes.map((node) => node.id)}
            tools={tools}
            agents={agents}
            canEdit={canEdit}
            saving={saving}
            onUpdate={updateNode}
            onUpdateConfig={updateConfig}
            onAddBranch={(index) => updateConfig(index, {
              branches: [...((nodes[index].config || {}).branches || []), { when: { field: '', gte: 1 }, next: '' }],
            })}
            onUpdateBranch={(index, branchIndex, patch) => {
              const branches = ((nodes[index].config || {}).branches || []).map((branch, position) => (
                position === branchIndex ? { ...branch, ...patch } : branch
              ));
              updateConfig(index, { branches });
            }}
            onRemoveBranch={(index, branchIndex) => updateConfig(index, {
              branches: ((nodes[index].config || {}).branches || []).filter((_, position) => position !== branchIndex),
            })}
          />
        </div>
      )}
      </div>

      {connectError && <p className="workflow-editor-error workflow-canvas-inline">{connectError}</p>}
      {error && <p className="workflow-editor-error">{error}</p>}
      {!error && problems.length > 0 && <p className="workflow-editor-warn">{problems[0]}</p>}
      {message && <p className="workflow-editor-success">{message}</p>}
    </div>
  );
}

export function WorkflowCanvas(props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
