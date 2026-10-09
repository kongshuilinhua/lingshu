import React, { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UnsavedChangesProvider, useUnsavedForm, useUnsavedNavigation } from './UnsavedChanges.jsx';

function Editor({ save, left, label = '配置' }) {
  const [value, setValue] = useState('原内容');
  const guard = useUnsavedForm({ value, label, onSave: () => save(value), onDiscard: setValue });
  const navigate = useUnsavedNavigation();
  return <div>
    <input aria-label={label} value={value} onChange={(event) => setValue(event.target.value)} />
    <button onClick={() => guard.confirmLeave(left)}>关闭{label}</button>
    <button onClick={() => navigate(left)}>离开{label}</button>
  </div>;
}

function setup(save = vi.fn().mockResolvedValue(true)) {
  const left = vi.fn();
  render(<UnsavedChangesProvider><Editor save={save} left={left} /></UnsavedChangesProvider>);
  return { left, save };
}

describe('未保存修改保护', () => {
  it('未修改或者撤销到原内容时直接退出', async () => {
    const { left } = setup();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '新内容' } });
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '原内容' } });
    fireEvent.click(screen.getByText('关闭配置'));
    await waitFor(() => expect(left).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('继续编辑保留修改，放弃修改还原内容并退出', async () => {
    const { left, save } = setup();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '新内容' } });
    fireEvent.click(screen.getByText('关闭配置'));
    fireEvent.click(screen.getByText('继续编辑'));
    expect(screen.getByRole('textbox')).toHaveValue('新内容');
    expect(left).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('关闭配置'));
    fireEvent.click(screen.getByText('放弃修改'));
    await waitFor(() => expect(left).toHaveBeenCalledOnce());
    expect(screen.getByRole('textbox')).toHaveValue('原内容');
    expect(save).not.toHaveBeenCalled();
  });

  it('等待保存成功后才退出，保存时不能重复执行或放弃', async () => {
    let finish;
    const save = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    const { left } = setup(save);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '新内容' } });
    fireEvent.click(screen.getByText('离开配置'));
    fireEvent.click(screen.getByText('保存并退出'));
    expect(left).not.toHaveBeenCalled();
    expect(screen.getByText('放弃修改')).toBeDisabled();
    expect(screen.getByText('继续编辑')).toBeDisabled();
    await act(async () => finish(true));
    expect(save).toHaveBeenCalledOnce();
    expect(save).toHaveBeenCalledWith('新内容');
    expect(left).toHaveBeenCalledOnce();
  });

  it('保存失败不退出，保留输入，允许继续编辑或重试', async () => {
    const save = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const { left } = setup(save);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '新内容' } });
    fireEvent.click(screen.getByText('离开配置'));
    fireEvent.click(screen.getByText('保存并退出'));
    expect(await screen.findByRole('alert')).toHaveTextContent('保存未完成');
    expect(left).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toHaveValue('新内容');
    fireEvent.click(screen.getByText('保存并退出'));
    await waitFor(() => expect(left).toHaveBeenCalledOnce());
  });

  it('导航检查所有编辑区域，单个表单关闭只检查自己', async () => {
    const left = vi.fn();
    const firstSave = vi.fn().mockResolvedValue(true);
    const secondSave = vi.fn().mockResolvedValue(true);
    render(<UnsavedChangesProvider><Editor label="智能体" save={firstSave} left={left} /><Editor label="绑定" save={secondSave} left={left} /></UnsavedChangesProvider>);
    fireEvent.change(screen.getByLabelText('智能体'), { target: { value: '新配置' } });
    fireEvent.change(screen.getByLabelText('绑定'), { target: { value: '新绑定' } });
    fireEvent.click(screen.getByText('关闭绑定'));
    expect(screen.getByRole('dialog')).toHaveTextContent('绑定尚未保存');
    fireEvent.click(screen.getByText('继续编辑'));
    fireEvent.click(screen.getByText('离开智能体'));
    expect(screen.getByRole('dialog')).toHaveTextContent('智能体、绑定尚未保存');
    fireEvent.click(screen.getByText('保存并退出'));
    await waitFor(() => expect(left).toHaveBeenCalledOnce());
    expect(firstSave).toHaveBeenCalledWith('新配置');
    expect(secondSave).toHaveBeenCalledWith('新绑定');
  });

  it('刷新保护只在有修改时触发，Escape 等同继续编辑', () => {
    const { left } = setup();
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '新内容' } });
    const dirty = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByText('离开配置'));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(left).not.toHaveBeenCalled();
  });

  it('先保存子资源，再保存由子资源新增的智能体绑定，避免丢失新配置', async () => {
    const state = { agent: '原内容' };
    const saveAgent = vi.fn().mockResolvedValue(true);
    const left = vi.fn();
    function DependentEditors() {
      const [resource, setResource] = useState('');
      const navigate = useUnsavedNavigation();
      useUnsavedForm({ value: state.agent, getCurrentValue: () => state.agent, label: '智能体', saveOrder: 100,
        onSave: () => saveAgent(state.agent) });
      useUnsavedForm({ value: resource, label: '新资源',
        onSave: async () => { state.agent = '包含新资源的配置'; return true; } });
      return <><input aria-label="资源名称" value={resource} onChange={(event) => setResource(event.target.value)} />
        <button onClick={() => navigate(left)}>退出</button></>;
    }
    render(<UnsavedChangesProvider><DependentEditors /></UnsavedChangesProvider>);
    fireEvent.change(screen.getByLabelText('资源名称'), { target: { value: '新资源' } });
    fireEvent.click(screen.getByText('退出'));
    fireEvent.click(screen.getByText('保存并退出'));
    await waitFor(() => expect(left).toHaveBeenCalledOnce());
    expect(saveAgent).toHaveBeenCalledWith('包含新资源的配置');
  });
});
