// Phase 4: Re-exported from lib/ (single source of truth)
import { API_BASE, AUTH_TOKEN_KEY, LEGACY_AUTH_TOKEN_KEY, ApiError, isAuthError, notifyAuthExpired, initialAuthToken, errorMessage, api } from './lib/api.js';
import { MAX_UPLOAD_BYTES, MAX_KNOWLEDGE_BYTES, KNOWLEDGE_FILE_ACCEPT, KNOWLEDGE_FILE_EXTENSIONS } from './lib/upload.js';
import { roleLabel, avatarInitial, isAdminRole, statusLabel } from './lib/format.js';

const JIGE_PROMPT = [
  '你将扮演一个人物角色，请根据角色设定回答用户问题。',
  '角色：热情、活泼，喜欢 rap 和篮球的练习生。',
  '要求：使用第一人称，语气生动，必要时可加入动作和神态描写。',
].join('\n');

const SAMPLE_MESSAGES = [
  { role: 'user', content: '篮球和 rap 的相似之处是什么？' },
  { role: 'assistant', content: '它们都需要节奏感、专注和表现力。运球像 flow，投篮像压拍，关键是稳准和自信。' },
  { role: 'user', content: '你最喜欢的篮球明星是谁？' },
  { role: 'assistant', content: '我喜欢斯蒂芬·库里，因为他的节奏、判断和出手都很适合用来比喻舞台表演。' },
];

function createAvatarDataUrl(file) {
  return new Promise((resolve, reject) => {
    const image = new window.Image();
    image.onload = () => {
      const size = 256;
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const context = canvas.getContext('2d');
      if (!context) {
        reject(new Error('头像处理失败'));
        return;
      }
      const sourceSize = Math.min(image.naturalWidth, image.naturalHeight);
      const sx = Math.max(0, (image.naturalWidth - sourceSize) / 2);
      const sy = Math.max(0, (image.naturalHeight - sourceSize) / 2);
      context.clearRect(0, 0, size, size);
      context.drawImage(image, sx, sy, sourceSize, sourceSize, 0, 0, size, size);
      URL.revokeObjectURL(image.src);
      resolve(canvas.toDataURL('image/webp', 0.82));
    };
    image.onerror = () => {
      URL.revokeObjectURL(image.src);
      reject(new Error('头像读取失败'));
    };
    image.src = URL.createObjectURL(file);
  });
}

function validateAvatarFile(file) {
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
    throw new Error('图标只支持 PNG、JPG、WebP 或 GIF。');
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new Error('图标文件不能超过 8MB。');
  }
}

function pickAgentIdentity(form) {
  return normalizeAgentIdentity({
    name: form?.name,
    description: form?.description,
    avatar: form?.avatar,
  });
}

function normalizeAgentIdentity(identity = {}) {
  return {
    name: String(identity.name || '').slice(0, 50),
    description: String(identity.description || '').slice(0, 500),
    avatar: String(identity.avatar || 'AI'),
  };
}

// Phase 4: imported from lib/memory.js
import { defaultMemoryProfile, normalizeMemoryProfile, profileToDraft, draftFacts, memoryProfilePayload, parsePreferences, safeJsonPreview, isPlainObject, isJsonCompatiblePreference } from './lib/memory.js';

// imported from lib/format.js
import { formatDateTime } from './lib/format.js';

function defaultAgentForm() {
  return {
    name: '智能体一号',
    avatar: '66',
    description: '喜欢唱跳 rap 篮球的活泼角色扮演智能体。',
    opening_message: '你好',
    system_prompt: JIGE_PROMPT,
    model_id: '',
    user_model_config_id: '',
    model: 'qwen-plus',
    temperature: 0.6,
    knowledge_base_ids: [],
    tool_ids: [],
    suggested_questions: ['你想问啥？'],
    variables: [],
    memory: { enabled: false, strategy: 'session_summary', max_messages: 48 },
    rag: { enabled_by_default: true, top_k: 4 },
    tool_policy: { mode: 'auto', allowed_tool_names: [], web_search_enabled: false },
  };
}

