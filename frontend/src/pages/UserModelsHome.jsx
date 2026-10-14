import React, { useEffect, useRef, useState } from 'react';
import { useUnsavedForm } from '../components/UnsavedChanges.jsx';
import { Check, ChevronDown, Plus, Trash2, Wand2, X } from 'lucide-react';
import { SecretInputDialog } from '../components/SecretInputDialog.jsx';
import {
  errorMessage,
  guessModelCapabilities,
  modelCapabilityChips,
  reasoningCapabilityForModel,
  reasoningLabel,
  userModelEditPayload,
  userModelFormPayload,
} from '../utils.js';

export function UserModelsHome({ adminModels, canManage, createModelConfig, deleteModelConfig, requestDeleteConfirm, setProfileError, updateModelConfig, ...userModelProps }) {
  return (
    <div className="content-page">
      <header className="page-heading">
        <div>
          <h1>我的模型</h1>
          <p>维护 OpenAI-compatible 或 Anthropic Messages 模型连接，保存后可在智能体中选择。</p>
        </div>
      </header>
      <UserModelsPanel requestDeleteConfirm={requestDeleteConfirm} setProfileError={setProfileError} {...userModelProps} />
      {canManage && (
        <ModelAdminPanel
          createModelConfig={createModelConfig}
          deleteModelConfig={deleteModelConfig}
          models={adminModels}
          requestDeleteConfirm={requestDeleteConfirm}
          setProfileError={setProfileError}
          updateModelConfig={updateModelConfig}
        />
      )}
    </div>
  );
}



function createUserModelForm() {
  // 统一页：只填协议 / base_url / api_key / 模型（可多选，同一连接一次注册多个模型）。
  // 文档、上下文、温度用后端默认值；图片与深度思考按模型名预填，用户可逐个修改。
  return {
    provider: 'openai-compatible',
    base_url: '',
    api_key: '',
    chat_models: [],
    capabilities: {},
    supports_document: true,
    max_context: '131072',
    default_temperature: '0.4',
    enabled: true,
    is_default: true,
  };
}

const PRIVATE_GATEWAY_ENV = 'USER_MODEL_ALLOW_PRIVATE_NETWORK';
const DRAFT_TEST_CONCURRENCY = 3;

function gatewayErrorText(message) {
  const text = String(message || '');
  if (text.includes(PRIVATE_GATEWAY_ENV)) {
    return `该地址是内网、自建网关或 HTTP 地址，默认只允许公网 HTTPS；如需接入，请部署方在后端 .env 设置 ${PRIVATE_GATEWAY_ENV}=true 后重启。`;
  }
  return text;
}

// 404/405 或「不认识的请求格式」多半是协议选错：同一网关可能只开放其中一种接口。
const PROTOCOL_MISMATCH_PATTERN = /HTTP 40[45]\b|not found|unknown (url|endpoint)|invalid url|unrecognized request/i;

function withProtocolHint(text, provider) {
  if (!text || !PROTOCOL_MISMATCH_PATTERN.test(text)) return text;
  const other = provider === 'anthropic' ? 'OpenAI-compatible' : 'Anthropic';
  return `${text}（地址或接口不存在，可能是协议不匹配，试试切换为 ${other}）`;
}

// 只识别官方地址；中转网关两种协议都可能开放，不做猜测
function providerForBaseUrl(baseUrl) {
  return /^https?:\/\/api\.anthropic\.com(\/|$)/i.test(String(baseUrl || '').trim()) ? 'anthropic' : null;
}

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}

// 单个模型的保存/测试载荷：显示名称取模型名；能力取用户在该模型行上的勾选（缺省按模型名预填）。
function draftModelPayload(form, chatModel) {
  const capability = form.capabilities[chatModel] || guessModelCapabilities(chatModel);
  const reasoningType = capability.reasoning_type || 'none';
  return userModelFormPayload({
    ...form,
    chat_model: chatModel,
    display_name: chatModel,
    supports_image: capability.supports_image,
    supports_reasoning: reasoningType !== 'none',
    reasoning_type: reasoningType,
  }, { includeApiKey: true });
}

async function runWithConcurrency(items, limit, worker) {
  const queue = [...items];
  const runners = Array.from({ length: Math.min(limit, queue.length) }, async () => {
    while (queue.length) await worker(queue.shift());
  });
  await Promise.all(runners);
}

function userModelEditForm(config) {
  return {
    display_name: config.display_name || '',
    provider: config.provider || 'openai-compatible',
    base_url: config.base_url || '',
    chat_model: config.chat_model || '',
    supports_document: config.supports_document !== false,
    supports_image: Boolean(config.supports_image),
    supports_reasoning: Boolean(config.supports_reasoning),
    reasoning_type: config.reasoning_type || (config.supports_reasoning ? 'prompt' : 'none'),
    reasoning_label: config.reasoning_label || reasoningLabel(config.reasoning_type || 'none'),
    max_context: String(config.max_context || 131072),
    default_temperature: String(config.default_temperature ?? 0.4),
    enabled: Boolean(config.enabled),
    is_default: Boolean(config.is_default),
  };
}

