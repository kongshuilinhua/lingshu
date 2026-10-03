import React, { Component, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AgentAvatar, UserAvatar } from './components/AgentAvatar.jsx';
import { ConfirmDialog } from './components/ConfirmDialog.jsx';

import {
  AlertTriangle,
  Bot,
  Boxes,
  Brain,
  Check,
  ChevronLeft,
  Database,
  FileText,
  FileX2,
  ImagePlus,
  Home,
  Layers,
  KeyRound,
  LogIn,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Send,
  ServerCog,
  Shield,
  Sparkles,
  SquarePen,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  UploadCloud,
  Wand2,
  X,
} from 'lucide-react';
import './styles.css';
// Phase 4: api/ client modules
import { fetchAgents, fetchMarketAgents, fetchReviews } from './api/agents.js';
import { fetchKnowledgeBases, fetchTools, fetchModels, fetchUserModels, fetchPromptTemplates, fetchMembers } from './api/resources.js';
import { useAuthStore } from './store/useAuthStore.js';
import { useAgentStore } from './store/useAgentStore.js';
import { useChatStore } from './store/useChatStore.js';

const lazyNamed = (loader, exportName) => lazy(() => loader().then((module) => ({ default: module[exportName] })));
const ChatView = lazyNamed(() => import('./views/ChatView.jsx'), 'ChatView');
const BuilderView = lazyNamed(() => import('./views/BuilderView.jsx'), 'BuilderView');
const AgentsHome = lazyNamed(() => import('./pages/AgentsHome.jsx'), 'AgentsHome');
const MarketHome = lazyNamed(() => import('./pages/MarketHome.jsx'), 'MarketHome');
const ReviewHome = lazyNamed(() => import('./pages/ReviewHome.jsx'), 'ReviewHome');
const MembersHome = lazyNamed(() => import('./pages/MembersHome.jsx'), 'MembersHome');
const KnowledgeHome = lazyNamed(() => import('./pages/KnowledgeHome.jsx'), 'KnowledgeHome');
const ResourceLibraryHome = lazyNamed(() => import('./pages/ResourceLibraryHome.jsx'), 'ResourceLibraryHome');
const ToolsHome = lazyNamed(() => import('./pages/ToolsHome.jsx'), 'ToolsHome');
const UserModelsHome = lazyNamed(() => import('./pages/UserModelsHome.jsx'), 'UserModelsHome');

function PageFallback() {
  return <p className="empty-state">加载中...</p>;
}


import {
  MAX_UPLOAD_BYTES,
  KNOWLEDGE_FILE_ACCEPT,
  KNOWLEDGE_FILE_EXTENSIONS,
  AUTH_TOKEN_KEY,
  LEGACY_AUTH_TOKEN_KEY,
  isAuthError,
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
  API_BASE,
} from './utils.js';