function agentPayload(form, { model = null } = {}) {
  const userModelId = form.user_model_config_id ? Number(form.user_model_config_id) : null;
  const systemModelId = !userModelId && form.model_id ? Number(form.model_id) : null;
  const temperature = Number(form.temperature);
  const rag = form.rag || { enabled_by_default: true, top_k: 4 };
  return {
    ...form,
    model_id: Number.isFinite(systemModelId) && systemModelId > 0 ? systemModelId : null,
    user_model_config_id: Number.isFinite(userModelId) && userModelId > 0 ? userModelId : null,
    model: form.model || null,
    temperature: Number.isFinite(temperature) ? temperature : 0.4,
    rag,
    knowledge_base_ids: numericIdList(form.knowledge_base_ids),
    tool_ids: numericIdList(form.tool_ids),
  };
}

function numericIdList(values) {
  return (values || [])
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 0);
}

function filterResourceItems(items, query, stringify) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return items || [];
  return (items || []).filter((item) => stringify(item).toLowerCase().includes(needle));
}

function filterPromptTemplates(items, query) {
  return filterResourceItems(
    items,
    query,
    (item) => `${item.title || ''} ${item.description || ''} ${item.content || ''} ${(item.tags || []).join(' ')}`,
  );
}

function defaultPromptTemplateForm() {
  return {
    title: '',
    description: '',
    category: 'general',
    tagsText: '',
    content: '',
    enabled: true,
  };
}

function defaultKnowledgeBaseForm() {
  return {
    name: '',
    description: '',
  };
}

function formFromPromptTemplate(template, overrides = {}) {
  return {
    title: template?.title || '',
    description: template?.description || '',
    category: template?.category || 'general',
    tagsText: (template?.tags || []).join(', '),
    content: template?.content || '',
    enabled: template?.enabled !== false,
    ...overrides,
  };
}

