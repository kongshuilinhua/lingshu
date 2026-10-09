import React, { Suspense, lazy, useRef, useState, useEffect } from 'react';
import {
  ChevronLeft,
  Plus,
  Check,
  Rocket,
  Brain,
  Boxes,
  Wand2,
  Search,
  Database,
  KeyRound,
  Sparkles,
  ServerCog,
  FileText,
  X,
  SquarePen,
  Layers,
  ChevronRight,
  Trash2
} from 'lucide-react';
import { AgentAvatar } from '../components/AgentAvatar.jsx';
import { PromptTemplateDialog } from '../components/PromptTemplateDialog.jsx';
import { KnowledgeBaseDialog } from '../components/KnowledgeBaseDialog.jsx';
import { KnowledgeDocumentList, KnowledgeUploadBox } from '../components/KnowledgeDocumentList.jsx';
import { ChatComposer } from '../components/chat/ChatComposer.jsx';
import { McpBindingPanel } from '../components/McpBindingPanel.jsx';
import { SkillBindingPanel } from '../components/SkillBindingPanel.jsx';
import { AgentSearchSetting } from '../components/AgentSearchSetting.jsx';
import {
  findModelForForm,
  modelCapabilityWarning,
  ragStatusText,
  webSearchStatusText,
  reasoningCapabilityForModel,
  thinkingStatusText,
  attachmentAcceptForModel,
  attachmentHintForModel,
  insertPromptAtEditor,
  defaultPromptTemplateForm,
  defaultKnowledgeBaseForm,
  promptTemplateFormPayload,
  errorMessage,
  roleLabel,
  draftFacts,
  safeJsonPreview,
  memoryProfilePayload,
  formatDateTime,
  toggleKb,
  toggleTool,
  handleAttachmentInput,
  handleAttachmentPaste,
  handleAttachmentDrop,
  SAMPLE_MESSAGES,
} from '../utils.js';

const MessageList = lazy(() => import('../components/MessageList.jsx').then((module) => ({ default: module.MessageList })));

function MessageListFallback() {
  return <p className="message-pending">加载消息...</p>;
}

// Simple tool type utilities
function toolType(tool) {
  return tool?.type || (tool?.name === 'builtin_search' ? 'builtin_search' : 'http');
}

function toolHasSecret(tool) {
  return Boolean(tool?.auth?.has_secret || tool?.has_secret || tool?.auth_has_secret);
}

// Simple event summary for debug panel
function debugEventSummary(event) {
  if (event.event === 'tool_call') {
    const status = event.status || 'unknown';
    const name = event.tool_name || event.tool_id || 'tool';
    return `${name} · ${event.tool_type || 'tool'} · ${status} · ${event.latency_ms ?? '-'}ms`;
  }
  if (event.event === 'rag_status') {
    const enabled = event.enabled === false ? 'disabled' : 'enabled';
    const source = event.effective_source || 'default';
    const dense = event.dense?.matched ?? 0;
    const bm25 = event.bm25?.matched ?? 0;
    const rrf = event.rrf?.matched ?? event.matched_chunks ?? 0;
    const rerank = event.rerank?.applied ? 'rerank on' : event.rerank?.enabled ? 'rerank fallback' : 'rerank off';
    const cache = event.cache?.hit ? 'cache hit' : 'cache miss';
    const evidence = event.no_evidence ? 'no evidence' : 'evidence ok';
    return `${enabled} · ${source} · dense ${dense} · bm25 ${bm25} · rrf ${rrf} · ${rerank} · ${cache} · ${evidence}`;
  }
  if (event.event === 'search_status') {
    if (event.enabled) {
      return `enabled · ${event.provider || 'web'} · results ${event.matched_results ?? 0} · ${event.latency_ms ?? '-'}ms`;
    }
    return event.requested ? `disabled · ${event.reason || 'search unavailable'}` : 'disabled · not requested';
  }
  if (event.event === 'memory_used') {
    const enabled = event.enabled === false ? 'disabled' : 'enabled';
    const profile = event.profile_found ? 'profile found' : 'profile missing';
    const summary = event.summary_used ? 'summary used' : 'summary skipped';
    const session = event.session_summary_used ? 'session summary used' : 'session summary skipped';
    return `${enabled} · ${profile} · ${summary} · facts ${event.facts_recalled ?? '-'}/${event.facts_total ?? event.facts_count ?? 0} · prefs ${(event.preferences_keys || []).length} · ${session}`;
  }
  if (event.event === 'memory_compaction') {
    if (!event.triggered) return '未触发 · 在 token 预算内';
    const degraded = event.degraded ? ' · 降级(保留旧摘要)' : '';
    const model = event.summarizer_model || '默认模型';
    return `压缩 ${event.older_turns ?? 0} 轮 · 保留 ${event.kept_turns ?? 0} 轮 · ${event.tokens_before ?? '-'}→${event.tokens_after ?? '-'} tok · ${model}${degraded} · ${event.latency_ms ?? '-'}ms`;
  }
  if (event.event === 'thinking_status') {
    if (event.enabled) {
      return event.type === 'prompt' ? 'enabled · prompt enhanced · not native reasoning' : `enabled · ${event.type || 'native'}`;
    }
    return event.reason === 'model_not_supported' ? 'disabled · model not supported' : `disabled · ${event.reason || 'not requested'}`;
  }
  return JSON.stringify(event);
}

const CHAT_COPY = {
  sendMessage: '发送消息...',
};

