import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { TriggersHome } from './TriggersHome.jsx';

const apiMock = vi.hoisted(() => vi.fn());
vi.mock('../utils.js', () => ({
  api: (...args) => apiMock(...args),
  errorMessage: (value) => (value instanceof Error ? value.message : String(value ?? '')),
}));

const LIST = {
  items: [
    {
      id: 1, name: '订单提醒', target_type: 'agent', target_id: 7, kind: 'webhook',
      token: 'tk-1', webhook_url: 'http://127.0.0.1:8000/api/triggers/hook/tk-1',
      message_template: '订单 {{order}} 到了吗', enabled: true,
      last_fired_at: null, next_run_at: null,
    },
    {
      id: 2, name: '早报', target_type: 'workflow', target_id: 3, kind: 'schedule',
      token: 'tk-2', webhook_url: '', cron: '0 9 * * *', timezone: 'Asia/Shanghai',
      message_template: '', enabled: false, last_fired_at: null, next_run_at: '2026-10-24T01:00:00',
    },
  ],
  targets: { agents: [{ id: 7, name: '客服智能体' }], workflows: [{ id: 3, name: '早报流程' }] },
};

describe('触发器页', () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockResolvedValue(LIST);
  });

  it('列表展示调用地址、目标名与 cron，目标 id 显示为名字而不是编号', async () => {
    render(<TriggersHome token="t" />);
    await waitFor(() => expect(screen.getByText('订单提醒')).toBeInTheDocument());
    expect(screen.getByText('http://127.0.0.1:8000/api/triggers/hook/tk-1')).toBeInTheDocument();
    expect(screen.getByText(/客服智能体/)).toBeInTheDocument();
    expect(screen.getByText(/早报流程/)).toBeInTheDocument();
    expect(screen.getByText('0 9 * * *')).toBeInTheDocument();
  });

  it('启用开关走 PATCH，而不是只改本地状态', async () => {
    render(<TriggersHome token="t" />);
    await waitFor(() => expect(screen.getByText('订单提醒')).toBeInTheDocument());
    apiMock.mockClear();
    fireEvent.click(screen.getAllByLabelText('启用')[1]);
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
      '/api/triggers/2',
      expect.objectContaining({ method: 'PATCH', body: { enabled: true } }),
    ));
  });

  it('webhook 型不显示 cron，定时型不显示调用地址', async () => {
    render(<TriggersHome token="t" />);
    await waitFor(() => expect(screen.getByText('订单提醒')).toBeInTheDocument());
    expect(screen.queryByText('Webhook')).not.toBeNull();
    expect(screen.getByText('定时')).toBeInTheDocument();
  });
});
