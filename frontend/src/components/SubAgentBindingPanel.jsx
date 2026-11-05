import React, { useEffect, useRef, useState } from 'react';
import { useUnsavedForm } from './UnsavedChanges.jsx';
import { Bot, Check, CircleAlert } from 'lucide-react';
import { api } from '../lib/api.js';
import './McpBindingPanel.css';

/**
 * 子智能体绑定面板（supervisor → sub-agent）。
 *
 * 绑定后，运行时会把每个已发布子智能体包装成 `call_agent_<id>` 工具，
 * 由模型在 ReAct 循环里决定是否委派。这里只负责选择与保存绑定关系。
 *
 * 只列出「同工作区 + 已发布」的智能体：未发布的没有快照可执行，
 * 后端也会拒绝（见 core/services/agents.py 的 replace_agent_sub_agent_bindings）。
 */
export function SubAgentBindingPanel({ agentId, token, canEdit }) {
  const [agents, setAgents] = useState([]);
  const [bindings, setBindings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const generation = useRef(0);
  const savedBindings = useRef([]);

  useEffect(() => {
    const current = ++generation.current;
    setAgents([]); setBindings([]); setDirty(false); setMessage(''); setError(''); setSaving(false);
    if (!agentId || !token) { setLoading(false); return undefined; }
    let live = true;
    setLoading(true);
    Promise.all([
      api('/api/agents', { token }),
      api(`/api/agents/${agentId}/sub-agent-bindings`, { token }),
    ]).then(([agentData, bindingData]) => {
      if (!live) return;
      setAgents(agentData.items || []);
      setBindings(bindingData.items || []);
      savedBindings.current = bindingData.items || [];
      setDirty(false);
    }).catch((err) => live && setError(err.message || '子智能体配置加载失败'))
      .finally(() => live && setLoading(false));
    return () => { live = false; if (generation.current === current) generation.current++; };
  }, [agentId, token]);

  const candidates = agents.filter((item) => item.id !== agentId && item.published_version_id);

  function toggle(targetId) {
    setBindings((current) => current.some((item) => item.target_agent_id === targetId)
      ? current.filter((item) => item.target_agent_id !== targetId)
      : [...current, { target_agent_id: targetId, enabled: true }]);
    setDirty(true);
    setMessage('');
    setError('');
  }

  async function save() {
    const current = generation.current;
    setSaving(true);
    setError('');
    try {
      const response = await api(`/api/agents/${agentId}/sub-agent-bindings`, {
        token, method: 'PUT', body: { items: bindings },
      });
      if (generation.current !== current) return false;
      setBindings(response.items || []);
      savedBindings.current = response.items || [];
      bindingGuard.markSaved();
      setDirty(false);
      setMessage('子智能体配置已保存。');
      return true;
    } catch (err) {
      if (generation.current === current) setError(err.message || '保存失败');
      return false;
    } finally {
      if (generation.current === current) setSaving(false);
    }
  }

  const bindingGuard = useUnsavedForm({ label: '子智能体绑定', enabled: canEdit && !loading, busy: saving,
    value: [...bindings].map((binding) => ({ target_agent_id: binding.target_agent_id, enabled: binding.enabled !== false }))
      .sort((a, b) => a.target_agent_id - b.target_agent_id), onSave: save,
    onDiscard: () => { setBindings(savedBindings.current); setDirty(false); } });

  return <section className="mcp-binding-panel">
    <div className="mcp-binding-header">
      <div><span className="mcp-binding-label">SUB AGENTS</span><h3><Bot size={17} /> 子智能体 <small>{bindings.length} 个</small></h3></div>
    </div>
    <p className="mcp-binding-intro">勾选后，模型可在对话中把子任务委派给这些已发布的智能体；是否委派由模型判断，不会强制调用。</p>
    {error && <p className="mcp-binding-error"><CircleAlert size={15} />{error}</p>}
    {message && <p className="mcp-binding-success"><Check size={15} />{message}</p>}
    {loading ? <p className="mcp-binding-empty">正在读取子智能体…</p> : candidates.length === 0 ? (
      <p className="mcp-binding-empty">还没有其他已发布的智能体可作为子智能体。先发布一个智能体再回来。</p>
    ) : <div className="mcp-binding-list">
      {candidates.map((item) => {
        const binding = bindings.find((row) => row.target_agent_id === item.id);
        return <div className="mcp-binding-service" key={item.id}>
          <label className="mcp-binding-tool">
            <input type="checkbox" disabled={!canEdit || saving} checked={Boolean(binding)} onChange={() => toggle(item.id)} />
            <span><strong>{item.name}</strong><small>{item.description || '暂无简介'}</small></span>
          </label>
        </div>;
      })}
    </div>}
    {canEdit && <div className="mcp-binding-footer">
      <span>{dirty ? '有未保存的子智能体选择' : '配置已同步'}</span>
      <button type="button" disabled={!dirty || saving || loading} onClick={save}>
        <Check size={14} />{saving ? '保存中' : '保存子智能体配置'}
      </button>
    </div>}
  </section>;
}
