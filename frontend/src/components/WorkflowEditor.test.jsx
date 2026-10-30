import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkflowEditor } from './WorkflowEditor.jsx';

const defaultNodes = [
  { id: 'start', type: 'Start', name: '接收用户输入', config: {} },
  { id: 'knowledge', type: 'Knowledge', name: '检索绑定知识库', config: { top_k: 4 } },
  { id: 'tool', type: 'Tool', name: '调用绑定工具', config: { tools: [] } },
  { id: 'llm', type: 'LLM', name: '生成候选回答', config: {} },
  { id: 'answer', type: 'Answer', name: '输出最终回答', config: {} },
];

function renderEditor(overrides = {}) {
  const onSave = vi.fn().mockResolvedValue({ nodes: defaultNodes });
  const loadWorkflow = overrides.loadWorkflow || vi.fn().mockResolvedValue({ nodes: defaultNodes });
  render(
    <WorkflowEditor
      agentId={1}
      token="t"
      canEdit
      tools={[]}
      onCountChange={overrides.onCountChange}
      loadWorkflow={loadWorkflow}
      saveWorkflow={overrides.saveWorkflow || onSave}
    />,
  );
  return { onSave, loadWorkflow };
}

describe('工作流编排（表单式）', () => {
  it('加载后按顺序展示节点', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByText('知识检索')).toBeInTheDocument());
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent('开始');
    expect(items[4]).toHaveTextContent('输出回答');
  });

  it('开始与输出回答节点不能移动', async () => {
    renderEditor();
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(5));
    const first = screen.getAllByRole('listitem')[0];
    expect(first.querySelector('[aria-label="上移"]')).toBeDisabled();
    expect(first.querySelector('[aria-label="下移"]')).toBeDisabled();
  });

  it('删除节点时清理指向它的跳转，避免留悬空边', async () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {}, next: 'llm' },
      { id: 'llm', type: 'LLM', config: {}, next: 'answer' },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    const saveWorkflow = vi.fn().mockResolvedValue({ nodes: [] });
    renderEditor({ loadWorkflow: vi.fn().mockResolvedValue({ nodes }), saveWorkflow });
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3));

    // 删掉中间的 LLM
    fireEvent.click(screen.getAllByRole('listitem')[1].querySelector('[aria-label="删除节点"]'));
    fireEvent.click(screen.getByRole('button', { name: /保存工作流/ }));

    await waitFor(() => expect(saveWorkflow).toHaveBeenCalled());
    const saved = saveWorkflow.mock.calls[0][1];
    expect(saved.map((node) => node.id)).toEqual(['start', 'answer']);
    // start 原本 next 指向已删除的 llm，必须被清掉
    expect(saved[0].next).toBeUndefined();
  });

  it('条件分支缺字段时禁止保存', async () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {} },
      { id: 'cond', type: 'Condition', config: { branches: [{ when: { field: '', gte: 1 }, next: 'answer' }], default: '' } },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    renderEditor({ loadWorkflow: vi.fn().mockResolvedValue({ nodes }) });
    await waitFor(() => expect(screen.getByText('条件分支')).toBeInTheDocument());
    expect(screen.getByText('条件分支缺少判断字段')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存工作流/ })).toBeDisabled();
  });

  it('知识节点的召回数会写入保存载荷', async () => {
    const saveWorkflow = vi.fn().mockResolvedValue({ nodes: defaultNodes });
    renderEditor({ saveWorkflow });
    await waitFor(() => expect(screen.getByText('知识检索')).toBeInTheDocument());
    fireEvent.click(screen.getByText('知识检索'));
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: /保存工作流/ }));

    await waitFor(() => expect(saveWorkflow).toHaveBeenCalled());
    const saved = saveWorkflow.mock.calls[0][1];
    expect(saved.find((node) => node.id === 'knowledge').config.top_k).toBe(8);
  });

  it('不可编辑时不显示增删与保存', async () => {
    render(
      <WorkflowEditor agentId={1} token="t" canEdit={false} loadWorkflow={vi.fn().mockResolvedValue({ nodes: defaultNodes })} />,
    );
    await waitFor(() => expect(screen.getByText('知识检索')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /添加节点/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /保存工作流/ })).toBeNull();
  });
});
