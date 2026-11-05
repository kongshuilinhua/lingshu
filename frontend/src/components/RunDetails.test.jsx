import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MessageList } from './MessageList.jsx';

const steps = {
  run: { id: 9, status: 'succeeded' },
  items: [
    { id: 1, node_id: 'start', node_type: 'Start', status: 'succeeded', latency_ms: 3, summary: {} },
    {
      id: 2, node_id: 'knowledge', node_type: 'Knowledge', status: 'succeeded', latency_ms: 3800,
      summary: { matched: 2, dense: 5, bm25: 4, rerank_applied: true, query: '营业时间',
        sources: [{ title: '运营手册', snippet: '周二至周日 9:30 开门', score: 0.83 }] },
    },
    {
      id: 3, node_id: 'tool', node_type: 'Tool', status: 'succeeded', latency_ms: 900,
      summary: { total_calls: 1, calls: [{ tool: 'weather_lookup', status: 'error', error_code: 'tool_error', arguments: { city: '杭州' } }] },
    },
    { id: 4, node_id: 'llm', node_type: 'LLM', status: 'succeeded', latency_ms: 1200, summary: { model: 'deepseek-v4', thinking_type: 'none' } },
  ],
};

describe('运行详情', () => {
  it('展开时才请求节点记录，并展示检索命中、工具失败和最慢节点', async () => {
    const loadRunSteps = vi.fn().mockResolvedValue(steps);
    render(<MessageList messages={[{ role: 'assistant', id: 5, run_id: 9, content: '答案' }]} loadRunSteps={loadRunSteps} />);

    expect(loadRunSteps).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /运行详情/ }));
    expect(loadRunSteps).toHaveBeenCalledWith(9);

    await waitFor(() => expect(screen.getByText('知识检索')).toBeInTheDocument());
    expect(screen.getByText(/命中 2 个片段 · 向量 5 · 关键词 4 · 已重排/)).toBeInTheDocument();
    expect(screen.getByText('运营手册')).toBeInTheDocument();
    expect(screen.getByText('weather_lookup')).toBeInTheDocument();
    expect(screen.getByText(/失败（tool_error）/)).toBeInTheDocument();
    // 最慢节点高亮
    expect(screen.getByText('3.8s')).toHaveClass('slowest');
    // 收起再展开不重复请求
    fireEvent.click(screen.getByRole('button', { name: /运行详情/ }));
    fireEvent.click(screen.getByRole('button', { name: /运行详情/ }));
    expect(loadRunSteps).toHaveBeenCalledTimes(1);
  });

  it('没有 run_id 的历史消息不显示运行详情', () => {
    render(<MessageList messages={[{ role: 'assistant', id: 5, content: '旧答案' }]} loadRunSteps={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /运行详情/ })).toBeNull();
  });

  it('加载失败时显示错误而不是空白', async () => {
    const loadRunSteps = vi.fn().mockRejectedValue(new Error('Run not found'));
    render(<MessageList messages={[{ role: 'assistant', id: 5, run_id: 9, content: '答案' }]} loadRunSteps={loadRunSteps} />);
    fireEvent.click(screen.getByRole('button', { name: /运行详情/ }));
    await waitFor(() => expect(screen.getByText('Run not found')).toBeInTheDocument());
  });
});
