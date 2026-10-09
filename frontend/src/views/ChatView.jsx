import { ChatComposer } from '../components/chat/ChatComposer.jsx';
import React, { Suspense, lazy } from 'react';
import {
  SquarePen,
  ImagePlus,
  FileText,
  X,
  Search,
  Sparkles,
  AlertTriangle,
  Brain,
  Database,
  Send
} from 'lucide-react';
import { AgentAvatar } from '../components/AgentAvatar.jsx';
import {
  reasoningCapabilityForModel,
  thinkingStatusText,
  ragStatusText,
  webSearchStatusText,
  attachmentAcceptForModel,
  attachmentHintForModel,
  uploadTypeFromContentType,
  modelCapabilityWarning,
  handleAttachmentInput,
  handleAttachmentPaste,
  handleAttachmentDrop,
} from '../utils.js';

const MessageList = lazy(() => import('../components/MessageList.jsx').then((module) => ({ default: module.MessageList })));

function MessageListFallback() {
  return <p className="message-pending">加载消息...</p>;
}

const CHAT_COPY = {
  noAgentTitle: '暂无可对话的智能体',
  noAgentDesc: '主对话页只开放已审核并上架的智能体。请先发布，普通用户发布后需要管理员审核。',
  welcomeTitle: '今天想让哪个智能体帮你？',
  welcomeDesc: '选择智能体后可以直接聊天，也可以进入配置页调整能力。',
  promptIntro: '介绍一下你的能力',
  promptPlan: '帮我整理一个方案',
  promptKb: '基于知识库回答一个问题',
  fallbackAgent: '智能体',
  sendPrefix: '给',
  sendSuffix: '发送消息',
};

