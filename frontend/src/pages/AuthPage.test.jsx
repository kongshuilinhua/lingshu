import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AuthPage } from './AuthPage.jsx';

const info = { email: 'member@example.com', workspace_name: '产品团队', role: 'user' };
const reply = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });
beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('已有账号登录后接受邀请，只交付目标工作区令牌', async () => {
  const done = vi.fn();
  const requests = [];
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    requests.push([url, options]);
    if (url.endsWith('/invites/link')) return reply(info);
    if (url.endsWith('/login')) return reply({ access_token: 'old-workspace', user: { email: info.email } });
    if (url.endsWith('/invites/accept')) return reply({ accepted: true, access_token: 'target-workspace' });
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(<AuthPage inviteToken="link" onAuthenticated={done} />);
  await screen.findByText(/产品团队/);
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  fireEvent.change(screen.getByPlaceholderText('密码，至少 8 位'), { target: { value: 'password123' } });
  fireEvent.click(screen.getByRole('button', { name: '登录并加入' }));
  await waitFor(() => expect(done).toHaveBeenCalledWith('target-workspace', info.email));
  const accept = requests.find(([url]) => url.endsWith('/invites/accept'));
  expect(accept[1].headers.Authorization).toBe('Bearer old-workspace');
  expect(JSON.parse(accept[1].body)).toEqual({ token: 'link' });
});

it('邀请接受失败保留入口，重试不需要重新登录', async () => {
  const done = vi.fn();
  let attempts = 0;
  let logins = 0;
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (url.endsWith('/invites/link')) return reply(info);
    if (url.endsWith('/login')) { logins++; return reply({ access_token: 'old', user: { email: info.email } }); }
    if (url.endsWith('/invites/accept')) return ++attempts === 1 ? reply({ detail: '暂时无法加入，请重试' }, 503) : reply({ access_token: 'joined' });
    throw new Error(url);
  }));
  render(<AuthPage inviteToken="link" onAuthenticated={done} />);
  await screen.findByText(/产品团队/);
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  fireEvent.change(screen.getByPlaceholderText('密码，至少 8 位'), { target: { value: 'password123' } });
  fireEvent.click(screen.getByRole('button', { name: '登录并加入' }));
  await screen.findByText('暂时无法加入，请重试');
  expect(done).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '接受邀请并加入' }));
  await waitFor(() => expect(done).toHaveBeenCalledWith('joined', info.email));
  expect(logins).toBe(1);
});

it('已登录时先显示工作区与账号，确认后才加入', async () => {
  const done = vi.fn();
  let accepted = false;
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (url.endsWith('/invites/link')) return reply(info);
    if (url.endsWith('/auth/account')) return reply({ user: { email: info.email } });
    if (url.endsWith('/invites/accept')) { accepted = true; return reply({ access_token: 'joined' }); }
    throw new Error(url);
  }));
  render(<AuthPage token="session" inviteToken="link" onAuthenticated={done} />);
  const join = await screen.findByRole('button', { name: '接受邀请并加入' });
  await waitFor(() => expect(join).toBeEnabled());
  expect(accepted).toBe(false);
  fireEvent.click(join);
  await waitFor(() => expect(done).toHaveBeenCalledWith('joined', info.email));
});

it('账号不匹配时禁止接受，提供切换账号入口', async () => {
  const logout = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url) => url.endsWith('/auth/account')
    ? reply({ user: { email: 'other@example.com' } }) : reply(info)));
  render(<AuthPage token="other" inviteToken="link" onLogout={logout} />);
  await screen.findByText(/当前账号与邀请邮箱不一致/);
  expect(screen.getByRole('button', { name: '接受邀请并加入' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '切换账号' }));
  expect(logout).toHaveBeenCalled();
});

it('邀请失效时不会误注册或加入，允许返回普通注册', async () => {
  const cancel = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'Invite expired' }, 410)));
  render(<AuthPage inviteToken="expired" onCancelInvite={cancel} />);
  await screen.findByText(/邀请链接已过期/);
  expect(screen.getByRole('button', { name: '注册并加入' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '暂不加入' }));
  expect(cancel).toHaveBeenCalled();
});

it('新账号通过邀请注册后直接进入目标工作区，不重复消费邀请', async () => {
  const done = vi.fn();
  const requests = [];
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    requests.push([url, options]);
    if (url.endsWith('/invites/link')) return reply(info);
    if (url.endsWith('/register')) return reply({ access_token: 'joined', user: { email: info.email } });
    throw new Error(url);
  }));
  render(<AuthPage inviteToken="link" onAuthenticated={done} />);
  await screen.findByText(/产品团队/);
  fireEvent.change(screen.getByPlaceholderText('姓名'), { target: { value: 'Member' } });
  fireEvent.change(screen.getByPlaceholderText('密码，至少 8 位'), { target: { value: 'password123' } });
  fireEvent.click(screen.getByRole('button', { name: '注册并加入' }));
  await waitFor(() => expect(done).toHaveBeenCalledWith('joined', info.email));
  expect(JSON.parse(requests.find(([url]) => url.endsWith('/register'))[1].body).invite_token).toBe('link');
  expect(requests.some(([url]) => url.endsWith('/invites/accept'))).toBe(false);
});

it('普通注册不携带邀请 token', async () => {
  const done = vi.fn();
  let payload;
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
    payload = JSON.parse(options.body);
    return reply({ access_token: 'personal', user: { email: 'me@example.com' } });
  }));
  render(<AuthPage onAuthenticated={done} />);
  fireEvent.change(screen.getByPlaceholderText('邮箱'), { target: { value: 'me@example.com' } });
  fireEvent.change(screen.getByPlaceholderText('姓名'), { target: { value: 'Me' } });
  fireEvent.change(screen.getByPlaceholderText('密码，至少 8 位'), { target: { value: 'password123' } });
  fireEvent.click(screen.getByRole('button', { name: '进入工作台' }));
  await waitFor(() => expect(done).toHaveBeenCalledWith('personal', 'me@example.com'));
  expect(payload).not.toHaveProperty('invite_token');
});
