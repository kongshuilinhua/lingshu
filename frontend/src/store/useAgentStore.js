/**
 * Agent 状态 Store — 管理 agents 列表、当前 activeAgent、agentForm 草稿。
 * 替代 main.jsx 中的 useState(agents), useState(activeAgentId), useState(agentForm) 等。
 */
import { create } from 'zustand';
import { api, isAuthError } from '../lib/api.js';

export const useAgentStore = create((set, get) => ({
  agents: [],
  activeAgentId: null,
  activeAgent: null,
  agentForm: {
    name: '',
    avatar: 'AI',
    description: '',
    opening_message: '',
    system_prompt: '',
    model_id: '',
    user_model_config_id: '',
    model: 'qwen-plus',
    temperature: 0.4,
    knowledge_base_ids: [],
    tool_ids: [],
    suggested_questions: [],
    variables: [],
    memory: { enabled: false, strategy: 'session_summary', max_messages: 12 },
    rag: { enabled_by_default: true, top_k: 4 },
    tool_policy: { mode: 'auto', allowed_tool_names: [], web_search_enabled: false },
  },

  setActiveAgentId: (id) => set({ activeAgentId: id }),
  setActiveAgent: (activeAgent) => set({ activeAgent }),
  setAgents: (agents) => set({ agents }),
  setAgentForm: (updater) => set((s) => ({
    agentForm: typeof updater === 'function' ? updater(s.agentForm) : { ...s.agentForm, ...updater },
  })),

  loadAgent: async (agentId, token) => {
    try {
      const data = await api(`/api/agents/${agentId}`, { token });
      const agent = data.agent;
      set({
        activeAgent: agent,
        activeAgentId: agentId,
        agentForm: {
          name: agent.name || '',
          avatar: agent.avatar || 'AI',
          description: agent.description || '',
          opening_message: agent.opening_message || '',
          system_prompt: agent.system_prompt || '',
          model_id: agent.model_id || '',
          user_model_config_id: agent.user_model_config_id || '',
          model: agent.model || '',
          temperature: agent.temperature ?? 0.4,
          knowledge_base_ids: agent.knowledge_base_ids || [],
          tool_ids: (agent.tools || []).map((t) => t.id),
          suggested_questions: agent.suggested_questions || [],
          variables: agent.variables || [],
          memory: agent.memory || { enabled: false, strategy: 'session_summary', max_messages: 12 },
          rag: agent.rag || { enabled_by_default: true, top_k: 4 },
          tool_policy: agent.tool_policy || { mode: 'auto', allowed_tool_names: [] },
        },
      });
    } catch (err) {
      if (isAuthError(err)) throw err;
      throw err;
    }
  },

  saveAgent: async (token) => {
    const { activeAgentId, agentForm } = get();
    if (!activeAgentId) return;
    await api(`/api/agents/${activeAgentId}`, { token, method: 'PATCH', body: agentForm });
  },

  publishAgent: async (token) => {
    const { activeAgentId } = get();
    if (!activeAgentId) return null;
    return api(`/api/agents/${activeAgentId}/publish`, { token, method: 'POST' });
  },
}));
