import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { McpResourceCatalog } from './McpResourceCatalog.jsx';
import { api } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({ api: vi.fn() }));

beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async (path) => path.endsWith('/connection-options')
    ? { oauth_redirect_url: 'http://127.0.0.1:8000/api/mcp/oauth/callback' } : { items: [] });
});

async function openConnection() {
  render(<McpResourceCatalog token="test" canManage mode="discover" />);
  await screen.findByText('还没有接入 MCP 服务');
  fireEvent.click(screen.getByRole('button', { name: '添加自定义连接' }));
}

describe('MCP 连接配置', () => {
  it('发现页没有模板仍显示已有服务，并且只查询本地资源接口', async () => {
    api.mockImplementation(async (path) => path === '/api/mcp/servers' ? { items: [
      { id: 1, name: 'github', transport: 'streamable_http', auth_type: 'bearer', catalog: { tools: [{ name: 'repo_search' }] } },
      { id: 2, name: '本地文件', transport: 'stdio', auth_type: 'none', catalog: {} },
    ] } : { items: [] });
    render(<McpResourceCatalog token="test" canManage mode="discover" />);
    await screen.findByText('github');
    expect(screen.getByText('本地文件')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索 MCP 服务'), { target: { value: 'repo_search' } });
    expect(screen.getByText('github')).toBeInTheDocument();
    expect(screen.queryByText('本地文件')).not.toBeInTheDocument();
    expect(api.mock.calls.map(([path]) => path).every((path) => [
      '/api/mcp/servers', '/api/mcp/stdio-templates', '/api/mcp/connection-options',
    ].includes(path))).toBe(true);
  });
  it('没有 stdio 模板时仍显示三种连接方式，并解释本地进程配置要求', async () => {
    await openConnection();
    expect(screen.getByRole('option', { name: 'Streamable HTTP（远程）' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'HTTP + SSE（兼容旧服务）' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('连接方式'), { target: { value: 'stdio' } });
    expect(screen.getByText(/当前部署未允许自定义启动程序/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登记服务' })).toBeDisabled();
  });

  it('可登记 SSE 服务并保留认证和地址', async () => {
    await openConnection();
    fireEvent.change(screen.getByLabelText('服务名称'), { target: { value: 'SSE service' } });
    fireEvent.change(screen.getByLabelText('连接方式'), { target: { value: 'sse' } });
    fireEvent.change(screen.getByLabelText('MCP 地址'), { target: { value: 'https://example.test/sse/' } });
    fireEvent.click(screen.getByRole('button', { name: '登记服务' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/api/mcp/servers', expect.objectContaining({
      method: 'POST', body: expect.objectContaining({ transport: 'sse', url: 'https://example.test/sse/' }),
    })));
  });

  it('无需部署模板即可从网页登记 stdio 命令、参数和环境变量', async () => {
    api.mockImplementation(async (path) => path.endsWith('/connection-options') ? { can_configure_stdio: true } : { items: [] });
    await openConnection();
    fireEvent.change(screen.getByLabelText('服务名称'), { target: { value: 'Local service' } });
    fireEvent.change(screen.getByLabelText('连接方式'), { target: { value: 'stdio' } });
    fireEvent.change(screen.getByLabelText('启动命令'), { target: { value: 'python' } });
    fireEvent.change(screen.getByLabelText('启动参数（JSON 数组）'), { target: { value: '["server.py"]' } });
    fireEvent.click(screen.getByRole('button', { name: '添加环境变量' }));
    fireEvent.change(screen.getByLabelText('环境变量名称 1'), { target: { value: 'TOKEN' } });
    fireEvent.change(screen.getByLabelText('环境变量值 1'), { target: { value: 'test-only' } });
    fireEvent.click(screen.getByRole('button', { name: '登记服务' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/api/mcp/servers', expect.objectContaining({ method: 'POST',
      body: expect.objectContaining({ command: 'python', args: ['server.py'], env: { TOKEN: 'test-only' } }) })));
  });

  it('GitHub OAuth 明确要求已注册应用并展示当前回调', async () => {
    await openConnection();
    fireEvent.change(screen.getByLabelText('MCP 地址'), { target: { value: 'https://api.githubcopilot.com/mcp/' } });
    fireEvent.change(screen.getByLabelText('认证方式'), { target: { value: 'oauth' } });
    expect(screen.getByLabelText('Client ID（已注册应用）')).toBeRequired();
    expect(screen.getByLabelText('Client Secret')).toBeRequired();
    expect(screen.getByLabelText('OAuth 回调地址')).toHaveValue('http://127.0.0.1:8000/api/mcp/oauth/callback');
    expect(screen.getByText(/GitHub 可使用 Bearer Token/)).toBeInTheDocument();
  });
});