function App() {
  // Phase 4: Zustand auth store replaces useState(token/me/workspace)
  const { token, me, workspace, setToken, logout: storeLogout, bootstrap: storeBootstrap } = useAuthStore();
  // Phase 4: Zustand agent store
  const { agents, activeAgentId, activeAgent, agentForm, setActiveAgentId, setAgents, setAgentForm, loadAgent: storeLoadAgent, saveAgent: storeSaveAgent } = useAgentStore();
  const [knowledgeBases, setKnowledgeBases] = useState([]);
  const [tools, setTools] = useState([]);
  const [promptTemplates, setPromptTemplates] = useState([]);
  const [models, setModels] = useState([]);
  const [adminModels, setAdminModels] = useState([]);
  const [userModels, setUserModels] = useState([]);
  const [runtimeStatus, setRuntimeStatus] = useState(() => defaultRuntimeStatus());
  const [marketAgents, setMarketAgents] = useState([]);
  const [reviewItems, setReviewItems] = useState([]);
  const [members, setMembers] = useState([]);
  // Phase 4: Zustand chat store replaces 19 useState calls
  const {
    sessions, activeSessionId, sessionTitleDraft, messages, sources, toolDebugEvents,
    feedbackByMessage, chatMode, chatVariables, ragEnabled, thinkingEnabled, searchEnabled,
    chatAttachments, uploadingAttachment, draft, busy, error, homePrompt,
    setSessions, setActiveSessionId, setSessionTitleDraft, setMessages, setSources,
    setToolDebugEvents, setFeedbackByMessage, setChatMode, setChatVariables,
    setRagEnabled, setThinkingEnabled, setSearchEnabled, setChatAttachments,
    setUploadingAttachment, setDraft, setError, setHomePrompt,
    startNewChat: storeStartNewChat, sendMessage: storeSendMessage,
  } = useChatStore();
  const [documents, setDocuments] = useState([]);
  const [toastMsg, setToastMsg] = useState('');
  const notify = useCallback((msg) => {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(''), 3000);
  }, []);
  const [authMode, setAuthMode] = useState('register');
  const [authForm, setAuthForm] = useState({ email: 'admin@example.com', name: 'Admin', password: 'password123' });
  const [docForm, setDocForm] = useState({ filename: 'guide.txt', text: '这里是一段知识库资料。', kb_id: '' });
  const [uploadingKnowledgeFile, setUploadingKnowledgeFile] = useState(false);
  const [uploadingFileName, setUploadingFileName] = useState('');
  const [view, setView] = useState('home');
  const [activeNav, setActiveNav] = useState('chat');
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [profileDialogOpen, setProfileDialogOpen] = useState(false);
  const [profileError, setProfileError] = useState('');
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [memoryProfile, setMemoryProfile] = useState(() => defaultMemoryProfile());
  const [memoryProfileDraft, setMemoryProfileDraft] = useState(() => profileToDraft(defaultMemoryProfile()));
  const [memoryProfileLoading, setMemoryProfileLoading] = useState(false);
  const [memoryProfileSaving, setMemoryProfileSaving] = useState(false);
  const [memoryProfileError, setMemoryProfileError] = useState('');
  const [agentIdentityDialog, setAgentIdentityDialog] = useState(null);
  const [agentIdentitySaving, setAgentIdentitySaving] = useState(false);
  const [agentIdentityError, setAgentIdentityError] = useState('');

  const activeSummary = useMemo(() => agents.find((item) => item.id === activeAgentId), [agents, activeAgentId]);
  const chatAgents = useMemo(
    () => agents.filter((item) => item.status === 'published' && item.published_version_id),
    [agents],
  );
  const selectedDraftModel = useMemo(
    () => findModelForForm(models, userModels, agentForm),
    [models, userModels, agentForm.model_id, agentForm.user_model_config_id, agentForm.model],
  );
  const currentThinkingModel = useMemo(
    () => (view === 'builder' ? selectedDraftModel : activeAgent?.user_model_config || activeAgent?.model_config || null),
    [view, selectedDraftModel, activeAgent],
  );
  const ragRuntime = useMemo(() => getRagRuntime(runtimeStatus), [runtimeStatus]);
  const webSearchRuntime = useMemo(() => getWebSearchRuntime(runtimeStatus), [runtimeStatus]);
  const activeKbId = Number(docForm.kb_id || knowledgeBases[0]?.id || 0);
  const canManage = isAdminRole(workspace?.role);
  const canEditActive = !!activeAgent && (canManage || activeAgent.created_by === me?.id);

  async function loadDocuments(kbId) {
    if (!kbId || !token) return;
    const data = await api(`/api/knowledge-bases/${kbId}/documents`, { token });
    setDocuments(data.items || []);
  }

  useEffect(() => {
    function handleAuthExpired() {
      logout();
      setError('登录已失效，请重新登录。');
    }
    window.addEventListener('lingshu-auth-expired', handleAuthExpired);
    return () => window.removeEventListener('lingshu-auth-expired', handleAuthExpired);
  }, []);

  useEffect(() => {
    if (token) {
      bootstrap().catch((err) => {
        if (isAuthError(err)) {
          logout();
          setError('登录已失效，请重新登录。');
        } else {
          setError(errorMessage(err));
        }
      });
    }
  }, [token]);

  useEffect(() => {
    if (activeAgentId) {
      loadAgent(activeAgentId).catch((err) => {
        if (isAuthError(err)) {
          logout();
          setError('登录已失效，请重新登录。');
        } else {
          setError(errorMessage(err));
        }
      });
    }
  }, [activeAgentId]);

  useEffect(() => {
    if (activeNav !== 'chat' || !chatAgents.length) return;
    if (!activeSummary || activeSummary.status !== 'published' || !activeSummary.published_version_id) {
      setActiveAgentId(chatAgents[0].id);
    }
  }, [activeNav, activeSummary, chatAgents]);

  useEffect(() => {
    if (!thinkingEnabled) return;
    const capability = reasoningCapabilityForModel(currentThinkingModel);
    if (!capability.supported) {
      setThinkingEnabled(false);
      if (currentThinkingModel) {
        setError('当前模型不支持深度思考，请更换支持推理的模型。');
      }
    }
  }, [
    currentThinkingModel?.id,
    currentThinkingModel?.source,
    currentThinkingModel?.model_name,
    currentThinkingModel?.chat_model,
    currentThinkingModel?.supports_reasoning,
    currentThinkingModel?.reasoning_type,
  ]);

  useEffect(() => {
    if (activeKbId) {
      loadDocuments(activeKbId).catch((err) => {
        if (isAuthError(err)) {
          logout();
          setError('登录已失效，请重新登录。');
        } else {
          setError(errorMessage(err));
        }
      });
    } else {
      setDocuments([]);
    }
  }, [activeKbId, token]);

  // 文档入库已后台异步化：只要列表里还有 indexing 状态的文档，就每 3s 轮询刷新，
  // 直到全部转为 indexed/failed 后自动停止（effect 在 documents 更新后重新求值，无 indexing 即不再设定时器）。
  useEffect(() => {
    if (!activeKbId || !token) return undefined;
    const hasIndexing = documents.some((doc) => doc.status === 'indexing');
    if (!hasIndexing) return undefined;
    const timer = setInterval(() => {
      loadDocuments(activeKbId).catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [documents, activeKbId, token]);

  useEffect(() => {
    refreshRuntimeStatus().catch(() => {});
  }, []);

  async function refreshRuntimeStatus() {
    const health = await api('/api/health').catch(() => defaultRuntimeStatus());
    setRuntimeStatus(health);
    return health;
  }

  function requestDeleteConfirm(options) {
    return new Promise((resolve) => {
      setConfirmDialog({
        title: options.title || '确认删除',
        message: options.message || '删除后不可恢复。',
        detail: options.detail || '',
        confirmLabel: options.confirmLabel || '删除',
        cancelLabel: options.cancelLabel || '取消',
        tone: options.tone || 'danger',
        resolve,
      });
    });
  }

  function closeConfirmDialog(confirmed) {
    setConfirmDialog((dialog) => {
      if (dialog?.resolve) dialog.resolve(Boolean(confirmed));
      return null;
    });
  }

  async function bootstrap() {
    await storeBootstrap();  // Phase 4: Zustand loads token/me/workspace
    const { token, me, workspace } = useAuthStore.getState();
    const [health, agentList, kbList, toolList, modelList, userModelList, marketList, reviewList, promptTemplateList, memberList] = await Promise.all([
      api('/api/health').catch(() => defaultRuntimeStatus()),
      fetchAgents(token).catch(() => []),
      fetchKnowledgeBases(token).catch(() => []),
      fetchTools(token).catch(() => []),
      fetchModels(false, token).catch(() => []),
      fetchUserModels(token).catch(() => []),
      fetchMarketAgents(token).catch(() => []),
      fetchReviews(token).catch(() => []),
      fetchPromptTemplates(false, token).catch(() => []),
      fetchMembers(token).catch(() => []),
    ]);
    setAgents(agentList);
    setKnowledgeBases(kbList);
    setTools(toolList);
    setPromptTemplates(promptTemplateList);
    setModels(modelList);
    setAdminModels(modelList);
    setUserModels(userModelList);
    setRuntimeStatus(health);
    if (isAdminRole(workspace?.role)) {
      const adminModelList = await fetchModels(true, token).catch(() => modelList);
      setAdminModels(adminModelList);
    }
    setMarketAgents(marketList);
    setReviewItems(reviewList);
    setMembers(memberList);
    const publishedAgents = agentList.filter((item) => item.status === 'published' && item.published_version_id);
    const fallbackAgent = publishedAgents[0] || agentList[0];
    if (!activeAgentId && fallbackAgent) {
      setActiveAgentId(fallbackAgent.id);
    } else if (activeAgentId && !agentList.some((item) => item.id === activeAgentId) && fallbackAgent) {
      setActiveAgentId(fallbackAgent.id);
    }
  }

  function logout() {
    // 先中断进行中的 SSE 流，否则登出后旧流仍会继续往 messages 里写 token。
    useChatStore.getState().stopStream();
    // 通知服务端把当前令牌加入黑名单。令牌有效期 24 小时，仅清本地存储的话
    // 泄露的令牌在这段时间内依然可用。失败不阻塞本地登出。
    if (token) {
      fetch(`${API_BASE}/api/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {});
    }
    storeLogout();  // Phase 4: Zustand handles token/me/workspace cleanup
    setAgents([]);
    setActiveAgentId(null);
    useAgentStore.getState().setActiveAgent(null);
    setKnowledgeBases([]);
    setTools([]);
    setPromptTemplates([]);
    setModels([]);
    setAdminModels([]);
    setUserModels([]);
    setRuntimeStatus(defaultRuntimeStatus());
    setMarketAgents([]);
    setReviewItems([]);
    setMembers([]);
    setSessions([]);
    setActiveSessionId(null);
    setSessionTitleDraft('');
    setMessages([]);
    setSources([]);
    setToolDebugEvents([]);
    setDocuments([]);
    setFeedbackByMessage({});
    setChatMode('published');
    setChatVariables({});
    setThinkingEnabled(false);
    setSearchEnabled(false);
    setDraft('');
    setHomePrompt('');
    setProfileError('');
    setMemoryProfile(defaultMemoryProfile());
    setMemoryProfileDraft(profileToDraft(defaultMemoryProfile()));
    setMemoryProfileLoading(false);
    setMemoryProfileSaving(false);
    setMemoryProfileError('');
    setError('');
    setView('home');
    setActiveNav('chat');
    setAccountMenuOpen(false);
  }

  async function authenticate(event) {
    event.preventDefault();
    setError('');
    try {
      const path = authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
      const payload = authMode === 'register'
        ? { email: authForm.email, name: authForm.name, password: authForm.password }
        : { email: authForm.email, password: authForm.password };
      const data = await api(path, { method: 'POST', body: payload });
      localStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
      useAuthStore.getState().setToken(data.access_token);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function loadAgent(agentId) {
    await storeLoadAgent(agentId, token);  // Phase 4: Zustand loads agent + agentForm
    const { activeAgent: agent, agentForm: form } = useAgentStore.getState();
    setRagEnabled(form.rag?.enabled_by_default ?? true);
    setThinkingEnabled(false);
    setSearchEnabled(false);
    setChatVariables(initVariableValues(agent.variables || []));
    setActiveSessionId(null);
    setMessages(agent.opening_message ? [{ role: 'assistant', content: agent.opening_message }] : []);
    setSources([]);
    setToolDebugEvents([]);
    setFeedbackByMessage({});
    await loadMemoryProfile(agentId);
    await loadSessions(agentId);
  }

  async function loadSessions(agentId) {
    const data = await api(`/api/agents/${agentId}/sessions`, { token });
    setSessions(data.items || []);
  }

  async function loadMemoryProfile(agentId = activeAgentId) {
    if (!agentId) return null;
    setMemoryProfileLoading(true);
    setMemoryProfileError('');
    try {
      const data = await api(`/api/agents/${agentId}/memory-profile`, { token });
      const profile = normalizeMemoryProfile(data.profile, agentId);
      setMemoryProfile(profile);
      setMemoryProfileDraft(profileToDraft(profile));
      return profile;
    } catch (err) {
      setMemoryProfile(defaultMemoryProfile(agentId));
      setMemoryProfileDraft(profileToDraft(defaultMemoryProfile(agentId)));
      setMemoryProfileError(errorMessage(err));
      return null;
    } finally {
      setMemoryProfileLoading(false);
    }
  }

  async function saveMemoryProfile() {
    if (!activeAgentId) return;
    setMemoryProfileSaving(true);
    setMemoryProfileError('');
    try {
      const payload = memoryProfilePayload(memoryProfileDraft);
      const data = await api(`/api/agents/${activeAgentId}/memory-profile`, { token, method: 'PATCH', body: payload });
      const profile = normalizeMemoryProfile(data.profile, activeAgentId);
      setMemoryProfile(profile);
      setMemoryProfileDraft(profileToDraft(profile));
    } catch (err) {
      setMemoryProfileError(errorMessage(err));
      throw err;
    } finally {
      setMemoryProfileSaving(false);
    }
  }

  async function deleteMemoryProfile() {
    if (!activeAgentId) return;
    const confirmed = await requestDeleteConfirm({
      title: '删除用户记忆',
      message: '删除当前用户在这个智能体上的资料记忆？',
      detail: '会话摘要不会被删除。',
      confirmLabel: '删除资料',
    });
    if (!confirmed) return;
    setMemoryProfileSaving(true);
    setMemoryProfileError('');
    try {
      await api(`/api/agents/${activeAgentId}/memory-profile`, { token, method: 'DELETE' });
      const profile = defaultMemoryProfile(activeAgentId);
      setMemoryProfile(profile);
      setMemoryProfileDraft(profileToDraft(profile));
    } catch (err) {
      setMemoryProfileError(errorMessage(err));
      throw err;
    } finally {
      setMemoryProfileSaving(false);
    }
  }

  async function loadSession(sessionId, options = {}) {
    const data = await api(`/api/sessions/${sessionId}`, { token });
    setActiveSessionId(sessionId);
    setSessionTitleDraft(data.session?.title || '');
    const loaded = data.messages || [];
    setMessages(loaded);
    setSources([...loaded].reverse().find((item) => item.sources?.length)?.sources || []);
    setToolDebugEvents([]);
    setFeedbackByMessage({});
    if (options.openHome) {
      setView('home');
      setActiveNav('chat');
    }
  }

  async function renameSession() {
    if (!activeSessionId || !sessionTitleDraft.trim()) return;
    const data = await api(`/api/sessions/${activeSessionId}`, {
      token,
      method: 'PATCH',
      body: { title: sessionTitleDraft.trim() },
    });
    setSessionTitleDraft(data.session.title);
    if (activeAgentId) await loadSessions(activeAgentId);
  }

  async function renameSessionById(sessionId, title) {
    const nextTitle = title.trim();
    if (!sessionId || !nextTitle) return null;
    const data = await api(`/api/sessions/${sessionId}`, {
      token,
      method: 'PATCH',
      body: { title: nextTitle },
    });
    if (sessionId === activeSessionId) {
      setSessionTitleDraft(data.session.title);
    }
    if (activeAgentId) await loadSessions(activeAgentId);
    return data.session;
  }

  async function deleteSession(sessionId) {
    if (!sessionId) return;
    await api(`/api/sessions/${sessionId}`, { token, method: 'DELETE' });
    if (sessionId === activeSessionId) {
      setActiveSessionId(null);
      setSessionTitleDraft('');
      setMessages(activeAgent?.opening_message ? [{ role: 'assistant', content: activeAgent.opening_message }] : []);
      setSources([]);
      setToolDebugEvents([]);
      setFeedbackByMessage({});
      setDraft('');
      setHomePrompt('');
      setChatAttachments([]);
    }
    if (activeAgentId) await loadSessions(activeAgentId);
  }

  async function updateProfile(patch) {
    setProfileError('');
    const data = await api('/api/auth/me', { token, method: 'PATCH', body: patch });
    useAuthStore.getState().setMe(data.user);
    return data.user;
  }

  async function refreshModels(includeDisabled = false) {
    const data = await api(includeDisabled ? '/api/models?include_disabled=true' : '/api/models', { token });
    if (includeDisabled) {
      setAdminModels(data.items || []);
    } else {
      setModels(data.items || []);
    }
    return data.items || [];
  }

  async function createModelConfig(payload) {
    await api('/api/admin/models', { token, method: 'POST', body: payload });
    await refreshModels(false);
    await refreshModels(true);
  }

  async function updateModelConfig(modelId, patch) {
    await api(`/api/admin/models/${modelId}`, { token, method: 'PATCH', body: patch });
    await refreshModels(false);
    await refreshModels(true);
  }

  async function deleteModelConfig(modelId) {
    await api(`/api/admin/models/${modelId}`, { token, method: 'DELETE' });
    await refreshModels(false);
    await refreshModels(true);
  }

  async function refreshUserModels() {
    const data = await api('/api/user-models', { token });
    setUserModels(data.items || []);
    return data.items || [];
  }

  async function createUserModelConfig(payload) {
    const data = await api('/api/user-models', { token, method: 'POST', body: payload });
    await refreshUserModels();
    return data.model_config;
  }

  async function testUserModelDraft(payload) {
    return api('/api/user-models/test', { token, method: 'POST', body: payload });
  }

  async function probeUserModels(payload) {
    return api('/api/user-models/probe-models', { token, method: 'POST', body: payload });
  }

  async function updateUserModelConfig(configId, patch) {
    const data = await api(`/api/user-models/${configId}`, { token, method: 'PATCH', body: patch });
    await refreshUserModels();
    return data.model_config;
  }


  async function deleteUserModelConfig(configId) {
    await api(`/api/user-models/${configId}`, { token, method: 'DELETE' });
    await refreshUserModels();
  }

  async function testUserModelConfig(configId) {
    return api(`/api/user-models/${configId}/test?detect_image=true`, { token, method: 'POST' });
  }

  async function refreshTools() {
    const data = await api('/api/tools', { token });
    setTools(data.items || []);
    return data.items || [];
  }

  async function createToolConfig(payload) {
    await api('/api/tools', { token, method: 'POST', body: payload });
    await refreshTools();
  }

  async function updateToolConfig(toolId, patch) {
    await api(`/api/tools/${toolId}`, { token, method: 'PATCH', body: patch });
    await refreshTools();
    if (activeAgentId) {
      await loadAgent(activeAgentId);
    }
  }

  async function deleteToolConfig(toolId) {
    await api(`/api/tools/${toolId}`, { token, method: 'DELETE' });
    await refreshTools();
    if (activeAgentId) {
      await loadAgent(activeAgentId);
    }
  }

  async function testToolConfig(toolId, payload) {
    return api(`/api/tools/${toolId}/test`, { token, method: 'POST', body: payload });
  }

  async function refreshPromptTemplates(includeDisabled = false) {
    const data = await api(includeDisabled ? '/api/prompt-templates?include_disabled=true' : '/api/prompt-templates', { token });
    setPromptTemplates(data.items || []);
    return data.items || [];
  }

  async function createPromptTemplate(payload) {
    const data = await api('/api/prompt-templates', { token, method: 'POST', body: payload });
    await refreshPromptTemplates();
    return data.template;
  }

  async function updatePromptTemplate(templateId, patch) {
    const data = await api(`/api/prompt-templates/${templateId}`, { token, method: 'PATCH', body: patch });
    await refreshPromptTemplates(true);
    return data.template;
  }

  async function deletePromptTemplate(templateId) {
    await api(`/api/prompt-templates/${templateId}`, { token, method: 'DELETE' });
    await refreshPromptTemplates(true);
  }

  async function copyBuiltinPromptTemplate(payload) {
    const data = await api('/api/prompt-templates/copy-builtin', { token, method: 'POST', body: payload });
    await refreshPromptTemplates();
    return data.template;
  }

  function openAgentIdentityDialog(mode = 'edit') {
    setAgentIdentityError('');
    setAgentIdentityDialog({
      mode,
      form: mode === 'create'
        ? pickAgentIdentity(defaultAgentForm())
        : pickAgentIdentity(agentForm),
    });
  }

  async function submitAgentIdentity(identity) {
    const nextIdentity = normalizeAgentIdentity(identity);
    if (!nextIdentity.name) {
      setAgentIdentityError('请填写智能体名称。');
      return;
    }
    setAgentIdentitySaving(true);
    setAgentIdentityError('');
    try {
      if (agentIdentityDialog?.mode === 'create') {
        const form = { ...defaultAgentForm(), ...nextIdentity };
        const data = await api('/api/agents', { token, method: 'POST', body: agentPayload(form) });
        await bootstrap();
        setActiveAgentId(data.agent.id);
        setView('builder');
        setActiveNav('agents');
      } else if (activeAgentId) {
        const form = { ...agentForm, ...nextIdentity };
        setAgentForm(form);
        await api(`/api/agents/${activeAgentId}`, { token, method: 'PATCH', body: agentPayload(form) });
        await bootstrap();
        await loadAgent(activeAgentId);
      }
      setAgentIdentityDialog(null);
    } catch (err) {
      setAgentIdentityError(errorMessage(err));
    } finally {
      setAgentIdentitySaving(false);
    }
  }

  async function createAgent(openBuilder = true) {
    if (openBuilder) {
      openAgentIdentityDialog('create');
      return;
    }
    const data = await api('/api/agents', { token, method: 'POST', body: agentPayload(defaultAgentForm()) });
    await bootstrap();
    setActiveAgentId(data.agent.id);
  }

  async function saveAgent() {
    if (!activeAgentId) return true;
    const body = agentPayload(agentForm, { model: selectedDraftModel });
    try {
      await api(`/api/agents/${activeAgentId}`, { token, method: 'PATCH', body });
      // 与预览前自动保存共用同一签名基线，手动保存后预览不再重复 PATCH
      lastDraftSaveSigRef.current = JSON.stringify(body);
      await bootstrap();
      await loadAgent(activeAgentId);
      return true;
    } catch (err) {
      // 之前保存失败是静默的（按钮无 catch），导致「改了以为存了」。必须用 toast 暴露。
      notify(`保存失败：${errorMessage(err)}`);
      return false;
    }
  }

  async function publishAgent() {
    if (!activeAgentId) return;
    const saved = await saveAgent();
    if (!saved) return; // 保存失败已提示，不继续发布
    try {
      const data = await api(`/api/agents/${activeAgentId}/publish`, { token, method: 'POST' });
      notify(data.review_required ? '已提交管理员审核，通过后会出现在市场。' : '已发布到市场。');
      await bootstrap();
    } catch (err) {
      notify(`发布失败：${errorMessage(err)}`);
    }
  }

  async function copyMarketAgent(agentId) {
    const data = await api(`/api/market/agents/${agentId}/copy`, { token, method: 'POST' });
    await bootstrap();
    setActiveAgentId(data.agent.id);
    setView('builder');
    setActiveNav('agents');
    notify('已复制到你的智能体草稿。');
  }

  async function approveReview(agentId) {
    await api(`/api/admin/agent-reviews/${agentId}/approve`, { token, method: 'POST' });
    await bootstrap();
    notify('审核通过，智能体已上架市场。');
  }

  async function rejectReview(agentId) {
    await api(`/api/admin/agent-reviews/${agentId}/reject`, { token, method: 'POST' });
    await bootstrap();
    notify('已驳回该智能体发布申请。');
  }

  async function createKnowledgeBase(payload = defaultKnowledgeBaseForm()) {
    const body = {
      name: String(payload?.name || '').trim(),
      description: String(payload?.description || '').trim(),
    };
    if (!body.name) {
      throw new Error('知识库名称不能为空。');
    }
    const data = await api('/api/knowledge-bases', { token, method: 'POST', body });
    await bootstrap();
    return data.knowledge_base;
  }

  async function updateKnowledgeBase(kbId, payload) {
    const body = {
      name: String(payload?.name || '').trim(),
      description: String(payload?.description || '').trim(),
    };
    if (!body.name) {
      throw new Error('知识库名称不能为空。');
    }
    const data = await api(`/api/knowledge-bases/${kbId}`, { token, method: 'PATCH', body });
    await bootstrap();
    return data.knowledge_base;
  }

  async function uploadDocument() {
    const kbId = Number(docForm.kb_id || knowledgeBases[0]?.id);
    if (!kbId) {
      notify('请先创建知识库。');
      return;
    }
    const filename = String(docForm.filename || 'guide.txt').trim();
    const text = String(docForm.text || '').trim();
    if (!text) {
      notify('请先粘贴要写入知识库的文本。');
      return;
    }
    try {
      await api(`/api/knowledge-bases/${kbId}/documents`, {
        token,
        method: 'POST',
        body: {
          title: filename,
          filename,
          content: text,
          content_type: 'text/plain',
          source_type: 'text',
        },
      });
      setDocForm({ filename: 'guide.txt', text: '', kb_id: String(kbId) });
      await loadDocuments(kbId);
      await bootstrap();
    } catch (err) {
      notify(`写入知识库失败：${errorMessage(err)}`);
      throw err;
    }
  }

  async function uploadKnowledgeFile(file, segmentConfig = null) {
    if (!file || !token) return;
    const kbId = Number(docForm.kb_id || knowledgeBases[0]?.id);
    if (!kbId) {
      setError('请先创建或选择知识库。');
      return;
    }
    setUploadingKnowledgeFile(true);
    setUploadingFileName(file.name);
    setError('');
    try {
      validateKnowledgeFile(file);
      const contentType = file.type || guessContentType(file.name);
      const contentBase64 = await fileToBase64(file);
      await api(`/api/knowledge-bases/${kbId}/documents`, {
        token,
        method: 'POST',
        body: {
          filename: file.name,
          title: file.name,
          content_type: contentType,
          content_base64: contentBase64,
          source_type: 'file',
          ...(segmentConfig ? { segment_config: segmentConfig } : {}),
        },
      });
      setDocForm((form) => ({ ...form, filename: file.name, text: '', kb_id: String(kbId) }));
      await loadDocuments(kbId);
      await bootstrap();
    } catch (err) {
      // 知识库页面不渲染聊天的 error 状态，失败必须用全局 toast 暴露，否则表现为「没反应」
      notify(`上传失败：${errorMessage(err)}`);
      setError(errorMessage(err));
    } finally {
      setUploadingKnowledgeFile(false);
      setUploadingFileName('');
    }
  }

  async function deleteDocument(documentId) {
    if (!activeKbId || !documentId) return;
    const document = documents.find((item) => item.id === documentId);
    const confirmed = await requestDeleteConfirm({
      title: '删除知识文档',
      message: `\u5220\u9664\u6587\u6863\u300c${document?.title || document?.filename || `document-${documentId}`}\u300d\uff1f`,
      detail: '该文档的分块和索引会一起删除。',
      confirmLabel: '删除文档',
    });
    if (!confirmed) return;
    try {
      await api(`/api/knowledge-bases/${activeKbId}/documents/${documentId}`, { token, method: 'DELETE' });
      await loadDocuments(activeKbId);
      await bootstrap();
    } catch (err) {
      notify(`删除文档失败：${errorMessage(err)}`);
    }
  }

  async function deleteKnowledgeBase(kb) {
    if (!kb?.id) return;
    const confirmed = await requestDeleteConfirm({
      title: '删除知识库',
      message: `删除知识库「${kb.name}」？`,
      detail: '所有文档、分块和向量数据会一起删除。',
      confirmLabel: '删除知识库',
    });
    if (!confirmed) return;
    try {
      await api(`/api/knowledge-bases/${kb.id}`, { token, method: 'DELETE' });
      if (String(activeKbId) === String(kb.id)) {
        setDocuments([]);
      }
      await bootstrap();
      notify('知识库已删除。');
    } catch (err) {
      notify(`删除知识库失败：${errorMessage(err)}`);
    }
  }

  async function deleteAgent(agent) {
    if (!agent?.id || agent.is_template) return;
    const allowed = canManage || agent.created_by === me?.id;
    if (!allowed) return;
    const confirmed = await requestDeleteConfirm({
      title: '删除智能体',
      message: `删除智能体「${agent.name || '未命名智能体'}」？`,
      detail: '删除后无法恢复，确定要删除吗？',
      confirmLabel: '删除智能体',
    });
    if (!confirmed) return;

    await api(`/api/agents/${agent.id}`, { token, method: 'DELETE' });
    const remaining = agents.filter((item) => item.id !== agent.id);
    const nextId = remaining[0]?.id || null;
    setAgents(remaining);
    setActiveAgentId(nextId);
    setActiveSessionId(null);
    setSessionTitleDraft('');
    setMessages([]);
    setSources([]);
    setFeedbackByMessage({});
    setChatVariables({});
    setView('home');
    setActiveNav(nextId ? 'agents' : 'chat');
    await bootstrap();
    if (nextId) {
      await loadAgent(nextId);
    } else {
      useAgentStore.getState().setActiveAgent(null);
      setAgentForm(defaultAgentForm());
      setSessions([]);
    }
  }

  function startNewChat() {
    setActiveSessionId(null);
    setSessionTitleDraft('');
    setMessages(activeAgent?.opening_message ? [{ role: 'assistant', content: activeAgent.opening_message }] : []);
    setSources([]);
    setToolDebugEvents([]);
    setFeedbackByMessage({});
    setDraft('');
    setHomePrompt('');
    setError('');
    setChatVariables(initVariableValues(agentForm.variables || []));
    setRagEnabled(agentForm.rag?.enabled_by_default ?? true);
    setThinkingEnabled(false);
    setSearchEnabled(false);
    setChatAttachments([]);
    if (view !== 'builder') {
      setView('home');
      setActiveNav('chat');
    }
  }

  async function submitFeedback(messageId, rating) {
    if (!messageId) return;
    const data = await api(`/api/messages/${messageId}/feedback`, {
      token,
      method: 'POST',
      body: { rating, comment: '' },
    });
    setFeedbackByMessage((items) => ({ ...items, [messageId]: data.feedback.rating }));
  }

  const chatModeRef = useRef(chatMode);
  useEffect(() => {
    chatModeRef.current = chatMode;
  }, [chatMode]);

  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  // 草稿预览自动保存的去重签名：记录上次已落库的 payload，避免每条消息都重复 PATCH
  const lastDraftSaveSigRef = useRef('');

  // Clean chat state when switching between draft debugging and published preview
  useEffect(() => {
    setMessages([]);
    setSources([]);
    setToolDebugEvents([]);
    setActiveSessionId(null);
    setError('');
  }, [chatMode]);

  async function sendMessage(event, explicitText) {
    event?.preventDefault();
    const text = (explicitText ?? draft ?? homePrompt).trim();
    const outgoingAttachments = chatAttachments;
    if ((!text && !outgoingAttachments.length) || !activeAgentId || busy) return;
    if (viewRef.current !== 'builder' && (!activeSummary || activeSummary.status !== 'published' || !activeSummary.published_version_id)) {
      const firstPublished = chatAgents[0];
      if (firstPublished) {
        setActiveAgentId(firstPublished.id);
      }
      setError('对话只能使用已经过审核并上架的智能体。');
      return;
    }
    const currentModel = viewRef.current === 'builder' ? selectedDraftModel : activeAgent?.user_model_config || activeAgent?.model_config || null;
    const modelWarning = modelCapabilityWarning(currentModel, outgoingAttachments);
    if (modelWarning) {
      setError(modelWarning);
      return;
    }
    const effectiveRagEnabled = ragRuntime.available && ragEnabled;
    const thinkingCapability = reasoningCapabilityForModel(currentModel);
    const effectiveThinkingEnabled = thinkingEnabled && thinkingCapability.supported;
    const effectiveSearchEnabled = webSearchRuntime.available && searchEnabled;
    if (thinkingEnabled && !thinkingCapability.supported) {
      setThinkingEnabled(false);
    }
    // 预览前自动保存草稿：草稿调试预览读的是后端 DB 状态，而非本地未保存的 agentForm。
    // 若不先落库，刚加的工具/知识库/配置不会在预览里生效（典型坑：加了工具却"搜不到"）。
    // 仅在「编排页 + 草稿模式 + 可编辑 + 确有改动」时触发，用签名去重避免每条消息重复 PATCH。
    if (viewRef.current === 'builder' && chatModeRef.current === 'draft' && canEditActive && activeAgentId) {
      const draftPayload = agentPayload(agentForm, { model: selectedDraftModel });
      const sig = JSON.stringify(draftPayload);
      if (sig !== lastDraftSaveSigRef.current) {
        try {
          await api(`/api/agents/${activeAgentId}`, { token, method: 'PATCH', body: draftPayload });
          lastDraftSaveSigRef.current = sig;
        } catch (err) {
          setError(errorMessage(err));
          return;
        }
      }
    }
    try {
      await storeSendMessage({
        text,
        activeAgentId,
        token,
        sessionId: activeSessionId || null,
        mode: viewRef.current === 'builder' ? chatModeRef.current : 'published',
        isDebug: viewRef.current === 'builder',
        ragEnabled: effectiveRagEnabled,
        ragOptions: agentForm.rag || undefined,
        thinkingEnabled: effectiveThinkingEnabled,
        searchEnabled: effectiveSearchEnabled,
        variables: castVariables(agentForm.variables || [], chatVariables),
        chatAttachments: outgoingAttachments,
      });
    } catch (err) {
      if (isAuthError(err)) {
        logout();
        setError('登录已失效，请重新登录。');
      } else {
        setError(errorMessage(err));
      }
    } finally {
      if (activeAgentId) loadSessions(activeAgentId).catch((err) => setError(errorMessage(err)));
    }
  }

  async function uploadChatAttachment(file) {
    if (!file || !token) return;
    setUploadingAttachment(true);
    setError('');
    try {
      if (file.size > MAX_UPLOAD_BYTES) {
        throw new Error('Upload file cannot exceed 8MB');
      }
      const contentType = file.type || guessContentType(file.name);
      const contentBase64 = await fileToBase64(file);
      const data = await api('/api/uploads', {
        token,
        method: 'POST',
        body: { filename: file.name, content_type: contentType, content_base64: contentBase64 },
      });
      setChatAttachments((items) => [...items, data.upload]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploadingAttachment(false);
    }
  }

  function addSuggestedQuestion() {
    setAgentForm((form) => ({ ...form, suggested_questions: [...(form.suggested_questions || []), '新的推荐问题'] }));
  }

  function updateSuggestedQuestion(index, value) {
    setAgentForm((form) => ({
      ...form,
      suggested_questions: (form.suggested_questions || []).map((item, itemIndex) => (itemIndex === index ? value : item)),
    }));
  }

  function removeSuggestedQuestion(index) {
    setAgentForm((form) => ({
      ...form,
      suggested_questions: (form.suggested_questions || []).filter((_, itemIndex) => itemIndex !== index),
    }));
  }

  function sendSuggestedQuestion(question) {
    sendMessage(null, question).catch((err) => setError(errorMessage(err)));
  }

  function addVariable() {
    setAgentForm((form) => {
      const key = `var_${(form.variables || []).length + 1}`;
      return {
        ...form,
        variables: [...(form.variables || []), { key, label: '变量', type: 'string', required: false, default_value: '' }],
      };
    });
  }

  function updateVariable(index, patch) {
    setAgentForm((form) => ({
      ...form,
      variables: (form.variables || []).map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)),
    }));
  }

  function removeVariable(index) {
    setAgentForm((form) => ({
      ...form,
      variables: (form.variables || []).filter((_, itemIndex) => itemIndex !== index),
    }));
  }

  function updateChatVariable(key, value) {
    setChatVariables((items) => ({ ...items, [key]: value }));
  }

  function openBuilder(agentId = activeAgentId) {
    if (agentId && agentId !== activeAgentId) setActiveAgentId(agentId);
    setView('builder');
    setActiveNav('agents');
  }

  function applyRoleTemplate() {
    setAgentForm((form) => ({
      ...form,
      name: '智能体一号',
      avatar: '66',
      description: '喜欢唱跳 rap 篮球的活泼角色扮演智能体。',
      opening_message: '你好',
      system_prompt: JIGE_PROMPT,
      suggested_questions: ['你想问啥？'],
      variables: [
        { key: 'city', label: '城市', type: 'string', required: false, default_value: '杭州' },
        { key: 'device_model', label: '设备型号', type: 'string', required: false, default_value: 'S10' },
      ],
      memory: { enabled: true, strategy: 'session_summary', max_messages: 48 },
    }));
  }

  if (!token) {
    return (
      <main className="auth-shell">
        <section className="auth-panel">
          <div className="brand-mark"><Sparkles size={22} /> Lingshu Agent</div>
          <h1>创建你的智能体工作台</h1>
          <p>本地账号、智能体聊天主页、我的模型配置和可发布的智能体工作台。</p>
          <form onSubmit={authenticate} className="auth-form">
            <div className="auth-toggle">
              <button type="button" className={authMode === 'register' ? 'active' : ''} onClick={() => setAuthMode('register')}>注册</button>
              <button type="button" className={authMode === 'login' ? 'active' : ''} onClick={() => setAuthMode('login')}>登录</button>
            </div>
            <input value={authForm.email} onChange={(e) => setAuthForm({ ...authForm, email: e.target.value })} placeholder="邮箱" />
            {authMode === 'register' && <input value={authForm.name} onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })} placeholder="姓名" />}
            <input type="password" value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} placeholder="密码，至少 8 位" />
            <button className="primary" type="submit"><LogIn size={18} />进入工作台</button>
          </form>
          {error && <p className="error">{error}</p>}
        </section>
      </main>
    );
  }

  const shellProps = {
    activeAgent,
    activeAgentId,
    activeNav,
    activeSummary,
    agentForm,
    agents,
    adminModels,
    busy,
    canManage,
    canEditActive,
    chatMode,
    chatAgents,
    chatAttachments,
    chatVariables,
    copyMarketAgent,
    copyBuiltinPromptTemplate,
    createAgent,
    createKnowledgeBase,
    updateKnowledgeBase,
    createModelConfig,
    createPromptTemplate,
    createToolConfig,
    createUserModelConfig,
    deleteAgent,
    deleteDocument,
    deleteKnowledgeBase,
    deleteModelConfig,
    deletePromptTemplate,
    deleteSession,
    deleteToolConfig,
    deleteUserModelConfig,
    requestDeleteConfirm,
    docForm,
    documents,
    draft,
    error,
    feedbackByMessage,
    homePrompt,
    knowledgeBases,
    loadSession,
    logout,
    marketAgents,
    me,
    members,
    messages,
    models,
    openBuilder,
    publishAgent,
    promptTemplates,
    memoryProfile,
    memoryProfileDraft,
    memoryProfileError,
    memoryProfileLoading,
    memoryProfileSaving,
    renameSessionById,
    // 页面模块拆分时漏传这四项：activeSessionId 缺失会让会话列表无法高亮当前会话，
    // 且 BuilderView 的标题编辑器整块 (activeSessionId && ...) 永远不渲染。
    // 四者必须一起补——只补 activeSessionId 会让编辑器开始渲染，然后在首次输入时
    // 因 setSessionTitleDraft 未定义而抛错。
    activeSessionId,
    sessionTitleDraft,
    setSessionTitleDraft,
    renameSession,
    ragRuntime,
    searchEnabled,
    thinkingEnabled,
    saveAgent,
    saveMemoryProfile,
    sendMessage,
    sendSuggestedQuestion,
    sessions,
    setError,
    setActiveAgentId,
    setActiveNav,
    setAgentForm,
    setChatMode,
    setAccountMenuOpen,
    setDocForm,
    setDraft,
    setHomePrompt,
    setMemoryProfileDraft,
    setRagEnabled,
    setSearchEnabled,
    setThinkingEnabled,
    setView,
    sources,
    startNewChat,
    submitFeedback,
    deleteMemoryProfile,
    approveReview,
    rejectReview,
    reviewItems,
    tools,
    toolDebugEvents,
    testToolConfig,
    testUserModelDraft,
    uploadChatAttachment,
    uploadingAttachment,
    uploadingKnowledgeFile,
    uploadingFileName,
    uploadDocument,
    uploadKnowledgeFile,
    loadDocuments,
    notify,
    updateChatVariable,
    updateModelConfig,
    updatePromptTemplate,
    updateProfile,
    updateToolConfig,
    updateUserModelConfig,
    view,
    workspace,
    accountMenuOpen,
    profileDialogOpen,
    profileError,
    setProfileError,
    setProfileDialogOpen,
    ragEnabled,
    setChatAttachments,
    testUserModelConfig,
    token,
    userModels,
    webSearchRuntime,
  };

  const builderProps = {
    ...shellProps,
    addSuggestedQuestion,
    addVariable,
    applyRoleTemplate,
    openAgentIdentityDialog,
    removeSuggestedQuestion,
    removeVariable,
    setAgentForm,
    updateSuggestedQuestion,
    updateVariable,
  };

  return (
    <BrowserRouter>
    <>
      <Suspense fallback={<PageFallback />}>
        {view === 'builder' ? <BuilderView {...builderProps} /> : <HomeView {...shellProps} />}
      </Suspense>
      {agentIdentityDialog && (
        <AgentIdentityDialog
          error={agentIdentityError}
          initialForm={agentIdentityDialog.form}
          mode={agentIdentityDialog.mode}
          onCancel={() => {
            if (!agentIdentitySaving) setAgentIdentityDialog(null);
          }}
          onSubmit={submitAgentIdentity}
          saving={agentIdentitySaving}
        />
      )}
      {confirmDialog && (
        <ConfirmDialog
          cancelLabel={confirmDialog.cancelLabel}
          confirmLabel={confirmDialog.confirmLabel}
          detail={confirmDialog.detail}
          message={confirmDialog.message}
          onCancel={() => closeConfirmDialog(false)}
          onConfirm={() => closeConfirmDialog(true)}
          title={confirmDialog.title}
          tone={confirmDialog.tone}
        />
      )}
      {toastMsg && <div className="toast success">{toastMsg}</div>}
    </>
    </BrowserRouter>
  );
}


function NavButton({ active, icon, label, onClick }) {
  return (
    <button type="button" className={active ? 'active' : ''} onClick={onClick}>
      {icon}
      {label}
    </button>
  );
}


function HomeView(props) {
  const {
    activeAgent,
    activeAgentId,
    activeNav,
    activeSessionId,
    activeSummary,
    agentForm,
    agents,
    adminModels,
    busy,
    canManage,
    canEditActive,
    chatMode,
    chatAgents,
    chatAttachments,
    chatVariables,
    copyBuiltinPromptTemplate,
    copyMarketAgent,
    createAgent,
    createKnowledgeBase,
    updateKnowledgeBase,
    createModelConfig,
    createPromptTemplate,
    createToolConfig,
    createUserModelConfig,
    deleteAgent,
    deleteDocument,
    deleteKnowledgeBase,
    deleteModelConfig,
    deletePromptTemplate,
    deleteSession,
    deleteToolConfig,
    deleteUserModelConfig,
    docForm,
    documents,
    draft,
    error,
    feedbackByMessage,
    homePrompt,
    knowledgeBases,
    loadSession,
    logout,
    marketAgents,
    me,
    members,
    messages,
    openBuilder,
    promptTemplates,
    requestDeleteConfirm,
    renameSessionById,
    sendMessage,
    sendSuggestedQuestion,
    sessions,
    setError,
    setActiveAgentId,
    setActiveNav,
    setAgentForm,
    setAccountMenuOpen,
    setChatMode,
    setChatAttachments,
    setDocForm,
    setHomePrompt,
    setView,
    sources,
    startNewChat,
    submitFeedback,
    approveReview,
    rejectReview,
    reviewItems,
    tools,
    testToolConfig,
    uploadChatAttachment,
    uploadingAttachment,
    uploadingKnowledgeFile,
    uploadingFileName,
    uploadDocument,
    uploadKnowledgeFile,
    loadDocuments,
    notify,
    updateChatVariable,
    updateModelConfig,
    updatePromptTemplate,
    updateProfile,
    updateToolConfig,
    updateUserModelConfig,
    workspace,
    accountMenuOpen,
    profileDialogOpen,
    profileError,
    ragEnabled,
    ragRuntime,
    searchEnabled,
    setSearchEnabled,
    setRagEnabled,
    setThinkingEnabled,
    setProfileError,
    setProfileDialogOpen,
    testUserModelDraft,
    testUserModelConfig,
    token,
    userModels,
    webSearchRuntime,
    thinkingEnabled,
  } = props;
  const [sessionMenuId, setSessionMenuId] = useState(null);
  const [sessionMenuPosition, setSessionMenuPosition] = useState(null);
  const [renamingSessionId, setRenamingSessionId] = useState(null);
  const [sessionRenameDraft, setSessionRenameDraft] = useState('');
  const showWelcome = messages.length === 0;
  const selectedMenuSession = useMemo(
    () => sessions.find((session) => session.id === sessionMenuId),
    [sessionMenuId, sessions],
  );

  useEffect(() => {
    if (!sessionMenuId) return undefined;
    const closeMenu = () => {
      setSessionMenuId(null);
      setSessionMenuPosition(null);
    };
    window.addEventListener('click', closeMenu);
    window.addEventListener('resize', closeMenu);
    window.addEventListener('scroll', closeMenu, true);
    return () => {
      window.removeEventListener('click', closeMenu);
      window.removeEventListener('resize', closeMenu);
      window.removeEventListener('scroll', closeMenu, true);
    };
  }, [sessionMenuId]);

  function beginSessionRename(session) {
    setRenamingSessionId(session.id);
    setSessionRenameDraft(session.title || '');
    setSessionMenuId(null);
    setSessionMenuPosition(null);
  }

  function cancelSessionRename() {
    setRenamingSessionId(null);
    setSessionRenameDraft('');
  }

  async function submitSessionRename(event) {
    event?.preventDefault();
    const title = sessionRenameDraft.trim();
    if (!renamingSessionId || !title) return;
    await renameSessionById(renamingSessionId, title);
    cancelSessionRename();
  }

  async function confirmDeleteSession(session) {
    setSessionMenuId(null);
    setSessionMenuPosition(null);
    const confirmed = await requestDeleteConfirm({
      title: '删除会话',
      message: `删除会话「${session.title || '未命名会话'}」？`,
      detail: '该会话中的消息、反馈和调试记录会一起删除。',
      confirmLabel: '删除会话',
    });
    if (!confirmed) return;
    try {
      setError('');
      await deleteSession(session.id);
      if (renamingSessionId === session.id) {
        cancelSessionRename();
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <main className="chat-app">
      <aside className="chat-sidebar">
        <div className="sidebar-brand">
          <span className="brand-dot"><Sparkles size={18} /></span>
          <strong>Lingshu Agent</strong>
        </div>
        <button className="new-chat" type="button" onClick={startNewChat}><SquarePen size={16} />新建会话</button>
        <nav className="main-nav">
          <NavButton icon={<Home size={17} />} label="首页" active={activeNav === 'chat'} onClick={() => setActiveNav('chat')} />
          <NavButton icon={<Bot size={17} />} label="智能体" active={activeNav === 'agents'} onClick={() => setActiveNav('agents')} />
          <NavButton icon={<Boxes size={17} />} label="市场" active={activeNav === 'market'} onClick={() => setActiveNav('market')} />
          <NavButton icon={<ServerCog size={17} />} label="我的模型" active={activeNav === 'my-models'} onClick={() => setActiveNav('my-models')} />
          <NavButton icon={<Layers size={17} />} label="资源库" active={activeNav === 'resources'} onClick={() => setActiveNav('resources')} />
          <NavButton icon={<Wand2 size={17} />} label="工具" active={activeNav === 'tools'} onClick={() => setActiveNav('tools')} />
          {canManage && <NavButton icon={<Shield size={17} />} label="审核" active={activeNav === 'reviews'} onClick={() => setActiveNav('reviews')} />}
          {canManage && <NavButton icon={<KeyRound size={17} />} label="成员" active={activeNav === 'members'} onClick={() => setActiveNav('members')} />}
          <NavButton icon={<Database size={17} />} label="知识库" active={activeNav === 'knowledge'} onClick={() => setActiveNav('knowledge')} />
        </nav>
        <div className="sidebar-section">
          <div className="sidebar-heading">
            <span>会话</span>
            <button type="button" onClick={startNewChat}><Plus size={14} /></button>
          </div>
          <div className="session-list">
            {sessions.map((session) => (
              <div key={session.id} className={`session-row ${session.id === activeSessionId ? 'active' : ''}`}>
                {renamingSessionId === session.id ? (
                  <form className="session-rename-form" onSubmit={(event) => submitSessionRename(event).catch((err) => console.error(err))}>
                    <MessageSquare size={14} />
                    <input
                      autoFocus
                      value={sessionRenameDraft}
                      onChange={(event) => setSessionRenameDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Escape') {
                          event.preventDefault();
                          cancelSessionRename();
                        }
                      }}
                      placeholder="会话标题"
                    />
                    <button type="submit" title="保存" aria-label="保存会话标题" disabled={!sessionRenameDraft.trim()}><Check size={14} /></button>
                    <button type="button" title="取消" aria-label="取消重命名" onClick={cancelSessionRename}><X size={14} /></button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      className="session-main"
                      onClick={() => {
                        setSessionMenuId(null);
                        setSessionMenuPosition(null);
                        loadSession(session.id, { openHome: true }).catch((err) => console.error(err));
                      }}
                    >
                      <MessageSquare size={14} />
                      <span>{session.title}</span>
                    </button>
                    <button
                      type="button"
                      className="session-more"
                      title="会话操作"
                      aria-label="会话操作"
                      onClick={(event) => {
                        event.stopPropagation();
                        if (sessionMenuId === session.id) {
                          setSessionMenuId(null);
                          setSessionMenuPosition(null);
                          return;
                        }
                        const rect = event.currentTarget.getBoundingClientRect();
                        const sidebarRect = event.currentTarget.closest('.chat-sidebar')?.getBoundingClientRect();
                        setSessionMenuPosition({
                          left: (sidebarRect?.right ?? rect.right) + 8,
                          top: Math.max(8, Math.min(rect.top - 2, window.innerHeight - 104)),
                        });
                        setSessionMenuId(session.id);
                      }}
                    >
                      <MoreHorizontal size={15} />
                    </button>
                  </>
                )}
              </div>
            ))}
            {sessions.length === 0 && <p className="sidebar-empty">还没有历史会话</p>}
          </div>
        </div>
        <div className="sidebar-user-wrap">
          {accountMenuOpen && (
            <div className="account-menu">
              <button
                className="account-menu-card"
                type="button"
                onClick={() => {
                  setProfileDialogOpen(true);
                  setAccountMenuOpen(false);
                }}
              >
                <UserAvatar user={me} className="account-avatar" />
                <span className="account-card-copy">
                  <strong>{me?.name || me?.email || '当前用户'}</strong>
                  <small>{roleLabel(workspace?.role)}</small>
                </span>
                <ChevronLeft className="account-chevron" size={16} />
              </button>
              <div className="account-menu-group">
                <button
                  type="button"
                  onClick={() => {
                    setProfileDialogOpen(true);
                    setAccountMenuOpen(false);
                  }}
                >
                  <KeyRound size={16} />
                  个人资料
                </button>
              </div>
              <button
                className="account-menu-logout"
                type="button"
                onClick={logout}
              >
                <LogOut size={16} />
                退出登录
              </button>
            </div>
          )}
          <button className="sidebar-user" type="button" onClick={() => setAccountMenuOpen(!accountMenuOpen)}>
            <UserAvatar user={me} className="account-avatar small" />
            <span className="sidebar-user-copy">
              <strong>{me?.name || me?.email || '当前用户'}</strong>
              <small>{roleLabel(workspace?.role)}</small>
            </span>
            <MoreHorizontal size={18} />
          </button>
        </div>
      </aside>

      {selectedMenuSession && sessionMenuPosition && (
        <div
          className="session-menu session-menu-floating"
          style={{ left: `${sessionMenuPosition.left}px`, top: `${sessionMenuPosition.top}px` }}
          onClick={(event) => event.stopPropagation()}
        >
          <button type="button" onClick={() => beginSessionRename(selectedMenuSession)}>
            <SquarePen size={14} />
            重命名
          </button>
          <button type="button" className="danger" onClick={() => confirmDeleteSession(selectedMenuSession).catch((err) => console.error(err))}>
            <Trash2 size={14} />
            删除
          </button>
        </div>
      )}

      <section className={`chat-main ${activeNav === 'chat' ? '' : 'no-topbar'}`}>
        <Suspense fallback={<PageFallback />}>
         {activeNav === 'chat' && (
          <ChatView
            activeAgent={activeAgent}
            activeAgentId={activeAgentId}
            activeSummary={activeSummary}
            chatAgents={chatAgents}
            canEditActive={canEditActive}
            openBuilder={openBuilder}
            setActiveAgentId={setActiveAgentId}
            activeSessionId={activeSessionId}
            agentForm={agentForm}
            busy={busy}
            chatAttachments={chatAttachments}
            chatVariables={chatVariables}
            error={error}
            feedbackByMessage={feedbackByMessage}
            homePrompt={homePrompt}
            messages={messages}
            sendMessage={sendMessage}
            sendSuggestedQuestion={sendSuggestedQuestion}
            setChatAttachments={setChatAttachments}
            setHomePrompt={setHomePrompt}
            ragEnabled={ragEnabled}
            setRagEnabled={setRagEnabled}
            searchEnabled={searchEnabled}
            setSearchEnabled={setSearchEnabled}
            sources={sources}
            submitFeedback={submitFeedback}
            ragRuntime={ragRuntime}
            webSearchRuntime={webSearchRuntime}
            thinkingEnabled={thinkingEnabled}
            setThinkingEnabled={setThinkingEnabled}
            uploadChatAttachment={uploadChatAttachment}
            uploadingAttachment={uploadingAttachment}
            updateChatVariable={updateChatVariable}
          />
        )}

        {activeNav === 'members' && canManage && (
          <MembersHome members={members} />
        )}

        {activeNav === 'knowledge' && (
          <KnowledgeHome
            canManage={canManage}
            createKnowledgeBase={createKnowledgeBase}
            updateKnowledgeBase={updateKnowledgeBase}
            deleteDocument={deleteDocument}
            deleteKnowledgeBase={deleteKnowledgeBase}
            docForm={docForm}
            documents={documents}
            knowledgeBases={knowledgeBases}
            setDocForm={setDocForm}
            setProfileError={setProfileError}
            token={token}
            uploadingKnowledgeFile={uploadingKnowledgeFile}
            uploadingFileName={uploadingFileName}
            uploadDocument={uploadDocument}
            uploadKnowledgeFile={uploadKnowledgeFile}
            loadDocuments={loadDocuments}
            notify={notify}
          />
        )}

        {activeNav === 'agents' && (
          <AgentsHome
            agents={agents}
            activeAgentId={activeAgentId}
            canManage={canManage}
            createAgent={createAgent}
            deleteAgent={deleteAgent}
            me={me}
            openBuilder={openBuilder}
            setActiveAgentId={setActiveAgentId}
          />
        )}

        {activeNav === 'market' && (
          <MarketHome
            agents={marketAgents}
            copyMarketAgent={copyMarketAgent}
          />
        )}

        {activeNav === 'my-models' && (
          <UserModelsHome
            adminModels={adminModels}
            canManage={canManage}
            createModelConfig={createModelConfig}
            deleteModelConfig={deleteModelConfig}
            requestDeleteConfirm={requestDeleteConfirm}
            setProfileError={setProfileError}
            updateModelConfig={updateModelConfig}
            userModels={userModels}
            createUserModelConfig={createUserModelConfig}
            updateUserModelConfig={updateUserModelConfig}
            deleteUserModelConfig={deleteUserModelConfig}
            testUserModelConfig={testUserModelConfig}
            testUserModelDraft={testUserModelDraft}
            probeUserModels={probeUserModels}
          />
        )}

        {activeNav === 'resources' && (
          <ResourceLibraryHome
            activeAgentId={activeAgentId}
            agentForm={agentForm}
            copyBuiltinPromptTemplate={copyBuiltinPromptTemplate}
            createPromptTemplate={createPromptTemplate}
            deletePromptTemplate={deletePromptTemplate}
            knowledgeBases={knowledgeBases}
            openBuilder={openBuilder}
            promptTemplates={promptTemplates}
            requestDeleteConfirm={requestDeleteConfirm}
            setActiveNav={setActiveNav}
            setAgentForm={setAgentForm}
            setProfileError={setProfileError}
            setView={setView}
            tools={tools}
            updatePromptTemplate={updatePromptTemplate}
          />
        )}

        {activeNav === 'tools' && (
          <ToolsHome
            createToolConfig={createToolConfig}
            deleteToolConfig={deleteToolConfig}
            openBuilder={openBuilder}
            requestDeleteConfirm={requestDeleteConfirm}
            setProfileError={setProfileError}
            testToolConfig={testToolConfig}
            tools={tools}
            updateToolConfig={updateToolConfig}
          />
        )}

        {activeNav === 'reviews' && canManage && (
          <ReviewHome
            items={reviewItems}
            approveReview={approveReview}
            rejectReview={rejectReview}
          />
        )}

        </Suspense>
      </section>
      {profileDialogOpen && (
        <ProfileDialog
          logout={logout}
          me={me}
          onClose={() => setProfileDialogOpen(false)}
          profileError={profileError}
          setProfileError={setProfileError}
          updateProfile={updateProfile}
          workspace={workspace}
        />
      )}
    </main>
  );
}

function AgentIdentityDialog({ error, initialForm, mode, onCancel, onSubmit, saving }) {
  const [form, setForm] = useState(() => normalizeAgentIdentity(initialForm));
  const title = mode === 'create' ? '创建智能体' : '编辑智能体';

  useEffect(() => {
    setForm(normalizeAgentIdentity(initialForm));
  }, [initialForm]);

  async function uploadAgentAvatar(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      validateAvatarFile(file);
      const avatar = await createAvatarDataUrl(file);
      setForm((current) => ({ ...current, avatar }));
    } catch (err) {
      setForm((current) => ({ ...current, localError: errorMessage(err) }));
    }
  }

  function submit(event) {
    event.preventDefault();
    onSubmit(form);
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="agent-identity-modal" onSubmit={submit}>
        <header>
          <h2>{title}</h2>
          <button type="button" aria-label="关闭" onClick={onCancel} disabled={saving}><X size={18} /></button>
        </header>
        <label className="field-stack">
          <span>智能体名称<b>*</b></span>
          <input
            value={form.name}
            maxLength={50}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="请输入智能体名称"
            autoFocus
          />
          <em>{form.name.length}/50</em>
        </label>
        <label className="field-stack">
          <span>智能体功能介绍</span>
          <textarea
            value={form.description}
            maxLength={500}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="介绍智能体的功能，将会展示给智能体的用户"
          />
          <em>{form.description.length}/500</em>
        </label>
        <div className="agent-avatar-picker">
          <span>图标 <b>*</b></span>
          <div>
            <AgentAvatar value={form.avatar} className="agent-avatar-preview" />
            <label className="agent-avatar-upload" title="上传图标">
              <ImagePlus size={16} />
              <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={uploadAgentAvatar} disabled={saving} />
            </label>
          </div>
        </div>
        {(error || form.localError) && <p className="error">{error || form.localError}</p>}
        <footer>
          <button type="button" onClick={onCancel} disabled={saving}>取消</button>
          <button className="primary" type="submit" disabled={saving || !form.name.trim()}>
            {saving ? '保存中...' : '确认'}
          </button>
        </footer>
      </form>
    </div>
  );
}



function ProfileDialog({
  logout,
  me,
  onClose,
  profileError,
  setProfileError,
  updateProfile,
  workspace,
}) {
  const [nameDraft, setNameDraft] = useState(me?.name || '');
  const [savingProfile, setSavingProfile] = useState(false);

  useEffect(() => {
    setNameDraft(me?.name || '');
  }, [me?.name]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  async function saveName() {
    const nextName = nameDraft.trim();
    if (!nextName || nextName === me?.name) return;
    setSavingProfile(true);
    try {
      await updateProfile({ name: nextName });
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSavingProfile(false);
    }
  }

  async function uploadAvatar(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
      setProfileError('头像只支持 PNG、JPG、WebP 或 GIF。');
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setProfileError('头像文件不能超过 8MB。');
      return;
    }
    setSavingProfile(true);
    try {
      const avatarUrl = await createAvatarDataUrl(file);
      await updateProfile({ avatar_url: avatarUrl });
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSavingProfile(false);
    }
  }

  return (
    <div className="profile-dialog-backdrop">
      <section className="profile-dialog" role="dialog" aria-modal="true" aria-label="个人资料" onClick={(event) => event.stopPropagation()}>
        <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭个人资料" onClick={onClose}>
          <X size={16} />
        </button>
        <div className="profile-card">
          <UserAvatar user={me} className="profile-avatar" />
          <div>
            <h3>个人信息</h3>
            <p>{me?.name || '未设置姓名'}</p>
          </div>
        </div>
        <div className="profile-actions">
          <label className="avatar-upload">
            {savingProfile ? '上传中...' : '上传头像'}
            <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={uploadAvatar} disabled={savingProfile} />
          </label>
        </div>
        <div className="profile-edit">
          <input value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} placeholder="姓名" />
          <button type="button" onClick={saveName} disabled={savingProfile || !nameDraft.trim()}>保存姓名</button>
        </div>
        {profileError && <p className="error">{profileError}</p>}
        <div className="profile-grid">
          <span>邮箱</span>
          <strong>{me?.email || '-'}</strong>
          <span>角色</span>
          <strong>{roleLabel(workspace?.role)}</strong>
          <span>账号状态</span>
          <strong>已登录</strong>
        </div>
        <button className="danger-action" type="button" onClick={() => { onClose(); logout(); }}><LogOut size={15} />退出登录</button>
      </section>
    </div>
  );
}

class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error(error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="fatal-error" style={{ padding: '20px', maxWidth: '800px', margin: '50px auto' }}>
          <h1>页面渲染失败</h1>
          <p style={{ color: '#d9383a', fontWeight: 'bold' }}>{this.state.error.message}</p>
          <pre style={{ textAlign: 'left', background: '#fafafa', border: '1px solid #eaeaea', borderRadius: '6px', padding: '15px', overflow: 'auto', fontSize: '11px', lineHeight: '1.5', fontFamily: 'monospace', color: '#333' }}>
            {this.state.error.stack}
          </pre>
          <button type="button" onClick={() => window.location.reload()} style={{ marginTop: '15px', padding: '8px 16px', background: '#181b25', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>刷新页面</button>
        </main>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(<AppErrorBoundary><App /></AppErrorBoundary>);
