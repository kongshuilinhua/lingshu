/**
 * 模型相关工具函数。
 * 从 utils.js 提取 —— 模型能力检测、标签渲染、表单默认值。
 */

function modelLabel(model) {
  if (!model) return '';
  return model.source === 'user' ? model.chat_model || model.model_name : model.model_name || model.chat_model || '';
}

function modelCapabilityChips(model) {
  if (!model) return [];
  const chips = [];
  if (model.supports_text !== false) chips.push('文本');
  if (model.supports_image) chips.push('图片');
  chips.push('文档附件后端解析');
  return chips.length ? chips : ['未声明能力'];
}

// 按模型名预填能力（与 Cherry Studio 等客户端做法一致）：只是默认值，用户可在表单里改。
// 第三方网关的模型名千差万别，猜不中也没关系——图片附件不会被前端拦截，深度思考可退回提示词增强。
const IMAGE_MODEL_PATTERN = /(^|[-_/.:])(vl|vision|visual|omni|multimodal|qvq|4v|\d+v)([-_/.:]|$)|gpt-4o|gpt-4\.1|gpt-5|claude|gemini|glm-4\.?\d*v|pixtral|llava|minicpm-v|internvl|kimi-vl/i;
const NATIVE_REASONING_PATTERN = /reasoner|(^|[-_/.:])(r1|o1|o3|o4)([-_/.:]|$)|thinking|qwq|qwen3|deepseek-v4|claude|gemini-2\.5|gpt-5|kimi-k2|glm-4\.5|glm-5/i;

function guessModelCapabilities(modelName) {
  const name = String(modelName || '');
  return {
    supports_image: IMAGE_MODEL_PATTERN.test(name),
    reasoning_type: NATIVE_REASONING_PATTERN.test(name) ? 'native' : 'prompt',
  };
}

function reasoningCapabilityForModel(model) {
  if (!model) {
    return { supported: false, type: 'none', label: '不支持', tooltip: '当前模型不支持深度思考，请更换支持推理的模型' };
  }
  let type = String(model.reasoning_type || '').trim();
  if (!type) type = model.supports_reasoning ? 'prompt' : 'none';
  if (!['native', 'prompt', 'none'].includes(type)) type = 'none';
  const supported = Boolean(model.supports_reasoning) && type !== 'none';
  if (!supported) {
    return { supported: false, type: 'none', label: '不支持', tooltip: '当前模型不支持深度思考，请更换支持推理的模型' };
  }
  const label = model.reasoning_label || reasoningLabel(type);
  return { supported: true, type, label, tooltip: type === 'prompt' ? '当前模型使用提示词增强，不是原生推理' : '当前模型支持原生深度思考' };
}

function reasoningLabel(type) {
  return { native: '深度思考', prompt: '提示词增强', none: '不支持' }[type] || '不支持';
}

function thinkingStatusText(capability, enabled) {
  if (!capability?.supported) return capability?.tooltip || '当前模型不支持深度思考';
  return enabled ? `${capability.label || '深度思考'} 已开启` : `${capability.label || '深度思考'} 已关闭`;
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
  return { ...config, source: 'user', model_name: config.chat_model, supports_text: true };
}

function chatModelOptions(models = [], userModels = []) {
  return [
    ...userModels.filter((model) => model.enabled).map((model) => ({
      value: `user:${model.id}`, label: model.display_name || model.chat_model,
      group: '我的模型', model: normalizeUserModelForUi(model),
    })),
    ...models.filter((model) => model.enabled !== false && model.supports_text !== false).map((model) => ({
      value: `system:${model.id}`, label: model.display_name || model.model_name,
      group: '平台模型', model: { ...model, source: 'system' },
    })),
  ];
}

function chatModelOverride(selection) {
  if (!selection) return undefined;
  const match = /^(user|system):([1-9]\d*)$/.exec(selection);
  if (!match) throw new Error('请选择有效的聊天模型。');
  return { source: match[1], id: Number(match[2]) };
}

function attachmentAcceptForModel(_model) {
  return '.txt,.md,.markdown,.csv,.pdf,.docx,image/*';
}

function attachmentHintForModel(_model) {
  return 'Attach image or document';
}

function modelCapabilityWarning(model, attachments = []) {
  if (!attachments.length) return '';
  if (!model) return '请先选择一个已启用模型再发送附件。';
  const hasDocument = attachments.some((item) => {
    const kind = item.type === 'image' || item.type === 'document' ? item.type : String(item.content_type || item.mime_type || '').startsWith('image/') ? 'image' : 'document';
    return kind === 'document';
  });
  if (hasDocument && model.supports_document === false) return '当前模型配置关闭了文档附件解析，请开启文档解析或移除文档附件。';
  return '';
}

export {
  guessModelCapabilities,
  modelLabel,
  modelCapabilityChips,
  reasoningCapabilityForModel,
  reasoningLabel,
  thinkingStatusText,
  findModelForForm,
  normalizeUserModelForUi,
  chatModelOptions,
  chatModelOverride,
  attachmentAcceptForModel,
  attachmentHintForModel,
  modelCapabilityWarning,
};
