import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RetrievalTestPanel } from './RetrievalTestPanel.jsx';

const hit = {
  query: '周二营业吗',
  latency_ms: 820,
  status: {
    reason: 'available',
    dense: { matched: 5 },
    bm25: { matched: 4 },
    rrf: { matched: 6 },
    rerank: { applied: true, error: null },
    translate: { applied: false },
    no_evidence: false,
  },
  items: [
    { chunk_id: 'c1', title: '运营手册', section: '营业时间', channel: 'rerank', score: 0.83, dense_score: 0.71, bm25_score: 0 },
    { chunk_id: 'c2', title: '运营手册', section: '闭店', channel: 'bm25', score: 0.21, dense_score: 0, bm25_score: 3.4 },
  ],
};

describe('召回测试面板', () => {
  it('提交问题后展示各通道召回数与每个片段的分数', async () => {
    const runRetrievalTest = vi.fn().mockResolvedValue(hit);
    render(<RetrievalTestPanel kbId={7} runRetrievalTest={runRetrievalTest} onClose={vi.fn()} />);

    fireEvent.change(screen.getByLabelText('测试问题'), { target: { value: '周二营业吗' } });
    fireEvent.click(screen.getByRole('button', { name: '测试' }));

    await waitFor(() => expect(runRetrievalTest).toHaveBeenCalledWith(7, { query: '周二营业吗', top_k: 6, rerank_enabled: true }));
    expect(screen.getByText(/向量召回 5 · 关键词召回 4 · 融合 6 · 已重排/)).toBeInTheDocument();
    expect(screen.getByText('营业时间')).toBeInTheDocument();
    expect(screen.getByText('得分 0.830')).toBeInTheDocument();
    expect(screen.getByText('关键词 3.400')).toBeInTheDocument();
  });

  it('未命中时提示会触发拒答', async () => {
    const runRetrievalTest = vi.fn().mockResolvedValue({
      query: '无关问题', latency_ms: 300,
      status: { reason: 'no_match', dense: { matched: 0 }, bm25: { matched: 0 }, rrf: { matched: 0 }, rerank: { applied: false }, no_evidence: true },
      items: [],
    });
    render(<RetrievalTestPanel kbId={7} runRetrievalTest={runRetrievalTest} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('测试问题'), { target: { value: '无关问题' } });
    fireEvent.click(screen.getByRole('button', { name: '测试' }));
    await waitFor(() => expect(screen.getByText(/没有命中任何片段/)).toBeInTheDocument());
  });

  it('关闭重排时按传入参数请求', async () => {
    const runRetrievalTest = vi.fn().mockResolvedValue(hit);
    render(<RetrievalTestPanel kbId={7} runRetrievalTest={runRetrievalTest} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('测试问题'), { target: { value: '营业时间' } });
    fireEvent.click(screen.getByLabelText('重排'));
    fireEvent.click(screen.getByRole('button', { name: '测试' }));
    await waitFor(() => expect(runRetrievalTest).toHaveBeenCalledWith(7, { query: '营业时间', top_k: 6, rerank_enabled: false }));
  });

  it('请求失败时显示错误信息', async () => {
    const runRetrievalTest = vi.fn().mockRejectedValue(new Error('Knowledge base not found'));
    render(<RetrievalTestPanel kbId={7} runRetrievalTest={runRetrievalTest} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('测试问题'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: '测试' }));
    await waitFor(() => expect(screen.getByText('Knowledge base not found')).toBeInTheDocument());
  });
});
