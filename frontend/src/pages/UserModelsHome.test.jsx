import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { UserModelsHome } from './UserModelsHome.jsx';

const success = (model) => ({ ok: true, model, latency_ms: 10, checks: { chat: { required: true, ok: true } } });

function fill(values) {
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
}

function openPicker() {
  if (screen.getByLabelText('chat_model').getAttribute('aria-expanded') !== 'true') {
    fireEvent.click(screen.getByLabelText('chat_model'));
  }
}

function addModel(name) {
  openPicker();
  fill({ 筛选模型: name });
  fireEvent.keyDown(screen.getByLabelText('筛选模型'), { key: 'Enter' });
}

function option(name) {
  // 已注册的选项可访问名会带「已注册」后缀
  return within(screen.getByRole('listbox', { name: '可选模型' })).getByRole('checkbox', { name: new RegExp(`^${name}( 已注册)?$`) });
}

function registration(overrides = {}) {
  const props = { canManage: false, adminModels: [], userModels: [], setProfileError: vi.fn(),
    createUserModelConfig: vi.fn(async () => ({ id: 1, supports_image: false })),
    updateUserModelConfig: vi.fn(), deleteUserModelConfig: vi.fn(), requestDeleteConfirm: vi.fn(),
    probeUserModels: vi.fn(async () => ({ ok: true, models: [] })),
    testUserModelDraft: vi.fn(async (payload) => success(payload.chat_model)), testUserModelConfig: vi.fn(), ...overrides };
  render(<UserModelsHome {...props} />);
  fireEvent.click(screen.getByRole('button', { name: '新增模型' }));
  fill({ chat_base_url: 'https://api.deepseek.com', chat_api_key: 'test-only-key' });
  return props;
}

async function probe(props) {
  fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
  await waitFor(() => expect(props.probeUserModels).toHaveBeenCalled());
  await screen.findByRole('listbox', { name: '可选模型' });
}

