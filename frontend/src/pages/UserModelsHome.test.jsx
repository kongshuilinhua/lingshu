import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UserModelsHome } from './UserModelsHome.jsx';

const success = { ok: true, checks: { chat: { required: true, ok: true } } };

function registration(overrides = {}) {
  const props = { canManage: false, adminModels: [], userModels: [], setProfileError: vi.fn(),
    createUserModelConfig: vi.fn(async () => ({ id: 1, supports_image: false })),
    updateUserModelConfig: vi.fn(), deleteUserModelConfig: vi.fn(), requestDeleteConfirm: vi.fn(),
    probeUserModels: vi.fn(async () => ({ ok: true, models: [] })),
    testUserModelDraft: vi.fn(async () => success), testUserModelConfig: vi.fn(), ...overrides };
  render(<UserModelsHome {...props} />);
  fireEvent.click(screen.getByRole('button', { name: '新增模型' }));
  fireEvent.change(screen.getByLabelText('chat_api_key'), { target: { value: 'test-only-key' } });
  return props;
}

describe('我的模型', () => {
  it('新增模型可以调用列表探测并显示返回的模型候选', async () => {
    const probeUserModels = vi.fn(async () => ({ ok: true, models: ['probe-model'] }));
    const setProfileError = vi.fn();
    render(<UserModelsHome canManage={false} adminModels={[]} userModels={[]}
      probeUserModels={probeUserModels} setProfileError={setProfileError}
      createUserModelConfig={vi.fn()} updateUserModelConfig={vi.fn()} deleteUserModelConfig={vi.fn()}
      testUserModelDraft={vi.fn()} testUserModelConfig={vi.fn()} requestDeleteConfirm={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '新增模型' }));
    const baseUrl = screen.getByLabelText('chat_base_url').value;
    fireEvent.change(screen.getByLabelText('chat_api_key'), { target: { value: 'test-only-key' } });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await waitFor(() => expect(probeUserModels).toHaveBeenCalledWith({ provider: 'openai-compatible', base_url: baseUrl, api_key: 'test-only-key' }));
    await waitFor(() => expect(document.querySelector('datalist option[value="probe-model"]')).not.toBeNull());
    expect(screen.getByRole('dialog', { name: '新增模型' })).toBeInTheDocument();
  });

  it('当前配置测试通过后才允许注册，并提交清洗后的字段', async () => {
    const props = registration();
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '测试当前配置' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存私有模型' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存私有模型' }));
    await waitFor(() => expect(props.createUserModelConfig).toHaveBeenCalledWith(expect.objectContaining({
      display_name: 'Qwen Plus', chat_model: 'qwen-plus', api_key: 'test-only-key',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    })));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '新增模型' })).not.toBeInTheDocument());
  });

  it('连接测试失败时不能保存模型', async () => {
    const props = registration({ testUserModelDraft: vi.fn(async () => ({ ok: false,
      message: '认证失败', checks: { chat: { required: true, ok: false } } })) });
    fireEvent.click(screen.getByRole('button', { name: '测试当前配置' }));
    await screen.findByText('认证失败');
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    expect(props.createUserModelConfig).not.toHaveBeenCalled();
  });

  it('编辑配置后不接受旧请求返回的测试成功结果', async () => {
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    registration({ testUserModelDraft: vi.fn(() => pending) });
    fireEvent.click(screen.getByRole('button', { name: '测试当前配置' }));
    fireEvent.change(screen.getByLabelText('chat_model'), { target: { value: 'different-model' } });
    await act(async () => finish(success));
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '测试当前配置' })).toBeEnabled();
  });

  it('更换连接时清除旧模型候选，并忽略迟到的探测响应', async () => {
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    const probe = vi.fn().mockResolvedValueOnce({ ok: true, models: ['old-model'] }).mockReturnValueOnce(pending);
    registration({ probeUserModels: probe });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await waitFor(() => expect(document.querySelector('datalist option[value="old-model"]')).not.toBeNull());
    fireEvent.change(screen.getByLabelText('chat_base_url'), { target: { value: 'https://example.com/v1' } });
    expect(document.querySelector('datalist option[value="old-model"]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    fireEvent.change(screen.getByLabelText('chat_api_key'), { target: { value: 'different-test-key' } });
    await act(async () => finish({ ok: true, models: ['late-model'] }));
    expect(document.querySelector('datalist option[value="late-model"]')).toBeNull();
    expect(screen.getByRole('button', { name: '拉取模型' })).toBeEnabled();
  });

  it('Ollama 预设要求填写受支持的网关，不提供会被后端拒绝的本机地址', () => {
    registration();
    fireEvent.click(screen.getByRole('button', { name: /^Ollama 网关/ }));
    expect(screen.getByLabelText('chat_base_url')).toHaveValue('');
    expect(screen.getByText(/当前不支持直接连接本机 HTTP 地址/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '测试当前配置' })).toBeDisabled();
  });

  it('Anthropic 预设的协议用于模型列表、连接测试和注册', async () => {
    const props = registration();
    fireEvent.click(screen.getByRole('button', { name: /^Anthropic \/ Claude/ }));
    expect(screen.getByLabelText('连接协议')).toHaveValue('anthropic');
    expect(screen.getByLabelText('chat_base_url')).toHaveValue('https://api.anthropic.com/v1');
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await waitFor(() => expect(props.probeUserModels).toHaveBeenCalledWith({ provider: 'anthropic',
      base_url: 'https://api.anthropic.com/v1', api_key: 'test-only-key' }));
    fireEvent.change(screen.getByLabelText('chat_model'), { target: { value: 'claude-test' } });
    fireEvent.click(screen.getByRole('button', { name: '测试当前配置' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存私有模型' })).toBeEnabled());
    expect(props.testUserModelDraft).toHaveBeenCalledWith(expect.objectContaining({ provider: 'anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: '保存私有模型' }));
    await waitFor(() => expect(props.createUserModelConfig).toHaveBeenCalledWith(expect.objectContaining({
      provider: 'anthropic', chat_model: 'claude-test', base_url: 'https://api.anthropic.com/v1',
    })));
  });
});
