import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { ThumbsUp, ThumbsDown, FileText, Search, ImagePlus, Brain, ChevronDown, ExternalLink, X } from 'lucide-react';
import { AgentAvatar } from './AgentAvatar.jsx';

export function MessageList({ messages, feedbackByMessage = {}, submitFeedback = () => {}, avatar = 'AI' }) {
  return (
    <>
      {messages.map((message, index) => (
        <div key={`${message.role}-${index}-${message.id || ''}`} className={`message ${message.role}`}>
          {message.role === 'user' ? (
            <span>我</span>
          ) : (
            <AgentAvatar value={avatar} />
          )}
          <div className="message-body">
            {message.role === 'assistant' ? (
              <div className={message.error ? 'message-error' : ''}>
                {message.thinking && (
                  <ThinkingPanel thinking={message.thinking} inProgress={Boolean(message.pending && !message.content)} />
                )}
                {message.process?.length > 0 && <ProcessNotes notes={message.process} />}
                {message.pending && !message.content
                  ? (!message.thinking && <p className="message-pending">思考中...</p>)
                  : <MarkdownContent content={message.content || ''} />}
              </div>
            ) : <>
              <p>{message.content}</p>
              {message.attachments?.length > 0 && (
                <div className="message-attachments">
                  {message.attachments.map((att) => (
                    <div key={att.id} className={att.kind === 'image' || att.type === 'image' ? 'message-attachment-image' : 'message-attachment-document'}>
                      {att.kind === 'image' || att.type === 'image' ? (
                        <img src={att.data_url || att.preview_url} alt={att.filename} />
                      ) : (
                        <span className="message-attachment-file"><FileText size={14} />{att.filename}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>}
            {message.role === 'assistant' && message.id && (
              <div className="feedback-actions">
                <button
                  type="button"
                  className={feedbackByMessage[message.id] === 'positive' ? 'selected' : ''}
                  title="回答有帮助"
                  onClick={() => submitFeedback(message.id, 'positive').catch((err) => console.error(err))}
                >
                  <ThumbsUp size={14} />
                </button>
                <button
                  type="button"
                  className={feedbackByMessage[message.id] === 'negative' ? 'selected' : ''}
                  title="回答不理想"
                  onClick={() => submitFeedback(message.id, 'negative').catch((err) => console.error(err))}
                >
                  <ThumbsDown size={14} />
                </button>
              </div>
            )}
            {message.role === 'assistant' && (message.sources || []).length > 0 && (
              <MessageSources sources={message.sources} />
            )}
          </div>
        </div>
      ))}
    </>
  );
}

export function ThinkingPanel({ thinking, inProgress }) {
  // 手动管理折叠：流式期间每个 thinking_token 都会触发重渲染，
  // 用受控 <details open> 会把用户手动折叠的面板重新顶开，故用 useState 保持用户操作。
  const [open, setOpen] = useState(true);
  return (
    <div className={`message-thinking ${inProgress ? 'in-progress' : ''}`}>
      <button type="button" className="message-thinking-toggle" onClick={() => setOpen((value) => !value)}>
        <Brain size={14} />
        <span>{inProgress ? '深度思考中…' : '已深度思考'}</span>
        <ChevronDown size={14} className={`thinking-chevron ${open ? 'open' : ''}`} />
      </button>
      {open && <pre className="message-thinking-body">{thinking}</pre>}
    </div>
  );
}

export function ProcessNotes({ notes }) {
  // 调工具前模型顺口说的话（「我先查一下」）：默认折叠，不混进最终回答
  const [open, setOpen] = useState(false);
  return (
    <div className="message-thinking message-process">
      <button type="button" className="message-thinking-toggle" onClick={() => setOpen((value) => !value)}>
        <Search size={14} />
        <span>过程说明（{notes.length}）</span>
        <ChevronDown size={14} className={`thinking-chevron ${open ? 'open' : ''}`} />
      </button>
      {open && <pre className="message-thinking-body">{notes.join('\n\n')}</pre>}
    </div>
  );
}

export function MarkdownContent({ content }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          // react-markdown v9 移除了 code 组件的 inline 属性：块级代码会被包进 <pre><code>，
          // 行内代码只是裸 <code>。因此用 pre 渲染代码块，code 仅负责行内代码，
          // 避免行内的 `int`、`long long` 等被误渲染成代码块。
          pre({ children }) {
            const codeEl = Array.isArray(children) ? children[0] : children;
            const className = codeEl?.props?.className || '';
            const match = /language-(\w+)/.exec(className);
            const code = String(codeEl?.props?.children ?? '').replace(/\n$/, '');
            return <CodeBlock language={match?.[1] || 'text'} code={code} />;
          },
          code({ className, children, ...props }) {
            return <code className={className} {...props}>{children}</code>;
          },
          a({ children, href, ...props }) {
            return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>;
          },
        }}
      >
        {content || ''}
      </ReactMarkdown>
    </div>
  );
}

export function CodeBlock({ language, code }) {
  async function copyCode() {
    await navigator.clipboard?.writeText(code);
  }
  return (
    <div className="code-block">
      <div className="code-header">
        <span>{language || 'text'}</span>
        <button type="button" onClick={copyCode}>复制</button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}

export function MessageSources({ sources }) {
  const [activeKey, setActiveKey] = useState(null);
  // 根据文档ID或标题进行去重，避免对同一文档的多个分片重复渲染完全相同的卡片
  const uniqueSources = [];
  const seenDocs = new Set();

  for (const src of sources) {
    const docKey = src.document_id || src.title || src.source_id;
    if (docKey) {
      if (!seenDocs.has(docKey)) {
        seenDocs.add(docKey);
        uniqueSources.push(src);
      }
    } else {
      uniqueSources.push(src);
    }
  }

  const visible = uniqueSources.slice(0, 4);
  const hiddenCount = Math.max(0, uniqueSources.length - visible.length);

  return (
    <details className="message-sources">
      <summary>引用来源 <span>{uniqueSources.length}</span></summary>
      <div className="message-source-list">
        {visible.map((source) => (
          <SourceChip key={sourceKey(source)} source={source} onSelect={() => setActiveKey(sourceKey(source))} />
        ))}
        {hiddenCount > 0 && (
          <button type="button" className="source-more" onClick={() => setActiveKey(sourceKey(uniqueSources[visible.length]))}>
            还有 {hiddenCount} 个
          </button>
        )}
      </div>
      {activeKey && <SourcePanel sources={sources} activeKey={activeKey} onClose={() => setActiveKey(null)} />}
    </details>
  );
}

function sourceKey(source) {
  return source.chunk_id || source.url || `${source.title}-${source.snippet}`;
}

function sourceMeta(source) {
  return [
    source.page ? `p.${source.page}` : '',
    source.section || '',
    source.retrieval_channel || '',
    Number.isFinite(Number(source.score)) ? Number(source.score).toFixed(2) : '',
  ].filter(Boolean).join(' · ');
}

export function SourceChip({ source, onSelect }) {
  const meta = sourceMeta(source);
  return (
    <button type="button" title={source.snippet || source.title} onClick={onSelect}>
      {source.url ? <Search size={14} /> : <FileText size={14} />}
      <strong>{source.title || source.source_id || 'source'}</strong>
      {meta && <small>{meta}</small>}
    </button>
  );
}

// 右侧引用面板：列出本轮回答用到的全部片段（卡片按文档合并过，这里逐条展开），
// 知识库片段显示命中原文，网页结果显示摘要并可跳转原网页。
function SourcePanel({ sources, activeKey, onClose }) {
  const activeRef = useRef(null);
  const knowledge = sources.filter((source) => !source.url);
  const web = sources.filter((source) => source.url);

  useEffect(() => {
    const onKeyDown = (event) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKeyDown);
    // 面板打开时给页面让出右侧宽度，避免盖住正在看的回答
    document.body.classList.add('source-panel-open');
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.classList.remove('source-panel-open');
    };
  }, [onClose]);

  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [activeKey]);

  const renderItem = (source, index) => {
    const key = sourceKey(source);
    const active = key === activeKey;
    const meta = sourceMeta(source);
    return (
      <li key={key} ref={active ? activeRef : null} className={active ? 'active' : ''}>
        <div className="source-panel-item-head">
          <span className="source-panel-index">{index + 1}</span>
          {source.url ? <Search size={14} /> : <FileText size={14} />}
          <strong>{source.title || source.source_id || 'source'}</strong>
        </div>
        {meta && <small>{meta}</small>}
        {source.snippet && <p>{source.snippet}</p>}
        {source.url && (
          <a href={source.url} target="_blank" rel="noreferrer">
            {safeHost(source.url)} <ExternalLink size={12} />
          </a>
        )}
      </li>
    );
  };

  return createPortal(
    <aside className="source-panel" role="complementary" aria-label="引用来源">
      <header>
        <strong>引用来源</strong>
        <button type="button" aria-label="关闭引用来源" onClick={onClose}><X size={16} /></button>
      </header>
      <div className="source-panel-body">
        {knowledge.length > 0 && (
          <section>
            <h4>知识库 · {knowledge.length}</h4>
            <ol>{knowledge.map(renderItem)}</ol>
          </section>
        )}
        {web.length > 0 && (
          <section>
            <h4>网页 · {web.length}</h4>
            <ol>{web.map(renderItem)}</ol>
          </section>
        )}
      </div>
    </aside>,
    document.body,
  );
}

function safeHost(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
