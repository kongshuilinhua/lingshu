import React, { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MarketHome } from './MarketHome.jsx';

vi.mock('../lib/api.js', () => ({
  api: vi.fn(async (path) => path.includes('/stdio-templates') ? { items: [
    { id: 'research', name: '研究助手', description: '检索研究资料', env_keys: [] },
  ] } : { items: [
    { id: 1, name: '研究检索 MCP', description: '查找资料', category: '研究',
      transport: 'streamable_http', auth_type: 'none', enabled: true, is_listed: true,
      can_edit: true, catalog: { checked_at: 'now', tools: [{ name: 'search', description: '检索' }] } },
  ] }),
}));

function MarketPreview() {
  const [marketTab, setMarketTab] = useState('agents');
  const [marketScope, setMarketScope] = useState('discover');
  return <MarketHome agents={[]} copyMarketAgent={vi.fn()} token="test" canManage
    marketTab={marketTab} setMarketTab={setMarketTab} marketScope={marketScope} setMarketScope={setMarketScope}
    resourcesProps={{ activeAgentId: 1, agentForm: {}, promptTemplates: [], tools: [], knowledgeBases: [],
      openBuilder: vi.fn(), setView: vi.fn(), setActiveNav: vi.fn(), setAgentForm: vi.fn(),
      setProfileError: vi.fn(), requestDeleteConfirm: async () => false }} />;
}

describe('市场资源分类', () => {
  it('MCP 接入位于市场，模板可直接带入连接配置', async () => {
    render(<MarketPreview />);
    expect(screen.getByRole('heading', { name: '市场', exact: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Skill/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /^MCP 服务/ }));
    expect(await screen.findByText('研究助手')).toBeInTheDocument();
    expect(await screen.findByText('研究检索 MCP')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '接入服务' }));
    const dialog = screen.getByRole('dialog', { name: '登记 MCP 服务' });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByLabelText('服务名称')).toHaveValue('研究助手');
    expect(screen.getByLabelText('部署模板')).toHaveValue('research');
    fireEvent.click(screen.getByRole('button', { name: '登记服务' }));
    expect(await screen.findByText('研究检索 MCP')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '我的资源' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: '接入服务' })).not.toBeInTheDocument();
  });

  it('我的资源在同一入口中管理提示词，切回发现仍能复用智能体', () => {
    render(<MarketPreview />);
    fireEvent.click(screen.getByRole('button', { name: '我的资源' }));
    fireEvent.click(screen.getByRole('button', { name: /^提示词/ }));
    expect(screen.getByRole('button', { name: '新建提示词' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '资源库' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发现', exact: true }));
    expect(screen.getByRole('heading', { name: '智能体', exact: true })).toBeInTheDocument();
  });

  it('发现页浏览能力，管理连接跳转到我的资源后才出现管理操作', async () => {
    render(<MarketPreview />);
    fireEvent.click(screen.getByRole('button', { name: /^MCP 服务/ }));
    await screen.findByText('研究检索 MCP');
    expect(screen.queryByRole('button', { name: '检测连接' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '管理连接' }));
    expect(screen.getByRole('button', { name: '我的资源' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '检测连接' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑服务' })).toBeInTheDocument();
  });
});
