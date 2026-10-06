import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AgentSearchSetting } from './AgentSearchSetting.jsx';

describe('Agent 联网搜索配置', () => {
  it('开关更新 Agent 配置并保留已有工具策略', () => {
    const onChange = vi.fn();
    render(<AgentSearchSetting policy={{ mode: 'auto', allowed_tool_names: ['calculator'], web_search_enabled: false }} onChange={onChange} />);
    fireEvent.click(screen.getByRole('checkbox', { name: '允许联网搜索' }));
    expect(onChange).toHaveBeenCalledWith({ mode: 'auto', allowed_tool_names: ['calculator', 'web_search'], web_search_enabled: true });
  });
  it('非可编辑 Agent 的开关不可修改', () => {
    render(<AgentSearchSetting policy={{ web_search_enabled: true }} onChange={vi.fn()} disabled />);
    expect(screen.getByRole('checkbox')).toBeDisabled();
    expect(screen.getByRole('checkbox')).toBeChecked();
  });
});
