import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore } from './useAuthStore.js';

beforeEach(() => useAuthStore.getState().logout());
afterEach(() => vi.unstubAllGlobals());
const response = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });

describe('账号加载', () => {
  it('普通成员可完成账号与工作区初始化', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response({ user: { id: 2 } }))
      .mockResolvedValueOnce(response({ workspace: { id: 1, role: 'user' } })));
    useAuthStore.getState().setToken('member-token');
    await useAuthStore.getState().bootstrap();
    expect(useAuthStore.getState()).toMatchObject({ token: 'member-token', me: { id: 2 }, canManage: false });
  });

  it('无工作区访问权限保留登录，返回实际错误', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response({ user: { id: 2 } }))
      .mockResolvedValueOnce(response({ detail: 'Workspace access denied' }, 403)));
    useAuthStore.getState().setToken('member-token');
    await useAuthStore.getState().bootstrap();
    expect(useAuthStore.getState()).toMatchObject({ token: 'member-token', error: 'Workspace access denied' });
  });

  it('旧会话迟到的 401 不会注销刚登录的账号', async () => {
    let complete;
    vi.stubGlobal('fetch', vi.fn(() => new Promise((resolve) => { complete = resolve; })));
    useAuthStore.getState().setToken('old-token');
    const pending = useAuthStore.getState().bootstrap();
    useAuthStore.getState().setToken('new-token');
    complete(response({ detail: 'Invalid token' }, 401));
    await pending;
    expect(useAuthStore.getState()).toMatchObject({ token: 'new-token', error: '' });
  });

  it('当前会话失效会清除令牌', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ detail: 'Invalid token' }, 401)));
    useAuthStore.getState().setToken('expired-token');
    await useAuthStore.getState().bootstrap();
    expect(useAuthStore.getState()).toMatchObject({ token: '', error: '登录已失效，请重新登录。' });
  });
});
