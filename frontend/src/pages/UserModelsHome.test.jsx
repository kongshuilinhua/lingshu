import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UserModelsHome } from './UserModelsHome.jsx';

const success = { ok: true, checks: { chat: { required: true, ok: true } } };

function fill(values) {
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
}

function registration(overrides = {}) {
  const props = { canManage: false, adminModels: [], userModels: [], setProfileError: vi.fn(),
    createUserModelConfig: vi.fn(async () => ({ id: 1, supports_image: false })),
    updateUserModelConfig: vi.fn(), deleteUserModelConfig: vi.fn(), requestDeleteConfirm: vi.fn(),
    probeUserModels: vi.fn(async () => ({ ok: true, models: [] })),
    testUserModelDraft: vi.fn(async () => success), testUserModelConfig: vi.fn(), ...overrides };
  render(<UserModelsHome {...props} />);
  fireEvent.click(screen.getByRole('button', { name: '新增模型' }));
  fill({ chat_base_url: 'https://api.deepseek.com', chat_api_key: 'test-only-key', chat_model: 'deepseek-chat' });
  return props;
}

describe('我的模型', () => {
  it('新增表单只有协议、地址、Key、模型四项且初值为空，没有厂家预设', () => {
    render(<UserModelsHome canManage={false} adminModels={[]} userModels={[]} setProfileError={vi.fn()}
      probeUserModels={vi.fn()} createUserModelConfig={vi.fn()} updateUserModelConfig={vi.fn()}
      deleteUserModelConfig={vi.fn()} testUserModelDraft={vi.fn()} testUserModelConfig={vi.fn()} requestDeleteConfirm={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '新增模型' }));
    expect(screen.getByLabelText('连接协议')).toHaveValue('openai-compatible');
    expect(screen.getByLabelText('chat_base_url')).toHaveValue('');
    expect(screen.getByLabelText('chat_model')).toHaveValue('');
    expect(screen.queryByLabelText('显示名称')).toBeNull();
    expect(screen.queryByText('自定义兼容网关')).toBeNull();
    expect(screen.getByRole('button', { name: '测试当前配置' })).toBeDisabled();
  });

  it('拉取成功后显示可见的模型列表与计数，点选即填入模型名', async () => {
    const props = registration({ probeUserModels: vi.fn(async () => ({ ok: true, models: ['deepseek-chat', 'deepseek-reasoner'] })) });
    fill({ chat_model: '' });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await waitFor(() => expect(props.probeUserModels).toHaveBeenCalledWith({ provider: 'openai-compatible',
      base_url: 'https://api.deepseek.com', api_key: 'test-only-key' }));
    expect(await screen.findByText(/已拉到 2 个模型/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'deepseek-reasoner' }));
    expect(screen.getByLabelText('chat_model')).toHaveValue('deepseek-reasoner');
  });

  it('输入模型名会筛选拉取到的列表', async () => {
    registration({ probeUserModels: vi.fn(async () => ({ ok: true, models: ['deepseek-chat', 'deepseek-reasoner', 'qwen-plus'] })) });
    fill({ chat_model: '' });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await screen.findByRole('button', { name: 'qwen-plus' });
    fill({ chat_model: 'reason' });
    expect(screen.getByRole('button', { name: 'deepseek-reasoner' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'qwen-plus' })).toBeNull();
  });

  it('拉取失败时在表单内显示原因，内网地址提示部署开关', async () => {
    registration({ probeUserModels: vi.fn(async () => { throw new Error('Public HTTPS base_url required (private or self-hosted gateways require USER_MODEL_ALLOW_PRIVATE_NETWORK=true)'); }) });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    expect(await screen.findByText(/USER_MODEL_ALLOW_PRIVATE_NETWORK=true 后重启/)).toBeInTheDocument();
  });

  it('测试通过后才允许保存，显示名称缺省取模型名，推理能力取自探测结果', async () => {
    const props = registration({ testUserModelDraft: vi.fn(async () => ({ ...success,
      detected_capabilities: { supports_text: true, supports_reasoning: true, reasoning_type: 'native' } })) });
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '测试当前配置' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存私有模型' })).toBeEnabled());
    expect(props.testUserModelDraft).toHaveBeenCalledWith(expect.objectContaining({ detect_reasoning: true }));
    fireEvent.click(screen.getByRole('button', { name: '保存私有模型' }));
    await waitFor(() => expect(props.createUserModelConfig).toHaveBeenCalledWith(expect.objectContaining({
      display_name: 'deepseek-chat', chat_model: 'deepseek-chat', api_key: 'test-only-key',
      base_url: 'https://api.deepseek.com', supports_reasoning: true, reasoning_type: 'native',
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
    fill({ chat_model: 'different-model' });
    await act(async () => finish(success));
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '测试当前配置' })).toBeEnabled();
  });

  it('更换连接时清除旧模型候选，并忽略迟到的探测响应', async () => {
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    const probe = vi.fn().mockResolvedValueOnce({ ok: true, models: ['old-model'] }).mockReturnValueOnce(pending);
    registration({ probeUserModels: probe });
    fill({ chat_model: '' });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await screen.findByRole('button', { name: 'old-model' });
    fill({ chat_base_url: 'https://example.com/v1' });
    expect(screen.queryByRole('button', { name: 'old-model' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    fill({ chat_api_key: 'different-test-key' });
    await act(async () => finish({ ok: true, models: ['late-model'] }));
    expect(screen.queryByRole('button', { name: 'late-model' })).toBeNull();
    expect(screen.getByRole('button', { name: '拉取模型' })).toBeEnabled();
  });

  it('切换协议不跳转也不清空已填字段', () => {
    registration();
    fireEvent.change(screen.getByLabelText('连接协议'), { target: { value: 'anthropic' } });
    expect(screen.getByLabelText('连接协议')).toHaveValue('anthropic');
    expect(screen.getByLabelText('chat_base_url')).toHaveValue('https://api.deepseek.com');
    expect(screen.getByLabelText('chat_api_key')).toHaveValue('test-only-key');
    expect(screen.getByLabelText('chat_model')).toHaveValue('deepseek-chat');
  });

  it('手选 Anthropic 协议后拉取、测试与保存都带该协议', async () => {
    const props = registration();
    fireEvent.change(screen.getByLabelText('连接协议'), { target: { value: 'anthropic' } });
    fill({ chat_base_url: 'https://console.tars-ai.com/v1', chat_model: 'claude-test' });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await waitFor(() => expect(props.probeUserModels).toHaveBeenCalledWith({ provider: 'anthropic',
      base_url: 'https://console.tars-ai.com/v1', api_key: 'test-only-key' }));
    fireEvent.click(screen.getByRole('button', { name: '测试当前配置' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存私有模型' })).toBeEnabled());
    expect(props.testUserModelDraft).toHaveBeenCalledWith(expect.objectContaining({ provider: 'anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: '保存私有模型' }));
    await waitFor(() => expect(props.createUserModelConfig).toHaveBeenCalledWith(expect.objectContaining({
      provider: 'anthropic', chat_model: 'claude-test', base_url: 'https://console.tars-ai.com/v1',
      supports_reasoning: false, reasoning_type: 'none',
    })));
  });
});
