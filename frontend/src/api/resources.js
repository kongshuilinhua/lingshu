/**
 * 知识库 + 工具 + 模型 API 客户端。
 */
import { api } from '../lib/api.js';

// ── 知识库 ──

export async function fetchKnowledgeBases(token) {
  const data = await api('/api/knowledge-bases', { token });
  return data.items || [];
}

export async function createKnowledgeBase(payload, token) {
  const data = await api('/api/knowledge-bases', { token, method: 'POST', body: payload });
  return data.knowledge_base;
}

export async function updateKnowledgeBase(kbId, payload, token) {
  const data = await api(`/api/knowledge-bases/${kbId}`, { token, method: 'PATCH', body: payload });
  return data.knowledge_base;
}

export async function deleteKnowledgeBase(kbId, token) {
  return api(`/api/knowledge-bases/${kbId}`, { token, method: 'DELETE' });
}

export async function uploadDocument(kbId, payload, token) {
  return api(`/api/knowledge-bases/${kbId}/documents`, { token, method: 'POST', body: payload });
}

export async function fetchDocuments(kbId, token) {
  const data = await api(`/api/knowledge-bases/${kbId}/documents`, { token });
  return data.items || [];
}

export async function deleteDocument(kbId, documentId, token) {
  return api(`/api/knowledge-bases/${kbId}/documents/${documentId}`, { token, method: 'DELETE' });
}

// ── 工具 ──

export async function fetchTools(token) {
  const data = await api('/api/tools', { token });
  return data.items || [];
}

export async function createTool(payload, token) {
  return api('/api/tools', { token, method: 'POST', body: payload });
}

export async function updateTool(toolId, patch, token) {
  return api(`/api/tools/${toolId}`, { token, method: 'PATCH', body: patch });
}

export async function deleteTool(toolId, token) {
  return api(`/api/tools/${toolId}`, { token, method: 'DELETE' });
}

// ── 模型 ──

export async function fetchModels(includeDisabled = false, token) {
  const url = includeDisabled ? '/api/models?include_disabled=true' : '/api/models';
  const data = await api(url, { token });
  return data.items || [];
}

export async function fetchUserModels(token) {
  const data = await api('/api/user-models', { token });
  return data.items || [];
}

export async function createUserModel(payload, token) {
  const data = await api('/api/user-models', { token, method: 'POST', body: payload });
  return data.model_config;
}

export async function updateUserModel(configId, patch, token) {
  const data = await api(`/api/user-models/${configId}`, { token, method: 'PATCH', body: patch });
  return data.model_config;
}

export async function deleteUserModel(configId, token) {
  return api(`/api/user-models/${configId}`, { token, method: 'DELETE' });
}

// ── Prompt 模板 ──

export async function fetchPromptTemplates(includeDisabled = false, token) {
  const url = includeDisabled ? '/api/prompt-templates?include_disabled=true' : '/api/prompt-templates';
  const data = await api(url, { token });
  return data.items || [];
}

// ── 健康检查 ──

export async function fetchHealth() {
  return api('/api/health');
}

// ── 成员 ──

export async function fetchMembers(token) {
  const data = await api('/api/workspaces/members', { token });
  return data.items || [];
}

export async function createInvite(payload, token) {
  const data = await api('/api/workspaces/invites', { token, method: 'POST', body: payload });
  return data.invite;
}

export async function fetchInvites(token) {
  const data = await api('/api/workspaces/invites', { token });
  return data.items || [];
}

export async function updateMemberRole(memberId, role, token) {
  const data = await api(`/api/workspaces/members/${memberId}`, { token, method: 'PATCH', body: { role } });
  return data.member;
}

export async function removeMember(memberId, token) {
  return api(`/api/workspaces/members/${memberId}`, { token, method: 'DELETE' });
}

export async function previewInvite(token) {
  return api(`/api/workspaces/invites/${encodeURIComponent(token)}`);
}