describe('我的模型', () => {
  it('新增表单只有协议、地址、Key、模型四项且初值为空，没有厂家预设', () => {
    render(<UserModelsHome canManage={false} adminModels={[]} userModels={[]} setProfileError={vi.fn()}
      probeUserModels={vi.fn()} createUserModelConfig={vi.fn()} updateUserModelConfig={vi.fn()}
      deleteUserModelConfig={vi.fn()} testUserModelDraft={vi.fn()} testUserModelConfig={vi.fn()} requestDeleteConfirm={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '新增模型' }));
    expect(screen.getByLabelText('连接协议')).toHaveValue('openai-compatible');
    expect(screen.getByLabelText('chat_base_url')).toHaveValue('');
    expect(screen.getByLabelText('chat_model')).toHaveTextContent('选择模型（可多选）');
    expect(screen.queryByLabelText('显示名称')).toBeNull();
    expect(screen.queryByText('自定义兼容网关')).toBeNull();
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled();
  });

  it('拉取后下拉列出全部模型，勾选一个后其余选项仍在', async () => {
    const props = registration({ probeUserModels: vi.fn(async () => ({ ok: true, models: ['deepseek-chat', 'deepseek-reasoner'] })) });
    await probe(props);
    expect(props.probeUserModels).toHaveBeenCalledWith({ provider: 'openai-compatible',
      base_url: 'https://api.deepseek.com', api_key: 'test-only-key' });
    expect(screen.getByText(/已拉到 2 个模型/)).toBeInTheDocument();
    fireEvent.click(option('deepseek-chat'));
    expect(option('deepseek-chat')).toBeChecked();
    expect(option('deepseek-reasoner')).not.toBeChecked();
    expect(screen.getByLabelText('chat_model')).toHaveTextContent('已选 1 个模型');
  });

  it('筛选只影响候选显示，全选筛选结果后清空筛选可看到全部', async () => {
    const props = registration({ probeUserModels: vi.fn(async () => ({ ok: true, models: ['deepseek-chat', 'deepseek-reasoner', 'qwen-plus'] })) });
    await probe(props);
    fill({ 筛选模型: 'deepseek' });
    expect(screen.queryByRole('checkbox', { name: 'qwen-plus' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '全选筛选结果' }));
    fill({ 筛选模型: '' });
    expect(option('deepseek-chat')).toBeChecked();
    expect(option('deepseek-reasoner')).toBeChecked();
    expect(option('qwen-plus')).not.toBeChecked();
  });

  it('同一连接已注册的模型标记为已注册且不可勾选', async () => {
    const props = registration({
      userModels: [{ id: 9, provider: 'openai-compatible', base_url: 'https://api.deepseek.com/', chat_model: 'deepseek-chat', enabled: true }],
      probeUserModels: vi.fn(async () => ({ ok: true, models: ['deepseek-chat', 'deepseek-reasoner'] })),
    });
    await probe(props);
    expect(option('deepseek-chat')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    expect(option('deepseek-chat')).not.toBeChecked();
    expect(option('deepseek-reasoner')).toBeChecked();
  });

  it('拉取失败时在表单内显示原因，内网地址提示部署开关', async () => {
    registration({ probeUserModels: vi.fn(async () => { throw new Error('Public HTTPS base_url required (private or self-hosted gateways require USER_MODEL_ALLOW_PRIVATE_NETWORK=true)'); }) });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    expect(await screen.findByText(/USER_MODEL_ALLOW_PRIVATE_NETWORK=true 后重启/)).toBeInTheDocument();
  });

  it('多选后逐个测连通，只保存通过的模型；默认只给第一个，能力按模型名预填', async () => {
    const testUserModelDraft = vi.fn(async (payload) => {
      if (payload.chat_model === 'broken') return { ok: false, model: 'broken', message: '认证失败' };
      return success(payload.chat_model);
    });
    const props = registration({ testUserModelDraft });
    addModel('deepseek-chat');
    addModel('deepseek-reasoner');
    addModel('broken');
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存 2 个模型' })).toBeEnabled());
    expect(testUserModelDraft).toHaveBeenCalledTimes(3);
    expect(testUserModelDraft).toHaveBeenCalledWith(expect.objectContaining({ chat_model: 'broken' }));
    expect(testUserModelDraft.mock.calls[0][0]).not.toHaveProperty('detect_image');
    expect(screen.getByText('认证失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '保存 2 个模型' }));
    await waitFor(() => expect(props.createUserModelConfig).toHaveBeenCalledTimes(2));
    expect(props.createUserModelConfig).toHaveBeenNthCalledWith(1, expect.objectContaining({
      display_name: 'deepseek-chat', chat_model: 'deepseek-chat', api_key: 'test-only-key',
      base_url: 'https://api.deepseek.com', is_default: true, supports_image: false, reasoning_type: 'prompt',
    }));
    expect(props.createUserModelConfig).toHaveBeenNthCalledWith(2, expect.objectContaining({
      chat_model: 'deepseek-reasoner', is_default: false, supports_image: false, supports_reasoning: true, reasoning_type: 'native',
    }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '新增模型' })).not.toBeInTheDocument());
    expect(screen.getByText(/已保存 2 个模型，1 个测试未通过的未保存/)).toBeInTheDocument();
  });

  it('全部测试失败时不能保存', async () => {
    const props = registration({ testUserModelDraft: vi.fn(async () => ({ ok: false, message: '认证失败' })) });
    addModel('deepseek-chat');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await screen.findByText('认证失败');
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    expect(props.createUserModelConfig).not.toHaveBeenCalled();
  });

  it('测试后新增勾选的模型需要重新测试才能保存', async () => {
    registration();
    addModel('deepseek-chat');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存私有模型' })).toBeEnabled());
    addModel('deepseek-reasoner');
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
  });

  it('更换连接后丢弃迟到的测试结果与旧模型候选', async () => {
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    const probeUserModels = vi.fn().mockResolvedValueOnce({ ok: true, models: ['old-model'] });
    registration({ testUserModelDraft: vi.fn(() => pending), probeUserModels });
    await probe({ probeUserModels });
    fireEvent.click(option('old-model'));
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    fill({ chat_base_url: 'https://example.com/v1' });
    await act(async () => finish(success('old-model')));
    expect(screen.getByRole('button', { name: '保存私有模型' })).toBeDisabled();
    openPicker();
    expect(screen.queryByText('已拉到 1 个模型', { exact: false })).toBeNull();
  });

  it('切换协议不跳转也不清空已填字段与已选模型', () => {
    registration();
    addModel('deepseek-chat');
    fireEvent.change(screen.getByLabelText('连接协议'), { target: { value: 'anthropic' } });
    expect(screen.getByLabelText('连接协议')).toHaveValue('anthropic');
    expect(screen.getByLabelText('chat_base_url')).toHaveValue('https://api.deepseek.com');
    expect(screen.getByLabelText('chat_api_key')).toHaveValue('test-only-key');
    expect(screen.getByLabelText('chat_model')).toHaveTextContent('已选 1 个模型');
  });

  it('手选 Anthropic 协议后拉取、测试与保存都带该协议', async () => {
    const props = registration();
    fireEvent.change(screen.getByLabelText('连接协议'), { target: { value: 'anthropic' } });
    fill({ chat_base_url: 'https://console.tars-ai.com/v1' });
    fireEvent.click(screen.getByRole('button', { name: '拉取模型' }));
    await waitFor(() => expect(props.probeUserModels).toHaveBeenCalledWith({ provider: 'anthropic',
      base_url: 'https://console.tars-ai.com/v1', api_key: 'test-only-key' }));
    addModel('claude-test');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存私有模型' })).toBeEnabled());
    expect(props.testUserModelDraft).toHaveBeenCalledWith(expect.objectContaining({ provider: 'anthropic' }));
    fireEvent.click(screen.getByRole('button', { name: '保存私有模型' }));
    await waitFor(() => expect(props.createUserModelConfig).toHaveBeenCalledWith(expect.objectContaining({
      provider: 'anthropic', chat_model: 'claude-test', base_url: 'https://console.tars-ai.com/v1',
    })));
  });

  it('能力按模型名预填，用户修改后保存的是修改值，且不需要重新测试', async () => {
    const props = registration();
    addModel('Qwen/Qwen3-VL-8B-Instruct');
    addModel('my-private-model');
    expect(screen.getByLabelText('Qwen/Qwen3-VL-8B-Instruct 支持图片')).toBeChecked();
    expect(screen.getByLabelText('Qwen/Qwen3-VL-8B-Instruct 深度思考')).toHaveValue('native');
    expect(screen.getByLabelText('my-private-model 支持图片')).not.toBeChecked();
    expect(screen.getByLabelText('my-private-model 深度思考')).toHaveValue('prompt');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存 2 个模型' })).toBeEnabled());
    fireEvent.click(screen.getByLabelText('my-private-model 支持图片'));
    fireEvent.change(screen.getByLabelText('my-private-model 深度思考'), { target: { value: 'none' } });
    expect(screen.getByRole('button', { name: '保存 2 个模型' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '保存 2 个模型' }));
    await waitFor(() => expect(props.createUserModelConfig).toHaveBeenCalledTimes(2));
    expect(props.createUserModelConfig).toHaveBeenNthCalledWith(1, expect.objectContaining({
      chat_model: 'Qwen/Qwen3-VL-8B-Instruct', supports_image: true, reasoning_type: 'native' }));
    expect(props.createUserModelConfig).toHaveBeenNthCalledWith(2, expect.objectContaining({
      chat_model: 'my-private-model', supports_image: true, supports_reasoning: false, reasoning_type: 'none' }));
  });

  it('填 Anthropic 官方地址自动切到 Anthropic 协议', () => {
    registration();
    fill({ chat_base_url: 'https://api.anthropic.com/v1' });
    expect(screen.getByLabelText('连接协议')).toHaveValue('anthropic');
  });

  it('接口不存在类错误提示可能是协议不匹配', async () => {
    registration({ testUserModelDraft: vi.fn(async () => ({ ok: false, model: 'm', message: 'Model call failed: HTTP 404. Not Found' })) });
    addModel('deepseek-chat');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByText(/可能是协议不匹配，试试切换为 Anthropic/)).toBeInTheDocument();
  });
});