function promptTemplateFormPayload(form) {
  return {
    title: String(form.title || '').trim(),
    description: String(form.description || '').trim(),
    category: String(form.category || 'general').trim() || 'general',
    tags: String(form.tagsText || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    content: String(form.content || ''),
    enabled: Boolean(form.enabled),
  };
}

function insertPromptIntoAgent(setAgentForm, content) {
  const text = String(content || '').trim();
  if (!text) return;
  setAgentForm((form) => ({
    ...form,
    system_prompt: joinPromptText(form.system_prompt, text),
  }));
}

function insertPromptAtEditor(ref, setAgentForm, content) {
  const text = String(content || '').trim();
  if (!text) return;
  
  // 完全替换现有的提示词系统 Prompt，而非在光标后追加
  setAgentForm((form) => ({ ...form, system_prompt: text }));
  
  const element = ref?.current;
  if (element) {
    window.requestAnimationFrame(() => {
      element.focus();
      element.setSelectionRange(text.length, text.length);
    });
  }
}

function joinPromptText(current, addition) {
  const left = String(current || '').trimEnd();
  const right = String(addition || '').trim();
  if (!left) return right;
  if (!right) return left;
  return `${left}\n\n${right}`;
}

function findModelForForm(models, userModels, form) {
  const userModelId = Number(form.user_model_config_id);
  if (Number.isFinite(userModelId) && userModelId > 0) {
    const config = userModels.find((model) => model.id === userModelId);
    if (config) return normalizeUserModelForUi(config);
  }
  const systemModelId = Number(form.model_id);
  return models.find((model) => model.id === systemModelId) || models.find((model) => model.model_name === form.model) || null;
}

function normalizeUserModelForUi(config) {
  return {
    ...config,
    source: 'user',
    model_name: config.chat_model,
    supports_text: true,
  };
}

// imported from lib/models.js
import { modelLabel } from './lib/models.js';

// imported from lib/models.js
import { modelCapabilityChips } from './lib/models.js';

function reasoningCapabilityForModel(model) {
  if (!model) {
    return {
      supported: false,
      type: 'none',
      label: '不支持',
      tooltip: '当前模型不支持深度思考，请更换支持推理的模型',
    };
  }
  let type = String(model.reasoning_type || '').trim();
  if (!type) type = model.supports_reasoning ? 'prompt' : 'none';
  if (!['native', 'prompt', 'none'].includes(type)) type = 'none';
  const supported = Boolean(model.supports_reasoning) && type !== 'none';
  if (!supported) {
    return {
      supported: false,
      type: 'none',
      label: '不支持',
      tooltip: '当前模型不支持深度思考，请更换支持推理的模型',
    };
  }
  const label = model.reasoning_label || reasoningLabel(type);
  return {
    supported: true,
    type,
    label,
    tooltip: type === 'prompt' ? '当前模型使用提示词增强，不是原生推理' : '当前模型支持原生深度思考',
  };
}

function reasoningLabel(type) {
  return { native: '深度思考', prompt: '提示词增强', none: '不支持' }[type] || '不支持';
}

function imageCapabilityFromTest(form, result) {
  const image = result?.detected_capabilities || {};
  const check = result?.checks?.image || {};
  if (image.image_confirmed || check.status === 'confirmed' || check.ok) {
    return { className: 'ok enabled', label: '图片探测通过' };
  }
  if (check.tested || image.image_status === 'failed') {
    return { className: 'pending', label: image.image_error_code ? `图片探测失败：${image.image_error_code}` : '图片探测失败' };
  }
  return { className: 'pending', label: '图片可发送，待测试' };
}

function thinkingStatusText(capability, enabled) {
  if (!capability?.supported) return capability?.tooltip || '当前模型不支持深度思考';
  return enabled
    ? `${capability.label || '深度思考'} 已开启`
    : `${capability.label || '深度思考'} 已关闭`;
}

function capabilityCheckLabel(name) {
  return {
    chat: 'chat',
    image: 'image',
  }[name] || name;
}

function defaultRuntimeStatus() {
  return {
    status: 'offline',
    issues: ['Backend health check is unavailable.'],
    dependencies: {
      embedding: { configured: false, available: false, model: '', base_url: '', mock: true, reason: 'health_unavailable' },
      vector_store: { available: false, active_backend: 'unknown', fallback: false },
      web_search: { configured: false, enabled: false, provider: 'duckduckgo_html', requires_api_key: false },
    },
  };
}

function getRagRuntime(status) {
  const embedding = status?.dependencies?.embedding || {};
  const vector = status?.dependencies?.vector_store || {};
  const model = String(embedding.model || '').trim();
  const reason = embedding.reason || vector.error || (status?.status === 'offline' ? 'health_unavailable' : '');
  const available = Boolean((embedding.available ?? embedding.configured) && !embedding.mock && (vector.available || vector.fallback));
  return {
    available,
    model: model || '后端默认 Embedding',
    baseUrl: embedding.base_url || '',
    mock: Boolean(embedding.mock),
    degraded: status?.status === 'degraded' || Boolean(vector.fallback),
    reason,
    vectorBackend: vector.active_backend || vector.backend || '',
    issues: status?.issues || [],
  };
}

function ragStatusText(runtime, enabled) {
  if (!runtime?.available) return runtime?.reason ? `后端默认 RAG 检索能力不可用：${runtime.reason}` : '后端默认 RAG 检索能力不可用，本轮不会检索知识库';
  if (runtime.degraded) return `RAG 可用但处于降级状态：${runtime.issues?.[0] || '部分依赖不可用'}`;
  return enabled
    ? `RAG 开启，使用后端默认 ${runtime.model} 检索知识库`
    : `RAG 关闭，后端默认 ${runtime.model} 本轮不检索知识库`;
}

function runtimeStatusMessage(ragRuntime, webSearchRuntime) {
  if (ragRuntime?.reason === 'health_unavailable') return '后端健康检查不可用，聊天和检索能力可能无法正常工作。';
  if (!ragRuntime?.available) return ragStatusText(ragRuntime, false);
  if (ragRuntime?.degraded) return ragStatusText(ragRuntime, false);
  if (!webSearchRuntime?.available) return '';
  return '';
}

function getWebSearchRuntime(status) {
  const search = status?.dependencies?.web_search || {};
  return {
    available: Boolean(search.enabled && search.configured),
    provider: search.provider || 'duckduckgo_html',
    requiresApiKey: Boolean(search.requires_api_key),
    topK: Number(search.top_k || 5),
  };
}

function webSearchStatusText(runtime, enabled) {
  if (!runtime?.available) return 'Web search unavailable: backend search is disabled';
  return enabled
    ? `Web search on: ${runtime.provider}, up to ${runtime.topK || 5} results`
    : 'Web search off: this turn will not call the search service';
}

function attachmentAcceptForModel(model) {
  return '.txt,.md,.markdown,.csv,.pdf,.docx,image/*';
}

function attachmentHintForModel(model) {
  return 'Attach image or document';
}

function userModelFormPayload(form, { includeApiKey = false } = {}) {
  const maxContext = Number(form.max_context);
  const temperature = Number(form.default_temperature);
  const payload = {
    display_name: String(form.display_name || '').trim(),
    provider: String(form.provider || 'openai-compatible').trim(),
    base_url: String(form.base_url || '').trim(),
    chat_model: String(form.chat_model || '').trim(),
    supports_image: false,
    supports_document: Boolean(form.supports_document),
    supports_reasoning: Boolean(form.supports_reasoning) && (form.reasoning_type || 'none') !== 'none',
    reasoning_type: form.reasoning_type || 'none',
    reasoning_label: reasoningLabel(form.reasoning_type || 'none'),
    max_context: Number.isFinite(maxContext) && maxContext > 0 ? maxContext : 131072,
    default_temperature: Number.isFinite(temperature) ? temperature : 0.4,
    enabled: Boolean(form.enabled),
    is_default: Boolean(form.is_default),
  };
  if (includeApiKey && String(form.api_key || '').trim()) {
    payload.api_key = String(form.api_key).trim();
  }
  return payload;
}

function userModelEditPayload(form) {
  const payload = userModelFormPayload(form);
  delete payload.is_default;
  payload.is_default = Boolean(form.is_default);
  return payload;
}

function uploadTypeFromContentType(contentType = '') {
  return String(contentType).startsWith('image/') ? 'image' : 'document';
}

function attachmentKind(item = {}) {
  if (item.type === 'image' || item.type === 'document') return item.type;
  return uploadTypeFromContentType(item.content_type || item.mime_type);
}

function modelCapabilityWarning(model, attachments = []) {
  if (!attachments.length) return '';
  if (!model) return '请先选择一个已启用模型再发送附件。';
  const hasDocument = attachments.some((item) => attachmentKind(item) === 'document');
  if (hasDocument && model.supports_document === false) return '当前模型配置关闭了文档附件解析，请开启文档解析或移除文档附件。';
  return '';
}

function toggleKb(id, form, setForm) {
  const exists = form.knowledge_base_ids.includes(id);
  setForm({ ...form, knowledge_base_ids: exists ? form.knowledge_base_ids.filter((item) => item !== id) : [...form.knowledge_base_ids, id] });
}

function toggleTool(id, form, setForm) {
  const exists = form.tool_ids.includes(id);
  setForm({ ...form, tool_ids: exists ? form.tool_ids.filter((item) => item !== id) : [...form.tool_ids, id] });
}

function initVariableValues(variables) {
  const values = {};
  for (const variable of variables || []) {
    values[variable.key] = variable.default_value ?? (variable.type === 'boolean' ? false : '');
  }
  return values;
}

function castVariables(definitions, values) {
  const result = {};
  for (const definition of definitions || []) {
    const value = values[definition.key] ?? definition.default_value;
    if (definition.type === 'number') {
      result[definition.key] = value === '' || value == null ? null : Number(value);
    } else if (definition.type === 'boolean') {
      result[definition.key] = value === true || value === 'true';
    } else {
      result[definition.key] = value == null ? '' : String(value);
    }
  }
  return result;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.onerror = () => reject(new Error('文件读取失败'));
    reader.readAsDataURL(file);
  });
}

