/**
 * Agent API 客户端。
 * 从 main.jsx 内联函数中提取 —— Agent CRUD + 发布 + 市场复制。
 */
import { api } from '../lib/api.js';

export async function fetchAgents(token) {
  const data = await api('/api/agents', { token });
  return data.items || [];
}

export async function fetchAgent(agentId, token) {
  const data = await api(`/api/agents/${agentId}`, { token });
  return data.agent;
}

export async function createAgent(payload, token) {
  const data = await api('/api/agents', { token, method: 'POST', body: payload });
  return data.agent;
}

export async function updateAgent(agentId, patch, token) {
  const data = await api(`/api/agents/${agentId}`, { token, method: 'PATCH', body: patch });
  return data.agent;
}

export async function deleteAgent(agentId, token) {
  return api(`/api/agents/${agentId}`, { token, method: 'DELETE' });
}

export async function publishAgent(agentId, token) {
  return api(`/api/agents/${agentId}/publish`, { token, method: 'POST' });
}

export async function copyMarketAgent(agentId, token) {
  return api(`/api/market/agents/${agentId}/copy`, { token, method: 'POST' });
}

export async function fetchSessions(agentId, token) {
  const data = await api(`/api/agents/${agentId}/sessions`, { token });
  return data.items || [];
}

export async function fetchMarketAgents(token) {
  const data = await api('/api/market/agents', { token });
  return data.items || [];
}
