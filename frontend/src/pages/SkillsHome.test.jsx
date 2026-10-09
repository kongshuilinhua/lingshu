import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SkillsHome } from './SkillsHome.jsx';
import { SkillBindingPanel } from '../components/SkillBindingPanel.jsx';
import { api } from '../lib/api.js';
import { UnsavedChangesProvider } from '../components/UnsavedChanges.jsx';

vi.mock('../lib/api.js', () => ({ api: vi.fn() }));
beforeEach(() => { api.mockReset(); });
const skill = { id: 1, name: '周报', slug: 'weekly-report', description: '整理工作记录', category: '办公', enabled: true,
  current_version_id: 12, versions: [{ id: 12, version: 2 }, { id: 11, version: 1 }], can_edit: true };

describe('Skill 管理及绑定', () => {
  it('切换版本先确认未保存内容，取消保留输入，放弃后加载新版本并清理修改状态', async () => {
    api.mockImplementation(async (path) => path === '/api/skills' ? { items: [skill] }
      : { version: { source: path.endsWith('/12') ? '第二版说明' : '第一版说明', scripts_approved: false, files: [] } });
    render(<UnsavedChangesProvider><SkillsHome token="test" canManage mode="mine" /></UnsavedChangesProvider>);
    await screen.findByText('周报');
    fireEvent.click(screen.getByRole('button', { name: '编辑', exact: true }));
    await screen.findByDisplayValue('第二版说明');
    fireEvent.change(screen.getByLabelText('SKILL.md'), { target: { value: '未保存的新内容' } });
    fireEvent.change(screen.getByLabelText('查看版本'), { target: { value: '11' } });
    expect(screen.getByRole('dialog', { name: '有未保存的修改' })).toBeInTheDocument();
    expect(api).not.toHaveBeenCalledWith('/api/skills/1/versions/11', expect.anything());
    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    expect(screen.getByLabelText('SKILL.md')).toHaveValue('未保存的新内容');
    expect(screen.getByLabelText('查看版本')).toHaveValue('12');
    fireEvent.change(screen.getByLabelText('查看版本'), { target: { value: '11' } });
    fireEvent.click(screen.getByRole('button', { name: '放弃修改' }));
    await screen.findByDisplayValue('第一版说明');
    fireEvent.click(screen.getByRole('button', { name: '关闭 Skill 配置' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
  it('放弃修改后版本加载失败时仍还原原内容，避免把未保存内容标成已保存', async () => {
    api.mockImplementation(async (path) => {
      if (path === '/api/skills') return { items: [skill] };
      if (path.endsWith('/11')) throw new Error('版本加载失败');
      return { version: { source: '原版本说明', scripts_approved: false, files: [] } };
    });
    render(<UnsavedChangesProvider><SkillsHome token="test" canManage mode="mine" /></UnsavedChangesProvider>);
    await screen.findByText('周报');
    fireEvent.click(screen.getByRole('button', { name: '编辑', exact: true }));
    await screen.findByDisplayValue('原版本说明');
    fireEvent.change(screen.getByLabelText('SKILL.md'), { target: { value: '未保存的修改' } });
    fireEvent.change(screen.getByLabelText('查看版本'), { target: { value: '11' } });
    fireEvent.click(screen.getByRole('button', { name: '放弃修改' }));
    expect((await screen.findAllByText('版本加载失败')).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('SKILL.md')).toHaveValue('原版本说明');
    expect(screen.getByLabelText('查看版本')).toHaveValue('12');
  });
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
