import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { McpBindingPanel } from './McpBindingPanel.jsx';
import { SkillBindingPanel } from './SkillBindingPanel.jsx';
import { api } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({ api: vi.fn() }));
beforeEach(() => api.mockReset());

const cases = [
  { name: 'MCP', Panel: McpBindingPanel, catalog: '/api/mcp/servers', suffix: 'mcp-bindings',
    resource: { id: 1, name: 'Echo', enabled: true, catalog: { tools: [{ name: 'echo' }] } },
    binding: { server_id: 1, selected_tools: ['echo'], enabled: true }, button: '保存 MCP 配置' },
  { name: 'Skill', Panel: SkillBindingPanel, catalog: '/api/skills', suffix: 'skill-bindings',
    resource: { id: 1, name: 'Echo', enabled: true, current_version_id: 10, versions: [{ id: 10, version: 1 }] },
    binding: { skill_id: 1, version_id: 10, enabled: true }, button: '保存 Skill 配置' },
];

describe.each(cases)('$name 绑定请求隔离', ({ Panel, catalog, suffix, resource, binding, button }) => {
  it('保存时禁止继续修改；切换智能体后旧保存结果不能覆盖新配置', async () => {
    let completeSave;
    api.mockImplementation((path, options) => {
      if (options?.method === 'PUT') return new Promise((resolve) => { completeSave = resolve; });
      return Promise.resolve({ items: path === catalog ? [resource] : [] });
    });
    const props = { token: 'test', canEdit: true, onOpenMarket: vi.fn() };
    const view = render(<Panel {...props} agentId={7} />);
    fireEvent.click(await screen.findByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: button }));
    expect(screen.getByRole('checkbox')).toBeDisabled();
    expect(api).toHaveBeenCalledWith(`/api/agents/7/${suffix}`, expect.objectContaining({ method: 'PUT' }));
    view.rerender(<Panel {...props} agentId={8} />);
    expect(await screen.findByRole('checkbox')).not.toBeChecked();
    await act(async () => completeSave({ items: [binding] }));
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByRole('button', { name: button })).toBeDisabled();
  });

  it('新智能体加载失败时清除旧资源和未保存绑定', async () => {
    api.mockImplementation((path) => path === `/api/agents/8/${suffix}`
      ? Promise.reject(new Error('新配置加载失败'))
      : Promise.resolve({ items: path === catalog ? [resource] : [] }));
    const props = { token: 'test', canEdit: true, onOpenMarket: vi.fn() };
    const view = render(<Panel {...props} agentId={7} />);
    fireEvent.click(await screen.findByRole('checkbox'));
    expect(screen.getByRole('button', { name: button })).toBeEnabled();
    view.rerender(<Panel {...props} agentId={8} />);
    await screen.findByText('新配置加载失败');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: button })).toBeDisabled();
  });
});
