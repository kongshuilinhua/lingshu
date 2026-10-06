import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, CircleAlert, PlugZap, RefreshCw, Settings2 } from 'lucide-react';
import { api } from '../lib/api.js';
import './McpBindingPanel.css';

export function McpBindingPanel({ agentId, token, canEdit, onOpenMarket }) {
  const [servers, setServers] = useState([]);
  const [bindings, setBindings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const generation = useRef(0);

  useEffect(() => {
    const current = ++generation.current;
    setServers([]);
    setBindings([]);
    setDirty(false);
    setMessage('');
    setSaving(false);
    if (!agentId || !token) { setLoading(false); return undefined; }
    let live = true;
    setLoading(true);
    setError('');
    Promise.all([
      api('/api/mcp/servers', { token }),
      api(`/api/agents/${agentId}/mcp-bindings`, { token }),
    ]).then(([serverData, bindingData]) => {
      if (!live) return;
      setServers(serverData.items || []);
      setBindings(bindingData.items || []);
      setDirty(false);
    }).catch((err) => live && setError(err.message || 'MCP 配置加载失败'))
      .finally(() => live && setLoading(false));
    return () => { live = false; if (generation.current === current) generation.current++; };
  }, [agentId, token]);

  const selectedCount = useMemo(() => bindings.reduce((total, item) => total + item.selected_tools.length, 0), [bindings]);

  function toggleTool(serverId, toolName) {
    setBindings((current) => {
      const existing = current.find((item) => item.server_id === serverId);
      const selected = new Set(existing?.selected_tools || []);
      if (selected.has(toolName)) selected.delete(toolName);
      else selected.add(toolName);
      const remaining = current.filter((item) => item.server_id !== serverId);
      return selected.size ? [...remaining, { server_id: serverId, selected_tools: [...selected], enabled: true }] : remaining;
    });
    setDirty(true);
    setMessage('');
    setError('');
  }

  async function save() {
    const current = generation.current;
    setSaving(true);
    setError('');
    try {
      const response = await api(`/api/agents/${agentId}/mcp-bindings`, {
        token, method: 'PUT', body: { items: bindings },
      });
      if (generation.current !== current) return;
      setBindings(response.items || []);
      setDirty(false);
      setMessage('MCP 工具配置已保存。');
    } catch (err) {
      if (generation.current === current) setError(err.message || '保存失败');
    } finally {
      if (generation.current === current) setSaving(false);
    }
  }

  return <section className="mcp-binding-panel">
    <div className="mcp-binding-header">
      <div><span className="mcp-binding-label">CONNECTED TOOLS</span><h3><PlugZap size={17} /> MCP 服务 <small>{bindings.length} 个服务 · {selectedCount} 个工具</small></h3></div>
      <button type="button" onClick={onOpenMarket}><Settings2 size={15} />打开市场</button>
    </div>
    <p className="mcp-binding-intro">勾选工具作为授权范围。模型需要时调用 tool_search 检索，再按需加载工具定义；启动时不展开全部工具。</p>
    {error && <p className="mcp-binding-error"><CircleAlert size={15} />{error}</p>}
    {message && <p className="mcp-binding-success"><Check size={15} />{message}</p>}
    {loading ? <p className="mcp-binding-empty">正在读取服务…</p> : servers.length === 0 ? (
      <div className="mcp-binding-empty">还没有接入可用的 MCP 服务。<button type="button" onClick={onOpenMarket}>去市场接入</button></div>
    ) : <div className="mcp-binding-list">
      {servers.filter((server) => server.enabled).map((server) => {
        const tools = server.catalog?.tools || [];
        const selected = new Set(bindings.find((item) => item.server_id === server.id)?.selected_tools || []);
        return <details className="mcp-binding-service" key={server.id} open={selected.size > 0 || undefined}>
          <summary><span className="mcp-binding-service-mark">{server.name.slice(0, 1).toUpperCase()}</span><span className="mcp-binding-service-name"><strong>{server.name}</strong><small>{server.transport === 'stdio' ? '本地服务' : '远程服务'} · {tools.length} 个工具</small></span><span className="mcp-binding-selected">{selected.size ? `已选 ${selected.size}` : '未选择'}</span></summary>
          <div className="mcp-binding-tools">
            {tools.length ? tools.map((tool) => <label key={tool.name} className="mcp-binding-tool">
              <input type="checkbox" disabled={!canEdit || saving} checked={selected.has(tool.name)} onChange={() => toggleTool(server.id, tool.name)} />
              <span><strong>{tool.title || tool.name}</strong><small>{tool.description || tool.name}</small></span>
            </label>) : <p>还未检测到工具。请先到市场检测连接。</p>}
          </div>
        </details>;
      })}
    </div>}
    {canEdit && <div className="mcp-binding-footer"><span>{dirty ? '有未保存的 MCP 工具选择' : '配置已同步'}</span><button type="button" disabled={!dirty || saving || loading} onClick={save}>{saving ? <RefreshCw size={14} className="spinning" /> : <Check size={14} />}{saving ? '保存中' : '保存 MCP 配置'}</button></div>}
  </section>;
}
