/**
 * ChatComposer — 消息输入框组件。
 * 从 ChatView.jsx 提取以避免 BuilderView ↔ ChatView 循环依赖。
 */
import React, { useRef, useState, useEffect } from 'react';
import {
  SquarePen, ImagePlus, FileText, X,
  AlertTriangle, Brain, Database, Send, ChevronDown,
} from 'lucide-react';
import { uploadTypeFromContentType } from '../../utils.js';

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
  sendMessage: '发送消息...',
  uploading: '附件上传中...',
  pendingAttachment: '个附件待发送',
  newChat: '新建会话',
  thinking: '深度思考',
  search: '联网搜索',
  rag: '知识库',
  unavailable: '不可用',
  on: '开启',
  off: '关闭',
};

function hasTransferFiles(dt) {
  if (!dt) return false;
  if (dt.files && dt.files.length) return true;
  if (dt.items && dt.items.length) {
    for (let i = 0; i < dt.items.length; i++) {
      if (dt.items[i].kind === 'file') return true;
    }
  }
  return false;
}

export function ChatComposer({
  attachmentAccept,
  attachmentDisabled,
  attachmentHint,
  attachments = [],
  className,
  currentModel,
  modelOptions = [],
  modelSelection = '',
  onModelChange,
  modelSelectionDisabled = false,
  includeNewChat = false,
  onAttachmentInput,
  onAttachmentPaste,
  onChange,
  onFileDrop,
  onNewChat,
  onSubmit,
  onToggleRag,
  onToggleThinking,
  placeholder,
  removeAttachment,
  ragAvailable,
  ragEnabled,
  ragStatus,
  submitDisabled,
  thinkingCapability,
  thinkingEnabled,
  value,
  runtimeWarning,
}) {
  const textareaRef = useRef(null);
  const [dragActive, setDragActive] = useState(false);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [value]);

  function handleKeyDown(event) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  function handleDragOver(event) {
    if (attachmentDisabled || !hasTransferFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDragActive(true);
  }

  function handleDragLeave(event) {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setDragActive(false);
    }
  }

  async function handleDrop(event) {
    if (attachmentDisabled || !hasTransferFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDragActive(false);
    await onFileDrop?.(event.dataTransfer.files);
  }

  const warningText = typeof runtimeWarning === 'object' ? runtimeWarning?.text : runtimeWarning;

  return (
    <form
      className={`${className} rich-composer ${dragActive ? 'drag-active' : ''}`}
      onSubmit={onSubmit}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={(event) => {
        handleDrop(event).catch((err) => console.error(err));
      }}
    >
      <div className="composer-top-tray" style={{ display: (attachments.length > 0 || warningText) ? 'block' : 'none', width: '100%' }}>
        {attachments.length > 0 ? (
          <AttachmentPreviewTray attachments={attachments} removeAttachment={removeAttachment} />
        ) : null}
        {warningText ? (
          <div className="composer-warning" style={{ marginTop: attachments.length > 0 ? '10px' : '0px' }}>
            <AlertTriangle size={14} />
            <span>{warningText}</span>
          </div>
        ) : null}
      </div>
      <textarea
        ref={textareaRef}
        className="composer-textarea"
        rows={1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={onAttachmentPaste}
        placeholder={placeholder}
      />
      <div className="composer-actions">
        <div className="composer-action-left">
          {onModelChange && (
            <div className="composer-model-picker" title="选择本次聊天使用的模型">
              <select
                aria-label="聊天模型"
                value={modelSelection}
                disabled={modelSelectionDisabled}
                onChange={(event) => onModelChange(event.target.value)}
              >
                <option value="">智能体默认模型</option>
                {['我的模型', '平台模型'].map((group) => (
                  modelOptions.some((option) => option.group === group) && (
                    <optgroup label={group} key={group}>
                      {modelOptions.filter((option) => option.group === group).map((option) => (
                        <option value={option.value} key={option.value}>{option.label}</option>
                      ))}
                    </optgroup>
                  )
                ))}
              </select>
              <ChevronDown size={13} aria-hidden="true" />
            </div>
          )}
          {includeNewChat && (
            <button type="button" className="composer-icon-button" title={CHAT_COPY.newChat} onClick={onNewChat}>
              <SquarePen size={16} />
            </button>
          )}
          <button
            type="button"
            className={thinkingEnabled ? 'thinking-toggle on' : 'thinking-toggle'}
            disabled={!thinkingCapability?.supported}
            title={thinkingCapability?.tooltip || CHAT_COPY.unavailable}
            aria-pressed={thinkingEnabled}
            onClick={onToggleThinking}
          >
            <Brain size={14} />
            <span>{CHAT_COPY.thinking}</span>
          </button>
          <button
            type="button"
            className={ragEnabled ? 'rag-toggle on' : 'rag-toggle'}
            disabled={!ragAvailable}
            title={ragStatus}
            aria-label={ragStatus}
            aria-pressed={ragEnabled}
            onClick={onToggleRag}
          >
            <Database size={14} />
            <span>{CHAT_COPY.rag}</span>
          </button>
        </div>
        <div className="composer-action-right">
          <label className={`attachment-button ${attachmentDisabled ? 'disabled' : ''}`} title={attachmentHint}>
            <AttachmentButtonIcon model={currentModel} size={18} />
            <input
              type="file"
              accept={attachmentAccept || undefined}
              disabled={attachmentDisabled}
              multiple
              onChange={onAttachmentInput}
              style={{ display: 'none' }}
            />
          </label>
          <button type="submit" className="composer-send-button" disabled={submitDisabled}>
            <Send size={18} />
          </button>
        </div>
      </div>
    </form>
  );
}

function AttachmentPreviewTray({ attachments, removeAttachment }) {
  return (
    <div className="attachment-preview-tray">
      {attachments.map((item) => {
        const isImage = item.type === 'image' || uploadTypeFromContentType(item.content_type) === 'image';
        return (
          <div className={isImage ? 'attachment-preview image' : 'attachment-preview document'} key={item.id}>
            {isImage ? (
              item.preview_url ? <img src={item.preview_url} alt={item.filename} /> : <ImagePlus size={22} />
            ) : (
              <FileText size={18} />
            )}
            {!isImage && <span>{item.filename}</span>}
            {!isImage && <small>{item.type || uploadTypeFromContentType(item.content_type)}</small>}
            <button type="button" aria-label={`移除 ${item.filename}`} onClick={() => removeAttachment(item.id)}>
              <X size={13} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

function AttachmentButtonIcon({ model, size = 16 }) {
  return <ImagePlus size={size} />;
}
