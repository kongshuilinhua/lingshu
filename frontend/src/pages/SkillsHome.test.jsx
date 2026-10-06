import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SkillsHome } from './SkillsHome.jsx';
import { SkillBindingPanel } from '../components/SkillBindingPanel.jsx';
import { api } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({ api: vi.fn() }));
beforeEach(() => { api.mockReset(); });
const skill = { id: 1, name: '周报', slug: 'weekly-report', description: '整理工作记录', category: '办公', enabled: true,
  current_version_id: 12, versions: [{ id: 12, version: 2 }, { id: 11, version: 1 }], can_edit: true };

describe('Skill 管理及绑定', () => {
  it('支持创建 SKILL.md，显示元数据按需加载说明', async () => {
    api.mockResolvedValue({ items: [] });
    render(<SkillsHome token="test" canManage mode="mine" />);
    fireEvent.click(screen.getByRole('button', { name: '创建 / 导入 Skill' }));
    fireEvent.change(screen.getByLabelText('显示名称'), { target: { value: '周报' } });
    fireEvent.change(screen.getByLabelText('SKILL.md'), { target: { value: '---\nname: weekly-report\ndescription: 周报\n---\n步骤' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Skill' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/api/skills', expect.objectContaining({ method: 'POST', body: expect.objectContaining({ name: '周报' }) })));
    expect(screen.getByText(/启动时只提供元数据/)).toBeInTheDocument();
  });
  it('绑定多个资源时固定选定的 Skill 版本', async () => {
    api.mockImplementation(async (path, options) => path === '/api/skills' ? { items: [skill] } : { items: options?.body?.items || [] });
    render(<SkillBindingPanel agentId={7} token="test" canEdit onOpenMarket={vi.fn()} />);
    await screen.findByText('周报');
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText('周报 版本'), { target: { value: '11' } });
    fireEvent.click(screen.getByRole('button', { name: '保存 Skill 配置' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/api/agents/7/skill-bindings', expect.objectContaining({
      method: 'PUT', body: { items: [{ skill_id: 1, version_id: 11, enabled: true }] },
    })));
  });

  it('发现中的 Skill 只展示说明与管理入口，启停编辑留在我的资源', async () => {
    api.mockResolvedValue({ items: [skill] });
    const onManage = vi.fn();
    render(<SkillsHome token="test" canManage mode="discover" onManage={onManage} />);
    await screen.findByText('周报');
    expect(screen.getByRole('button', { name: '查看说明' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '停用' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑', exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '管理 Skill' }));
    expect(onManage).toHaveBeenCalledOnce();
  });
});
