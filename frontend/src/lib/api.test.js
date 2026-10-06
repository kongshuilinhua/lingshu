import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, isAuthError } from './api.js';

afterEach(() => vi.unstubAllGlobals());

function rejectRequest(status, detail) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: false, status, json: async () => ({ detail }),
  })));
}

describe('认证与权限错误', () => {
  it('登录密码错误显示原因，不触发会话过期', async () => {
    rejectRequest(401, 'Invalid email or password');
    const expired = vi.fn();
    window.addEventListener('lingshu-auth-expired', expired);
    try {
      await expect(api('/api/auth/login', { method: 'POST', body: {} }))
        .rejects.toMatchObject({ status: 401, message: '邮箱或密码不正确，请重新输入。' });
      expect(expired).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('lingshu-auth-expired', expired);
    }
  });

  it('普通成员请求管理员接口的 403 不会注销登录', async () => {
    rejectRequest(403, 'Admin role required');
    const expired = vi.fn();
    window.addEventListener('lingshu-auth-expired', expired);
    try {
      await expect(api('/api/admin/agent-reviews', { token: 'member-token' }))
        .rejects.toMatchObject({ status: 403 });
      expect(expired).not.toHaveBeenCalled();
      expect(isAuthError({ status: 403 })).toBe(false);
    } finally {
      window.removeEventListener('lingshu-auth-expired', expired);
    }
  });

  it('已认证请求的 401 带上所属令牌，供页面识别过期会话', async () => {
    rejectRequest(401, 'Invalid token');
    const expired = vi.fn();
    window.addEventListener('lingshu-auth-expired', expired);
    try {
      await expect(api('/api/auth/me', { token: 'expired-token' })).rejects.toMatchObject({ status: 401 });
      expect(expired).toHaveBeenCalledOnce();
      expect(expired.mock.calls[0][0].detail.token).toBe('expired-token');
    } finally {
      window.removeEventListener('lingshu-auth-expired', expired);
    }
  });
});
