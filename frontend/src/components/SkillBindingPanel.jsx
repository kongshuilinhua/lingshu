import React, { useEffect, useRef, useState } from 'react';
import { useUnsavedForm } from './UnsavedChanges.jsx';
import { api } from '../lib/api.js';
import './McpBindingPanel.css';
import '../pages/SkillsHome.css';

export function SkillBindingPanel({ agentId, token, canEdit, onOpenMarket }) {
  const [skills, setSkills] = useState([]);
  const [bindings, setBindings] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const savedBindings = useRef([]);
  useEffect(() => {
    const current = ++generation.current;
    let live = true;
    setSkills([]); setBindings([]); setDirty(false); setError(''); setBusy(false); setLoading(true);
    if (!agentId || !token) { setLoading(false); return undefined; }
    Promise.all([api('/api/skills', { token }), api(`/api/agents/${agentId}/skill-bindings`, { token })])
      .then(([data, saved]) => { if (live) { setSkills(data.items || []); setBindings(saved.items || []); savedBindings.current = saved.items || []; setDirty(false); setError(''); } })
      .catch((err) => live && setError(err.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; if (generation.current === current) generation.current++; };
  }, [agentId, token]);
  function toggle(skill) {
    setBindings((current) => current.some((item) => item.skill_id === skill.id) ? current.filter((item) => item.skill_id !== skill.id)
      : [...current, { skill_id: skill.id, version_id: skill.current_version_id, enabled: true }]);
    setDirty(true);
  }
  async function save() {
    const current = generation.current;
    setBusy(true); setError('');
    try {
      const result = await api(`/api/agents/${agentId}/skill-bindings`, { token, method: 'PUT', body: { items: bindings } });
      if (generation.current === current) { setBindings(result.items || []); savedBindings.current = result.items || []; bindingGuard.markSaved(); setDirty(false); }
      return generation.current === current;
    } catch (err) { if (generation.current === current) setError(err.message); return false; }
    finally { if (generation.current === current) setBusy(false); }
  }
  const bindingGuard = useUnsavedForm({ label: 'Skill 绑定', enabled: canEdit && !loading, busy,
    value: [...bindings].map((binding) => ({ skill_id: binding.skill_id, version_id: binding.version_id,
      enabled: binding.enabled !== false })).sort((a, b) => a.skill_id - b.skill_id), onSave: save,
    onDiscard: () => { setBindings(savedBindings.current); setDirty(false); } });
  return <section className="mcp-binding-panel"><header className="mcp-binding-heading"><div><h3>Skill · 按需加载</h3><p>启动时提供名称和用途；模型调用 load_skill 后加载说明。</p></div><button type="button" onClick={onOpenMarket}>管理 Skill</button></header>
    {error && <p className="mcp-binding-error">{error}</p>}
    {loading ? <p className="mcp-binding-empty">正在读取 Skill…</p> : !skills.length ? <p className="mcp-binding-empty">还没有可绑定的 Skill。<button type="button" onClick={onOpenMarket}>去市场创建</button></p> : <div className="mcp-binding-list">{skills.map((skill) => {
      const binding = bindings.find((item) => item.skill_id === skill.id);
      return <div className="mcp-binding-service" key={skill.id}><label className="mcp-binding-tool"><input type="checkbox" checked={Boolean(binding)} disabled={!canEdit || busy || (!skill.enabled && !binding)} onChange={() => toggle(skill)} /><span><strong>{skill.name}{!skill.enabled ? '（已停用）' : ''}</strong><small>{skill.description}</small></span></label>{binding && <select className="skill-binding-version" aria-label={`${skill.name} 版本`} value={binding.version_id} disabled={!canEdit || busy} onChange={(event) => { setBindings(bindings.map((item) => item.skill_id === skill.id ? { ...item, version_id: Number(event.target.value) } : item)); setDirty(true); }}>{skill.versions.map((version) => <option value={version.id} key={version.id}>固定版本 {version.version}</option>)}</select>}</div>;
    })}</div>}
    {canEdit && <div className="mcp-binding-footer"><span>{dirty ? '有未保存的 Skill 选择' : '版本已固定'}</span><button type="button" disabled={!dirty || busy || loading} onClick={save}>{busy ? '保存中…' : '保存 Skill 配置'}</button></div>}
  </section>;
}
