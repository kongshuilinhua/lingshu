import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { KnowledgeHome } from './KnowledgeHome.jsx';

function renderHome(overrides = {}) {
  const props = {
    canManage: true,
    createKnowledgeBase: vi.fn(),
    updateKnowledgeBase: vi.fn(async () => ({})),
    deleteDocument: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
    docForm: { kb_id: '' },
    documents: [],
    knowledgeBases: [
      { id: 1, name: 'KB1', description: '', document_count: 2, enabled: true, updated_at: '2026-10-01T08:00:00' },
      { id: 2, name: 'KB2', description: '', document_count: 0, enabled: true, updated_at: null },
    ],
    setDocForm: vi.fn(),
    setProfileError: vi.fn(),
    uploadingKnowledgeFile: false,
    uploadingFileName: '',
    uploadDocument: vi.fn(),
    uploadKnowledgeFile: vi.fn(),
    token: 'test-token',
    loadDocuments: vi.fn(),
    notify: vi.fn(),
    ...overrides,
  };
  render(<KnowledgeHome {...props} />);
  return props;
}

describe('知识库列表展示层真实性', () => {
  it('时间列显示真实 updated_at', async () => {
    renderHome();
    await waitFor(() => expect(screen.getByText('KB1')).toBeInTheDocument());
    expect(screen.getByText(/2026\/10\/01/)).toBeInTheDocument();
  });

  it('updated_at 缺失时显示占位符，不显示伪造日期', async () => {
    renderHome();
    await waitFor(() => expect(screen.getByText('KB2')).toBeInTheDocument());
    // KB2 的 updated_at 为 null：必须显示占位符
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('2026-05-30')).toBeNull();
  });

  it('不再渲染硬编码的「类型」列', async () => {
    renderHome();
    await waitFor(() => expect(screen.getByText('KB1')).toBeInTheDocument());
    expect(screen.queryByText('类型')).toBeNull();
    expect(screen.queryByText('文档库')).toBeNull();
  });

  it('不再渲染「复制到其他空间」这个假按钮', async () => {
    renderHome();
    await waitFor(() => expect(screen.getByText('KB1')).toBeInTheDocument());
    fireEvent.click(screen.getAllByTitle('操作')[0]);
    expect(screen.queryByText('复制到其他空间')).toBeNull();
    // 真实操作仍在
    expect(screen.getByText('删除')).toBeInTheDocument();
  });

  it('开关受控于后端 enabled，切换时调用 updateKnowledgeBase 传 enabled', async () => {
    const props = renderHome();
    await waitFor(() => expect(screen.getByText('KB1')).toBeInTheDocument());

    const switches = screen.getAllByRole('checkbox');
    expect(switches[0]).toBeChecked();

    fireEvent.click(switches[0]);

    await waitFor(() => {
      expect(props.updateKnowledgeBase).toHaveBeenCalledWith(1, { enabled: false });
    });
    await waitFor(() => expect(props.notify).toHaveBeenCalledWith('「KB1」已停用'));
  });

  it('更新失败时回滚开关并提示错误', async () => {
    const updateKnowledgeBase = vi.fn(async () => {
      throw new Error('后端挂了');
    });
    const props = renderHome({ updateKnowledgeBase });
    await waitFor(() => expect(screen.getByText('KB1')).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole('checkbox')[0]);

    await waitFor(() => expect(props.notify).toHaveBeenCalledWith('「KB1」状态更新失败：后端挂了'));
    // 回滚到后端权威值（仍为启用）
    await waitFor(() => expect(screen.getAllByRole('checkbox')[0]).toBeChecked());
  });

  it('enabled 为 false 的知识库初始渲染为关闭', async () => {
    renderHome({
      knowledgeBases: [
        { id: 3, name: 'KB3', description: '', document_count: 0, enabled: false, updated_at: '2026-10-02T09:30:00' },
      ],
    });
    await waitFor(() => expect(screen.getByText('KB3')).toBeInTheDocument());
    expect(screen.getAllByRole('checkbox')[0]).not.toBeChecked();
  });
});