function guessContentType(filename) {
  const suffix = filename.toLowerCase().split('.').pop();
  const types = {
    txt: 'text/plain',
    md: 'text/markdown',
    markdown: 'text/markdown',
    csv: 'text/csv',
    pdf: 'application/pdf',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  };
  return types[suffix] || 'application/octet-stream';
}

function validateKnowledgeFile(file) {
  if (file.size > MAX_KNOWLEDGE_BYTES) {
    throw new Error('知识库文件不能超过 30MB');
  }
  const suffix = String(file.name || '').toLowerCase().split('.').pop();
  if (!KNOWLEDGE_FILE_EXTENSIONS.includes(suffix)) {
    throw new Error('知识库仅支持 TXT、MD、CSV、PDF、DOCX');
  }
}

async function handleKnowledgeFileInput(event, uploadKnowledgeFile) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  await uploadKnowledgeFile(file);
}

async function handleAttachmentInput(event, uploadChatAttachment) {
  const files = filesFromList(event.target.files);
  event.target.value = '';
  await uploadAttachmentFiles(files, uploadChatAttachment);
}

async function handleAttachmentPaste(event, uploadChatAttachment) {
  const files = filesFromClipboard(event.clipboardData);
  if (!files.length) return;
  event.preventDefault();
  await uploadAttachmentFiles(files, uploadChatAttachment);
}

