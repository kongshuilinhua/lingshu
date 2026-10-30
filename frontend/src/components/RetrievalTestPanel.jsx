import React, { useState } from 'react';
import { Search, X } from 'lucide-react';
import { api, errorMessage } from '../utils.js';
import { MetadataFilterEditor, cleanFilter, emptyFilter } from './MetadataFilterEditor.jsx';

const CHANNEL_LABELS = { dense: '向量', bm25: '关键词', rrf: '融合', rerank: '重排' };

/**
 * 知识库召回测试：输入问题，看这个库会命中哪些片段、各通道分数是多少。
 *
 * 走的是对话时同一个 retrieve()（后端关闭缓存），结果与智能体实际拿到的一致，
 * 用来判断「答不上来」到底是没切到、没召回、还是被重排压下去了。
 */
export function RetrievalTestPanel({ kbId, token, onClose, runRetrievalTest, metadataKeys = [] }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState(emptyFilter);
  const [topK, setTopK] = useState(6);
  const [rerank, setRerank] = useState(true);
  const [state, setState] = useState({ loading: false, error: '', result: null });

  async function run(event) {
    event?.preventDefault();
    const text = query.trim();
    if (!text || state.loading) return;
    setState({ loading: true, error: '', result: null });
    try {
      const payload = { query: text, top_k: topK, rerank_enabled: rerank };
      const metadataFilter = cleanFilter(filter);
      if (metadataFilter) payload.metadata_filter = metadataFilter;
      const result = runRetrievalTest
        ? await runRetrievalTest(kbId, payload)
        : await api(`/api/knowledge-bases/${kbId}/retrieval-test`, { token, method: 'POST', body: payload });
      setState({ loading: false, error: '', result });
    } catch (err) {
      setState({ loading: false, error: errorMessage(err), result: null });
    }
  }

  const result = state.result;
  const status = result?.status || {};

  return (
    <section className="retrieval-test" aria-label="召回测试">
      <header className="retrieval-test-header">
        <h4>召回测试</h4>
        <button type="button" className="retrieval-test-close" aria-label="关闭召回测试" onClick={onClose}><X size={16} /></button>
      </header>
      <form className="retrieval-test-form" onSubmit={run}>
        <div className="retrieval-test-input">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入用户可能会问的问题"
            maxLength={500}
            aria-label="测试问题"
          />
        </div>
        <label>
          返回
          <select value={topK} onChange={(event) => setTopK(Number(event.target.value))} aria-label="返回片段数">
            {[3, 6, 10, 20].map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          条
        </label>
        <label className="retrieval-test-toggle">
          <input type="checkbox" checked={rerank} onChange={(event) => setRerank(event.target.checked)} />
          重排
        </label>
        <button type="submit" className="primary" disabled={!query.trim() || state.loading}>
          {state.loading ? '检索中…' : '测试'}
        </button>
      </form>
      <MetadataFilterEditor value={filter} onChange={setFilter} keys={metadataKeys} />

      {state.error && <p className="retrieval-test-error">{state.error}</p>}

      {result && (
        <>
          <p className="retrieval-test-summary">
            {status.reason === 'all_knowledge_bases_disabled'
              ? '该知识库已停用，对话时不会检索。'
              : status.reason === 'metadata_filter_no_document'
              ? '没有文档满足这组元数据条件。'
              : <>
                {status.metadata_filter && `过滤后 ${status.metadata_filter.documents} 篇文档 · `}
                向量召回 {status.dense?.matched ?? 0} · 关键词召回 {status.bm25?.matched ?? 0} · 融合 {status.rrf?.matched ?? 0}
                {status.rerank?.applied && ' · 已重排'}
                {status.rerank?.error && ` · 重排失败（已降级）`}
                {status.translate?.applied && ' · 已跨语言翻译'}
                {' · '}{result.latency_ms}ms
              </>}
          </p>
          {status.no_evidence && result.items.length > 0 && (
            <p className="retrieval-test-warn">分数偏低：开启「无证据拒答」时，模型会被要求只依据这些片段作答。</p>
          )}
          {result.items.length === 0 && !['all_knowledge_bases_disabled', 'metadata_filter_no_document'].includes(status.reason) && (
            <p className="retrieval-test-empty">没有命中任何片段。对话时会触发「无证据拒答」。</p>
          )}
          <ol className="retrieval-test-results">
            {result.items.map((item, index) => (
              <li key={item.chunk_id || index}>
                <div className="retrieval-test-meta">
                  <span className="retrieval-test-rank">#{index + 1}</span>
                  <strong>{item.title}</strong>
                  {item.section && <span className="retrieval-test-section">{item.section}</span>}
                  <span className="retrieval-test-channel">{CHANNEL_LABELS[item.channel] || item.channel}</span>
                </div>
                <div className="retrieval-test-scores">
                  <span>得分 {formatScore(item.score)}</span>
                  <span>向量 {formatScore(item.dense_score)}</span>
                  <span>关键词 {formatScore(item.bm25_score)}</span>
                </div>
                <p>{item.snippet}</p>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function formatScore(value) {
  if (value == null) return '-';
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(3) : '-';
}
