import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ChatComposer } from './ChatComposer.jsx';
import { chatModelOptions, chatModelOverride } from '../../lib/models.js';

describe('聊天模型选择', () => {
  const options = chatModelOptions([
    { id: 1, model_name: 'System chat', enabled: true },
    { id: 2, model_name: 'Disabled', enabled: false },
    { id: 3, model_name: 'Image only', enabled: true, supports_text: false },
  ], [
    { id: 1, display_name: 'My model', chat_model: 'private-chat', enabled: true },
    { id: 2, display_name: 'Disabled private', enabled: false },
  ]);

  it('分组显示可用模型，同 ID 的平台和私有模型分别选择，支持恢复默认', () => {
    const change = vi.fn();
    render(<ChatComposer value="" onChange={vi.fn()} modelOptions={options} onModelChange={change} />);
    const select = screen.getByRole('combobox', { name: '聊天模型' });
    expect(screen.getAllByRole('option')).toHaveLength(3);
    expect(screen.getByRole('group', { name: '我的模型' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '平台模型' })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'user:1' } });
    expect(change).toHaveBeenLastCalledWith('user:1');
    expect(chatModelOverride('user:1')).toEqual({ source: 'user', id: 1 });
    fireEvent.change(select, { target: { value: 'system:1' } });
    expect(chatModelOverride(change.mock.lastCall[0])).toEqual({ source: 'system', id: 1 });
    fireEvent.change(select, { target: { value: '' } });
    expect(chatModelOverride(change.mock.lastCall[0])).toBeUndefined();
  });

  it('生成期间禁止切换模型', () => {
    render(<ChatComposer value="" onChange={vi.fn()} modelOptions={options} onModelChange={vi.fn()} modelSelectionDisabled />);
    expect(screen.getByRole('combobox', { name: '聊天模型' })).toBeDisabled();
  });
});
