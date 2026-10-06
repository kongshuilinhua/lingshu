/**
 * API 客户端基础模块。
 * 从 utils.js 提取 —— API 调用、错误处理、认证令牌管理。
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
const AUTH_TOKEN_KEY = 'lingshu_token';
const LEGACY_AUTH_TOKEN_KEY = 'sweeper_token';

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function isAuthError(error) {
  return error?.status === 401;
}

function notifyAuthExpired(token) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('lingshu-auth-expired', { detail: { token } }));
  }
}

function initialAuthToken() {
  const token = localStorage.getItem(AUTH_TOKEN_KEY) || localStorage.getItem(LEGACY_AUTH_TOKEN_KEY) || '';
  if (token && !localStorage.getItem(AUTH_TOKEN_KEY)) {
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.removeItem(LEGACY_AUTH_TOKEN_KEY);
  }
  return token;
}

function errorMessage(value) {
  if (!value) return '操作失败，请稍后重试。';
  if (value instanceof Error) return value.message;
  if (typeof value === 'string') {
    if (value === 'Invalid email or password') return '邮箱或密码不正确，请重新输入。';
    return value;
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        const path = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== 'body').join('.') : '';
        return [path, item?.msg].filter(Boolean).join('：');
      })
      .filter(Boolean)
      .join('；') || '请求参数不正确。';
  }
  if (typeof value === 'object') {
    return value.detail ? errorMessage(value.detail) : JSON.stringify(value);
  }
  return String(value);
}

async function api(path, { method = 'GET', token, body } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new ApiError(errorMessage(data.detail || data.message || `HTTP ${response.status}`), response.status, data);
    if (token && isAuthError(error)) notifyAuthExpired(token);
    throw error;
  }
  return data;
}

export {
  API_BASE,
  AUTH_TOKEN_KEY,
  LEGACY_AUTH_TOKEN_KEY,
  ApiError,
  isAuthError,
  notifyAuthExpired,
  initialAuthToken,
  errorMessage,
  api,
};
