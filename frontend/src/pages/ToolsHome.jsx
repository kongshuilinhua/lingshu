import React, { useState } from 'react';
import { useUnsavedForm } from '../components/UnsavedChanges.jsx';
import { Bot, Check, KeyRound, Layers, Plus, Shield, Sparkles, SquarePen, Trash2, X } from 'lucide-react';
import { SecretInputDialog } from '../components/SecretInputDialog.jsx';
import { errorMessage } from '../utils.js';

export function ToolsHome({ createToolConfig, deleteToolConfig, openBuilder, requestDeleteConfirm, setProfileError, testToolConfig, tools, updateToolConfig }) {
  return (
    <div className="content-page">
      <header className="page-heading">
        <div>
          <h1>工具</h1>
          <p>管理可绑定到智能体的内置搜索和 HTTP 工具。密钥只在保存时提交，保存后仅显示 has_secret 状态。</p>
        </div>
        <button className="primary" type="button" onClick={openBuilder}><Bot size={16} />打开 Builder</button>
      </header>
      <ToolsPanel
        createToolConfig={createToolConfig}
        deleteToolConfig={deleteToolConfig}
        requestDeleteConfirm={requestDeleteConfirm}
        setProfileError={setProfileError}
        testToolConfig={testToolConfig}
        tools={tools}
        updateToolConfig={updateToolConfig}
      />
    </div>
  );
}



const HTTP_TOOL_PRESET = {
  type: 'http',
  name: 'weather_lookup',
  label: 'Weather lookup',
  description: 'Fetches weather data from an HTTPS API.',
  enabled: true,
  method: 'GET',
  url: 'https://api.example.com/weather',
  headers_schema: JSON.stringify({}, null, 2),
  query_schema: JSON.stringify({ city: { type: 'string', required: true } }, null, 2),
  body_schema: JSON.stringify({}, null, 2),
  auth_type: 'none',
  auth_header_name: 'Authorization',
  auth_query_name: '',
  auth_secret: '',
  response_path: '$',
  timeout_seconds: '10',
};

const BUILTIN_SEARCH_PRESET = {
  ...HTTP_TOOL_PRESET,
  type: 'builtin_search',
  name: 'builtin_search',
  label: '内置搜索',
  description: '\u5e73\u53f0\u5185\u7f6e\u7684\u8054\u7f51\u641c\u7d22\u5de5\u5177\u3002',
  method: 'GET',
  url: '',
  query_schema: JSON.stringify({ query: { type: 'string', required: true } }, null, 2),
  auth_type: 'none',
  response_path: '$',
};

function createToolForm(type = 'http') {
  return { ...(type === 'builtin_search' ? BUILTIN_SEARCH_PRESET : HTTP_TOOL_PRESET) };
}

function formFromTool(tool, overrides = {}) {
  const type = toolType(tool);
  return {
    ...createToolForm(type === 'builtin_search' ? 'builtin_search' : 'http'),
    type: type === 'builtin_search' ? 'builtin_search' : 'http',
    name: tool?.name || '',
    label: tool?.label || '',
    description: tool?.description || '',
    enabled: tool?.enabled !== false,
    method: tool?.method || 'GET',
    url: tool?.url || '',
    headers_schema: JSON.stringify(tool?.headers_schema || {}, null, 2),
    query_schema: JSON.stringify(tool?.query_schema || {}, null, 2),
    body_schema: JSON.stringify(tool?.body_schema || {}, null, 2),
    auth_type: tool?.auth?.type || tool?.auth_type || 'none',
    auth_header_name: tool?.auth?.header_name || tool?.auth_header_name || 'Authorization',
    auth_query_name: tool?.auth?.query_name || tool?.auth_query_name || '',
    auth_secret: '',
    response_path: tool?.response_path || '$',
    timeout_seconds: String(tool?.timeout_seconds || 10),
    ...overrides,
  };
}

function isUserTool(tool) {
  return Boolean(tool?.created_by);
}

// Dual-way parameter translator: Array <=> JSON Schema string
function paramsToSchema(paramsArray) {
  const schema = {};
  paramsArray.forEach(p => {
    if (p.name.trim()) {
      schema[p.name.trim()] = {
        type: p.type || 'string',
        required: Boolean(p.required),
        description: p.description || ''
      };
    }
  });
  return JSON.stringify(schema, null, 2);
}