function UserModelsPanel({
  createUserModelConfig,
  deleteUserModelConfig,
  requestDeleteConfirm,
  setProfileError,
  probeUserModels,
  testUserModelDraft,
  testUserModelConfig,
  updateUserModelConfig,
  userModels,
}) {
  const [form, setForm] = useState(createUserModelForm);
  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [draftTesting, setDraftTesting] = useState(false);
  const [draftResults, setDraftResults] = useState({});
  const [testingId, setTestingId] = useState(null);
  const [testResults, setTestResults] = useState({});
  const [keyDialogConfig, setKeyDialogConfig] = useState(null);
  const [editConfig, setEditConfig] = useState(null);
  const [editForm, setEditForm] = useState(null);
  const [probedModels, setProbedModels] = useState([]);
  const [probeNotice, setProbeNotice] = useState(null);
  const [probing, setProbing] = useState(false);
  const [formError, setFormError] = useState('');
  const [modelQuery, setModelQuery] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef(null);
  const formVersion = useRef(0);
  const modelProbeVersion = useRef(0);
  const formReady = Boolean(form.base_url.trim() && form.api_key.trim() && form.chat_models.length);
  const pendingModels = form.chat_models.filter((name) => !draftResults[name]);
  const passedModels = form.chat_models.filter((name) => draftResults[name]?.ok);
  const canSaveForm = formReady && !pendingModels.length && passedModels.length > 0;
  const registeredModels = new Set(userModels
    .filter((config) => (config.provider || 'openai-compatible') === form.provider
      && normalizeBaseUrl(config.base_url) === normalizeBaseUrl(form.base_url))
    .map((config) => config.chat_model));
  const query = modelQuery.trim();
  const modelOptions = [...new Set([...probedModels, ...form.chat_models])]
    .filter((name) => !query || name.toLowerCase().includes(query.toLowerCase()));
  const selectableOptions = modelOptions.filter((name) => !registeredModels.has(name));
  const canAddQuery = Boolean(query) && !modelOptions.includes(query) && !registeredModels.has(query);
  const createGuard = useUnsavedForm({ enabled: formOpen, value: form, label: '新增模型配置',
    busy: saving || draftTesting, onSave: () => submitUserModel() });
  const editGuard = useUnsavedForm({ enabled: Boolean(editConfig), value: editForm, label: '模型配置',
    busy: saving, onSave: () => submitEditUserModel() });

  useEffect(() => {
    if (!pickerOpen) return undefined;
    const close = (event) => {
      if (pickerRef.current && !pickerRef.current.contains(event.target)) setPickerOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [pickerOpen]);

  function updateForm(patch) {
    // 连接参数变化会让所有测试结果失效；仅改模型勾选时保留其余模型的结果。
    if ('base_url' in patch && !('provider' in patch)) {
      const detected = providerForBaseUrl(patch.base_url);
      if (detected) patch = { ...patch, provider: detected };
    }
    if ('base_url' in patch || 'api_key' in patch || 'provider' in patch) {
      formVersion.current += 1;
      resetModelProbe();
      setDraftResults({});
    }
    setFormError('');
    setForm((current) => ({ ...current, ...patch }));
  }

  function setSelectedModels(names) {
    const next = [...new Set(names)].filter((name) => name && !registeredModels.has(name));
    setFormError('');
    // 新勾选的模型按名称预填能力；已有的保留用户改过的值
    setForm((current) => ({
      ...current,
      chat_models: next,
      capabilities: Object.fromEntries(next.map((name) => [name, current.capabilities[name] || guessModelCapabilities(name)])),
    }));
    setDraftResults((current) => Object.fromEntries(Object.entries(current).filter(([name]) => next.includes(name))));
  }

  function updateCapability(name, patch) {
    // 能力只影响保存的声明，不影响连通性，所以不清空测试结果
    setForm((current) => ({ ...current, capabilities: { ...current.capabilities, [name]: { ...current.capabilities[name], ...patch } } }));
  }

  function toggleModel(name) {
    setSelectedModels(form.chat_models.includes(name)
      ? form.chat_models.filter((item) => item !== name)
      : [...form.chat_models, name]);
  }

  function addQueryModel() {
    if (!canAddQuery) return;
    setSelectedModels([...form.chat_models, query]);
    setModelQuery('');
  }

  function resetModelProbe() {
    modelProbeVersion.current += 1;
    setProbedModels([]);
    setProbeNotice(null);
    setProbing(false);
  }

  function openCreateForm() {
    formVersion.current += 1;
    resetModelProbe();
    setForm(createUserModelForm());
    setDraftResults({});
    setModelQuery('');
    setPickerOpen(false);
    setFormError('');
    setNotice('');
    setProfileError('');
    setFormOpen(true);
  }

  function closeCreateForm() {
    if (saving || draftTesting) return;
    createGuard.confirmLeave(() => {
      formVersion.current += 1;
      resetModelProbe();
      setFormOpen(false);
      setDraftResults({});
    });
  }

  function openEditForm(config) {
    setEditConfig(config);
    setEditForm(userModelEditForm(config));
    setNotice('');
    setProfileError('');
  }

  function closeEditForm() {
    if (saving) return;
    editGuard.confirmLeave(() => { setEditConfig(null); setEditForm(null); });
  }

  function updateEditForm(patch) {
    setEditForm((current) => ({ ...current, ...patch }));
  }

  async function submitUserModel(event) {
    event?.preventDefault();
    if (!canSaveForm || saving || draftTesting) return false;
    setSaving(true);
    setNotice('');
    setProfileError('');
    setFormError('');
    const saved = [];
    const failed = [];
    for (const name of passedModels) {
      try {
        // 「设为默认」只给本次第一个成功保存的模型
        await createUserModelConfig(draftModelPayload({ ...form, is_default: form.is_default && !saved.length }, name));
        saved.push(name);
      } catch (err) {
        failed.push(`${name}：${gatewayErrorText(errorMessage(err))}`);
      }
    }
    setSaving(false);
    if (failed.length) {
      // 已保存的从勾选中移除，失败的留在表单里便于重试
      setSelectedModels(form.chat_models.filter((name) => !saved.includes(name)));
      setFormError(`${saved.length ? `已保存 ${saved.length} 个；` : ''}以下模型保存失败：${failed.join('；')}`);
      return false;
    }
    createGuard.markSaved();
    setForm(createUserModelForm());
    setFormOpen(false);
    setDraftResults({});
    const skipped = form.chat_models.length - saved.length;
    setNotice(`已保存 ${saved.length} 个模型${skipped ? `，${skipped} 个测试未通过的未保存` : ''}。`);
    return true;
  }

  async function testDraftModel() {
    const version = formVersion.current;
    const targets = [...form.chat_models];
    setDraftTesting(true);
    setNotice('');
    setProfileError('');
    setFormError('');
    setDraftResults({});
    await runWithConcurrency(targets, DRAFT_TEST_CONCURRENCY, async (name) => {
      let result;
      try {
        result = await testUserModelDraft(draftModelPayload(form, name));
      } catch (err) {
        result = { ok: false, model: name, message: gatewayErrorText(errorMessage(err)) };
      }
      if (!result.ok) result = { ...result, message: withProtocolHint(result.message, form.provider) };
      if (version === formVersion.current) setDraftResults((current) => ({ ...current, [name]: result }));
    });
    setDraftTesting(false);
  }

  async function probeModelsDraft() {
    if (!form.base_url.trim() || !form.api_key.trim()) {
      setNotice('请先填写 base_url 和 api_key');
      return;
    }
    const version = ++modelProbeVersion.current;
    setProbing(true);
    setProbedModels([]);
    setProbeNotice(null);
    setProfileError('');
    setNotice('');
    try {
      const result = await probeUserModels({ provider: form.provider, base_url: form.base_url, api_key: form.api_key });
      if (version !== modelProbeVersion.current) return;
      const models = result.models || [];
      setProbedModels(models);
      if (models.length) setPickerOpen(true);
      // 🛡️ 成功也必须有可见反馈：模型此前只塞进 <datalist>，而候选项仅在聚焦输入框时才弹出，
      // 于是「拉取成功」与「什么都没发生」在界面上无法区分。这里给出明确计数与下一步提示。
      if (result.ok) {
        setProbeNotice({
          ok: true,
          text: models.length
            ? `已拉到 ${models.length} 个模型，在 chat_model 下拉中勾选（可多选）。`
            : '接口可用，但未返回任何模型，请在 chat_model 下拉中手动输入模型 ID。',
        });
      } else {
        setProbeNotice({ ok: false, text: withProtocolHint(gatewayErrorText(result.message), form.provider) || '拉取模型列表失败' });
      }
    } catch (err) {
      if (version === modelProbeVersion.current) setProbeNotice({ ok: false, text: gatewayErrorText(errorMessage(err)) });
    } finally {
      if (version === modelProbeVersion.current) setProbing(false);
    }
  }

  async function patchUserModel(config, patch) {
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      await updateUserModelConfig(config.id, patch);
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function submitEditUserModel(event) {
    event?.preventDefault();
    if (!editConfig || !editForm || !editForm.display_name.trim() || !editForm.chat_model.trim() || !editForm.base_url.trim()) return false;
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      const updated = await updateUserModelConfig(editConfig.id, userModelEditPayload(editForm));
      editGuard.markSaved();
      setEditConfig(null);
      setEditForm(null);
      setNotice(updated?.supports_image ? '模型已保存，图片探测通过。' : '模型已保存；图片探测未通过，但聊天发送不会被前端拦截。');
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function replaceKey(config, apiKey) {
    if (!String(apiKey || '').trim()) {
      setProfileError('API key cannot be empty');
      return;
    }
    if (await patchUserModel(config, { api_key: String(apiKey).trim() }) !== true) return false;
    setKeyDialogConfig(null);
    setNotice('API Key 已替换，页面不会显示已保存的密钥。');
    return true;
  }

  async function deleteUserModel(config) {
    const confirmed = await requestDeleteConfirm({
      title: '删除私有模型',
      message: `删除「${config.display_name || config.chat_model}」？`,
      detail: '如果已有智能体使用它，后端会保留该配置。',
      confirmLabel: '删除模型',
    });
    if (!confirmed) return;
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      await deleteUserModelConfig(config.id);
    } catch (err) {
      const message = errorMessage(err);
      if (message.toLowerCase().includes('model config is in use')) {
        setNotice('该模型配置正在被智能体使用，已保留。请先切换相关智能体模型，或改为停用。');
      } else {
        setProfileError(message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function testUserModel(config) {
    setTestingId(config.id);
    setNotice('');
    setProfileError('');
    try {
      const result = await testUserModelConfig(config.id);
      setTestResults((items) => ({ ...items, [config.id]: result }));
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <section className="plain-panel user-models-panel">
      <div className="panel-title-row">
        <div>
          <h3>我的模型</h3>
          <p>填写协议、地址、API Key 和模型名，测试通过后保存，即可在智能体配置里使用。</p>
        </div>
        <div className="panel-actions">
          <span className="soft-pill">{userModels.length} 个私有模型</span>
          <button className="primary-model-action" type="button" onClick={openCreateForm}><Plus size={15} />新增模型</button>
        </div>
      </div>
      {formOpen && (
        <div className="profile-dialog-backdrop">
          <section className="user-model-dialog" role="dialog" aria-modal="true" aria-label="新增模型" onClick={(event) => event.stopPropagation()}>
            <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭新增模型" onClick={closeCreateForm} disabled={saving || draftTesting}>
              <X size={16} />
            </button>
            <header className="model-dialog-heading">
              <h3>新增模型</h3>
              <p>任何 OpenAI 兼容或 Anthropic Messages 端点都可接入；运行温度等参数在智能体配置里决定。</p>
            </header>
            <form className="user-model-form" onSubmit={submitUserModel}>
              <div className="user-model-grid compact">
                <label className="field-stack">
                  <span>连接协议</span>
                  <select value={form.provider} onChange={(event) => updateForm({ provider: event.target.value })}>
                    <option value="openai-compatible">OpenAI-compatible（Chat Completions）</option>
                    <option value="anthropic">Anthropic（Messages）</option>
                  </select>
                </label>
                <label className="field-stack">
                  <span>chat_base_url</span>
                  <input value={form.base_url} onChange={(event) => updateForm({ base_url: event.target.value })} placeholder={form.provider === 'anthropic' ? 'https://api.anthropic.com/v1' : 'https://api.deepseek.com'} />
                </label>
                <label className="field-stack">
                  <span>chat_api_key</span>
                  <input type="password" value={form.api_key} onChange={(event) => updateForm({ api_key: event.target.value })} placeholder="只提交一次，不会回显" autoComplete="off" />
                </label>
                <div className="field-stack chat-model-field">
                  <span>chat_model</span>
                  <div className="chat-model-row">
                    <div className="model-multiselect" ref={pickerRef}>
                      <button type="button" className="model-multiselect-trigger" aria-label="chat_model" aria-haspopup="listbox"
                        aria-expanded={pickerOpen} onClick={() => setPickerOpen((open) => !open)}>
                        <span>{form.chat_models.length ? `已选 ${form.chat_models.length} 个模型` : '选择模型（可多选）'}</span>
                        <ChevronDown size={15} />
                      </button>
                      {pickerOpen && (
                        <div className="model-multiselect-panel">
                          <input aria-label="筛选模型" value={modelQuery} autoFocus placeholder="筛选，或输入模型 ID 后回车添加"
                            onChange={(event) => setModelQuery(event.target.value)}
                            onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addQueryModel(); } }} />
                          <div className="model-multiselect-actions">
                            <button type="button" disabled={!selectableOptions.length}
                              onClick={() => setSelectedModels([...form.chat_models, ...selectableOptions])}>全选{query ? '筛选结果' : ''}</button>
                            <button type="button" disabled={!form.chat_models.length} onClick={() => setSelectedModels([])}>清空</button>
                          </div>
                          <ul role="listbox" aria-label="可选模型" aria-multiselectable="true">
                            {canAddQuery && (
                              <li><button type="button" className="model-multiselect-add" onClick={addQueryModel}>添加「{query}」</button></li>
                            )}
                            {modelOptions.map((name) => {
                              const registered = registeredModels.has(name);
                              return (
                                <li key={name} role="option" aria-selected={form.chat_models.includes(name)}>
                                  <label className={registered ? 'disabled' : ''}>
                                    <input type="checkbox" checked={form.chat_models.includes(name)} disabled={registered} onChange={() => toggleModel(name)} />
                                    <span>{name}</span>
                                    {registered && <small>已注册</small>}
                                  </label>
                                </li>
                              );
                            })}
                            {!modelOptions.length && !canAddQuery && (
                              <li className="model-multiselect-empty">{probedModels.length ? '没有匹配的模型' : '先点「拉取模型」，或直接输入模型 ID 回车添加'}</li>
                            )}
                          </ul>
                        </div>
                      )}
                    </div>
                    <button type="button" disabled={probing || !form.base_url.trim() || !form.api_key.trim()} onClick={probeModelsDraft}>{probing ? '拉取中' : '拉取模型'}</button>
                  </div>
                </div>
              </div>
              {probeNotice && (
                <p className={probeNotice.ok ? 'model-probe-hint ok' : 'model-probe-hint error'} role="status">{probeNotice.text}</p>
              )}
              {form.chat_models.length > 0 && (
                <SelectedModelRows
                  models={form.chat_models}
                  capabilities={form.capabilities}
                  results={draftResults}
                  testing={draftTesting}
                  onChange={updateCapability}
                  onRemove={toggleModel}
                />
              )}
              <div className="model-checks state-checks">
                <label><input type="checkbox" checked={form.is_default} onChange={(event) => updateForm({ is_default: event.target.checked })} />设为默认（多选时只给第一个）</label>
              </div>
              {formError && <p className="model-probe-hint error" role="alert">{formError}</p>}
              {formReady && pendingModels.length > 0 && !draftTesting && <p className="model-row-warning">保存前请先点击“测试连接”，只检查地址、Key 和模型名能否连通。能力按模型名预填，可在上表修改；图片附件不会被前端拦截。</p>}
              <div className="model-form-actions">
                <button className="preset-action" type="button" disabled={draftTesting || saving || !formReady} onClick={testDraftModel}>{draftTesting ? '测试中' : '测试连接'}</button>
                <button className="primary-model-action" type="submit" disabled={saving || draftTesting || !canSaveForm}>
                  <Plus size={15} />{passedModels.length > 1 ? `保存 ${passedModels.length} 个模型` : '保存私有模型'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
      {notice && <p className="model-row-warning">{notice}</p>}
      <div className="user-model-list">
        {userModels.map((config) => (
            <div className="user-model-row" key={config.id}>
              <div className="model-admin-main">
                <strong>{config.display_name || config.chat_model}</strong>
                <small>{config.chat_model} · {config.base_url}</small>
                <div className="model-row-tags">
                  <span>{config.provider === 'anthropic' ? 'Anthropic Messages' : 'OpenAI-compatible'}</span>
                  <span className={config.enabled ? 'enabled' : ''}>{config.enabled ? '启用' : '停用'}</span>
                  <span className={config.is_default ? 'enabled' : ''}>{config.is_default ? '默认' : '非默认'}</span>
                  <span>{config.has_api_key ? 'Key 已保存' : '缺少 Key'}</span>
                  {modelCapabilityChips(config).map((label) => (
                    <span className="enabled" key={label}>{label}</span>
                  ))}
                </div>
              </div>
              <div className="user-model-actions">
                <button type="button" disabled={saving} onClick={() => patchUserModel(config, { enabled: !config.enabled })}>{config.enabled ? '停用' : '启用'}</button>
                <button type="button" disabled={saving || config.is_default} onClick={() => patchUserModel(config, { is_default: true })}>设默认</button>
                <button type="button" disabled={saving} onClick={() => openEditForm(config)}>编辑</button>
                <button type="button" disabled={saving} onClick={() => setKeyDialogConfig(config)}>替换 Key</button>
                <button className="model-delete-button" type="button" disabled={saving} onClick={() => deleteUserModel(config)}><Trash2 size={14} />删除</button>
              </div>
            </div>
        ))}
        {userModels.length === 0 && <p className="muted">还没有私有模型配置。点「新增模型」填写地址和 API Key，拉取后可一次勾选多个模型注册。</p>}
      </div>
      {keyDialogConfig && (
        <SecretInputDialog
          label="API Key"
          message={`替换「${keyDialogConfig.display_name || keyDialogConfig.chat_model}」的 API Key。新 key 只提交一次，保存后不回显。`}
          onCancel={() => !saving && setKeyDialogConfig(null)}
          onSubmit={(value) => replaceKey(keyDialogConfig, value).catch((err) => setProfileError(errorMessage(err)))}
          saving={saving}
          submitLabel="替换 Key"
          title="替换模型 API Key"
        />
      )}
      {editConfig && editForm && (
        <div className="profile-dialog-backdrop">
          <section className="user-model-dialog" role="dialog" aria-modal="true" aria-label="编辑模型" onClick={(event) => event.stopPropagation()}>
            <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭编辑模型" onClick={closeEditForm} disabled={saving}>
              <X size={16} />
            </button>
            <header className="model-dialog-heading">
              <h3>编辑模型</h3>
              <p>修改模型地址和名称。API Key 不回显，需要用“替换 Key”单独更新。</p>
            </header>
            <form className="user-model-form" onSubmit={submitEditUserModel}>
              <div className="model-channel-card">
                <div className="model-channel-heading">
                  <strong>{editConfig.display_name || editConfig.chat_model}</strong>
                  <small>测试连接只检查能否连通；能力由你声明，运行参数由具体智能体决定。</small>
                </div>
                <div className="user-model-grid compact">
                  <label className="field-stack">
                    <span>连接协议</span>
                    <select value={editForm.provider} onChange={(event) => updateEditForm({ provider: event.target.value })}>
                      <option value="openai-compatible">OpenAI-compatible（Chat Completions）</option>
                      <option value="anthropic">Anthropic（Messages）</option>
                    </select>
                  </label>
                  <label className="field-stack">
                    <span>显示名称</span>
                    <input value={editForm.display_name} onChange={(event) => updateEditForm({ display_name: event.target.value })} />
                  </label>
                  <label className="field-stack">
                    <span>chat_base_url</span>
                    <input value={editForm.base_url} onChange={(event) => updateEditForm({ base_url: event.target.value })} />
                  </label>
                  <label className="field-stack">
                    <span>chat_model</span>
                    <input value={editForm.chat_model} onChange={(event) => updateEditForm({ chat_model: event.target.value })} />
                  </label>
                </div>
                <div className="model-checks state-checks">
                  <label><input type="checkbox" checked={Boolean(editForm.supports_image)} onChange={(event) => updateEditForm({ supports_image: event.target.checked })} />支持图片</label>
                  <label>
                    深度思考
                    <select aria-label="深度思考" value={editForm.reasoning_type} onChange={(event) => updateEditForm({ reasoning_type: event.target.value, supports_reasoning: event.target.value !== 'none' })}>
                      <option value="native">原生深度思考</option>
                      <option value="prompt">提示词增强</option>
                      <option value="none">不支持</option>
                    </select>
                  </label>
                  <label><input type="checkbox" checked={editForm.supports_document} onChange={(event) => updateEditForm({ supports_document: event.target.checked })} />文档附件后端解析</label>
                  <label><input type="checkbox" checked={editForm.enabled} onChange={(event) => updateEditForm({ enabled: event.target.checked })} />启用</label>
                  <label><input type="checkbox" checked={editForm.is_default} onChange={(event) => updateEditForm({ is_default: event.target.checked })} />设为默认</label>
                </div>
                <div className="model-capability-summary">
                  <span className="enabled">文本</span>
                  <span className={editForm.supports_image ? 'enabled' : ''}>{editForm.supports_image ? '图片' : '不支持图片'}</span>
                  <span className={editForm.supports_document ? 'enabled' : ''}>文档附件后端解析</span>
                  <span className={reasoningCapabilityForModel(editForm).supported ? 'enabled' : ''}>{reasoningCapabilityForModel(editForm).label}</span>
                </div>
              </div>
              <div className="model-form-actions">
                <button type="button" disabled={saving} onClick={closeEditForm}>取消</button>
                <button className="preset-action" type="button" disabled={saving || testingId === editConfig.id} onClick={() => testUserModel(editConfig)}>
                  {testingId === editConfig.id ? '测试中...' : '测试连接'}
                </button>
                <button className="primary-model-action" type="submit" disabled={saving || !editForm.display_name.trim() || !editForm.base_url.trim() || !editForm.chat_model.trim()}>
                  <Check size={15} />保存修改
                </button>
              </div>
              {testResults[editConfig.id] && <UserModelTestResult result={testResults[editConfig.id]} />}
            </form>
          </section>
        </div>
      )}
    </section>
  );
}

function SelectedModelRows({ models, capabilities, results, testing, onChange, onRemove }) {
  return (
    <div className="selected-model-rows" role="table" aria-label="已选模型">
      <div className="selected-model-row head" role="row">
        <span role="columnheader">模型</span>
        <span role="columnheader">图片</span>
        <span role="columnheader">深度思考</span>
        <span role="columnheader">连接</span>
        <span />
      </div>
      {models.map((name) => {
        const capability = capabilities[name] || guessModelCapabilities(name);
        const result = results[name];
        return (
          <div className="selected-model-row" role="row" key={name}>
            <strong role="cell" title={name}>{name}</strong>
            <span role="cell">
              <input type="checkbox" aria-label={`${name} 支持图片`} checked={Boolean(capability.supports_image)}
                onChange={(event) => onChange(name, { supports_image: event.target.checked })} />
            </span>
            <span role="cell">
              <select aria-label={`${name} 深度思考`} value={capability.reasoning_type}
                onChange={(event) => onChange(name, { reasoning_type: event.target.value })}>
                <option value="native">原生</option>
                <option value="prompt">提示词增强</option>
                <option value="none">不支持</option>
              </select>
            </span>
            <span role="cell" className={result ? (result.ok ? 'ok' : 'fail') : ''}>
              {result ? (result.ok ? `✓ ${result.latency_ms ?? '-'}ms` : '✗ 未通过') : (testing ? '测试中…' : '未测试')}
            </span>
            <button type="button" aria-label={`移除 ${name}`} onClick={() => onRemove(name)}><X size={13} /></button>
            {result && !result.ok && result.message && <small className="selected-model-error">{result.message}</small>}
          </div>
        );
      })}
    </div>
  );
}

function UserModelTestResult({ result }) {
  return (
    <div className={result.ok ? 'user-model-test ok' : 'user-model-test'}>
      <strong>{result.ok ? '连接成功' : '连接失败'} · {result.model} · {result.latency_ms}ms</strong>
      {!result.ok && <small>{result.error_code ? `${result.error_code} · ` : ''}{result.message}</small>}
    </div>
  );
}

const QWEN_MODEL_PRESET = {
  display_name: 'Qwen Plus',
  model_name: 'qwen-plus',
  provider: 'openai-compatible',
  max_context: '131072',
  default_temperature: 0.4,
  supports_text: true,
  supports_image: false,
  supports_document: true,
  supports_reasoning: true,
  reasoning_type: 'prompt',
  reasoning_label: '提示词增强',
  enabled: true,
};

const MODEL_CAPABILITY_PRESETS = {
  qwen_plus: {
    label: 'Qwen 文本',
    values: QWEN_MODEL_PRESET,
  },
  text_document: {
    label: '文本模型',
    values: { supports_text: true, supports_image: false, supports_document: true, supports_reasoning: true, reasoning_type: 'prompt', reasoning_label: 'prompt' },
  },
  vision_document: {
    label: '视觉模型',
    values: { supports_text: true, supports_image: true, supports_document: true, supports_reasoning: true, reasoning_type: 'prompt', reasoning_label: 'prompt' },
  },
  text_only: {
    label: '\u7eaf\u6587\u672c',
    values: { supports_text: true, supports_image: false, supports_document: true, supports_reasoning: false, reasoning_type: 'none', reasoning_label: 'none' },
  },
  custom: {
    label: '\u81ea\u5b9a\u4e49',
    values: {},
  },
};

function createModelForm() {
  return { ...QWEN_MODEL_PRESET, preset: 'qwen_plus' };
}

function ModelAdminPanel({ createModelConfig, deleteModelConfig, models, requestDeleteConfirm, setProfileError, updateModelConfig }) {
  const [form, setForm] = useState(createModelForm);
  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rowNotice, setRowNotice] = useState({ modelId: null, message: '' });
  const formGuard = useUnsavedForm({ enabled: formOpen, value: form, label: '系统模型配置', busy: saving,
    onSave: () => createModel() });

  function applyPreset(preset) {
    const values = MODEL_CAPABILITY_PRESETS[preset]?.values || {};
    setForm((current) => ({
      ...current,
      ...values,
      preset,
    }));
  }

  async function createModel(event) {
    event?.preventDefault();
    if (!form.display_name.trim() || !form.model_name.trim()) return false;
    setSaving(true);
    setProfileError('');
    setRowNotice({ modelId: null, message: '' });
    try {
      await createModelConfig(modelFormPayload(form));
      formGuard.markSaved();
      setForm(createModelForm());
      setFormOpen(false);
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  function openCreateModel() {
    setForm(createModelForm());
    setRowNotice({ modelId: null, message: '' });
    setProfileError('');
    setFormOpen(true);
  }

  function closeCreateModel() {
    if (saving) return;
    formGuard.confirmLeave(() => setFormOpen(false));
  }

  async function patchModel(model, patch) {
    setSaving(true);
    setProfileError('');
    setRowNotice({ modelId: null, message: '' });
    try {
      await updateModelConfig(model.id, patch);
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function deleteModel(model) {
    const label = model.display_name || model.model_name;
    const confirmed = await requestDeleteConfirm({
      title: '删除系统模型',
      message: `确定删除「${label}」？`,
      detail: '删除只适用于未被引用的自定义模型；默认模型或已被智能体使用的模型会被后端保留，请改用停用。',
      confirmLabel: '删除模型',
    });
    if (!confirmed) return;
    setSaving(true);
    setProfileError('');
    setRowNotice({ modelId: null, message: '' });
    try {
      await deleteModelConfig(model.id);
    } catch (err) {
      const message = errorMessage(err);
      const normalizedMessage = message.toLowerCase();
      setRowNotice({ modelId: model.id, message: modelDeleteGuidance(message) });
      if (!normalizedMessage.includes('model is protected') && !normalizedMessage.includes('model is in use')) {
        setProfileError(message);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="plain-panel model-admin-panel">
      <div className="panel-title-row">
        <div>
          <h3>模型管理</h3>
          <p>管理员维护可被智能体选择的模型，以及文本、图片等对话输入能力。文档附件由后端解析成文本。</p>
        </div>
        <div className="panel-actions">
          <span className="soft-pill">{models.length} 个模型</span>
          <button className="primary-model-action" type="button" onClick={openCreateModel}><Plus size={15} />新增系统模型</button>
        </div>
      </div>
      {formOpen && (
        <div className="profile-dialog-backdrop">
          <section className="user-model-dialog" role="dialog" aria-modal="true" aria-label="新增系统模型" onClick={(event) => event.stopPropagation()}>
            <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭新增系统模型" onClick={closeCreateModel} disabled={saving}>
              <X size={16} />
            </button>
            <header className="model-dialog-heading">
              <h3>新增系统模型</h3>
              <p>管理员维护平台内置模型能力，普通用户仍优先使用自己的模型配置。</p>
            </header>
            <form className="model-admin-form" onSubmit={createModel}>
              <div className="model-form-section">
                <div className="model-section-title">
                  <strong>基础字段</strong>
                  <span>普通管理员只需要确认名称、能力预设和启用状态。</span>
                </div>
                <div className="model-basic-grid">
                  <label className="field-stack">
                    <span>连接协议</span>
                    <select value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value })}>
                      <option value="openai-compatible">OpenAI-compatible</option>
                      <option value="anthropic">Anthropic Messages（使用服务器 Anthropic 配置）</option>
                    </select>
                  </label>
                  <label className="field-stack">
                    <span>显示名称</span>
                    <input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Qwen Plus" />
                  </label>
                  <label className="field-stack">
                    <span>模型名</span>
                    <input value={form.model_name} onChange={(event) => setForm({ ...form, model_name: event.target.value })} placeholder="qwen-plus" />
                  </label>
                  <label className="field-stack">
                    <span>能力预设</span>
                    <select value={form.preset} onChange={(event) => applyPreset(event.target.value)}>
                      {Object.entries(MODEL_CAPABILITY_PRESETS).map(([value, preset]) => (
                        <option key={value} value={value}>{preset.label}</option>
                      ))}
                    </select>
                  </label>
                  <div className="model-capability-summary compact">
                    <span className={form.supports_text ? 'enabled' : ''}>文本</span>
                    <span className={form.supports_image ? 'enabled' : ''}>{form.supports_image ? '图片声明' : '图片未声明'}</span>
                    <span className="enabled">文档附件后端解析</span>
                    <span className={reasoningCapabilityForModel(form).supported ? 'enabled' : ''}>{reasoningCapabilityForModel(form).label}</span>
                  </div>
                  <label className="model-enabled-check">
                    <input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />
                    <span>
                      <strong>启用</strong>
                      <small>出现在智能体模型选择中。</small>
                    </span>
                  </label>
                </div>
                <div className="model-preset-banner">
                  <Wand2 size={16} />
                  <span>
                    <strong>Qwen 快捷预设</strong>
                    <small>填入 qwen-plus、OpenAI-compatible 和常用能力声明。运行温度在智能体配置里决定。</small>
                  </span>
                  <button type="button" className="preset-action" disabled={saving} onClick={() => applyPreset('qwen_plus')}>填入 qwen-plus</button>
                </div>
              </div>
              <div className="model-form-actions">
                <button className="primary-model-action" type="submit" disabled={saving || !form.display_name.trim() || !form.model_name.trim()}><Plus size={15} />保存系统模型</button>
              </div>
            </form>
          </section>
        </div>
      )}
      <div className="model-admin-list">
        {models.map((model) => (
          <div className="model-admin-row" key={model.id}>
            <div className="model-admin-main">
              <strong>{model.display_name || model.model_name}</strong>
              <small>{model.model_name} · {model.provider}</small>
              <div className="model-row-tags">
                <span className={model.enabled ? 'enabled' : ''}>{model.enabled ? '启用' : '已停用'}</span>
                {modelCapabilityChips(model).map((label) => (
                  <span className="enabled" key={label}>{label}</span>
                ))}
              </div>
              {rowNotice.modelId === model.id && <p className="model-row-warning">{rowNotice.message}</p>}
            </div>
            <div className="model-admin-actions">
              <button type="button" className={model.enabled ? 'model-state-toggle enabled' : 'model-state-toggle'} disabled={saving} onClick={() => patchModel(model, { enabled: !model.enabled })}>{model.enabled ? '停用' : '启用'}</button>
              <button className="model-delete-button" type="button" title="删除模型配置" disabled={saving} onClick={() => deleteModel(model)}><Trash2 size={14} />删除</button>
            </div>
          </div>
        ))}
        {models.length === 0 && <p className="muted">还没有模型配置。</p>}
      </div>
    </section>
  );
}

function modelFormPayload(form) {
  const maxContext = Number(form.max_context);
  const temperature = Number(form.default_temperature);
  return {
    display_name: form.display_name.trim(),
    model_name: form.model_name.trim(),
    provider: form.provider.trim() || 'openai-compatible',
    supports_text: Boolean(form.supports_text),
    supports_image: Boolean(form.supports_image),
    supports_document: Boolean(form.supports_document),
    supports_reasoning: Boolean(form.supports_reasoning) && (form.reasoning_type || 'none') !== 'none',
    reasoning_type: form.reasoning_type || 'none',
    reasoning_label: reasoningLabel(form.reasoning_type || 'none'),
    max_context: Number.isFinite(maxContext) && maxContext > 0 ? maxContext : 8192,
    default_temperature: Number.isFinite(temperature) ? temperature : 0.4,
    enabled: Boolean(form.enabled),
  };
}


function modelDeleteGuidance(message) {
  const normalizedMessage = message.toLowerCase();
  if (normalizedMessage.includes('model is protected')) {
    return '该模型是默认模型或最后一个可用文本模型，后端已保留该行。请先新增可用模型，或对不再使用的模型执行停用。';
  }
  if (normalizedMessage.includes('model is in use')) {
    return '该模型已被智能体或已发布版本引用，后端已保留该行。请改用停用，让它从新建选择列表隐藏，同时保留历史记录。';
  }
  return message;
}



















