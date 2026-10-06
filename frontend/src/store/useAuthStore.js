/**
 * 认证状态 Store — 管理 token / me / workspace。
 * 替代 main.jsx 中的 useState(token), useState(me), useState(workspace)。
 */
import { create } from 'zustand';
import { api } from '../lib/api.js';
import { initialAuthToken, AUTH_TOKEN_KEY, isAuthError } from '../lib/api.js';
import { isAdminRole } from '../lib/format.js';

export const useAuthStore = create((set, get) => ({
  token: initialAuthToken(),
  me: null,
  workspace: null,
  canManage: false,
  error: '',

  setToken: (token) => set({ token, me: null, workspace: null, canManage: false, error: '' }),
  setMe: (me) => set({ me }),

  logout: () => {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    set({
      token: '',
      me: null,
      workspace: null,
      canManage: false,
      error: '',
    });
  },

  bootstrap: async () => {
    const { token } = get();
    if (!token) return;
    try {
      const profile = await api('/api/auth/me', { token });
      if (get().token !== token) return;
      const ws = await api('/api/workspaces/current', { token });
      if (get().token !== token) return;
      set({
        me: profile.user,
        workspace: ws.workspace,
        canManage: isAdminRole(ws.workspace?.role),
        error: '',
      });
    } catch (err) {
      if (get().token !== token) return;
      if (isAuthError(err)) {
        get().logout();
        set({ error: '登录已失效，请重新登录。' });
      } else {
        set({ error: err.message || '加载用户信息失败' });
      }
    }
  },
}));