function schemaToParams(schemaStr) {
  try {
    const schema = JSON.parse(schemaStr || '{}');
    return Object.entries(schema).map(([name, spec], index) => ({
      id: `${name}-${index}-${Date.now()}-${Math.random()}`,
      name,
      type: spec?.type || 'string',
      required: Boolean(spec?.required),
      description: spec?.description || ''
    }));
  } catch (e) {
    return [];
  }
}

function ParamTableEditor({ label, params, onChange }) {
  const addRow = () => {
    const newRow = {
      id: `param-${Date.now()}-${Math.random()}`,
      name: '',
      type: 'string',
      required: false,
      description: ''
    };
    onChange([...params, newRow]);
  };

  const removeRow = (id) => {
    onChange(params.filter(p => p.id !== id));
  };

  const updateRow = (id, patch) => {
    onChange(params.map(p => p.id === id ? { ...p, ...patch } : p));
  };

  return (
    <div className="param-table-editor-wrapper" style={{ marginTop: '14px', border: '1px solid #dfe4ef', borderRadius: '10px', padding: '14px', background: '#f8fafc' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <strong style={{ fontSize: '13px', color: '#1f2937' }}>{label}</strong>
        <button 
          type="button" 
          onClick={addRow}
          style={{ background: '#eef2ff', color: '#4d43e6', border: '1px solid #c7d2fe', padding: '4px 10px', borderRadius: '6px', fontSize: '12px', fontWeight: 'bold', cursor: 'pointer' }}
        >
          + 添加参数
        </button>
      </div>
      
      {params.length === 0 ? (
        <p style={{ fontStyle: 'italic', fontSize: '12px', color: '#94a3b8', margin: '4px 0', textAlign: 'center' }}>暂无参数，点击右上角一键添加。</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {params.map((row) => (
            <div key={row.id} style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <input 
                type="text" 
                placeholder="参数名" 
                value={row.name} 
                onChange={(e) => updateRow(row.id, { name: e.target.value })}
                style={{ flex: 2, padding: '5px 8px', border: '1px solid #dfe4ef', borderRadius: '6px', fontSize: '12px', color: '#111827' }}
              />
              <select 
                value={row.type} 
                onChange={(e) => updateRow(row.id, { type: e.target.value })}
                style={{ flex: 1.5, padding: '5px 8px', border: '1px solid #dfe4ef', borderRadius: '6px', fontSize: '12px', background: '#fff', color: '#111827' }}
              >
                <option value="string">string</option>
                <option value="number">number</option>
                <option value="integer">integer</option>
                <option value="boolean">boolean</option>
              </select>
              <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', fontSize: '12px', color: '#4b5563', padding: '0 4px', whiteSpace: 'nowrap' }}>
                <input 
                  type="checkbox" 
                  checked={row.required} 
                  onChange={(e) => updateRow(row.id, { required: e.target.checked })} 
                />
                必填
              </label>
              <input 
                type="text" 
                placeholder="参数描述或说明" 
                value={row.description} 
                onChange={(e) => updateRow(row.id, { description: e.target.value })}
                style={{ flex: 3, padding: '5px 8px', border: '1px solid #dfe4ef', borderRadius: '6px', fontSize: '12px', color: '#111827' }}
              />
              <button 
                type="button" 
                onClick={() => removeRow(row.id)}
                style={{ background: '#fee2e2', color: '#ef4444', border: 'none', padding: '6px 10px', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold', fontSize: '12px' }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ToolsPanel({ createToolConfig, deleteToolConfig, requestDeleteConfirm, setProfileError, testToolConfig, tools, updateToolConfig }) {
  const [form, setForm] = useState(createToolForm);
  const [formOpen, setFormOpen] = useState(false);
  const [editingTool, setEditingTool] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [testingId, setTestingId] = useState(null);
  const [testingTool, setTestingTool] = useState(null);
  const formGuard = useUnsavedForm({ enabled: formOpen, value: form, label: '工具配置', busy: saving,
    onSave: () => form.name.trim() && form.label.trim() ? submitTool() : false });
  const [testInputById, setTestInputById] = useState({});
  const [testBodyById, setTestBodyById] = useState({});
  const [testResults, setTestResults] = useState({});
  const [secretDialogTool, setSecretDialogTool] = useState(null);
  
  // States for visual parameter editor
  const [headersParams, setHeadersParams] = useState([]);
  const [queryParams, setQueryParams] = useState([]);
  const [bodyParams, setBodyParams] = useState([]);

  const isHttpForm = form.type === 'http';
  const needsBodySchema = isHttpForm && !['GET', 'DELETE'].includes(String(form.method || '').toUpperCase());
  const needsAuthSecret = isHttpForm && form.auth_type !== 'none';
  const needsAuthHeader = isHttpForm && ['bearer', 'header'].includes(form.auth_type);
  const needsAuthQuery = isHttpForm && form.auth_type === 'query';

  function switchType(type) {
    const preset = createToolForm(type);
    setForm((current) => ({
      ...preset,
      name: current.name && current.type === type ? current.name : preset.name,
      auth_type: type === 'builtin_search' ? 'none' : current.auth_type || preset.auth_type,
    }));
    setHeadersParams(schemaToParams(preset.headers_schema));
    setQueryParams(schemaToParams(preset.query_schema));
    setBodyParams(schemaToParams(preset.body_schema));
  }

  function updateToolForm(patch) {
    setForm((current) => {
      const next = { ...current, ...patch };
      if (Object.prototype.hasOwnProperty.call(patch, 'method') && ['GET', 'DELETE'].includes(String(patch.method).toUpperCase())) {
        next.body_schema = '{}';
        setBodyParams([]);
      }
      if (Object.prototype.hasOwnProperty.call(patch, 'auth_type')) {
        if (patch.auth_type === 'none') {
          next.auth_secret = '';
        }
        if (patch.auth_type !== 'query') {
          next.auth_query_name = '';
        }
        if (!['bearer', 'header'].includes(patch.auth_type)) {
          next.auth_header_name = 'Authorization';
        }
      }
      return next;
    });
  }

  function openToolForm(type = 'http') {
    const defaultForm = createToolForm(type);
    setForm(defaultForm);
    setHeadersParams(schemaToParams(defaultForm.headers_schema));
    setQueryParams(schemaToParams(defaultForm.query_schema));
    setBodyParams(schemaToParams(defaultForm.body_schema));
    setEditingTool(null);
    setNotice('');
    setProfileError('');
    setFormOpen(true);
  }

  function openEditTool(tool) {
    const editForm = formFromTool(tool);
    setForm(editForm);
    setHeadersParams(schemaToParams(editForm.headers_schema));
    setQueryParams(schemaToParams(editForm.query_schema));
    setBodyParams(schemaToParams(editForm.body_schema));
    setEditingTool(tool);
    setNotice('');
    setProfileError('');
    setFormOpen(true);
  }

  function openCopyTool(tool) {
    const copyForm = formFromTool(tool, { name: `${tool.name || 'tool'}_copy`, label: `${tool.label || tool.name} 副本`, enabled: true });
    setForm(copyForm);
    setHeadersParams(schemaToParams(copyForm.headers_schema));
    setQueryParams(schemaToParams(copyForm.query_schema));
    setBodyParams(schemaToParams(copyForm.body_schema));
    setEditingTool(null);
    setNotice('');
    setProfileError('');
    setFormOpen(true);
  }

  function closeToolForm() {
    if (saving) return;
    formGuard.confirmLeave(() => { setFormOpen(false); setEditingTool(null); });
  }

  async function submitTool(event) {
    event?.preventDefault();
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      const payload = toolFormPayload(form, { includeSecret: !editingTool });
      if (editingTool?.id) {
        await updateToolConfig(editingTool.id, payload);
      } else {
        await createToolConfig(payload);
      }
      setForm(createToolForm(form.type));
      formGuard.markSaved();
      setEditingTool(null);
      setFormOpen(false);
      setNotice(editingTool ? '工具已更新。' : '工具已保存，密钥不会在页面或接口响应中回显。');
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function patchTool(tool, patch) {
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      await updateToolConfig(tool.id, patch);
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function replaceToolSecret(tool, nextSecret) {
    if (!String(nextSecret || '').trim()) {
      setProfileError('Secret cannot be empty');
      return;
    }
    const saved = await patchTool(tool, {
      auth: {
        type: tool.auth?.type || tool.auth_type || 'bearer',
        header_name: tool.auth?.header_name || tool.auth_header_name || 'Authorization',
        query_name: tool.auth?.query_name || tool.auth_query_name || null,
        secret: String(nextSecret).trim(),
      },
    });
    if (!saved) return false;
    setSecretDialogTool(null);
    setNotice('工具密钥已替换，页面仅保留 has_secret 状态。');
    return true;
  }

  function openToolTest(tool) {
    setTestingTool(tool);
    setProfileError('');
    setNotice('');
    setTestInputById((items) => ({ ...items, [tool.id]: items[tool.id] || defaultToolTestInput(tool) }));
  }

  function closeToolTest() {
    if (testingId) return;
    setTestingTool(null);
  }

  async function deleteTool(tool) {
    const confirmed = await requestDeleteConfirm({
      title: '删除工具',
      message: `删除「${tool.label || tool.name}」？`,
      detail: '如果已有智能体绑定，后端会按约束拒绝。',
      confirmLabel: '删除工具',
    });
    if (!confirmed) return;
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      await deleteToolConfig(tool.id);
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function testTool(tool) {
    setTestingId(tool.id);
    setNotice('');
    setProfileError('');
    try {
      const payload = {
        input: parseJsonField(testInputById[tool.id] || '{}', 'test input'),
        body: parseOptionalJsonField(testBodyById[tool.id] || '', 'test body'),
      };
      const result = await testToolConfig(tool.id, payload);
      setTestResults((items) => ({ ...items, [tool.id]: result }));
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <section className="plain-panel tools-panel">
      <div className="panel-title-row">
        <div>
          <h3>工具</h3>
          <p>HTTP 工具按 Day2 契约提交 method、url、schema、auth、response_path 和 timeout_seconds；内置搜索使用 builtin_search 类型。</p>
        </div>
        <div className="panel-actions">
          <span className="soft-pill">{tools.length} 个工具</span>
          <button className="primary-model-action" type="button" onClick={() => openToolForm('http')}><Plus size={15} />新增工具</button>
        </div>
      </div>

      {formOpen && (
        <div className="profile-dialog-backdrop">
          <section className="resource-form-dialog tool-config-dialog" role="dialog" aria-modal="true" aria-label={editingTool ? '编辑工具' : '新增工具'} onClick={(event) => event.stopPropagation()}>
            <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭工具表单" onClick={closeToolForm} disabled={saving}>
              <X size={16} />
            </button>
            <header className="model-dialog-heading">
              <h3>{editingTool ? '编辑工具' : '新增工具'}</h3>
              <p>{editingTool ? '修改工具基础配置、Schema、超时和启用状态。已保存的密钥不会回显，需要单独替换。' : '配置可绑定到智能体的 HTTP 工具或内置联网搜索工具。密钥只提交一次，保存后不回显。'}</p>
            </header>
            <form className="tool-form dialog-form" onSubmit={submitTool}>
              <div className="tool-type-switch">
                <button type="button" disabled={!!editingTool} className={form.type === 'http' ? 'active' : ''} onClick={() => switchType('http')}>HTTP</button>
                <button type="button" disabled={!!editingTool} className={form.type === 'builtin_search' ? 'active' : ''} onClick={() => switchType('builtin_search')}>builtin_search</button>
              </div>
              <div className="tool-form-grid">
                <label className="field-stack">
                  <span>name</span>
                  <input value={form.name} onChange={(event) => updateToolForm({ name: event.target.value })} placeholder="weather_lookup" autoFocus />
                </label>
                <label className="field-stack">
                  <span>label</span>
                  <input value={form.label} onChange={(event) => updateToolForm({ label: event.target.value })} placeholder="Weather lookup" />
                </label>
                {isHttpForm && (
                  <label className="field-stack">
                    <span>method</span>
                    <select value={form.method} onChange={(event) => updateToolForm({ method: event.target.value })}>
                      {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((method) => <option key={method} value={method}>{method}</option>)}
                    </select>
                  </label>
                )}
                {isHttpForm && (
                  <label className="field-stack tool-url-field">
                    <span>url</span>
                    <input value={form.url} onChange={(event) => updateToolForm({ url: event.target.value })} placeholder="https://api.example.com/weather" />
                  </label>
                )}
                {isHttpForm && (
                  <label className="field-stack">
                    <span>response_path</span>
                    <input value={form.response_path} onChange={(event) => updateToolForm({ response_path: event.target.value })} placeholder="$" />
                  </label>
                )}
                {isHttpForm && (
                  <label className="field-stack">
                    <span>timeout_seconds</span>
                    <input type="number" min="1" max="30" value={form.timeout_seconds} onChange={(event) => updateToolForm({ timeout_seconds: event.target.value })} />
                  </label>
                )}
                <label className="field-stack tool-description-field">
                  <span>description</span>
                  <textarea value={form.description} onChange={(event) => updateToolForm({ description: event.target.value })} placeholder="工具能力说明" />
                </label>
              </div>
              {/* 可视化参数结构编辑器 */}
              <div className="coze-param-editor-container" style={{ gridColumn: 'span 2', display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '8px' }}>
                {isHttpForm && (
                  <ParamTableEditor 
                    label="Headers 参数结构定义 (headers_schema)" 
                    params={headersParams} 
                    onChange={(next) => {
                      setHeadersParams(next);
                      updateToolForm({ headers_schema: paramsToSchema(next) });
                    }} 
                  />
                )}
                <ParamTableEditor 
                  label={isHttpForm ? "Query 请求参数定义 (query_schema)" : "联网搜索参数定义 (search_query_schema)"} 
                  params={queryParams} 
                  onChange={(next) => {
                    setQueryParams(next);
                    updateToolForm({ query_schema: paramsToSchema(next) });
                  }} 
                />
                {needsBodySchema && (
                  <ParamTableEditor 
                    label="Body 请求体定义 (body_schema)" 
                    params={bodyParams} 
                    onChange={(next) => {
                      setBodyParams(next);
                      updateToolForm({ body_schema: paramsToSchema(next) });
                    }} 
                  />
                )}
              </div>
              {isHttpForm && (
                <div className="tool-auth-grid">
                  <label className="field-stack">
                    <span>auth.type</span>
                    <select value={form.auth_type} onChange={(event) => updateToolForm({ auth_type: event.target.value })}>
                      <option value="none">none</option>
                      <option value="bearer">bearer</option>
                      <option value="header">header</option>
                      <option value="query">query</option>
                    </select>
                  </label>
                  {needsAuthHeader && (
                    <label className="field-stack">
                      <span>auth.header_name</span>
                      <input value={form.auth_header_name} onChange={(event) => updateToolForm({ auth_header_name: event.target.value })} placeholder="Authorization" />
                    </label>
                  )}
                  {needsAuthQuery && (
                    <label className="field-stack">
                      <span>auth.query_name</span>
                      <input value={form.auth_query_name} onChange={(event) => updateToolForm({ auth_query_name: event.target.value })} placeholder="api_key" />
                    </label>
                  )}
                  {!editingTool && needsAuthSecret && (
                    <label className="field-stack">
                      <span>auth.secret</span>
                       <input type="password" value={form.auth_secret} onChange={(event) => updateToolForm({ auth_secret: event.target.value })} placeholder="只提交一次，不回显" autoComplete="off" />
                    </label>
                  )}
                  {editingTool && needsAuthSecret && (
                    <div className="tool-edit-secret-note">
                      <strong>密钥不在编辑表单中回显</strong>
                      <span>需要换密钥时，在列表里点击“替换 Secret”。</span>
                    </div>
                  )}
                </div>
              )}
              <div className="model-checks">
                <label><input type="checkbox" checked={form.enabled} onChange={(event) => updateToolForm({ enabled: event.target.checked })} />启用</label>
                {isHttpForm && (
                  <span className="tool-security-note">HTTP 工具必须使用 https://，后端负责阻断 localhost、私网和 metadata 地址。</span>
                )}
              </div>
              <footer className="dialog-actions">
                <button type="button" onClick={closeToolForm} disabled={saving}>取消</button>
                <button className="primary-model-action" type="submit" disabled={saving || !form.name.trim() || !form.label.trim() || (form.type === 'http' && !form.url.trim())}>
                  <Plus size={15} />{saving ? '保存中...' : editingTool ? '保存修改' : '保存工具'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      )}

      {notice && <p className="model-row-warning">{notice}</p>}

      <div className="tool-list" style={{ gap: '12px' }}>
        {tools.map((tool) => {
          const type = toolType(tool);
          const isHttp = type === 'http';
          const isSearch = type === 'builtin_search';
          const isBuiltin = type === 'builtin';

          // Type Badges styling
          let typeStyle = { background: '#f3f4f6', color: '#374151', border: '1px solid #e5e7eb' };
          if (isSearch) typeStyle = { background: '#e0f2fe', color: '#0369a1', border: '1px solid #bae6fd' };
          else if (isBuiltin) typeStyle = { background: '#f3e8ff', color: '#6b21a8', border: '1px solid #e9d5ff' };
          else if (isHttp) typeStyle = { background: '#dcfce7', color: '#15803d', border: '1px solid #bbf7d0' };

          const enabled = tool.enabled !== false;
          
          return (
            <article 
              className="tool-list-row" 
              key={tool.id}
              style={{
                transition: 'all 0.2s ease',
                border: '1px solid #e5e7eb',
                borderRadius: '12px',
                padding: '16px',
                background: '#ffffff',
                boxShadow: '0 1px 3px rgba(0,0,0,0.02)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = '#4d43e6';
                e.currentTarget.style.boxShadow = '0 10px 20px rgba(77, 67, 230, 0.05)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = '#e5e7eb';
                e.currentTarget.style.boxShadow = '0 1px 3px rgba(0,0,0,0.02)';
              }}
            >
              <div className="tool-row-main" style={{ display: 'flex', gap: '14px', alignItems: 'center', minWidth: 0, flex: 1 }}>
                <span 
                  className={`tool-kind ${type}`} 
                  style={{
                    ...typeStyle,
                    padding: '4px 10px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: 'bold',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}
                >
                  {type}
                </span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <strong style={{ fontSize: '14px', fontWeight: 700, color: '#111827' }}>{tool.label || tool.name}</strong>
                    <span 
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        padding: '2px 8px',
                        borderRadius: '12px',
                        fontSize: '11px',
                        background: enabled ? 'rgba(16, 185, 129, 0.08)' : 'rgba(107, 114, 128, 0.08)',
                        color: enabled ? '#10b981' : '#6b7280',
                        fontWeight: 600,
                      }}
                    >
                      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: enabled ? '#10b981' : '#6b7280' }} />
                      {enabled ? '已启用' : '已禁用'}
                    </span>
                  </div>
                  <small style={{ display: 'block', marginTop: '4px', fontSize: '12px', color: '#6b7280', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={tool.description}>
                    <code style={{ background: '#f3f4f6', padding: '2px 6px', borderRadius: '4px', marginRight: '6px', fontSize: '11px', fontFamily: 'monospace', color: '#4b5563' }}>
                      {tool.name}
                    </code>
                    {tool.description || '暂无详细说明'}
                  </small>
                </div>
              </div>

              <div className="tool-row-meta" style={{ display: 'flex', gap: '12px', alignItems: 'center', color: '#4b5563', fontSize: '12px' }}>
                <span style={{ background: '#f3f4f6', padding: '3px 8px', borderRadius: '6px', fontWeight: 'bold', color: '#374151' }}>
                  {tool.method || 'GET'}
                </span>
                <span style={{ color: '#9ca3af' }}>|</span>
                <span>超时 {tool.timeout_seconds || 10}s</span>
                <span style={{ color: '#9ca3af' }}>|</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: toolHasSecret(tool) ? '#b45309' : '#6b7280' }}>
                  <KeyRound size={12} />
                  {toolHasSecret(tool) ? '已配密钥' : '免鉴权'}
                </span>
              </div>

              <div className="tool-row-actions" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <button 
                  type="button" 
                  disabled={testingId === tool.id} 
                  onClick={() => openToolTest(tool)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    padding: '6px 12px',
                    borderRadius: '8px',
                    background: 'rgba(77, 67, 230, 0.08)',
                    color: '#4d43e6',
                    border: '1px solid rgba(77, 67, 230, 0.15)',
                    fontWeight: 600,
                    cursor: 'pointer',
                    fontSize: '12px',
                  }}
                >
                  <Sparkles size={13} />
                  {testingId === tool.id ? '测试中...' : '测试'}
                </button>
                {isUserTool(tool) ? (
                  <>
                    <button 
                      type="button" 
                      disabled={saving} 
                      onClick={() => openEditTool(tool)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        padding: '6px 12px',
                        borderRadius: '8px',
                        background: '#ffffff',
                        border: '1px solid #dfe4ef',
                        color: '#374151',
                        fontWeight: 600,
                        cursor: 'pointer',
                        fontSize: '12px',
                      }}
                    >
                      <SquarePen size={13} />
                      编辑
                    </button>
                    <button 
                      type="button" 
                      disabled={saving} 
                      onClick={() => patchTool(tool, { enabled: tool.enabled === false })}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        padding: '6px 12px',
                        borderRadius: '8px',
                        background: '#ffffff',
                        border: '1px solid #dfe4ef',
                        color: enabled ? '#d97706' : '#059669',
                        fontWeight: 600,
                        cursor: 'pointer',
                        fontSize: '12px',
                      }}
                    >
                      <Shield size={13} />
                      {enabled ? '禁用' : '启用'}
                    </button>
                    {isHttp && (
                      <button 
                        type="button" 
                        disabled={saving} 
                        onClick={() => setSecretDialogTool(tool)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px',
                          padding: '6px 12px',
                          borderRadius: '8px',
                          background: '#ffffff',
                          border: '1px solid #dfe4ef',
                          color: '#4b5563',
                          fontWeight: 600,
                          cursor: 'pointer',
                          fontSize: '12px',
                        }}
                      >
                        <KeyRound size={13} />
                        更新密钥
                      </button>
                    )}
                    <button 
                      className="model-delete-button" 
                      type="button" 
                      disabled={saving} 
                      onClick={() => deleteTool(tool)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        padding: '6px 12px',
                        borderRadius: '8px',
                        background: '#fee2e2',
                        border: '1px solid #fecaca',
                        color: '#ef4444',
                        fontWeight: 600,
                        cursor: 'pointer',
                        fontSize: '12px',
                      }}
                    >
                      <Trash2 size={13} />
                      删除
                    </button>
                  </>
                ) : (
                  <button 
                    type="button" 
                    disabled={saving} 
                    onClick={() => openCopyTool(tool)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      padding: '6px 12px',
                      borderRadius: '8px',
                      background: 'rgba(77, 67, 230, 0.05)',
                      border: '1px solid rgba(77, 67, 230, 0.15)',
                      color: '#4d43e6',
                      fontWeight: 600,
                      cursor: 'pointer',
                      fontSize: '12px',
                    }}
                  >
                    <Layers size={13} />
                    复制为自定义
                  </button>
                )}
              </div>
            </article>
          );
        })}
        {tools.length === 0 && (
          <p className="muted" style={{ padding: '24px', textAlign: 'center', background: '#f9fafb', borderRadius: '12px', border: '1px dashed #e5e7eb', color: '#6b7280' }}>
            当前没有可用工具。保存 builtin_search 或 HTTP 工具后即可在 Builder 中绑定。
          </p>
        )}
      </div>
      {testingTool && (
        <div className="profile-dialog-backdrop">
          <section className="resource-form-dialog tool-test-dialog" role="dialog" aria-modal="true" aria-label="测试工具" onClick={(event) => event.stopPropagation()}>
            <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭工具测试" onClick={closeToolTest} disabled={!!testingId}>
              <X size={16} />
            </button>
            <header className="model-dialog-heading">
              <h3>测试工具</h3>
              <p>{testingTool.label || testingTool.name}</p>
            </header>
            <div className="tool-test-box">
              <label className="field-stack">
                <span>test input</span>
                <textarea value={testInputById[testingTool.id] || defaultToolTestInput(testingTool)} onChange={(event) => setTestInputById((items) => ({ ...items, [testingTool.id]: event.target.value }))} />
              </label>
              <label className="field-stack">
                <span>test body</span>
                 <textarea value={testBodyById[testingTool.id] || ''} onChange={(event) => setTestBodyById((items) => ({ ...items, [testingTool.id]: event.target.value }))} placeholder="可空，JSON body" />
              </label>
              {testResults[testingTool.id] && <ToolTestResult result={testResults[testingTool.id]} />}
              <footer className="dialog-actions">
                <button type="button" onClick={closeToolTest} disabled={!!testingId}>关闭</button>
                <button className="primary-model-action" type="button" disabled={testingId === testingTool.id} onClick={() => testTool(testingTool)}>
                  <Check size={15} />{testingId === testingTool.id ? '测试中...' : '运行测试'}
                </button>
              </footer>
            </div>
          </section>
        </div>
      )}
      {secretDialogTool && (
        <SecretInputDialog
          label="Secret"
          message={`替换「${secretDialogTool.label || secretDialogTool.name}」的密钥。新密钥只提交一次，保存后不回显。`}
          onCancel={() => !saving && setSecretDialogTool(null)}
          onSubmit={(value) => replaceToolSecret(secretDialogTool, value).catch((err) => setProfileError(errorMessage(err)))}
          saving={saving}
          submitLabel="替换 Secret"
          title="替换工具 Secret"
        />
      )}
    </section>
  );
}

function ToolTestResult({ result }) {
  return (
    <div className={result.ok ? 'tool-test-result ok' : 'tool-test-result'}>
      <strong>{result.ok ? '测试成功' : '测试失败'}</strong>
      <span>{result.tool_type || 'tool'} · {result.status_code || '-'} · {result.latency_ms ?? '-'}ms · {result.content_type || '-'}</span>
      <pre>{result.result_preview || result.error || result.message || JSON.stringify(result, null, 2)}</pre>
    </div>
  );
}


function toolFormPayload(form, { includeSecret = false } = {}) {
  const timeout = Number(form.timeout_seconds);
  const isHttp = form.type === 'http';
  const authType = isHttp ? form.auth_type || 'none' : 'none';
  const auth = {
    type: authType,
    header_name: ['bearer', 'header'].includes(authType) ? form.auth_header_name || 'Authorization' : null,
    query_name: authType === 'query' ? form.auth_query_name || null : null,
  };
  if (includeSecret && authType !== 'none' && String(form.auth_secret || '').trim()) {
    auth.secret = String(form.auth_secret).trim();
  }
  const method = String(form.method || 'GET').toUpperCase();
  const hasBodySchema = isHttp && !['GET', 'DELETE'].includes(method);
  return {
    type: isHttp ? 'http' : form.type,
    name: String(form.name || '').trim(),
    label: String(form.label || '').trim(),
    description: String(form.description || '').trim(),
    enabled: Boolean(form.enabled),
    method,
    url: isHttp ? String(form.url || '').trim() : '',
    headers_schema: isHttp ? parseJsonField(form.headers_schema, 'headers_schema') : {},
    query_schema: parseJsonField(form.query_schema, 'query_schema'),
    body_schema: hasBodySchema ? parseJsonField(form.body_schema, 'body_schema') : {},
    auth,
    response_path: isHttp ? String(form.response_path || '$').trim() || '$' : '$',
    timeout_seconds: isHttp && Number.isFinite(timeout) ? Math.min(30, Math.max(1, timeout)) : 10,
  };
}

function parseJsonField(value, label) {
  const text = String(value ?? '').trim();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
}

function parseOptionalJsonField(value, label) {
  const text = String(value ?? '').trim();
  return text ? parseJsonField(text, label) : null;
}

function toolType(tool) {
  return tool?.type || (tool?.name === 'builtin_search' ? 'builtin_search' : 'http');
}

function toolHasSecret(tool) {
  return Boolean(tool?.auth?.has_secret || tool?.has_secret || tool?.auth_has_secret);
}

function defaultToolTestInput(tool) {
  if (toolType(tool) === 'builtin_search') return '{\n  "query": "Lingshu Agent"\n}';
  return '{\n  "city": "Hangzhou"\n}';
}

