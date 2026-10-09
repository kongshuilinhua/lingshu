import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    if (suffix === 'mcp-bindings') expect(screen.getByRole('button', { name: '取消全选 Echo 工具' })).toBeDisabled();
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

describe('MCP 批量选择', () => {
  const tools = Array.from({ length: 49 }, (_, index) => ({ name: `github_${index}` }));
  const otherBinding = { server_id: 2, selected_tools: ['echo'], enabled: true };
  const server = { id: 1, name: 'GitHub', enabled: true, catalog: { tools } };
  const props = { agentId: 7, token: 'test', canEdit: true, onOpenMarket: vi.fn() };

  it('全选 49 个工具，保留其他服务配置；取消全选后仅移除当前服务', async () => {
    api.mockImplementation(async (path, options) => ({ items: path === '/api/mcp/servers' ? [server]
      : options?.method === 'PUT' ? options.body.items : [otherBinding, { server_id: 1, selected_tools: [tools[0].name], enabled: true }] }));
    render(<McpBindingPanel {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: '全选 GitHub 工具' }));
    expect(screen.getByText('已选 49 / 49')).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox')).toHaveLength(49);
    screen.getAllByRole('checkbox').forEach((checkbox) => expect(checkbox).toBeChecked());
    fireEvent.click(screen.getByRole('button', { name: '保存 MCP 配置' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/api/agents/7/mcp-bindings', expect.objectContaining({ method: 'PUT', body: { items: [
      otherBinding, { server_id: 1, selected_tools: tools.map((tool) => tool.name), enabled: true },
    ] } })));
    await screen.findByText('MCP 工具配置已保存。');
    fireEvent.click(screen.getByRole('button', { name: '取消全选 GitHub 工具' }));
    fireEvent.click(screen.getByRole('button', { name: '保存 MCP 配置' }));
    await waitFor(() => expect(api).toHaveBeenLastCalledWith('/api/agents/7/mcp-bindings', expect.objectContaining({ method: 'PUT', body: { items: [otherBinding] } })));
  });

  it('只读配置不能批量选择', async () => {
    api.mockImplementation(async (path) => ({ items: path === '/api/mcp/servers' ? [server] : [{ server_id: 1, selected_tools: [tools[0].name], enabled: true }] }));
    render(<McpBindingPanel {...props} canEdit={false} />);
    expect(await screen.findByRole('button', { name: '全选 GitHub 工具' })).toBeDisabled();
  });

  it('全选超过总上限时保留原有选择并提示原因', async () => {
    api.mockImplementation(async (path) => ({ items: path === '/api/mcp/servers' ? [server]
      : [{ ...otherBinding, selected_tools: Array.from({ length: 360 }, (_, index) => `other_${index}`) },
        { server_id: 1, selected_tools: [tools[0].name], enabled: true }] }));
    render(<McpBindingPanel {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: '全选 GitHub 工具' }));
    expect(screen.getByText(/最多绑定 20 个 MCP 服务、400 个工具/)).toBeInTheDocument();
    expect(screen.getByText('已选 1 / 49')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存 MCP 配置' })).toBeDisabled();
  });
});