export function ChatView({
  modelOptions,
  modelSelection,
  onModelChange,
  currentChatModel,
  activeAgent,
  activeAgentId,
  activeSummary,
  chatAgents,
  canEditActive,
  openBuilder,
  setActiveAgentId,
  // ChatHomeV2 props
  activeSessionId,
  agentForm,
  busy,
  chatAttachments,
  chatVariables,
  error,
  feedbackByMessage,
  homePrompt,
  messages,
  sendMessage,
  sendSuggestedQuestion,
  setChatAttachments,
  setHomePrompt,
  ragEnabled,
  setRagEnabled,
  searchEnabled,
  setSearchEnabled,
  sources,
  submitFeedback,
  ragRuntime,
  webSearchRuntime,
  thinkingEnabled,
  setThinkingEnabled,
  uploadChatAttachment,
  uploadingAttachment,
  updateChatVariable,
}) {
  return (
    <>
      <header className="chat-topbar">
        <div className="agent-select">
          <AgentAvatar value={activeSummary?.avatar || activeAgent?.avatar || 'AI'} />
          <select
            value={chatAgents.some((agent) => agent.id === activeAgentId) ? activeAgentId : ''}
            onChange={(e) => setActiveAgentId(Number(e.target.value))}
          >
            <option value="" disabled>
              {chatAgents.length ? '选择已上架智能体' : '暂无已上架智能体'}
            </option>
            {chatAgents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
          <button
            className="agent-edit-button"
            type="button"
            disabled={!canEditActive}
            title="编辑智能体"
            aria-label="编辑智能体"
            onClick={() => openBuilder(activeAgentId)}
          >
            <SquarePen size={16} />
          </button>
        </div>
      </header>

      <ChatHomeV2
        modelOptions={modelOptions}
        modelSelection={modelSelection}
        onModelChange={onModelChange}
        currentChatModel={currentChatModel}
        activeAgent={activeAgent}
        activeSessionId={activeSessionId}
        agentForm={agentForm}
        busy={busy}
        chatAgents={chatAgents}
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
    </>
  );
}

function ChatHomeV2({
  modelOptions,
  modelSelection,
  onModelChange,
  currentChatModel,
  activeAgent,
  activeSessionId,
  agentForm,
  busy,
  chatAgents,
  chatAttachments,
  chatVariables,
  error,
  feedbackByMessage,
  homePrompt,
  messages,
  sendMessage,
  sendSuggestedQuestion,
  setChatAttachments,
  setHomePrompt,
  ragEnabled,
  setRagEnabled,
  ragRuntime,
  searchEnabled,
  setSearchEnabled,
  webSearchRuntime,
  thinkingEnabled,
  setThinkingEnabled,
  sources,
  submitFeedback,
  uploadChatAttachment,
  uploadingAttachment,
  updateChatVariable,
}) {
  const currentModel = currentChatModel;
  const ragAvailable = ragRuntime.available;
  const effectiveRagEnabled = ragAvailable && ragEnabled;
  const ragStatus = ragStatusText(ragRuntime, effectiveRagEnabled);
  const searchAvailable = webSearchRuntime.available;
  const effectiveSearchEnabled = searchAvailable && searchEnabled;
  const searchStatus = webSearchStatusText(webSearchRuntime, effectiveSearchEnabled);
  const thinkingCapability = reasoningCapabilityForModel(currentModel);
  const effectiveThinkingEnabled = thinkingEnabled && thinkingCapability.supported;
  const modelWarning = modelCapabilityWarning(currentModel, chatAttachments);
  const attachmentAccept = attachmentAcceptForModel(currentModel);
  const attachmentDisabled = uploadingAttachment || !attachmentAccept;
  const attachmentHint = chatAttachments.length ? `${chatAttachments.length} file ready` : attachmentHintForModel(currentModel);
  const conversationStarted = Boolean(activeSessionId) || messages.some((message) => message.role === 'user');
  const runtimeWarning = modelWarning || { text: '' }; // Fallback
  const hasChatAgent = chatAgents.length > 0;

  return (
    <div className={`chat-home ${conversationStarted ? 'has-conversation' : 'is-empty'}`}>
      <div className="conversation">
        {!hasChatAgent ? (
          <section className="welcome-panel">
            <AgentAvatar value="AI" className="welcome-avatar" />
            <h1>{CHAT_COPY.noAgentTitle}</h1>
            <p>{CHAT_COPY.noAgentDesc}</p>
          </section>
        ) : !conversationStarted ? (
          <section className="welcome-panel">
            <AgentAvatar value={activeAgent?.avatar || agentForm.avatar || 'AI'} className="welcome-avatar" />
            <h1>{CHAT_COPY.welcomeTitle}</h1>
            <p>{activeAgent?.description || agentForm.description || CHAT_COPY.welcomeDesc}</p>
            <div className="quick-prompts">
              {(agentForm.suggested_questions?.length ? agentForm.suggested_questions : [CHAT_COPY.promptIntro, CHAT_COPY.promptPlan, CHAT_COPY.promptKb]).map((question, index) => (
                <button type="button" key={`${question}-${index}`} onClick={() => sendSuggestedQuestion(question)}>
                  {question}
                </button>
              ))}
            </div>
          </section>
        ) : (
          <Suspense fallback={<MessageListFallback />}>
            <MessageList
              messages={messages}
              feedbackByMessage={feedbackByMessage}
              submitFeedback={submitFeedback}
              avatar={activeAgent?.avatar || agentForm.avatar || 'AI'}
            />
          </Suspense>
        )}
      </div>
      {(agentForm.variables || []).length > 0 && (
        <VariableBar variables={agentForm.variables} values={chatVariables} onChange={updateChatVariable} />
      )}
      {hasChatAgent && (
        <div className="composer-dock">
          <ChatComposer
            className="home-composer"
            value={homePrompt}
            onChange={setHomePrompt}
            placeholder={`${CHAT_COPY.sendPrefix} ${activeAgent?.name || agentForm.name || CHAT_COPY.fallbackAgent} ${CHAT_COPY.sendSuffix}`}
            onSubmit={(event) => sendMessage(event, homePrompt)}
            submitDisabled={busy || uploadingAttachment || !!modelWarning || (!homePrompt.trim() && !chatAttachments.length)}
            attachmentAccept={attachmentAccept}
            attachmentDisabled={attachmentDisabled}
            attachmentHint={attachmentHint}
            currentModel={currentModel}
            modelOptions={modelOptions}
            modelSelection={modelSelection}
            onModelChange={onModelChange}
            modelSelectionDisabled={busy}
            onAttachmentInput={(event) => handleAttachmentInput(event, uploadChatAttachment)}
            onAttachmentPaste={(event) => handleAttachmentPaste(event, uploadChatAttachment)}
            attachments={chatAttachments}
            removeAttachment={(id) => setChatAttachments((items) => items.filter((item) => item.id !== id))}
            onFileDrop={(files) => handleAttachmentDrop(files, uploadChatAttachment)}
            runtimeWarning={runtimeWarning}
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
        </div>
      )}
      {error && <p className="error inline">{error}</p>}
    </div>
  );
}

function VariableBar({ variables, values, onChange }) {
  return (
    <div className="chat-variable-bar">
      {variables.map((variable) => (
        <label key={variable.key}>
          {variable.label || variable.key}
          {variable.type === 'boolean' ? (
            <select
              value={String(values[variable.key] ?? variable.default_value ?? false)}
              onChange={(e) => onChange(variable.key, e.target.value === 'true')}
            >
              <option value="false">false</option>
              <option value="true">true</option>
            </select>
          ) : (
            <input
              type={variable.type === 'number' ? 'number' : 'text'}
              value={values[variable.key] ?? variable.default_value ?? ''}
              onChange={(e) => onChange(variable.key, e.target.value)}
            />
          )}
        </label>
      ))}
    </div>
  );
}
