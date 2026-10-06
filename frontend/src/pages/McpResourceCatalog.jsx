import React, { useEffect, useMemo, useState } from 'react';
import { Boxes, Check, ChevronDown, ChevronRight, CircleAlert, ExternalLink, KeyRound, Plus, RefreshCw, Search, Server, ShieldCheck, Trash2, X } from 'lucide-react';
import { api } from '../lib/api.js';
import { parseStdioConfigs } from '../lib/mcpConfig.js';
import { mcpAuthStatus } from '../lib/mcpStatus.js';
import './McpResourceCatalog.css';

const EMPTY_FORM = {
  name: '', description: '', category: '通用', transport: 'streamable_http',
  url: '', auth_type: 'none', auth_secret: '', client_id: '', client_secret: '', scope: '',
  token_endpoint_auth_method: 'client_secret_basic', template_id: '', env: {}, is_listed: false,
  stdio_source: 'custom', command: '', args_text: '[]', env_rows: [],
};

function serverForm(server) {
  return server ? {
    ...EMPTY_FORM,
    name: server.name,
    description: server.description || '',
    category: server.category || '通用',
    transport: server.transport,
    url: server.url || '',
    auth_type: server.auth_type || 'none',
    client_id: server.auth_config?.client_id || '',
    scope: server.auth_config?.scope || '',
    token_endpoint_auth_method: server.auth_config?.token_endpoint_auth_method || 'client_secret_basic',
    is_listed: Boolean(server.is_listed),
    command: server.stdio_config?.command || '',
    args_text: JSON.stringify(server.stdio_config?.args || [], null, 2),
    env_rows: (server.stdio_config?.env_keys || []).map((name) => ({ name, value: '', saved: true, changed: false })),
  } : { ...EMPTY_FORM, env: {} };
}

