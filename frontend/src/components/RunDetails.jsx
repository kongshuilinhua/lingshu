import React, { useState } from 'react';
import { Activity, ChevronDown } from 'lucide-react';

const NODE_LABELS = {
  Start: '开始',
  Knowledge: '知识检索',
  Tool: '工具',
  LLM: '模型生成',
  Answer: '输出回答',
  Condition: '条件分支',
  HumanApproval: '人工审批',
};

const RAG_REASONS = {
  on_demand: '按需调用：交给模型决定是否检索',
  disabled: '本轮关闭了知识库',
  small_talk: '寒暄，不使用检索片段',
  no_evidence_refused: '没有检索到片段，已拒答',
  no_knowledge_base: '未绑定知识库',
  all_knowledge_bases_disabled: '绑定的知识库都已停用',
};

/**
 * 运行详情：按节点展开一次回答的执行过程（耗时、检索命中、工具调用、模型）。
 *
 * 数据按需加载：只有展开时才请求 /api/runs/{id}/steps，历史消息很多时不会批量拉取。
 * `loadRunSteps` 由上层注入（带鉴权），组件本身不关心 token。
 */
export function RunDetails({ runId, loadRunSteps }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ loading: false, error: '', data: null });

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || state.data || state.loading) return;
    setState({ loading: true, error: '', data: null });
    try {
      const data = await loadRunSteps(runId);
      setState({ loading: false, error: '', data });
    } catch (err) {
      setState({ loading: false, error: err?.message || '运行详情加载失败', data: null });
    }
  }

  const items = state.data?.items || [];
  const total = items.reduce((sum, item) => sum + (item.latency_ms || 0), 0);
  const slowest = items.reduce((max, item) => Math.max(max, item.latency_ms || 0), 0);

  return (
    <div className="run-details">
      <button type="button" className="message-thinking-toggle" aria-expanded={open} onClick={toggle}>
        <Activity size={14} />
        <span>运行详情{total ? ` · ${formatMs(total)}` : ''}</span>
        <ChevronDown size={14} className={`thinking-chevron ${open ? 'open' : ''}`} />
      </button>
      {open && (
        <div className="run-details-body">
          {state.loading && <p className="run-details-empty">加载中…</p>}
          {state.error && <p className="run-details-error">{state.error}</p>}
          {!state.loading && !state.error && items.length === 0 && state.data && (
            <p className="run-details-empty">这次运行没有记录节点。</p>
          )}
          {items.length > 0 && (
            <ol className="run-steps">
              {items.map((item) => (
                <li key={item.id} className={`run-step ${item.status}`}>
                  <div className="run-step-head">
                    <strong>{NODE_LABELS[item.node_type] || item.node_type}</strong>
                    <span className="run-step-id">{item.node_id}</span>
                    {item.status !== 'succeeded' && <span className="run-step-status">{item.status}</span>}
                    {item.latency_ms != null && (
                      <span className={`run-step-latency ${slowest && item.latency_ms === slowest && items.length > 1 ? 'slowest' : ''}`}>
                        {formatMs(item.latency_ms)}
                      </span>
                    )}
                  </div>
                  <StepSummary type={item.node_type} summary={item.summary || {}} />
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

function StepSummary({ type, summary }) {
  if (type === 'Knowledge') {
    const reason = RAG_REASONS[summary.reason];
    return (
      <div className="run-step-detail">
        {reason ? <p>{reason}</p> : (
          <p>
            命中 {summary.matched ?? 0} 个片段
            {summary.dense != null && ` · 向量 ${summary.dense}`}
            {summary.bm25 != null && ` · 关键词 ${summary.bm25}`}
            {summary.rerank_applied && ' · 已重排'}
            {summary.cache_hit && ' · 缓存命中'}
          </p>
        )}
        {summary.query && <p className="run-step-muted">检索词：{summary.query}</p>}
        {(summary.sources || []).length > 0 && (
          <ul className="run-step-sources">
            {summary.sources.map((source, index) => (
              <li key={`${source.title}-${index}`}>
                <span>{source.title || '未命名片段'}</span>
                {source.score != null && <small>{Number(source.score).toFixed(2)}</small>}
                <p>{source.snippet}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  if (type === 'Tool') {
    if (!summary.total_calls) return <p className="run-step-muted">模型没有调用工具</p>;
    return (
      <div className="run-step-detail">
        <p>调用 {summary.total_calls} 次</p>
        <ul className="run-step-calls">
          {(summary.calls || []).map((call, index) => (
            <li key={`${call.tool}-${index}`} className={call.status === 'error' ? 'error' : ''}>
              <code>{call.tool}</code>
              {call.status === 'error' && <small>失败{call.error_code ? `（${call.error_code}）` : ''}</small>}
              {call.arguments && typeof call.arguments === 'object' && Object.keys(call.arguments).length > 0 && (
                <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
              )}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (type === 'LLM') {
    if (!summary.model) return null;
    return (
      <p className="run-step-muted">
        {summary.model}
        {summary.thinking_type && summary.thinking_type !== 'none' && ` · 思考 ${summary.thinking_type}`}
        {summary.history_message_count ? ` · 带 ${summary.history_message_count} 条历史` : ''}
        {summary.used_profile_memory ? ' · 用了长期记忆' : ''}
      </p>
    );
  }
  if (type === 'HumanApproval' && summary.prompt) {
    return <p className="run-step-muted">{summary.prompt}</p>;
  }
  return null;
}

function formatMs(ms) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