async function handleAttachmentDrop(files, uploadChatAttachment) {
  await uploadAttachmentFiles(filesFromList(files), uploadChatAttachment);
}

async function uploadAttachmentFiles(files, uploadChatAttachment) {
  for (const file of files) {
    await uploadChatAttachment(file);
  }
}

function filesFromList(fileList) {
  return Array.from(fileList || []).filter(Boolean);
}

function filesFromClipboard(clipboardData) {
  const files = filesFromList(clipboardData?.files);
  if (files.length) return files;
  return Array.from(clipboardData?.items || [])
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter(Boolean);
}

function hasTransferFiles(dataTransfer) {
  return Array.from(dataTransfer?.types || []).includes('Files');
}

export {
  API_BASE,
  MAX_UPLOAD_BYTES,
  KNOWLEDGE_FILE_ACCEPT,
  KNOWLEDGE_FILE_EXTENSIONS,
  AUTH_TOKEN_KEY,
  LEGACY_AUTH_TOKEN_KEY,
  ApiError,
  isAuthError,
  notifyAuthExpired,
  initialAuthToken,
  JIGE_PROMPT,
  SAMPLE_MESSAGES,
  roleLabel,
  avatarInitial,
  isAdminRole,
  statusLabel,
  errorMessage,
  api,
  createAvatarDataUrl,
  validateAvatarFile,
  pickAgentIdentity,
  normalizeAgentIdentity,
  defaultMemoryProfile,
  normalizeMemoryProfile,
  profileToDraft,
  draftFacts,
  memoryProfilePayload,
  parsePreferences,
  safeJsonPreview,
  isPlainObject,
  isJsonCompatiblePreference,
  formatDateTime,
  defaultAgentForm,
  agentPayload,
  numericIdList,
  filterResourceItems,
  filterPromptTemplates,
  defaultPromptTemplateForm,
  defaultKnowledgeBaseForm,
  formFromPromptTemplate,
  promptTemplateFormPayload,
  insertPromptIntoAgent,
  insertPromptAtEditor,
  joinPromptText,
  findModelForForm,
  normalizeUserModelForUi,
  modelLabel,
  modelCapabilityChips,
  reasoningCapabilityForModel,
  reasoningLabel,
  imageCapabilityFromTest,
  thinkingStatusText,
  capabilityCheckLabel,
  defaultRuntimeStatus,
  getRagRuntime,
  ragStatusText,
  runtimeStatusMessage,
  getWebSearchRuntime,
  webSearchStatusText,
  attachmentAcceptForModel,
  attachmentHintForModel,
  userModelFormPayload,
  userModelEditPayload,
  uploadTypeFromContentType,
  attachmentKind,
  modelCapabilityWarning,
  toggleKb,
  toggleTool,
  initVariableValues,
  castVariables,
  fileToBase64,
  guessContentType,
  validateKnowledgeFile,
  handleKnowledgeFileInput,
  handleAttachmentInput,
  handleAttachmentPaste,
  handleAttachmentDrop,
  uploadAttachmentFiles,
  filesFromList,
  filesFromClipboard,
  hasTransferFiles,
};