export function McpResourceCatalog({ token, canManage, onCatalogChange, mode = 'all', onOpenDiscovery, onOpenResources, onConnected }) {
  const [servers, setServers] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('全部');
  const [expandedId, setExpandedId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [oauthUrl, setOauthUrl] = useState('');
  const [oauthServerId, setOauthServerId] = useState(null);
  const [preview, setPreview] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(serverForm());
  const [connectionOptions, setConnectionOptions] = useState({});
  const [importJson, setImportJson] = useState('');
  const [importedConfigs, setImportedConfigs] = useState([]);

  async function refresh() {
    const [serverData, templateData] = await Promise.all([
      api('/api/mcp/servers', { token }),
      api('/api/mcp/stdio-templates', { token }),
    ]);
    setServers(serverData.items || []);
    onCatalogChange?.(serverData.items || []);
    setTemplates(templateData.items || []);
  }

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([
      api('/api/mcp/servers', { token }),
      api('/api/mcp/stdio-templates', { token }),
      api('/api/mcp/connection-options', { token }).catch(() => ({})),
    ]).then(([serverData, templateData, options]) => {
      if (!live) return;
      setServers(serverData.items || []);
      onCatalogChange?.(serverData.items || []);
      setTemplates(templateData.items || []);
      setConnectionOptions(options);
    }).catch((err) => live && setError(err.message || '服务目录加载失败'))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [token, onCatalogChange]);

  useEffect(() => {
    if (!oauthServerId) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const status = await api(`/api/mcp/servers/${oauthServerId}/oauth/status`, { token });
        if (status.authorized) {
          window.clearInterval(timer);
          setOauthServerId(null);
          setOauthUrl('');
          setNotice('OAuth 授权已完成，可以检测服务能力。');
          refresh().catch(() => {});
        }
      } catch { /* keep the authorization link visible for retry */ }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [oauthServerId, token]);

  const categories = useMemo(() => ['全部', ...new Set(servers.map((item) => item.category || '通用'))], [servers]);
  const visible = useMemo(() => servers.filter((server) => {
    const categoryMatch = category === '全部' || (server.category || '通用') === category;
    const text = `${server.name} ${server.description || ''} ${(server.catalog?.tools || []).map((tool) => tool.name).join(' ')}`.toLowerCase();
    return categoryMatch && text.includes(query.trim().toLowerCase());
  }), [servers, category, query]);
  const connectedCount = servers.filter((item) => Boolean(item.catalog?.checked_at)).length;
  const visibleTemplates = templates.filter((item) => `${item.name} ${item.description || ''}`.toLowerCase().includes(query.trim().toLowerCase()));

  function openForm(server = null) {
    setEditingId(server?.id || null);
    setForm({ ...serverForm(server), stdio_source: connectionOptions.can_configure_stdio ? 'custom' : 'template' });
    setImportJson('');
    setImportedConfigs([]);
    setError('');
    setDrawerOpen(true);
  }

  function connectTemplate(template) {
    setEditingId(null);
    setForm({ ...serverForm(), name: template.name, description: template.description || '',
      transport: 'stdio', stdio_source: 'template', template_id: template.id });
    setError('');
    setDrawerOpen(true);
  }

  function applyImported(config) {
    setForm((current) => ({ ...current, name: config.name, transport: 'stdio', stdio_source: 'custom', template_id: '',
      command: config.command, args_text: JSON.stringify(config.args, null, 2),
      env_rows: Object.entries(config.env).map(([name, value]) => ({ name, value, changed: true })) }));
  }

  function importConfig() {
    try {
      const configs = parseStdioConfigs(importJson);
      setImportedConfigs(configs);
      applyImported(configs[0]);
      setImportJson('');
      setError('');
    } catch (err) { setError(err.message); }
  }

  async function save(event) {
    event.preventDefault();
    setError('');
    setWorkingId('save');
    try {
      const payload = { ...form, name: form.name.trim(), description: form.description.trim(), category: form.category.trim() || '通用' };
      delete payload.stdio_source;
      delete payload.args_text;
      delete payload.env_rows;
      if (form.transport === 'stdio' && form.stdio_source === 'custom') {
        let args;
        try { args = JSON.parse(form.args_text); } catch { throw new Error('启动参数必须是有效的 JSON 数组。'); }
        if (!Array.isArray(args) || args.some((arg) => typeof arg !== 'string')) throw new Error('启动参数必须是字符串数组。');
        const names = form.env_rows.map((row) => row.name.trim());
        if (names.some((name) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) || new Set(names).size !== names.length) throw new Error('环境变量名不正确或重复。');
        payload.command = form.command.trim();
        payload.args = args;
        payload.template_id = '';
        payload.env = Object.fromEntries(form.env_rows.filter((row) => !row.saved || (row.changed && row.value !== '')).map((row) => [row.name.trim(), row.value]));
        const original = servers.find((item) => item.id === editingId);
        payload.env_remove = (original?.stdio_config?.env_keys || []).filter((name) => !names.includes(name));
      }
      if (editingId) {
        const original = servers.find((item) => item.id === editingId);
        const patch = {
          name: payload.name, description: payload.description,
          category: payload.category, is_listed: payload.is_listed,
        };
        if (form.transport !== 'stdio') {
          if (form.url !== original?.url) patch.url = form.url;
          if (form.auth_type !== original?.auth_type) patch.auth_type = form.auth_type;
          if (form.auth_secret) patch.auth_secret = form.auth_secret;
          if (form.client_id && form.client_id !== original?.auth_config?.client_id) patch.client_id = form.client_id;
          if (form.client_secret) patch.client_secret = form.client_secret;
          if (['oauth', 'client_credentials'].includes(form.auth_type) && form.scope !== (original?.auth_config?.scope || '')) patch.scope = form.scope;
          if (['oauth', 'client_credentials'].includes(form.auth_type) && form.token_endpoint_auth_method !== (original?.auth_config?.token_endpoint_auth_method || 'client_secret_basic')) patch.token_endpoint_auth_method = form.token_endpoint_auth_method;
        } else if (form.stdio_source === 'custom' && connectionOptions.can_configure_stdio) {
          if (payload.command !== original?.stdio_config?.command) patch.command = payload.command;
          if (JSON.stringify(payload.args) !== JSON.stringify(original?.stdio_config?.args || [])) patch.args = payload.args;
          if (Object.keys(payload.env).length) patch.env = payload.env;
          if (payload.env_remove.length) patch.env_remove = payload.env_remove;
        }
        await api(`/api/mcp/servers/${editingId}`, { token, method: 'PATCH', body: patch });
        setNotice('服务信息已更新。');
      } else {
        await api('/api/mcp/servers', { token, method: 'POST', body: payload });
        setNotice('服务已接入工作区。检测连接后即可在智能体中选择工具。');
      }
      await refresh();
      setDrawerOpen(false);
      if (!editingId) onConnected?.();
    } catch (err) {
      setError(err.message || '保存失败');
    } finally {
      setWorkingId(null);
    }
  }

  async function probe(server) {
    setError('');
    setNotice('');
    setWorkingId(server.id);
    try {
      const result = await api(`/api/mcp/servers/${server.id}/probe`, { token, method: 'POST' });
      await refresh();
      setExpandedId(server.id);
      setNotice(`已发现 ${(result.catalog?.tools || []).length} 个工具。`);
    } catch (err) {
      setError(`${server.name}：${err.message || '连接失败'}`);
      await refresh().catch(() => {});
    } finally {
      setWorkingId(null);
    }
  }

  async function startOAuth(server) {
    setWorkingId(server.id);
    setError('');
    try {
      const result = await api(`/api/mcp/servers/${server.id}/oauth/start`, { token, method: 'POST' });
      if (result.authorized) {
        setNotice('该服务已完成授权。');
      } else {
        setOauthUrl(result.authorization_url);
        setOauthServerId(server.id);
        setNotice('请打开授权链接并完成登录，页面会自动更新连接状态。');
      }
    } catch (err) {
      setError(err.message || '授权无法启动');
    } finally {
      setWorkingId(null);
    }
  }

  async function inspectCapability(server, kind, item) {
    setWorkingId(server.id);
    setError('');
    try {
      const response = kind === 'resource'
        ? await api(`/api/mcp/servers/${server.id}/resources/read`, { token, method: 'POST', body: { uri: item.uri } })
        : await api(`/api/mcp/servers/${server.id}/prompts/get`, { token, method: 'POST', body: { name: item.name, arguments: {} } });
      setPreview({ title: item.name || item.uri, content: JSON.stringify(response.result, null, 2).slice(0, 12000) });
    } catch (err) {
      setError(err.message || '读取失败');
    } finally {
      setWorkingId(null);
    }
  }

  async function remove(server) {
    setWorkingId(server.id);
    setError('');
    try {
      await api(`/api/mcp/servers/${server.id}`, { token, method: 'DELETE' });
      await refresh();
      setDeletingId(null);
      setNotice(`已移除 ${server.name}。`);
    } catch (err) {
      setError(err.message || '移除失败');
    } finally {
      setWorkingId(null);
    }
  }

  const selectedTemplate = templates.find((item) => item.id === form.template_id);
  const githubRemote = (() => { try { return new URL(form.url).hostname === 'api.githubcopilot.com'; } catch { return false; } })();

  return (
    <div className="mcp-market">
      <header className="mcp-resource-header">
        <div>
          <h2>{mode === 'connections' ? '我的 MCP 服务' : 'MCP 服务'}</h2>
          <p>{mode === 'connections' ? '管理已接入的连接、认证、工具和共享设置。' : '浏览工作区可用能力和接入模板；连接配置在“我的资源”中管理。'}</p>
        </div>
        <div className="mcp-resource-header-actions">
          {mode !== 'discover' && <span>{servers.length} 个服务 · {connectedCount} 个已检测 · {servers.reduce((count, server) => count + (server.catalog?.tools || []).length, 0)} 个工具</span>}
          <button type="button" className="mcp-primary" onClick={mode === 'connections' ? onOpenDiscovery : () => openForm()}><Plus size={16} />{mode === 'connections' ? '接入新服务' : '添加自定义连接'}</button>
        </div>
      </header>

      {error && <p className="mcp-alert"><CircleAlert size={16} />{error}</p>}
      {notice && <p className="mcp-notice"><Check size={16} />{notice}</p>}
      {oauthUrl && <div className="mcp-oauth-banner"><KeyRound size={18} /><span>授权页面已准备好。完成授权后回到这里。</span><a href={oauthUrl} target="_blank" rel="noopener noreferrer">打开授权页面 <ExternalLink size={14} /></a></div>}

      <div className="mcp-catalog-heading">
        <div><h3>查找 MCP 服务</h3><p className="mcp-form-hint">搜索当前工作区已接入的服务、工具{mode !== 'connections' ? '和部署模板' : ''}。</p></div>
        <label className="mcp-search"><Search size={17} /><input aria-label="搜索 MCP 服务" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索服务名称或工具" /></label>
      </div>

      {mode !== 'connections' && visibleTemplates.length > 0 && <section className="mcp-discovery" aria-label="MCP 接入模板">
        <div className="mcp-discovery-heading"><h3>可接入的服务</h3><p>选择部署方提供的模板，配置后即可使用。</p></div>
        <div className="mcp-template-grid">
          {visibleTemplates.map((template) => <article className="mcp-template-card" key={template.id}>
            <span className="mcp-template-icon"><Boxes size={20} /></span>
            <div><h4>{template.name}</h4><p>{template.description || '通过预置模板接入 MCP 服务。'}</p><small>{template.env_keys.length ? `需要配置 ${template.env_keys.length} 项凭据` : '无需额外凭据'}</small></div>
            <button type="button" onClick={() => connectTemplate(template)}>接入服务 <ChevronRight size={14} /></button>
          </article>)}
        </div>
      </section>}

      <section className="mcp-catalog" aria-label="已接入的 MCP 服务">
        <div className="mcp-catalog-heading">
          <div><h2>{mode === 'discover' ? '工作区可用服务' : '已接入的服务'}</h2></div>
        </div>
        <div className="mcp-filters" role="group" aria-label="分类筛选">
          {categories.map((item) => <button type="button" key={item} className={category === item ? 'active' : ''} onClick={() => setCategory(item)}>{item}</button>)}
        </div>
        {loading ? <p className="mcp-empty">正在读取服务目录…</p> : visible.length === 0 ? (
          <div className="mcp-empty"><Boxes size={31} /><strong>{servers.length ? '没有匹配的服务' : '还没有接入 MCP 服务'}</strong><span>选择市场中的模板或添加远程 MCP 地址，检测连接后即可在智能体中选用工具。</span></div>
        ) : (
          <div className="mcp-grid">
            {visible.map((server) => {
              const tools = server.catalog?.tools || [];
              const expanded = expandedId === server.id;
              const editable = Boolean(server.can_edit);
              const auth = mcpAuthStatus(server);
              const failed = server.catalog?.probe_status === 'failed';
              return <article className={`mcp-card ${expanded ? 'expanded' : ''}`} key={server.id}>
                <div className="mcp-card-top">
                  <div className="mcp-card-icon"><Server size={23} strokeWidth={1.8} /></div>
                  <span className="mcp-transport">{server.transport === 'stdio' ? 'LOCAL / STDIO' : server.transport === 'sse' ? 'REMOTE / SSE' : 'STREAMABLE HTTP'}</span>
                </div>
                <div className="mcp-card-body">
                  <span className="mcp-category">{server.category || '通用'}</span>
                  <h3>{server.name}</h3>
                  <p>{server.description || '这个服务尚未填写说明。'}</p>
                </div>
                <div className="mcp-card-meta">
                  <span className={server.catalog?.checked_at && !failed ? 'ready' : ''}><span className="mcp-status-dot" />{failed ? '最近检测未通过' : server.catalog?.checked_at ? `${tools.length} 个工具` : '待检测'}</span>
                  {server.auth_type !== 'none' && <span className={auth.ready ? 'ready' : ''} title={auth.ready ? `上次检测通过：${server.catalog?.checked_at || ''}` : server.auth_type}><KeyRound size={13} />{workingId === server.id ? '正在验证' : auth.label}</span>}
                  {!server.is_listed && <span><ShieldCheck size={13} />仅自己可见</span>}
                </div>
                <div className="mcp-card-actions">
                  {mode === 'discover' ? <button type="button" onClick={() => { setExpandedId(server.id); onOpenResources?.(); }}>管理连接 <ChevronRight size={15} /></button> : <button type="button" onClick={() => probe(server)} disabled={workingId != null}><RefreshCw size={15} className={workingId === server.id ? 'spinning' : ''} />{workingId === server.id ? '检测中' : '检测连接'}</button>}
                  {mode !== 'discover' && server.auth_type === 'oauth' && editable && <button type="button" onClick={() => startOAuth(server)} disabled={workingId != null}><KeyRound size={15} />{auth.state === 'verified' || auth.state === 'authorized' ? '重新授权' : '授权'}</button>}
                  <button type="button" onClick={() => setExpandedId(expanded ? null : server.id)}>{expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}查看能力</button>
                </div>
                {expanded && <div className="mcp-capabilities">
                  <div className="mcp-capability-counts"><span>TOOLS {tools.length}</span><span>RESOURCES {(server.catalog?.resources || []).length}</span><span>PROMPTS {(server.catalog?.prompts || []).length}</span></div>
                  {tools.length ? (mode === 'discover' ? tools.slice(0, 5) : tools).map((tool) => <div className="mcp-tool-line" key={tool.name}><strong>{tool.title || tool.name}</strong><small>{tool.description || tool.name}</small></div>) : <p>连接测试后展示服务能力。</p>}
                  {mode === 'discover' && tools.length > 5 && <p>还有 {tools.length - 5} 个工具，进入“我的资源”查看完整能力并配置连接。</p>}
                  {mode !== 'discover' && (server.catalog?.resources || []).map((resource) => <button className="mcp-capability-link" type="button" key={resource.uri} disabled={workingId != null} onClick={() => inspectCapability(server, 'resource', resource)}><span>资源</span>{resource.name || resource.uri}<ExternalLink size={13} /></button>)}
                  {mode !== 'discover' && (server.catalog?.prompts || []).map((prompt) => <button className="mcp-capability-link" type="button" key={prompt.name} disabled={workingId != null} onClick={() => inspectCapability(server, 'prompt', prompt)}><span>提示词</span>{prompt.name}<ExternalLink size={13} /></button>)}
                  {mode !== 'discover' && editable && <div className="mcp-manage-actions"><button type="button" onClick={() => openForm(server)}>编辑服务</button>{deletingId === server.id ? <><button type="button" className="danger" onClick={() => remove(server)}>确认移除</button><button type="button" onClick={() => setDeletingId(null)}>取消</button></> : <button type="button" onClick={() => setDeletingId(server.id)}><Trash2 size={14} />移除</button>}</div>}
                </div>}
              </article>;
            })}
          </div>
        )}
      </section>

      {drawerOpen && <div className="mcp-drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setDrawerOpen(false); }}>
        <aside className="mcp-drawer" role="dialog" aria-modal="true" aria-label={editingId ? '编辑 MCP 服务' : '登记 MCP 服务'}>
          <div className="mcp-drawer-head"><div><span className="mcp-section-kicker">REGISTER</span><h2>{editingId ? '编辑服务' : '登记 MCP 服务'}</h2></div><button type="button" aria-label="关闭" onClick={() => setDrawerOpen(false)}><X size={19} /></button></div>
          <form onSubmit={save} className="mcp-form">
            <label>服务名称<input required maxLength={120} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：知识库检索" /></label>
            <label>简介<textarea maxLength={2000} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="告诉团队它能完成什么" /></label>
            <label>分类<input maxLength={80} value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} placeholder="开发 / 数据 / 办公" /></label>
            {!editingId && <label>连接方式<select value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value })}><option value="streamable_http">Streamable HTTP（远程）</option><option value="sse">HTTP + SSE（兼容旧服务）</option><option value="stdio">stdio（后端本地进程）</option></select></label>}
            {form.transport !== 'stdio' ? <>
              <label>MCP 地址<input required type="url" value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} placeholder="https://example.com/mcp" /></label>
              <p className="mcp-form-hint">仅连接公网 HTTPS。请填写服务最终地址，跨站跳转会被拒绝。</p>
              <label>认证方式<select value={form.auth_type} onChange={(event) => setForm({ ...form, auth_type: event.target.value, token_endpoint_auth_method: githubRemote && event.target.value === 'oauth' ? 'client_secret_post' : form.token_endpoint_auth_method })}><option value="none">无认证</option><option value="bearer">Bearer Token</option>{canManage && <option value="oauth">OAuth（浏览器授权）</option>}{canManage && <option value="client_credentials">OAuth Client Credentials</option>}</select></label>
              {githubRemote && <p className="mcp-form-hint">GitHub 可使用 Bearer Token（PAT）。OAuth 需要先注册 GitHub App 或 OAuth App，并填写该应用的 Client ID 和 Secret，不能自动注册应用。</p>}
              {form.auth_type === 'bearer' && <label>访问令牌<input type="password" value={form.auth_secret} onChange={(event) => setForm({ ...form, auth_secret: event.target.value })} placeholder={editingId ? '留空表示沿用已保存的令牌' : '只提交一次，不会回显'} autoComplete="off" /></label>}
              {form.auth_type === 'oauth' && <>
                <label>Client ID（已注册应用）<input required={githubRemote} value={form.client_id} onChange={(event) => setForm({ ...form, client_id: event.target.value })} placeholder="支持动态注册的服务可留空" /></label>
                <label>Client Secret<input required={githubRemote && !servers.find((item) => item.id === editingId)?.auth_config?.has_client_secret} type="password" value={form.client_secret} onChange={(event) => setForm({ ...form, client_secret: event.target.value })} placeholder={editingId ? '留空沿用已保存的密钥' : '应用密钥，只提交一次'} autoComplete="off" /></label>
                <label>令牌端点认证<select value={form.token_endpoint_auth_method} onChange={(event) => setForm({ ...form, token_endpoint_auth_method: event.target.value })}><option value="client_secret_basic">client_secret_basic</option><option value="client_secret_post">client_secret_post</option></select></label>
                <label>OAuth 回调地址<input readOnly value={connectionOptions.oauth_redirect_url || ''} placeholder="部署者需配置后端回调地址" /></label>
                <p className="mcp-form-hint">在应用提供方登记上面完全一致的回调地址。开发环境使用本机 8000 端口，远程部署需配置公开的回调地址。</p>
                <label>Scope（可选）<input value={form.scope} onChange={(event) => setForm({ ...form, scope: event.target.value })} placeholder="read write" /></label><p className="mcp-form-hint">保存后在服务卡片点击“授权”。令牌由工作区共享，只有管理员可完成授权。</p>
              </>}
              {form.auth_type === 'client_credentials' && <><label>Client ID<input value={form.client_id} onChange={(event) => setForm({ ...form, client_id: event.target.value })} placeholder={editingId ? '留空表示沿用已保存的 ID' : '服务提供的 Client ID'} /></label><label>Client Secret<input type="password" value={form.client_secret} onChange={(event) => setForm({ ...form, client_secret: event.target.value })} placeholder={editingId ? '留空表示沿用已保存的密钥' : '只提交一次，不会回显'} autoComplete="off" /></label><label>Scope（可选）<input value={form.scope} onChange={(event) => setForm({ ...form, scope: event.target.value })} placeholder="read write" /></label><label>令牌端点认证<select value={form.token_endpoint_auth_method} onChange={(event) => setForm({ ...form, token_endpoint_auth_method: event.target.value })}><option value="client_secret_basic">client_secret_basic</option><option value="client_secret_post">client_secret_post</option></select></label></>}
            </> : <>
              <p className="mcp-form-hint">stdio 会在后端所在机器启动进程。命令填写可执行程序，参数单独填写；密钥放在环境变量中加密保存。</p>
              {!canManage && <p className="mcp-alert">stdio 服务需要管理员登记；登记后可在智能体中选择已共享的服务和工具。</p>}
              {connectionOptions.can_configure_stdio && <label>启动配置<select value={form.stdio_source} onChange={(event) => setForm({ ...form, stdio_source: event.target.value })}><option value="custom">自定义命令</option>{!editingId && templates.length > 0 && <option value="template">部署模板</option>}</select></label>}
              {form.stdio_source === 'custom' && connectionOptions.can_configure_stdio ? <>
                {!editingId && <details className="mcp-json-import"><summary>导入 MCP JSON 配置</summary><label>JSON 配置<textarea value={importJson} onChange={(event) => setImportJson(event.target.value)} placeholder={'{"mcpServers":{"服务名":{"command":"python","args":["server.py"],"env":{}}}}'} /></label><button type="button" onClick={importConfig}>解析并填入</button><p className="mcp-form-hint">多个服务可选择其中一条登记，导入后请核对启动命令。</p></details>}
                {importedConfigs.length > 1 && <label>选择导入的服务<select onChange={(event) => applyImported(importedConfigs[Number(event.target.value)])}>{importedConfigs.map((item, index) => <option value={index} key={item.name}>{item.name}</option>)}</select></label>}
                <label>启动命令<input required value={form.command} onChange={(event) => setForm({ ...form, command: event.target.value })} placeholder="python、npx、uvx 或可执行程序完整路径" /></label>
                <label>启动参数（JSON 数组）<textarea value={form.args_text} onChange={(event) => setForm({ ...form, args_text: event.target.value })} placeholder={'["server.py"]'} /></label>
                <div className="mcp-env-heading"><strong>环境变量</strong><button type="button" onClick={() => setForm({ ...form, env_rows: [...form.env_rows, { name: '', value: '', changed: true }] })}>添加环境变量</button></div>
                {form.env_rows.map((row, index) => <div className="mcp-env-row" key={index}>
                  <input aria-label={`环境变量名称 ${index + 1}`} readOnly={row.saved} value={row.name} onChange={(event) => setForm({ ...form, env_rows: form.env_rows.map((item, i) => i === index ? { ...item, name: event.target.value } : item) })} placeholder="变量名" />
                  <input aria-label={`环境变量值 ${index + 1}`} type="password" autoComplete="off" value={row.value} onChange={(event) => setForm({ ...form, env_rows: form.env_rows.map((item, i) => i === index ? { ...item, value: event.target.value, changed: true } : item) })} placeholder={row.saved ? '已保存，留空沿用' : '变量值'} />
                  <button type="button" aria-label={`移除环境变量 ${index + 1}`} onClick={() => setForm({ ...form, env_rows: form.env_rows.filter((_, i) => i !== index) })}><X size={16} /></button>
                </div>)}
                {editingId && <p className="mcp-form-hint">已保存的变量值不会回显；未修改的变量会沿用，点击移除会在保存后删除该变量。</p>}
              </> : <>
                {canManage && !templates.length && <p className="mcp-alert">当前部署未允许自定义启动程序，也没有部署模板。</p>}
                {editingId ? <p className="mcp-form-hint">当前部署使用已登记的启动配置。</p> : <label>部署模板<select required value={form.template_id} onChange={(event) => setForm({ ...form, template_id: event.target.value, env: {} })}><option value="">选择部署模板</option>{templates.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>}
                {!editingId && selectedTemplate?.env_keys?.map((key) => <label key={key}>{key}<input type="password" value={form.env[key] || ''} onChange={(event) => setForm({ ...form, env: { ...form.env, [key]: event.target.value } })} autoComplete="off" /></label>)}
              </>}
            </>}
            {canManage && <label className="mcp-check"><input type="checkbox" checked={form.is_listed} onChange={(event) => setForm({ ...form, is_listed: event.target.checked })} /><span>在当前工作区共享，供其他成员的智能体选择</span></label>}
            {error && <p className="mcp-alert"><CircleAlert size={16} />{error}</p>}
            <div className="mcp-form-actions"><button type="button" onClick={() => setDrawerOpen(false)}>取消</button><button type="submit" className="mcp-primary" disabled={workingId != null || (form.transport === 'stdio' && (!canManage || (!editingId && !(form.stdio_source === 'custom' && connectionOptions.can_configure_stdio) && !selectedTemplate)))}>{workingId === 'save' ? '保存中…' : editingId ? '保存修改' : '登记服务'}</button></div>
          </form>
        </aside>
      </div>}
      {preview && <div className="mcp-preview-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreview(null); }}><section className="mcp-preview" role="dialog" aria-modal="true" aria-label="MCP 能力预览"><div><h2>{preview.title}</h2><button type="button" aria-label="关闭" onClick={() => setPreview(null)}><X size={18} /></button></div><pre>{preview.content}</pre></section></div>}
    </div>
  );
}
