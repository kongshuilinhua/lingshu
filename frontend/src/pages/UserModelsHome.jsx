import React, { useState } from 'react';
import { Check, Plus, ServerCog, Trash2, Wand2, X } from 'lucide-react';
import { SecretInputDialog } from '../components/SecretInputDialog.jsx';
import {
  capabilityCheckLabel,
  errorMessage,
  imageCapabilityFromTest,
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
          <p>维护你自己的 OpenAI-compatible 模型连接，保存后可在智能体配置里选择。</p>
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



const USER_MODEL_PRESETS = [
  {
    id: 'qwen',
    label: 'DashScope / Qwen',
    modelHint: 'qwen-plus',
    description: '阿里云百炼，推荐默认入口，支持通义千问；图片能力由测试自动检测，RAG/Embedding 由后端默认配置提供。',
    values: {
      display_name: 'Qwen Plus',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      chat_model: 'qwen-plus',
      supports_image: false,
      supports_document: true,
      supports_reasoning: true,
      reasoning_type: 'prompt',
      reasoning_label: '提示词增强',
      max_context: '131072',
      default_temperature: '0.4',
    },
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    modelHint: 'deepseek-v4-flash',
    description: 'DeepSeek 官方兼容接口。这里只配置主聊天模型；知识库检索使用后端默认 Embedding。',
    values: {
      display_name: 'DeepSeek V4 Flash',
      base_url: 'https://api.deepseek.com',
      chat_model: 'deepseek-v4-flash',
      supports_image: false,
      supports_document: false,
      supports_reasoning: true,
      reasoning_type: 'prompt',
      reasoning_label: '提示词增强',
      max_context: '64000',
      default_temperature: '0.6',
    },
  },
  {
    id: 'kimi',
    label: 'Kimi / Moonshot',
    modelHint: 'moonshot-v1-8k',
    description: '月之暗面兼容接口。模型名可按控制台改成最新 Kimi 模型。',
    values: {
      display_name: 'Kimi Moonshot',
      base_url: 'https://api.moonshot.cn/v1',
      chat_model: 'moonshot-v1-8k',
      supports_image: false,
      supports_document: false,
      supports_reasoning: true,
      reasoning_type: 'prompt',
      reasoning_label: '提示词增强',
      max_context: '32768',
      default_temperature: '0.4',
    },
  },
  {
    id: 'zhipu',
    label: '智谱 GLM',
    modelHint: 'glm-4-flash',
    description: '智谱 BigModel 兼容接口。适合 GLM 系列文本模型。',
    values: {
      display_name: 'GLM',
      base_url: 'https://open.bigmodel.cn/api/paas/v4',
      chat_model: 'glm-4-flash',
      supports_image: false,
      supports_document: true,
      supports_reasoning: true,
      reasoning_type: 'prompt',
      reasoning_label: '提示词增强',
      max_context: '128000',
      default_temperature: '0.4',
    },
  },
  {
    id: 'volcengine',
    label: '火山方舟 / 豆包',
    modelHint: 'ep-xxxxxxxx',
    description: '火山方舟兼容接口，model 通常填写控制台创建的 endpoint id。',
    values: {
      display_name: 'Doubao Ark',
      base_url: 'https://ark.cn-beijing.volces.com/api/v3',
      chat_model: 'ep-xxxxxxxx',
      supports_image: false,
      supports_document: true,
      supports_reasoning: false,
      reasoning_type: 'none',
      reasoning_label: '不支持',
      max_context: '128000',
      default_temperature: '0.4',
    },
  },
  {
    id: 'qianfan',
    label: '百度千帆 / ERNIE',
    modelHint: 'ernie-4.5-turbo-128k',
    description: '百度智能云千帆兼容接口。保存前按控制台可用模型名调整。',
    values: {
      display_name: 'ERNIE',
      base_url: 'https://qianfan.baidubce.com/v2',
      chat_model: 'ernie-4.5-turbo-128k',
      supports_image: false,
      supports_document: true,
      supports_reasoning: true,
      reasoning_type: 'prompt',
      reasoning_label: '提示词增强',
      max_context: '128000',
      default_temperature: '0.4',
    },
  },
  {
    id: 'siliconflow',
    label: '硅基流动',
    modelHint: 'Qwen/Qwen3-32B',
    description: '聚合国产和开源模型，模型名建议从控制台复制。',
    values: {
      display_name: 'SiliconFlow Qwen',
      base_url: 'https://api.siliconflow.cn/v1',
      chat_model: 'Qwen/Qwen3-32B',
      supports_image: false,
      supports_document: true,
      supports_reasoning: true,
      reasoning_type: 'prompt',
      reasoning_label: '提示词增强',
      max_context: '32768',
      default_temperature: '0.5',
    },
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    modelHint: 'deepseek/deepseek-chat',
    description: '海外聚合网关。建议使用非 GPT 模型作为默认配置。',
    values: {
      display_name: 'OpenRouter DeepSeek',
      base_url: 'https://openrouter.ai/api/v1',
      chat_model: 'deepseek/deepseek-chat',
      supports_image: false,
      supports_document: false,
      supports_reasoning: true,
      reasoning_type: 'prompt',
      reasoning_label: '提示词增强',
      max_context: '64000',
      default_temperature: '0.6',
    },
  },
  {
    id: 'ollama',
    label: 'Ollama 本机',
    modelHint: 'qwen2.5:7b',
    description: '本机开发可用。Docker 内运行 API 时，通常要把 127.0.0.1 改成 host.docker.internal。',
    values: {
      display_name: 'Local Ollama',
      base_url: 'http://127.0.0.1:11434/v1',
      chat_model: 'qwen2.5:7b',
      supports_image: false,
      supports_document: true,
      supports_reasoning: false,
      reasoning_type: 'none',
      reasoning_label: '不支持',
      max_context: '32768',
      default_temperature: '0.4',
    },
  },
  {
    id: 'custom',
    label: '自定义兼容网关',
    modelHint: '填写控制台模型名',
    description: '适用于私有部署、代理网关、One API、LiteLLM、New API 或 OpenAI-compatible 服务。',
    values: {
      display_name: 'Custom Model',
      base_url: '',
      chat_model: '',
      supports_image: false,
      supports_document: false,
      supports_reasoning: false,
      reasoning_type: 'none',
      reasoning_label: '不支持',
      max_context: '32768',
      default_temperature: '0.4',
    },
  },
];

const USER_MODEL_PRESET_MAP = Object.fromEntries(USER_MODEL_PRESETS.map((preset) => [preset.id, preset]));

function createUserModelForm(presetId = 'qwen') {
  const preset = USER_MODEL_PRESET_MAP[presetId] || USER_MODEL_PRESET_MAP.qwen;
  return {
    provider: 'openai-compatible',
    api_key: '',
    enabled: true,
    is_default: true,
    preset_id: preset.id,
    ...preset.values,
  };
}

function userModelEditForm(config) {
  return {
    display_name: config.display_name || '',
    provider: config.provider || 'openai-compatible',
    base_url: config.base_url || '',
    chat_model: config.chat_model || '',
    supports_document: config.supports_document !== false,
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
  const [draftTestResult, setDraftTestResult] = useState(null);
  const [testingId, setTestingId] = useState(null);
  const [testResults, setTestResults] = useState({});
  const [keyDialogConfig, setKeyDialogConfig] = useState(null);
  const [editConfig, setEditConfig] = useState(null);
  const [editForm, setEditForm] = useState(null);
  const [probedModels, setProbedModels] = useState([]);
  const [probing, setProbing] = useState(false);
  const activePreset = USER_MODEL_PRESET_MAP[form.preset_id] || USER_MODEL_PRESET_MAP.custom;
  const formReady = Boolean(form.display_name.trim() && form.base_url.trim() && form.chat_model.trim() && form.api_key.trim());
  const canSaveForm = formReady && draftTestResult?.ok;
  const imageProbeStatus = imageCapabilityFromTest(form, draftTestResult);

  function updateForm(patch) {
    setDraftTestResult(null);
    setForm((current) => ({ ...current, ...patch }));
  }

  function applyPreset(presetId) {
    const preset = USER_MODEL_PRESET_MAP[presetId] || USER_MODEL_PRESET_MAP.custom;
    setDraftTestResult(null);
    setForm((current) => ({
      ...current,
      ...preset.values,
      provider: 'openai-compatible',
      preset_id: preset.id,
      api_key: current.api_key || '',
    }));
  }

  function openCreateForm() {
    setForm(createUserModelForm());
    setDraftTestResult(null);
    setNotice('');
    setProfileError('');
    setFormOpen(true);
  }

  function closeCreateForm() {
    if (saving || draftTesting) return;
    setFormOpen(false);
    setDraftTestResult(null);
  }

  function openEditForm(config) {
    setEditConfig(config);
    setEditForm(userModelEditForm(config));
    setNotice('');
    setProfileError('');
  }

  function closeEditForm() {
    if (saving) return;
    setEditConfig(null);
    setEditForm(null);
  }

  function updateEditForm(patch) {
    setEditForm((current) => ({ ...current, ...patch }));
  }

  async function submitUserModel(event) {
    event.preventDefault();
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      const saved = await createUserModelConfig(userModelFormPayload(form, { includeApiKey: true }));
      setForm(createUserModelForm());
      setFormOpen(false);
      setDraftTestResult(null);
      setNotice(saved?.supports_image ? '模型连接已保存，图片探测通过。' : '模型连接已保存；图片探测未通过，但聊天发送不会被前端拦截。');
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function testDraftModel() {
    setDraftTesting(true);
    setNotice('');
    setProfileError('');
    setDraftTestResult(null);
    try {
      const result = await testUserModelDraft({ ...userModelFormPayload(form, { includeApiKey: true }), detect_image: true });
      setDraftTestResult(result);
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setDraftTesting(false);
    }
  }

  async function probeModelsDraft() {
    if (!form.base_url.trim() || !form.api_key.trim()) {
      setNotice('请先填写 base_url 和 api_key');
      return;
    }
    setProbing(true);
    setNotice('');
    try {
      const result = await probeUserModels({ base_url: form.base_url, api_key: form.api_key });
      setProbedModels(result.models || []);
      if (!result.ok) setNotice(result.message || '拉取模型列表失败');
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setProbing(false);
    }
  }

  async function patchUserModel(config, patch) {
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      const updated = await updateUserModelConfig(config.id, patch);
      if (updated?.image_detection?.tested) {
        setNotice(updated.supports_image ? '图片探测通过。' : '图片探测未通过；这只是诊断结果，不会拦截图片发送。');
      }
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function submitEditUserModel(event) {
    event.preventDefault();
    if (!editConfig || !editForm) return;
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      const updated = await updateUserModelConfig(editConfig.id, userModelEditPayload(editForm));
      setEditConfig(null);
      setEditForm(null);
      setNotice(updated?.supports_image ? '模型已保存，图片探测通过。' : '模型已保存；图片探测未通过，但聊天发送不会被前端拦截。');
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function replaceKey(config, apiKey) {
    if (!String(apiKey || '').trim()) {
      setProfileError('API key cannot be empty');
      return;
    }
    await patchUserModel(config, { api_key: String(apiKey).trim() });
    setKeyDialogConfig(null);
    setNotice('API Key 已替换，页面不会显示已保存的密钥。');
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
      if (result?.detected_capabilities) {
        const patch = {};
        const detectedReasoningSupport = result.detected_capabilities.supports_reasoning;
        if (typeof detectedReasoningSupport === 'boolean' && detectedReasoningSupport !== Boolean(config.supports_reasoning)) {
          patch.supports_reasoning = detectedReasoningSupport;
          patch.reasoning_type = detectedReasoningSupport ? (result.detected_capabilities.reasoning_type || 'prompt') : 'none';
          patch.reasoning_label = reasoningLabel(patch.reasoning_type);
        }
        if (Object.keys(patch).length) {
          await updateUserModelConfig(config.id, patch);
        }
      }
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
          <p>选择常见兼容网关预设，填入 API Key 和模型名，保存后即可在智能体配置里使用。</p>
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
              <p>选择厂商预设，填入 API Key 和模型名；运行温度等参数在智能体配置里决定。</p>
            </header>
            <form className="user-model-form" onSubmit={submitUserModel}>
              <div className="provider-preset-panel">
                <div className="provider-preset-header">
                  <ServerCog size={16} />
                  <span>
                    <strong>模型厂商预设</strong>
                    <small>预设填地址、常用模型名和显式能力；图片测试只作为诊断结果。</small>
                  </span>
                </div>
                <div className="provider-preset-grid">
                  {USER_MODEL_PRESETS.map((preset) => (
                    <button
                      type="button"
                      key={preset.id}
                      className={form.preset_id === preset.id ? 'active' : ''}
                      disabled={saving}
                      onClick={() => applyPreset(preset.id)}
                    >
                      <strong>{preset.label}</strong>
                      <small>{preset.modelHint}</small>
                    </button>
                  ))}
                </div>
                <p className="provider-preset-note">{activePreset.description}</p>
              </div>
              <div className="model-channel-grid">
                <div className="model-channel-card">
                  <div className="model-channel-heading">
                    <strong>聊天模型</strong>
                    <small>用于 Agent 对话和工具推理；这里只维护连接与基础能力。</small>
                  </div>
                  <div className="user-model-grid compact">
                    <label className="field-stack">
                      <span>显示名称</span>
                       <input value={form.display_name} onChange={(event) => updateForm({ display_name: event.target.value, preset_id: form.preset_id || 'custom' })} placeholder="Qwen Plus" />
                    </label>
                    <label className="field-stack">
                      <span>chat_base_url</span>
                      <input value={form.base_url} onChange={(event) => updateForm({ base_url: event.target.value, preset_id: 'custom' })} placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
                    </label>
                    <label className="field-stack">
                      <span>chat_api_key</span>
                      <input type="password" value={form.api_key} onChange={(event) => updateForm({ api_key: event.target.value })} placeholder="只提交一次，不会回显" autoComplete="off" />
                    </label>
                    <label className="field-stack">
                      <span>chat_model</span>
                      <div className="chat-model-row" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <input value={form.chat_model} onChange={(event) => updateForm({ chat_model: event.target.value, preset_id: form.preset_id || 'custom' })} placeholder="qwen-plus" list="probed-models" style={{ flex: 1 }} />
                        <datalist id="probed-models">
                          {probedModels.map((m) => <option key={m} value={m} />)}
                        </datalist>
                        <button type="button" disabled={probing || !form.base_url.trim() || !form.api_key.trim()} onClick={probeModelsDraft}>{probing ? '拉取中' : '拉取模型'}</button>
                      </div>
                    </label>
                  </div>
                </div>
              </div>
              <label className="model-enabled-check inline">
                <input type="checkbox" checked disabled />
                <span>
                  <strong>图片测试仅用于诊断</strong>
                  <small>保存和测试会向模型发送最小图片请求；无论探测结果如何，聊天发送都不会被前端拦截。</small>
                </span>
              </label>
              <div className="model-capability-summary">
                <span className="enabled">文本</span>
                <span className={imageProbeStatus.className}>{imageProbeStatus.label}</span>
                <span className="enabled">文档附件后端解析</span>
                <span className={reasoningCapabilityForModel(form).supported ? 'enabled' : ''}>{reasoningCapabilityForModel(form).label}</span>
                <small>图片附件会随聊天请求发送；如果网关或模型不支持，会显示真实返回结果。</small>
              </div>
              <div className="model-checks state-checks">
                <label><input type="checkbox" checked={form.enabled} onChange={(event) => updateForm({ enabled: event.target.checked })} />启用</label>
                <label><input type="checkbox" checked={form.is_default} onChange={(event) => updateForm({ is_default: event.target.checked })} />设为默认</label>
              </div>
              {formReady && !draftTestResult?.ok && <p className="model-row-warning">保存前请先点击“测试当前配置”。图片探测只作诊断；文档附件由后端解析成文本，RAG/Embedding 使用后端默认配置。</p>}
              {draftTestResult && <UserModelTestResult result={draftTestResult} />}
              <div className="model-form-actions">
                <button className="preset-action" type="button" disabled={draftTesting || saving || !formReady} onClick={testDraftModel}>{draftTesting ? '检测中' : '测试当前配置'}</button>
                <button className="primary-model-action" type="submit" disabled={saving || !canSaveForm}><Plus size={15} />保存私有模型</button>
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
        {userModels.length === 0 && <p className="muted">还没有私有模型配置。选择一个厂商预设，填入 API Key，保存后就可以在智能体配置里选择它。</p>}
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
                  <small>测试连接会检查 chat 和图片请求；运行参数由具体智能体决定。</small>
                </div>
                <div className="user-model-grid compact">
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
                  <label><input type="checkbox" checked={editForm.supports_document} onChange={(event) => updateEditForm({ supports_document: event.target.checked })} />文档附件后端解析</label>
                  <label><input type="checkbox" checked={editForm.enabled} onChange={(event) => updateEditForm({ enabled: event.target.checked })} />启用</label>
                  <label><input type="checkbox" checked={editForm.is_default} onChange={(event) => updateEditForm({ is_default: event.target.checked })} />设为默认</label>
                </div>
                <div className="model-capability-summary">
                  <span className="enabled">文本</span>
                  <span className="enabled">图片可发送</span>
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

function UserModelTestResult({ result }) {
  const checks = Object.entries(result.checks || {}).filter(([, check]) => check.required);
  const imageCapability = imageCapabilityFromTest(null, result);
  const chatError = result.detected_capabilities?.chat_error || result.checks?.chat?.message || '';
  const chatErrorCode = result.detected_capabilities?.chat_error_code || result.checks?.chat?.error_code || '';
  const imageError = result.detected_capabilities?.image_error || result.checks?.image?.message || '';
  const imageErrorCode = result.detected_capabilities?.image_error_code || result.checks?.image?.error_code || '';
  return (
    <div className={result.ok ? 'user-model-test ok' : 'user-model-test'}>
      <strong>{result.ok ? '能力检查通过' : '能力检查失败'} · {result.model} · {result.latency_ms}ms</strong>
      <div className="capability-checks">
        {result.detected_capabilities && (
          <span className={imageCapability.className}>
            图片: {imageCapability.label}
          </span>
        )}
        {result.detected_capabilities && (
          <span className={result.detected_capabilities.supports_reasoning ? 'ok' : ''}>
            深度思考: {result.detected_capabilities.supports_reasoning ? reasoningLabel(result.detected_capabilities.reasoning_type || 'prompt') : '不支持'}
          </span>
        )}
        {checks.map(([name, check]) => (
          <span key={name} className={check.ok ? 'ok' : 'fail'}>
            {capabilityCheckLabel(name)}: {check.ok ? '通过' : '失败'}
          </span>
        ))}
      </div>
      <small>{result.message}</small>
      {chatError && <small>chat 测试失败：{chatErrorCode ? `${chatErrorCode} · ` : ''}{chatError}</small>}
      {imageError && <small>图片探测失败：{imageErrorCode ? `${imageErrorCode} · ` : ''}{imageError}</small>}
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

  function applyPreset(preset) {
    const values = MODEL_CAPABILITY_PRESETS[preset]?.values || {};
    setForm((current) => ({
      ...current,
      ...values,
      preset,
    }));
  }

  async function createModel(event) {
    event.preventDefault();
    setSaving(true);
    setProfileError('');
    setRowNotice({ modelId: null, message: '' });
    try {
      await createModelConfig(modelFormPayload(form));
      setForm(createModelForm());
      setFormOpen(false);
    } catch (err) {
      setProfileError(errorMessage(err));
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
    setFormOpen(false);
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



















