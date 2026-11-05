import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkspaceSwitcher } from './WorkspaceSwitcher.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const reply = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });

it('仅选择成功后交付新令牌，失败保留当前工作区', async () => {
  const switched = vi.fn();
  let fail = true;
  let switchRequests = 0;
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (url.endsWith('/api/workspaces')) return reply({ items: [{ id: 1, name: '个人工作区' }, { id: 2, name: '团队' }] });
    if (url.endsWith('/2/switch')) {
      switchRequests++;
      return fail ? reply({ detail: '成员身份已失效' }, 404) : reply({ access_token: 'team' });
    }
    throw new Error(url);
  }));
  let allowNavigation = false;
  const confirmNavigation = (action) => { if (allowNavigation) return action(); };
  render(<WorkspaceSwitcher token="personal" workspace={{ id: 1, name: '个人工作区' }} onSwitch={switched} confirmNavigation={confirmNavigation} />);
  await screen.findByRole('option', { name: '团队' });
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '2' } });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(switched).not.toHaveBeenCalled();
  expect(switchRequests).toBe(0);
  allowNavigation = true;
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '2' } });
  await screen.findByText('成员身份已失效');
  expect(switched).not.toHaveBeenCalled();
  expect(screen.getByRole('combobox')).toHaveValue('1');
  fail = false;
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '2' } });
  await waitFor(() => expect(switched).toHaveBeenCalledWith('team'));
});
