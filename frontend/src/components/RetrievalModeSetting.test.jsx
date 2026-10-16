import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { RetrievalModeSetting } from './RetrievalModeSetting.jsx';

describe('知识库检索方式', () => {
  it('切换检索方式时回传新值', () => {
    const onChange = vi.fn();
    render(<RetrievalModeSetting mode="on_demand" onChange={onChange} />);
    expect(screen.getByRole('radio', { name: '按需调用' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: '自动检索' }));
    expect(onChange).toHaveBeenCalledWith('auto');
  });

  it('旧数据缺少检索方式时按自动检索展示', () => {
    render(<RetrievalModeSetting mode={undefined} onChange={vi.fn()} />);
    expect(screen.getByRole('radio', { name: '自动检索' })).toBeChecked();
  });

  it('按需调用下提示补充知识库描述，自动检索下不提示', () => {
    const { rerender } = render(<RetrievalModeSetting mode="on_demand" missingDescriptions={['员工制度']} onChange={vi.fn()} />);
    expect(screen.getByText(/员工制度/)).toBeTruthy();
    rerender(<RetrievalModeSetting mode="auto" missingDescriptions={['员工制度']} onChange={vi.fn()} />);
    expect(screen.queryByText(/员工制度/)).toBeNull();
  });

  it('不可编辑时禁用', () => {
    render(<RetrievalModeSetting mode="auto" disabled onChange={vi.fn()} />);
    expect(screen.getByRole('radio', { name: '按需调用' })).toBeDisabled();
  });
});
