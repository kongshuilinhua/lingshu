/**
 * 覆盖 SSE 流式聊天的三个修复点：主动取消、畸形帧容错、被抢占时不误清 busy。
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { useChatStore } from './useChatStore.js';

/** 把若干 SSE 帧包装成 fetch 返回的可读流。控制发送节奏以便断言中途状态。 */
function streamResponse(frames, { holdOpen = false } = {}) {
  let releaseHold;
  const held = new Promise((resolve) => {
    releaseHold = resolve;
  });
  const encoder = new TextEncoder();
  let index = 0;
  const body = {
    getReader: () => ({
      read: async () => {
        if (index < frames.length) {
          const chunk = encoder.encode(frames[index]);
          index += 1;
          return { done: false, value: chunk };
        }
        // holdOpen 让流停在"已发完但未结束"的状态，模拟服务端仍连着。
        if (holdOpen) await held;
        return { done: true, value: undefined };
      },
    }),
  };
  return { response: { ok: true, body }, releaseHold: () => releaseHold() };
}

function sse(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const BASE_ARGS = { text: '你好', activeAgentId: 1, token: 't', sessionId: null };

describe('useChatStore 流式聊天', () => {
  beforeEach(() => {
    useChatStore.setState({ messages: [], busy: false, error: '', sources: [], toolDebugEvents: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('正常流把 token 累加进最后一条消息', async () => {
    const { response } = streamResponse([sse('token', { content: '世' }), sse('token', { content: '界' })]);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    await useChatStore.getState().sendMessage(BASE_ARGS);

    const messages = useChatStore.getState().messages;
    expect(messages.at(-1).content).toBe('世界');
    expect(messages.at(-1).pending).toBe(false);
    expect(useChatStore.getState().busy).toBe(false);
  });

  it('畸形数据帧被跳过，已收到的正文不被破坏', async () => {
    const { response } = streamResponse([
      sse('token', { content: '前' }),
      'event: token\ndata: {坏帧不是合法 JSON\n\n',
      sse('token', { content: '后' }),
    ]);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    await useChatStore.getState().sendMessage(BASE_ARGS);

    // 修复前：JSON.parse 抛出会冒泡到外层 catch，正文被替换成错误文本。
    expect(useChatStore.getState().messages.at(-1).content).toBe('前后');
    expect(useChatStore.getState().messages.at(-1).error).toBeUndefined();
  });

  it('stopStream 中断读循环，且不把中断记成错误', async () => {
    const { response, releaseHold } = streamResponse([sse('token', { content: '半' })], { holdOpen: true });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    const pending = useChatStore.getState().sendMessage(BASE_ARGS);
    await vi.waitFor(() => expect(useChatStore.getState().messages.at(-1).content).toBe('半'));

    useChatStore.getState().stopStream();
    releaseHold();
    await pending;

    expect(useChatStore.getState().error).toBe('');
    expect(useChatStore.getState().messages.at(-1).error).toBeUndefined();
    expect(useChatStore.getState().messages.at(-1).pending).toBe(false);
  });

  it('新流抢占旧流时，旧流收尾不清掉新流的 busy 态', async () => {
    const first = streamResponse([sse('token', { content: '旧' })], { holdOpen: true });
    const second = streamResponse([sse('token', { content: '新' })], { holdOpen: true });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce(first.response).mockResolvedValueOnce(second.response),
    );

    const firstRun = useChatStore.getState().sendMessage(BASE_ARGS);
    await vi.waitFor(() => expect(useChatStore.getState().messages.at(-1).content).toBe('旧'));

    // 第二次 sendMessage 内部会先 stopStream 掐掉第一条。
    const secondRun = useChatStore.getState().sendMessage({ ...BASE_ARGS, text: '再来' });
    first.releaseHold();
    await firstRun;

    // 修复前：旧流的 finally 无条件 set busy=false，会误关新流的加载态。
    expect(useChatStore.getState().busy).toBe(true);

    second.releaseHold();
    await secondRun;
    expect(useChatStore.getState().busy).toBe(false);
  });
});
