import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { api, errorMessage } from '../utils.js';
import { useUnsavedForm } from './UnsavedChanges.jsx';
import { NODE_TYPES, TYPE_LABELS, defaultConfig, newId, validateWorkflow } from './workflowModel.js';
import { NodeConfig } from './WorkflowNodeConfig.jsx';

/**
 * 工作流编排（表单式）。
 *
 * 🎯 为什么先做表单而不是拖拽画布：
 *   编辑的对象是「节点列表 + 跳转关系」这份 JSON，画布只是它的另一种视图。
 *   先把数据契约和编辑能力做扎实，后续换成画布时只替换视图层，不动后端与存储。
 *
 * 🧠 执行语义（与 core/runtime/graph.py 一致）：
 *   - 节点按数组顺序执行，未显式指定 next 时走下一个节点；
 *   - Condition 节点按 branches 顺序求值，首个命中的 next 胜出，否则走 default；
 *   - 节点只能经过一次，所以这里不允许设计回边（循环需要执行器改造，尚未支持）。
 */

export function WorkflowEditor({
  agentId, token, canEdit, tools = [], onCountChange,
  // 可注入的读写实现：默认直接走接口；测试与后续画布视图可替换
  loadWorkflow, saveWorkflow,
}) {
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [openId, setOpenId] = useState(null);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const generation = useRef(0);
  const savedNodes = useRef([]);

  useEffect(() => {
    const current = ++generation.current;
    setNodes([]); setError(''); setMessage(''); setLoading(true); setOpenId(null);
    if (!agentId || !token) { setLoading(false); return undefined; }
    let live = true;
    const load = loadWorkflow || ((id) => api(`/api/agents/${id}/workflow`, { token }));
    load(agentId)
      .then((data) => {
        if (!live) return;
        const loaded = Array.isArray(data.nodes) ? data.nodes : [];
        setNodes(loaded);
        savedNodes.current = loaded;
      })
      .catch((err) => live && setError(errorMessage(err)))
      .finally(() => live && setLoading(false));
    return () => { live = false; if (generation.current === current) generation.current++; };
  }, [agentId, token, loadWorkflow]);

  const nodeIds = useMemo(() => nodes.map((node) => node.id), [nodes]);

  useEffect(() => {
    onCountChange?.(nodes.length);
  }, [nodes.length, onCountChange]);

  function update(index, patch) {
    setNodes((current) => current.map((node, position) => (position === index ? { ...node, ...patch } : node)));
    setMessage('');
  }

  function updateConfig(index, patch) {
    setNodes((current) => current.map((node, position) => (
      position === index ? { ...node, config: { ...(node.config || {}), ...patch } } : node
    )));
    setMessage('');
  }

  function addNode(type) {
    setNodes((current) => {
      const node = { id: newId(type, current), type, name: TYPE_LABELS[type], config: defaultConfig(type) };
      // Start 永远在最前、Answer 永远在最后，避免用户排出一个跑不通的顺序
      if (type === 'Start') return [node, ...current];
      if (type === 'Answer') return [...current, node];
      return [...current, node];
    });
    setAddMenuOpen(false);
    setMessage('');
  }

  function removeNode(index) {
    setNodes((current) => {
      const removed = current[index];
      const next = current.filter((_, position) => position !== index);
      // 清理指向被删节点的跳转，避免留下悬空边
      return next.map((node) => {
        const cleaned = { ...node };
        if (cleaned.next === removed.id) delete cleaned.next;
        if (cleaned.type === 'Condition') {
          const config = { ...(cleaned.config || {}) };
          config.branches = (config.branches || []).filter((branch) => branch.next !== removed.id);
          if (config.default === removed.id) config.default = '';
          cleaned.config = config;
        }
        return cleaned;
      });
    });
    setOpenId(null);
    setMessage('');
  }

  function move(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= nodes.length) return;
    // 开始必须在首位、输出回答必须在末位：执行器从数组第 0 个节点开始跑，
    // 允许把它们拖到中间会产生「看起来能存、实际不按预期执行」的图。
    const moving = nodes[index];
    const swapped = nodes[target];
    if (moving.type === 'Start' || moving.type === 'Answer') return;
    if (swapped.type === 'Start' || swapped.type === 'Answer') return;
    setNodes((current) => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setMessage('');
  }

  function addBranch(index) {
    const config = nodes[index].config || {};
    updateConfig(index, {
      branches: [...(config.branches || []), { when: { field: '', gte: 1 }, next: '' }],
    });
  }

  function updateBranch(index, branchIndex, patch) {
    const config = nodes[index].config || {};
    const branches = (config.branches || []).map((branch, position) => (
      position === branchIndex ? { ...branch, ...patch } : branch
    ));
    updateConfig(index, { branches });
  }

  function removeBranch(index, branchIndex) {
    const config = nodes[index].config || {};
    updateConfig(index, { branches: (config.branches || []).filter((_, position) => position !== branchIndex) });
  }

  const problems = useMemo(() => validateWorkflow(nodes), [nodes]);

  async function save() {
    if (problems.length) { setError(problems[0]); return false; }
    const current = generation.current;
    setSaving(true); setError(''); setMessage('');
    try {
      // position 只是画布阶段的布局元数据，这里保留原值、不新增
      const payload = nodes.map((node) => {
        const { id, type, name, config, next, position } = node;
        return {
          id, type, name: name || TYPE_LABELS[type] || type, config: config || {},
          ...(next ? { next } : {}),
          ...(position ? { position } : {}),
        };
      });
      const save = saveWorkflow || ((id, nodesToSave) => api(`/api/agents/${id}/workflow`, { token, method: 'PATCH', body: { nodes: nodesToSave } }));
      const response = await save(agentId, payload);
      if (generation.current !== current) return false;
      const saved = response.nodes || payload;
      setNodes(saved);
      savedNodes.current = saved;
      guard.markSaved();
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
    label: '工作流编排',
    enabled: canEdit && !loading,
    busy: saving,
    value: JSON.stringify(nodes),
    onSave: save,
    onDiscard: () => { setNodes(savedNodes.current); setMessage(''); },
  });

  if (loading) return <p className="workflow-editor-empty">正在读取工作流…</p>;

  return (
    <div className="workflow-editor">
      <p className="workflow-editor-intro">
        节点按顺序执行。未设置跳转时走向下一个节点；条件分支按顺序判断，命中即跳转。
      </p>

      <ol className="workflow-node-list">
        {nodes.map((node, index) => (
          <li key={node.id} className={`workflow-node ${openId === node.id ? 'open' : ''}`}>
            <div className="workflow-node-head" onClick={() => setOpenId(openId === node.id ? null : node.id)}>
              <ChevronRight size={14} className={`workflow-caret ${openId === node.id ? 'open' : ''}`} />
              <span className="workflow-node-index">{index + 1}</span>
              <strong>{TYPE_LABELS[node.type] || node.type}</strong>
              <span className="workflow-node-hint">{NODE_TYPES.find((item) => item.type === node.type)?.hint}</span>
              {canEdit && (
                <span className="workflow-node-actions" onClick={(event) => event.stopPropagation()}>
                  <button type="button" aria-label="上移" disabled={!movable(index, -1, nodes) || saving} onClick={() => move(index, -1)}><ArrowUp size={13} /></button>
                  <button type="button" aria-label="下移" disabled={!movable(index, 1, nodes) || saving} onClick={() => move(index, 1)}><ArrowDown size={13} /></button>
                  <button type="button" aria-label="删除节点" disabled={saving} onClick={() => removeNode(index)}><Trash2 size={13} /></button>
                </span>
              )}
            </div>

            {openId === node.id && (
              <div className="workflow-node-body">
                <NodeConfig
                  node={node}
                  index={index}
                  nodes={nodes}
                  nodeIds={nodeIds}
                  tools={tools}
                  canEdit={canEdit}
                  saving={saving}
                  onUpdate={update}
                  onUpdateConfig={updateConfig}
                  onAddBranch={addBranch}
                  onUpdateBranch={updateBranch}
                  onRemoveBranch={removeBranch}
                />
              </div>
            )}
          </li>
        ))}
      </ol>

      {nodes.length === 0 && <p className="workflow-editor-empty">还没有节点，先添加「开始」和「输出回答」。</p>}

      {error && <p className="workflow-editor-error">{error}</p>}
      {!error && problems.length > 0 && <p className="workflow-editor-warn">{problems[0]}</p>}
      {message && <p className="workflow-editor-success">{message}</p>}

      {canEdit && (
        <div className="workflow-editor-footer">
          <div className="workflow-add">
            <button type="button" onClick={() => setAddMenuOpen((open) => !open)} disabled={saving}>
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
          <button type="button" className="primary" disabled={saving || problems.length > 0} onClick={save}>
            {saving ? '保存中…' : '保存工作流'}
          </button>
        </div>
      )}
    </div>
  );
}

function movable(index, delta, nodes) {
  const target = index + delta;
  if (target < 0 || target >= nodes.length) return false;
  const moving = nodes[index];
  const swapped = nodes[target];
  return moving.type !== 'Start' && moving.type !== 'Answer'
    && swapped.type !== 'Start' && swapped.type !== 'Answer';
}