export function BuilderView(props) {
  const {
    activeAgent,
    activeAgentId,
    activeSessionId,
    activeSummary,
    addSuggestedQuestion,
    addVariable,
    agentForm,
    agents,
    applyRoleTemplate,
    busy,
    canManage,
    canEditActive,
    confirmNavigation,
    chatMode,
    chatAttachments,
    chatVariables,
    createAgent,
    createKnowledgeBase,
    deleteDocument,
    docForm,
    documents,
    draft,
    error,
    feedbackByMessage,
    knowledgeBases,
    loadSession,
    me,
    messages,
    models,
    openAgentIdentityDialog,
    publishAgent,
    promptTemplates,
    createPromptTemplate,
    memoryProfile,
    memoryProfileDraft,
    memoryProfileError,
    memoryProfileLoading,
    memoryProfileSaving,
    removeSuggestedQuestion,
    removeVariable,
    renameSession,
    ragRuntime,
    searchEnabled,
    saveAgent,
    saveMemoryProfile,
    sendMessage,
    sendSuggestedQuestion,
    sessionTitleDraft,
    sessions,
    setActiveAgentId,
    setActiveNav,
    setMarketTab,
    setMarketScope,
    setChatMode,
    setChatAttachments,
    setDocForm,
    setDraft,
    setMemoryProfileDraft,
    setRagEnabled,
    setSearchEnabled,
    setThinkingEnabled,
    setSessionTitleDraft,
    setView,
    setAgentForm,
    setProfileError,
    sources,
    startNewChat,
    submitFeedback,
    deleteMemoryProfile,
    tools,
    token,
    toolDebugEvents,
    uploadChatAttachment,
    uploadingAttachment,
    uploadingKnowledgeFile,
    updateChatVariable,
    updateSuggestedQuestion,
    updateVariable,
    uploadDocument,
    uploadKnowledgeFile,
    userModels,
    workspace,
    ragEnabled,
    thinkingEnabled,
    webSearchRuntime,
  } = props;

  const selectedModel = findModelForForm(models, userModels, agentForm);
  const promptEditorRef = useRef(null);

  // Coze Redesign Collapsible panel states
  const [expandedSections, setExpandedSections] = useState({
    tools: true,
    kb: true,
    memorySession: true,
    memoryUser: false,
    onboarding: false,
  });

  // Modal open states
  const [toolsModalOpen, setToolsModalOpen] = useState(false);
  const [kbModalOpen, setKbModalOpen] = useState(false);
  
  // Search filter states
  const [toolsSearch, setToolsSearch] = useState('');
  const [kbSearch, setKbSearch] = useState('');

  const toggleSection = (key) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const [promptTemplateDialogOpen, setPromptTemplateDialogOpen] = useState(false);
  const [promptTemplateForm, setPromptTemplateForm] = useState(() => defaultPromptTemplateForm());
  const [promptTemplateSaving, setPromptTemplateSaving] = useState(false);
  const [knowledgeDialogOpen, setKnowledgeDialogOpen] = useState(false);
  const [knowledgeForm, setKnowledgeForm] = useState(() => defaultKnowledgeBaseForm());
  const [knowledgeSaving, setKnowledgeSaving] = useState(false);
  const modelWarning = modelCapabilityWarning(selectedModel, chatAttachments);
  const ragAvailable = ragRuntime.available;
  const effectiveRagEnabled = ragAvailable && ragEnabled;
  const ragStatus = ragStatusText(ragRuntime, effectiveRagEnabled);
  const searchAvailable = webSearchRuntime.available;
  const effectiveSearchEnabled = searchAvailable && searchEnabled;
  const searchStatus = webSearchStatusText(webSearchRuntime, effectiveSearchEnabled);
  const thinkingCapability = reasoningCapabilityForModel(selectedModel);
  const effectiveThinkingEnabled = thinkingEnabled && thinkingCapability.supported;
  const thinkingStatus = thinkingStatusText(thinkingCapability, effectiveThinkingEnabled);
  const attachmentAccept = attachmentAcceptForModel(selectedModel);
  const attachmentDisabled = uploadingAttachment || !attachmentAccept;
  const attachmentHint = chatAttachments.length ? `${chatAttachments.length} file ready` : attachmentHintForModel(selectedModel);

  function openPromptTemplateDialog() {
    const content = String(agentForm.system_prompt || '').trim();
    if (!content) {
      setProfileError('当前 Prompt 为空，不能保存为模板。');
      return;
    }
    setPromptTemplateForm({
      ...defaultPromptTemplateForm(),
      title: `${agentForm.name || '智能体'} Prompt`,
      description: `由「${agentForm.name || '当前智能体'}」保存`,
      category: 'custom',
      tagsText: '自定义',
      content,
    });
    setProfileError('');
    setPromptTemplateDialogOpen(true);
  }

  function closePromptTemplateDialog() {
    if (promptTemplateSaving) return;
    setPromptTemplateDialogOpen(false);
    setPromptTemplateForm(defaultPromptTemplateForm());
  }

  async function submitPromptTemplate(event) {
    event.preventDefault();
    setPromptTemplateSaving(true);
    setProfileError('');
    try {
      await createPromptTemplate(promptTemplateFormPayload(promptTemplateForm));
      setPromptTemplateDialogOpen(false);
      setPromptTemplateForm(defaultPromptTemplateForm());
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setPromptTemplateSaving(false);
    }
  }

  function openKnowledgeDialog() {
    setKnowledgeForm(defaultKnowledgeBaseForm());
    setProfileError('');
    setKnowledgeDialogOpen(true);
  }

  function closeKnowledgeDialog() {
    if (knowledgeSaving) return;
    setKnowledgeDialogOpen(false);
    setKnowledgeForm(defaultKnowledgeBaseForm());
  }

  async function submitKnowledgeBase(event) {
    event.preventDefault();
    setKnowledgeSaving(true);
    setProfileError('');
    try {
      const saved = await createKnowledgeBase(knowledgeForm);
      if (saved?.id) {
        setAgentForm((form) => ({
          ...form,
          knowledge_base_ids: form.knowledge_base_ids.includes(saved.id) ? form.knowledge_base_ids : [...form.knowledge_base_ids, saved.id],
        }));
        setDocForm((current) => ({ ...current, kb_id: String(saved.id) }));
      }
      setKnowledgeDialogOpen(false);
      setKnowledgeForm(defaultKnowledgeBaseForm());
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setKnowledgeSaving(false);
    }
  }

  return (
    <main className="builder-shell">
      <header className="builder-topbar">
        <div className="bot-title">
          <button className="ghost-icon" type="button" title="返回主页" onClick={() => setView('home')}><ChevronLeft size={18} /></button>
          <AgentAvatar value={activeSummary?.avatar || agentForm.avatar || 'AI'} className="bot-avatar" />
          <strong>{activeSummary?.name || agentForm.name || '智能体一号'}</strong>
          <button className="ghost-icon small" type="button" title="编辑基础信息" disabled={!canEditActive} onClick={() => openAgentIdentityDialog('edit')}>
            <SquarePen size={15} />
          </button>
          <span className="mode-chip">智能体配置</span>
        </div>
        <div className="builder-switcher">
          <span>当前智能体</span>
          <select value={activeAgentId || ''} onChange={(e) => setActiveAgentId(Number(e.target.value))}>
            {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
          </select>
          <button type="button" onClick={() => createAgent(true)}><Plus size={14} />新建</button>
        </div>
        <div className="top-actions">
          <span className="save-state">{activeSummary?.status === 'pending_review' ? '等待管理员审核' : '草稿可保存后提交'}</span>
          <button className="ghost-icon" type="button" title="保存草稿" disabled={!canEditActive} onClick={saveAgent}><Check size={16} /></button>
          <button className="publish" type="button" disabled={!canEditActive} onClick={publishAgent}><Rocket size={16} />{canManage ? '发布' : '提交审核'}</button>
        </div>
      </header>

      <div className="workbench">
        <aside className="persona-panel">
          <header className="column-header">
            <h2>人设与回复逻辑</h2>
            <span>Prompt</span>
          </header>
          <Panel title="人设与回复逻辑" icon={<Brain size={16} />}>
            <textarea ref={promptEditorRef} className="prompt-editor" value={agentForm.system_prompt} onChange={(e) => setAgentForm({ ...agentForm, system_prompt: e.target.value })} placeholder="写清楚智能体角色、目标、边界、工具调用规则和回答格式" />
          </Panel>

          <section className="prompt-library">
            <div className="prompt-library-head">
              <strong>提示词模板</strong>
              <div>
                <button type="button" onClick={openPromptTemplateDialog}>保存当前 Prompt 为模板</button>
                <button type="button" onClick={() => confirmNavigation(() => { setMarketScope('mine'); setMarketTab('prompts'); setView('home'); setActiveNav('market'); })}>我的提示词</button>
              </div>
            </div>
            <div className="template-grid">
              {promptTemplates.map((template) => (
                <article className="prompt-template-card" key={template.id}>
                  <button
                    type="button"
                    className="prompt-template-card-main"
                    onClick={() => insertPromptAtEditor(promptEditorRef, setAgentForm, template.content)}
                  >
                    <strong>{template.title}</strong>
                    <span>{template.description || template.content}</span>
                  </button>
                  <div className="builder-template-preview" role="tooltip">
                    <span>{template.source === 'builtin' ? '平台预置' : '我的模板'}</span>
                    <strong>{template.title}</strong>
                    <p>{template.description || '暂无描述'}</p>
                    <pre>{template.content}</pre>
                    <button type="button" onClick={() => insertPromptAtEditor(promptEditorRef, setAgentForm, template.content)}>插入提示词</button>
                  </div>
                </article>
              ))}
              {promptTemplates.length === 0 && <p className="muted">暂无提示词模板。</p>}
            </div>
          </section>
        </aside>

        <section className="builder-panel">
          <header className="column-header">
            <h2>编排</h2>
            <span>Configure</span>
          </header>

          <ModelConfigPanel
            agentForm={agentForm}
            models={models}
            openMyModels={() => confirmNavigation(() => {
              setView('home');
              setActiveNav('my-models');
            })}
            setAgentForm={setAgentForm}
            setRagEnabled={setRagEnabled}
            ragRuntime={ragRuntime}
            userModels={userModels}
          />

          {/* ==================== 技能/工具 ==================== */}
          <div className="coze-group-title">技能</div>
          <div className={`coze-accordion-item ${expandedSections.tools ? 'expanded' : ''}`}>
            <div className="coze-accordion-header" onClick={() => toggleSection('tools')}>
              <div className="coze-header-left">
                <ChevronRight size={14} className="coze-caret-icon" />
                <span>工具 <span className="coze-header-count">({agentForm.tool_ids.length})</span></span>
              </div>
              <button 
                type="button" 
                className="coze-add-button" 
                onClick={(e) => { e.stopPropagation(); setToolsModalOpen(true); }}
              >
                + 添加
              </button>
            </div>
            <div className="coze-accordion-body">
              {tools.filter(t => agentForm.tool_ids.includes(t.id)).map((tool) => (
                <div className="coze-bound-card" key={tool.id}>
                  <div className="coze-bound-card-info" style={{ minWidth: 0, flex: 1 }}>
                    <span className="coze-bound-card-icon">
                      {toolType(tool) === 'builtin_search' ? <Search size={14} /> : <Wand2 size={14} />}
                    </span>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="coze-bound-card-title">{tool.label}</div>
                      <div className="coze-bound-card-meta">{tool.description}</div>
                    </div>
                  </div>
                  <button 
                    type="button" 
                    className="coze-bound-card-remove" 
                    title="解绑工具"
                    onClick={(e) => { e.stopPropagation(); toggleTool(tool.id, agentForm, setAgentForm); }}
                  >
                    ✕
                  </button>
                </div>
              ))}
              {agentForm.tool_ids.length === 0 && (
                <p className="muted" style={{ fontSize: '12px', textAlign: 'center', margin: '10px 0 0' }}>
                  暂未绑定任何工具，点击“+ 添加”引入能力。
                </p>
              )}
            </div>
          </div>

          <McpBindingPanel
            agentId={activeAgentId}
            token={token}
            canEdit={canEditActive}
            onOpenMarket={() => confirmNavigation(() => { setMarketScope('discover'); setMarketTab('mcp'); setView('home'); setActiveNav('market'); })}
          />
          <AgentSearchSetting policy={agentForm.tool_policy} disabled={!canEditActive}
            onChange={(policy) => setAgentForm({ ...agentForm, tool_policy: policy })} />
          <SkillBindingPanel agentId={activeAgentId} token={token} canEdit={canEditActive}
            onOpenMarket={() => confirmNavigation(() => { setMarketScope('mine'); setMarketTab('skills'); setView('home'); setActiveNav('market'); })} />

          {/* ==================== 知识/文本 ==================== */}
          <div className="coze-group-title">知识</div>
          <div className={`coze-accordion-item ${expandedSections.kb ? 'expanded' : ''}`}>
            <div className="coze-accordion-header" onClick={() => toggleSection('kb')}>
              <div className="coze-header-left">
                <ChevronRight size={14} className="coze-caret-icon" />
                <span>文本 <span className="coze-header-count">({agentForm.knowledge_base_ids.length})</span></span>
              </div>
              <button 
                type="button" 
                className="coze-add-button" 
                onClick={(e) => { e.stopPropagation(); setKbModalOpen(true); }}
              >
                + 添加
              </button>
            </div>
            <div className="coze-accordion-body">
              {knowledgeBases.filter(kb => agentForm.knowledge_base_ids.includes(kb.id)).map((kb) => {
                const isSelectedForUpload = String(docForm.kb_id) === String(kb.id);
                return (
                  <div key={kb.id} style={{ marginBottom: '8px' }}>
                    <div 
                      className="coze-bound-card" 
                      style={{ 
                        cursor: 'pointer', 
                        borderColor: isSelectedForUpload ? '#4d43e6' : '#dfe4ef',
                        background: isSelectedForUpload ? 'rgba(77, 67, 230, 0.02)' : '#f8fafc'
                      }}
                      onClick={() => setDocForm((current) => ({ ...current, kb_id: String(kb.id) }))}
                    >
                      <div className="coze-bound-card-info" style={{ minWidth: 0, flex: 1 }}>
                        <span className="coze-bound-card-icon kb">
                          <Database size={14} />
                        </span>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div className="coze-bound-card-title">{kb.name}</div>
                          <div className="coze-bound-card-meta">{kb.description || '无描述'}</div>
                        </div>
                      </div>
                      <button 
                        type="button" 
                        className="coze-bound-card-remove" 
                        title="解绑知识库"
                        onClick={(e) => { e.stopPropagation(); toggleKb(kb.id, agentForm, setAgentForm); }}
                      >
                        ✕
                      </button>
                    </div>
                    
                    {/* Only show upload documents list if this KB card is selected */}
                    {isSelectedForUpload && (
                      <div style={{ marginTop: '8px', padding: '12px', background: '#ffffff', border: '1px dashed #dfe4ef', borderRadius: '8px' }}>
                        <div style={{ fontSize: '11px', fontWeight: 'bold', color: '#4d43e6', marginBottom: '8px' }}>
                          📂 知识文档管理 (当前知识库: “{kb.name}”)
                        </div>
                        <KnowledgeDocumentList 
                          documents={documents.filter(doc => String(doc.knowledge_base_id) === String(kb.id))} 
                          deleteDocument={deleteDocument} 
                        />
                        <div style={{ marginTop: '10px' }}>
                          <KnowledgeUploadBox
                            docForm={docForm}
                            setDocForm={setDocForm}
                            uploadDocument={uploadDocument}
                            uploadKnowledgeFile={uploadKnowledgeFile}
                            uploadingKnowledgeFile={uploadingKnowledgeFile}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
              {agentForm.knowledge_base_ids.length === 0 && (
                <p className="muted" style={{ fontSize: '12px', textAlign: 'center', margin: '10px 0 0' }}>
                  暂未绑定任何知识库，点击“+ 添加”引入检索数据。
                </p>
              )}
            </div>
          </div>

          {/* ==================== 记忆大类 ==================== */}
          <div className="coze-group-title">记忆</div>
          
          {/* 会话记忆 */}
          <div className={`coze-accordion-item ${expandedSections.memorySession ? 'expanded' : ''}`}>
            <div className="coze-accordion-header" onClick={() => toggleSection('memorySession')}>
              <div className="coze-header-left">
                <ChevronRight size={14} className="coze-caret-icon" />
                <span>对话上下文</span>
              </div>
            </div>
            <div className="coze-accordion-body">
              <p className="mcp-binding-intro">近期对话始终作为上下文传入。开启摘要压缩后，更早对话可压缩保留；与用户长期画像分开管理。</p>
              <ConfigRow label="历史摘要压缩">
                <Toggle
                  checked={!!agentForm.memory?.enabled}
                  label={agentForm.memory?.enabled ? '开启' : '关闭'}
                  onChange={(value) => setAgentForm({ ...agentForm, memory: { ...(agentForm.memory || {}), enabled: value, strategy: 'session_summary' } })}
                />
              </ConfigRow>
                <ConfigRow label="近期上下文消息数">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', width: '100%' }}>
                    <input
                      type="range"
                      min="1"
                      max="100"
                      step="1"
                      style={{ flex: 1, accentColor: '#4d43e6', height: '6px', background: '#dfe4ef', borderRadius: '4px', cursor: 'pointer' }}
                      value={agentForm.memory?.max_messages ?? 12}
                      onChange={(e) => setAgentForm({ ...agentForm, memory: { ...(agentForm.memory || {}), max_messages: Number(e.target.value), strategy: 'session_summary' } })}
                    />
                    <span style={{ minWidth: '24px', fontWeight: 'bold', color: '#4d43e6', fontSize: '13px', textAlign: 'right' }}>
                      {agentForm.memory?.max_messages ?? 12}
                    </span>
                  </div>
                </ConfigRow>
            </div>
          </div>

          {/* 用户长期记忆 */}
          <div className={`coze-accordion-item ${expandedSections.memoryUser ? 'expanded' : ''}`}>
            <div className="coze-accordion-header" onClick={() => toggleSection('memoryUser')}>
              <div className="coze-header-left">
                <ChevronRight size={14} className="coze-caret-icon" />
                <span>长期记忆 / 用户画像</span>
              </div>
            </div>
            <div className="coze-accordion-body">
              <AgentMemoryProfilePanel
                activeAgentId={activeAgentId}
                canEditActive={canEditActive}
                deleteMemoryProfile={deleteMemoryProfile}
                memoryProfile={memoryProfile}
                memoryProfileDraft={memoryProfileDraft}
                memoryProfileError={memoryProfileError}
                memoryProfileLoading={memoryProfileLoading}
                memoryProfileSaving={memoryProfileSaving}
                saveMemoryProfile={saveMemoryProfile}
                setMemoryProfileDraft={setMemoryProfileDraft}
              />
            </div>
          </div>

          {promptTemplateDialogOpen && (
            <PromptTemplateDialog
              editingTemplate={null}
              form={promptTemplateForm}
              onCancel={closePromptTemplateDialog}
              onChange={setPromptTemplateForm}
              onSubmit={submitPromptTemplate}
              saving={promptTemplateSaving}
            />
          )}

          {knowledgeDialogOpen && (
            <KnowledgeBaseDialog
              form={knowledgeForm}
              onCancel={closeKnowledgeDialog}
              onChange={setKnowledgeForm}
              onSubmit={submitKnowledgeBase}
              saving={knowledgeSaving}
            />
          )}

          {/* ==================== 对话体验 ==================== */}
          <div className="coze-group-title">对话体验</div>
          <div className={`coze-accordion-item ${expandedSections.onboarding ? 'expanded' : ''}`}>
            <div className="coze-accordion-header" onClick={() => toggleSection('onboarding')}>
              <div className="coze-header-left">
                <ChevronRight size={14} className="coze-caret-icon" />
                <span>开场白与引导问题</span>
              </div>
            </div>
            <div className="coze-accordion-body">
              <label className="field-label" style={{ fontWeight: 600, color: '#374151', fontSize: '13px' }}>开场白文案</label>
              <textarea 
                value={agentForm.opening_message} 
                onChange={(e) => setAgentForm({ ...agentForm, opening_message: e.target.value })} 
                placeholder="例如：你好！我是智能助理，今天有什么可以帮您的？" 
                style={{ width: '100%', minHeight: '80px', padding: '10px', borderRadius: '8px', border: '1px solid #dfe4ef', marginTop: '6px', fontSize: '13px' }}
              />
              <small className="counter" style={{ display: 'block', textAlign: 'right', margin: '4px 0 10px', color: '#94a3b8' }}>
                {agentForm.opening_message?.length ?? 0}/1000
              </small>
              
              <div style={{ borderTop: '1px solid #eef0f5', paddingTop: '10px', marginTop: '10px' }}>
                <ConfigRow label="开场引导问题"><span className="muted" style={{ fontSize: '12px', color: '#94a3b8' }}>前台展示</span></ConfigRow>
                <div className="dynamic-list" style={{ marginTop: '8px' }}>
                  {(agentForm.suggested_questions || []).map((question, index) => (
                    <div className="list-row" key={index} style={{ display: 'flex', gap: '8px', marginBottom: '6px' }}>
                      <input 
                        style={{ flex: 1, padding: '6px 10px', border: '1px solid #dfe4ef', borderRadius: '6px', fontSize: '13px' }}
                        value={question} 
                        onChange={(e) => updateSuggestedQuestion(index, e.target.value)} 
                      />
                      <button 
                        type="button" 
                        className="danger text"
                        style={{ padding: '0 8px', color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', fontSize: '13px' }}
                        onClick={() => removeSuggestedQuestion(index)}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
                <button 
                  type="button" 
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '8px', color: '#4d43e6', fontWeight: 600, background: 'none', border: 'none', cursor: 'pointer', fontSize: '12px' }}
                  onClick={addSuggestedQuestion}
                >
                  <Plus size={12} /> 输入引导问题
                </button>
              </div>
            </div>
          </div>

          {/* ==================== 选择工具弹窗 ==================== */}
          {toolsModalOpen && (
            <div className="coze-modal-backdrop" onClick={() => setToolsModalOpen(false)}>
              <div className="coze-modal-container" onClick={(e) => e.stopPropagation()}>
                <div className="coze-modal-header">
                  <h3>选择工具 (Select Tool)</h3>
                  <button className="coze-modal-close-btn" onClick={() => setToolsModalOpen(false)}>✕</button>
                </div>
                <div className="coze-modal-body">
                  <div className="coze-modal-search">
                    <input 
                      type="text" 
                      placeholder="搜索可用工具名称或描述..." 
                      value={toolsSearch}
                      onChange={(e) => setToolsSearch(e.target.value)}
                      autoFocus
                    />
                  </div>
                  <div className="coze-modal-list">
                    {tools
                      .filter(t => 
                        t.label.toLowerCase().includes(toolsSearch.toLowerCase()) || 
                        t.description.toLowerCase().includes(toolsSearch.toLowerCase())
                      )
                      .map((tool) => {
                        const isAdded = agentForm.tool_ids.includes(tool.id);
                        return (
                          <div className="coze-modal-row" key={tool.id}>
                            <div className="coze-modal-row-info">
                              <span style={{ fontSize: '16px', marginRight: '8px' }}>
                                {toolType(tool) === 'builtin_search' ? '🔍' : '🛠️'}
                              </span>
                              <strong className="coze-modal-row-title">{tool.label}</strong>
                              <div className="coze-modal-row-desc">{tool.description}</div>
                            </div>
                            <button
                              type="button"
                              className={`coze-modal-row-btn ${isAdded ? 'added' : ''}`}
                              disabled={isAdded}
                              onClick={() => {
                                if (isAdded) return;
                                toggleTool(tool.id, agentForm, setAgentForm);
                              }}
                            >
                              {isAdded ? '已添加' : '添加'}
                            </button>
                          </div>
                        );
                      })
                    }
                    {tools.length === 0 && <p className="muted" style={{ textAlign: 'center' }}>无可用工具。</p>}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ==================== 选择知识库弹窗 ==================== */}
          {kbModalOpen && (
            <div className="coze-modal-backdrop" onClick={() => setKbModalOpen(false)}>
              <div className="coze-modal-container" onClick={(e) => e.stopPropagation()}>
                <div className="coze-modal-header">
                  <h3>选择知识库 (Select Knowledge Base)</h3>
                  <button className="coze-modal-close-btn" onClick={() => setKbModalOpen(false)}>✕</button>
                </div>
                <div className="coze-modal-body">
                  <div style={{ display: 'flex', gap: '10px', marginBottom: '16px' }}>
                    <div className="coze-modal-search" style={{ flex: 1, marginBottom: 0 }}>
                      <input 
                        type="text" 
                        placeholder="搜索我的知识库..." 
                        value={kbSearch}
                        onChange={(e) => setKbSearch(e.target.value)}
                        autoFocus
                      />
                    </div>
                    <button 
                      type="button"
                      className="coze-modal-row-btn"
                      style={{ background: '#10b981', borderColor: '#10b981', padding: '0 16px', height: '38px', borderRadius: '8px' }}
                      onClick={() => {
                        setKbModalOpen(false);
                        openKnowledgeDialog();
                      }}
                    >
                      + 新建知识库
                    </button>
                  </div>
                  <div className="coze-modal-list">
                    {knowledgeBases
                      .filter(kb => kb.name.toLowerCase().includes(kbSearch.toLowerCase()))
                      .map((kb) => {
                        const isAdded = agentForm.knowledge_base_ids.includes(kb.id);
                        return (
                          <div className="coze-modal-row" key={kb.id}>
                            <div className="coze-modal-row-info">
                              <span style={{ fontSize: '16px', marginRight: '8px' }}>📄</span>
                              <strong className="coze-modal-row-title">{kb.name}</strong>
                              <div className="coze-modal-row-desc">{kb.description || '暂无描述信息'}</div>
                            </div>
                            <button
                              type="button"
                              className={`coze-modal-row-btn ${isAdded ? 'added' : ''}`}
                              disabled={isAdded}
                              onClick={() => {
                                if (isAdded) return;
                                toggleKb(kb.id, agentForm, setAgentForm);
                              }}
                            >
                              {isAdded ? '已添加' : '添加'}
                            </button>
                          </div>
                        );
                      })
                    }
                    {knowledgeBases.length === 0 && <p className="muted" style={{ textAlign: 'center' }}>无可用知识库，点击右上角新建。</p>}
                  </div>
                </div>
              </div>
            </div>
          )}
        </section>

        <section className="chat-stage">
          <header className="stage-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <h2>预览与调试</h2>
              <span className="eyebrow">{activeSummary?.name || '智能体一号'}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <button
                type="button"
                className="coze-add-button"
                style={{
                  background: '#ffffff',
                  border: '1px solid #dfe4ef',
                  color: '#667085',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  padding: '5px 10px',
                  fontSize: '12px',
                  borderRadius: '6px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  boxShadow: '0 1px 2px rgba(0, 0, 0, 0.05)',
                  height: '28px'
                }}
                onClick={startNewChat}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = '#4d43e6';
                  e.currentTarget.style.color = '#4d43e6';
                  e.currentTarget.style.background = 'rgba(77, 67, 230, 0.02)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = '#dfe4ef';
                  e.currentTarget.style.color = '#667085';
                  e.currentTarget.style.background = '#ffffff';
                }}
              >
                <Trash2 size={13} />
                <span>清空会话</span>
              </button>
              <div className="mode-toggle" title="选择调试版本" style={{ margin: 0 }}>
                <button type="button" className={chatMode === 'draft' ? 'active' : ''} onClick={() => setChatMode('draft')}>草稿调试</button>
                <button type="button" className={chatMode === 'published' ? 'active' : ''} onClick={() => setChatMode('published')}>已发布预览</button>
              </div>
            </div>
          </header>
          <div className="messages">
            {messages.length <= 1 ? (
              <div className="preview-hero compact">
                <AgentAvatar value={agentForm.avatar} className="bot-avatar large" />
                <strong>{agentForm.name || '智能体一号'}</strong>
                <p>{agentForm.opening_message || '你好'}</p>
              </div>
            ) : (
              <Suspense fallback={<MessageListFallback />}>
                <MessageList messages={messages} feedbackByMessage={feedbackByMessage} submitFeedback={submitFeedback} avatar={activeAgent?.avatar || agentForm.avatar || 'AI'} />
              </Suspense>
            )}
          </div>
          {messages.length <= 1 && (agentForm.suggested_questions || []).length > 0 && (
            <div className="suggestion-strip">
              {(agentForm.suggested_questions || []).map((question, index) => (
                <button key={`${question}-${index}`} type="button" onClick={() => sendSuggestedQuestion(question)}>
                  {question}
                </button>
              ))}
            </div>
          )}
          {(agentForm.variables || []).length > 0 && (
            <VariableBar variables={agentForm.variables} values={chatVariables} onChange={updateChatVariable} />
          )}
          {!modelWarning && selectedModel && (
            <div className="runtime-status">
              <span>{selectedModel ? `${selectedModel.display_name || selectedModel.model_name} · ${selectedModel.model_name}` : '请选择已启用模型'}</span>
              <strong>{ragStatus}</strong>
            </div>
          )}
          {activeSessionId && (
            <div className="session-editor">
              <input value={sessionTitleDraft} onChange={(e) => setSessionTitleDraft(e.target.value)} placeholder="会话标题" />
              <button type="button" onClick={() => renameSession().catch((err) => console.error(err))}>保存标题</button>
            </div>
          )}
          {(toolDebugEvents || []).length > 0 && (
            <div className="builder-debug-panel">
              <div className="debug-title">
                <span>运行轨迹</span>
                <span>{toolDebugEvents.length} 条</span>
              </div>
              <div className="debug-event-list">
                {toolDebugEvents.map((evt, index) => (
                  <div key={`${evt.event}-${index}-${evt.received_at || ''}`} className={`debug-event ${evt.event}`}>
                    <span>{evt.received_at || ''}</span>
                    <strong>{evt.event}</strong>
                    <small title={debugEventSummary(evt)}>{debugEventSummary(evt)}</small>
                  </div>
                ))}
              </div>
            </div>
          )}
          <ChatComposer
            className="composer"
            value={draft}
            onChange={setDraft}
            placeholder={CHAT_COPY.sendMessage}
            onSubmit={sendMessage}
            submitDisabled={busy || uploadingAttachment || !!modelWarning || !activeAgentId || (!draft.trim() && !chatAttachments.length)}
            includeNewChat
            onNewChat={startNewChat}
            attachmentAccept={attachmentAccept}
            attachmentDisabled={attachmentDisabled}
            attachmentHint={attachmentHint}
            currentModel={selectedModel}
            onAttachmentInput={(event) => handleAttachmentInput(event, uploadChatAttachment)}
            onAttachmentPaste={(event) => handleAttachmentPaste(event, uploadChatAttachment)}
            attachments={chatAttachments}
            removeAttachment={(id) => setChatAttachments((items) => items.filter((item) => item.id !== id))}
            onFileDrop={(files) => handleAttachmentDrop(files, uploadChatAttachment)}
            runtimeWarning={modelWarning}
            searchAvailable={searchAvailable}
            searchEnabled={effectiveSearchEnabled}
            searchStatus={searchStatus}
            onToggleSearch={() => setSearchEnabled(!searchEnabled)}
            thinkingCapability={thinkingCapability}
            thinkingEnabled={effectiveThinkingEnabled}
            onToggleThinking={() => {
              if (!thinkingCapability.supported) return;
              setThinkingEnabled(!thinkingEnabled);
            }}
            ragAvailable={ragAvailable}
            ragEnabled={effectiveRagEnabled}
            ragStatus={ragStatus}
            onToggleRag={() => setRagEnabled(!ragEnabled)}
          />
          {error && <p className="error inline">{error}</p>}
        </section>
      </div>
    </main>
  );
}

// ==========================================
// Helper Sub-Components within BuilderView
// ==========================================

function Panel({ title, icon, children }) {
  return (
    <section className="panel">
      <h3>{icon}{title}</h3>
      {children}
    </section>
  );
}

function ConfigRow({ label, children }) {
  return (
    <div className="config-row">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

function Toggle({ checked, disabled = false, label, onChange }) {
  return (
    <button type="button" className={`toggle-switch ${checked ? 'on' : ''}`} disabled={disabled} onClick={() => onChange(!checked)}>
      <span />
      {label}
    </button>
  );
}

function ToolRow({ desc, enabled, icon, meta, onClick, title }) {
  return (
    <div className={`tool-row ${enabled ? 'enabled' : 'disabled'}`} onClick={onClick} style={{ cursor: 'pointer' }}>
      <span className="tool-icon">{icon}</span>
      <div className="tool-body">
        <strong>{title}</strong>
        <p>{desc}</p>
        <small>{meta}</small>
      </div>
      <span className="tool-switch">
        <Toggle checked={enabled} label={enabled ? '绑定' : '未绑定'} onChange={onClick} />
      </span>
    </div>
  );
}

function ModelConfigPanel({ agentForm, models, openMyModels, setAgentForm, setRagEnabled, ragRuntime, userModels }) {
  const selected = findModelForForm(models, userModels, agentForm);
  const hasEnabledUserModel = userModels.some((model) => model.enabled);
  const ragAvailable = ragRuntime.available;
  const modelValue = agentForm.user_model_config_id
    ? `user:${agentForm.user_model_config_id}`
    : agentForm.model_id
      ? `system:${agentForm.model_id}`
      : '';

  function selectModel(value) {
    const [scope, id] = String(value || '').split(':');
    if (scope === 'user') {
      const model = userModels.find((item) => item.id === Number(id));
      setAgentForm({
        ...agentForm,
        user_model_config_id: model?.id || '',
        model_id: '',
        model: model?.chat_model || agentForm.model,
        temperature: agentForm.temperature ?? 0.4,
      });
      return;
    }
    const model = models.find((item) => item.id === Number(id));
    setAgentForm({
      ...agentForm,
      model_id: model?.id || '',
      user_model_config_id: '',
      model: model?.model_name || agentForm.model,
      temperature: agentForm.temperature ?? 0.4,
    });
  }

  function updateRag(patch) {
    const next = { ...(agentForm.rag || { enabled_by_default: true, top_k: 4 }), ...patch };
    setAgentForm({ ...agentForm, rag: next });
    if ('enabled_by_default' in patch) setRagEnabled(next.enabled_by_default);
  }

  return (
    <Panel title="模型与检索" icon={<Brain size={16} />}>
      <ConfigRow label="对话模型">
        <select value={modelValue} onChange={(event) => selectModel(event.target.value)}>
          <option value="">选择对话模型</option>
          {userModels.length > 0 && (
            <optgroup label="我的模型">
              {userModels.filter((model) => model.enabled).map((model) => (
                <option key={`user-${model.id}`} value={`user:${model.id}`}>{model.display_name || model.chat_model}</option>
              ))}
            </optgroup>
          )}
          <optgroup label="系统预设">
            {models.map((model) => (
              <option key={model.id} value={`system:${model.id}`}>{model.display_name || model.model_name}</option>
            ))}
          </optgroup>
        </select>
      </ConfigRow>
      {selected && (
        <div className="model-capabilities">
          {selected.capabilities?.map((label) => (
            <span className="enabled" key={label}>{label}</span>
          ))}
          <small>{selected.description || selected.model_name}</small>
        </div>
      )}
      <div className={ragAvailable ? 'rag-model-status ready' : 'rag-model-status warning'}>
        <Layers size={15} />
        <span>
          <strong>{ragAvailable ? `RAG 使用后端默认 ${ragRuntime.model}` : '后端默认 RAG 检索能力不可用'}</strong>
          <small>{ragAvailable ? `${ragRuntime.baseUrl || '默认模型网关'} · ${ragRuntime.vectorBackend || 'vector'} · ${ragRuntime.mock ? 'mock 向量' : '真实向量'}` : (ragRuntime.reason || '请在后端环境变量中配置 OPENAI_EMBEDDING_MODEL、可用 API Key 和向量库。')}</small>
        </span>
      </div>
      <div className={hasEnabledUserModel ? 'private-model-guide ready' : 'private-model-guide'}>
        <ServerCog size={15} />
        <span>
          <strong>{hasEnabledUserModel ? '优先使用我的模型' : '建议先配置我的模型'}</strong>
          <small>{hasEnabledUserModel ? '已启用的私有模型排在系统预设前面，保存智能体时使用 user_model_config_id。' : '先到“我的模型”选择厂商预设或自定义兼容网关，保存后智能体会优先绑定 user_model_config_id。'}</small>
        </span>
        <button type="button" onClick={openMyModels}>我的模型</button>
      </div>
      <ConfigRow label="温度">
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', width: '100%' }}>
          <input
            type="range"
            min="0"
            max="2"
            step="0.1"
            style={{ flex: 1, accentColor: '#4d43e6', height: '6px', background: '#dfe4ef', borderRadius: '4px', cursor: 'pointer' }}
            value={agentForm.temperature ?? 0.7}
            disabled={selected?.provider === 'anthropic'}
            onChange={(e) => setAgentForm({ ...agentForm, temperature: Number(e.target.value) })}
          />
          <span style={{ minWidth: '32px', fontWeight: 'bold', color: '#4d43e6', fontSize: '14px', textAlign: 'right' }}>
            {selected?.provider === 'anthropic' ? '厂商默认' : Number(agentForm.temperature ?? 0.7).toFixed(1)}
          </span>
        </div>
      </ConfigRow>
      <ConfigRow label="默认启用 RAG">
        <Toggle
          checked={ragAvailable && (agentForm.rag?.enabled_by_default ?? true)}
          disabled={!ragAvailable}
          label={!ragAvailable ? '不可用' : (agentForm.rag?.enabled_by_default ?? true) ? '开启' : '关闭'}
          onChange={(value) => {
            if (!ragAvailable) return;
            updateRag({ enabled_by_default: value });
          }}
        />
      </ConfigRow>
      <ConfigRow label="检索数量">
        <input type="number" min="1" max="20" value={agentForm.rag?.top_k ?? 4} onChange={(e) => updateRag({ top_k: Number(e.target.value) })} />
      </ConfigRow>
      <div className="rag-grid-settings">
        <label>
          Dense
          <input type="number" min="1" max="50" value={agentForm.rag?.dense_top_k ?? 12} onChange={(e) => updateRag({ dense_top_k: Number(e.target.value) })} />
        </label>
        <label>
          BM25
          <input type="number" min="1" max="50" value={agentForm.rag?.bm25_top_k ?? 12} onChange={(e) => updateRag({ bm25_top_k: Number(e.target.value) })} />
        </label>
        <label>
          RRF K
          <input type="number" min="1" max="200" value={agentForm.rag?.rrf_k ?? 60} onChange={(e) => updateRag({ rrf_k: Number(e.target.value) })} />
        </label>
      </div>
      <ConfigRow label="Rerank">
        <Toggle
          checked={agentForm.rag?.rerank_enabled ?? true}
          label={(agentForm.rag?.rerank_enabled ?? true) ? 'On' : 'Off'}
          onChange={(value) => updateRag({ rerank_enabled: value })}
        />
      </ConfigRow>
    </Panel>
  );
}

function AgentMemoryProfilePanel({
  activeAgentId,
  canEditActive,
  deleteMemoryProfile,
  memoryProfile,
  memoryProfileDraft,
  memoryProfileError,
  memoryProfileLoading,
  memoryProfileSaving,
  saveMemoryProfile,
  setMemoryProfileDraft,
}) {
  const [localError, setLocalError] = useState('');
  const factsCount = draftFacts(memoryProfileDraft.factsText).length;
  const preferences = safeJsonPreview(memoryProfileDraft.preferencesText);
  const preferenceKeys = preferences && typeof preferences === 'object' && !Array.isArray(preferences) ? Object.keys(preferences) : [];
  const busy = memoryProfileLoading || memoryProfileSaving;

  const updateDraft = (patch) => {
    setLocalError('');
    setMemoryProfileDraft((draft) => ({ ...draft, ...patch }));
  };

  async function submitProfile() {
    setLocalError('');
    try {
      memoryProfilePayload(memoryProfileDraft);
      await saveMemoryProfile();
    } catch (err) {
      setLocalError(errorMessage(err));
    }
  }

  return (
    <Panel title="用户记忆" icon={<Brain size={16} />}>
      <div className="memory-profile-shell">
        <div className="memory-profile-head">
          <div>
            <strong>{memoryProfileDraft.enabled ? '运行时注入' : '仅保存，不注入'}</strong>
            <span>当前用户 × 当前智能体</span>
          </div>
          <Toggle
            checked={!!memoryProfileDraft.enabled}
            label={memoryProfileDraft.enabled ? '开启' : '关闭'}
            onChange={(value) => updateDraft({ enabled: value })}
          />
        </div>
        <div className="memory-profile-meta">
          <span>facts {factsCount}/50</span>
          <span>preferences {preferenceKeys.length}</span>
          <span>{memoryProfile?.updated_at ? `更新 ${formatDateTime(memoryProfile.updated_at)}` : '尚未保存'}</span>
        </div>
        <label className="field-stack">
          <span>摘要</span>
          <textarea
            maxLength={4000}
            value={memoryProfileDraft.summary}
            onChange={(event) => updateDraft({ summary: event.target.value })}
            placeholder="这个用户对该智能体的偏好摘要。"
          />
          <small>{memoryProfileDraft.summary?.length ?? 0}/4000</small>
        </label>
        <div className="memory-profile-two-col">
          <label className="field-stack">
            <span>Facts (按行分割，最多 50 条)</span>
            <textarea
              value={memoryProfileDraft.factsText}
              onChange={(event) => updateDraft({ factsText: event.target.value })}
              placeholder="用户说杭州天气热&#10;用户使用的是 Mac 电脑"
            />
          </label>
          <label className="field-stack">
            <span>Preferences (标准 JSON 字典)</span>
            <textarea
              className={preferences ? 'json-ok' : 'json-error'}
              value={memoryProfileDraft.preferencesText}
              onChange={(event) => updateDraft({ preferencesText: event.target.value })}
              placeholder='{&#10;  "language": "zh",&#10;  "editor": "vscode"&#10;}'
            />
          </label>
        </div>
        {localError && <p className="error">{localError}</p>}
        {memoryProfileError && <p className="error">{memoryProfileError}</p>}
        <div className="memory-profile-foot">
          <button type="button" className="danger text" disabled={busy || !memoryProfile} onClick={deleteMemoryProfile}>清空记忆</button>
          <button type="button" disabled={busy || (preferences === null && memoryProfileDraft.preferencesText.trim() !== '')} onClick={submitProfile}>
            {memoryProfileSaving ? '保存中...' : '保存记忆'}
          </button>
        </div>
      </div>
    </Panel>
  );
}














