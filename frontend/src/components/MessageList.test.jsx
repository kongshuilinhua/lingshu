import React from 'react';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MessageList } from './MessageList.jsx';

const sources = [
  { title: '星澜咖啡馆运营手册', document_id: 40, chunk_id: 'c1', section: '营业时间', retrieval_channel: 'rerank', score: 0.83,
    snippet: '星澜咖啡馆每周二至周日营业，营业时间为上午 9 点 30 分到晚上 8 点 45 分。' },
  { title: '星澜咖啡馆运营手册', document_id: 40, chunk_id: 'c2', retrieval_channel: 'rerank', score: 0.12,
    snippet: '每周一闭店做设备保养，全天不对外营业。' },
  { title: '咖啡因的作用', url: 'https://example.com/caffeine', chunk_id: 'web-search-1', source_type: 'web_search',
    snippet: '咖啡因是一种中枢神经兴奋剂。' },
];

function renderAnswer() {
  render(<MessageList messages={[{ role: 'assistant', id: 1, content: '周二至周日 9:30 开门。', sources }]} />);
  fireEvent.click(screen.getByText('引用来源'));
}

describe('引用来源面板', () => {
  it('点击引用卡片在右侧展开本轮全部片段，含同一文档被合并的片段', () => {
    renderAnswer();
    expect(screen.queryByRole('complementary', { name: '引用来源' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /星澜咖啡馆运营手册/ }));

    const panel = screen.getByRole('complementary', { name: '引用来源' });
    expect(within(panel).getByText(/上午 9 点 30 分/)).toBeInTheDocument();
    expect(within(panel).getByText(/每周一闭店/)).toBeInTheDocument();
    expect(within(panel).getByText(/营业时间 · rerank · 0.83/)).toBeInTheDocument();
    expect(within(panel).getByText('知识库 · 2')).toBeInTheDocument();
  });

  it('联网结果在面板里展示摘要并给出原网页链接', () => {
    renderAnswer();
    fireEvent.click(screen.getByRole('button', { name: /咖啡因的作用/ }));

    const panel = screen.getByRole('complementary', { name: '引用来源' });
    expect(within(panel).getByText('网页 · 1')).toBeInTheDocument();
    expect(within(panel).getByText('咖啡因是一种中枢神经兴奋剂。')).toBeInTheDocument();
    const link = within(panel).getByRole('link', { name: /example\.com/ });
    expect(link).toHaveAttribute('href', 'https://example.com/caffeine');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('点中的卡片在面板中高亮，可用关闭按钮或 Esc 收起', () => {
    renderAnswer();
    fireEvent.click(screen.getByRole('button', { name: /咖啡因的作用/ }));
    const panel = screen.getByRole('complementary', { name: '引用来源' });
    expect(within(panel).getByText('咖啡因是一种中枢神经兴奋剂。').closest('li')).toHaveClass('active');

    fireEvent.click(within(panel).getByRole('button', { name: '关闭引用来源' }));
    expect(screen.queryByRole('complementary', { name: '引用来源' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /星澜咖啡馆运营手册/ }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('complementary', { name: '引用来源' })).toBeNull();
  });
});

describe('过程说明', () => {
  it('调工具前的说明默认折叠，展开后可见，且不混进正文', () => {
    render(<MessageList messages={[{ role: 'assistant', content: '营业到 22 点。', process: ['我先查一下。'] }]} />);
    expect(screen.queryByText('我先查一下。')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /过程说明（1）/ }));
    expect(screen.getByText('我先查一下。')).toBeTruthy();
    expect(screen.getByText('营业到 22 点。')).toBeTruthy();
  });
});
