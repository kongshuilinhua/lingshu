import { describe, expect, it } from 'vitest';
import { mcpAuthStatus } from './mcpStatus.js';

describe('MCP 认证状态', () => {
  it('检测成功的 Bearer 服务显示认证已验证', () => {
    expect(mcpAuthStatus({ auth_type: 'bearer', has_credential: true, catalog: { checked_at: 'now' } })).toMatchObject({ label: '认证已验证', ready: true });
  });
  it('保存应用凭据不等于完成 OAuth 授权', () => {
    expect(mcpAuthStatus({ auth_type: 'oauth', has_credential: true, auth_status: 'pending_authorization' }).label).toBe('待授权');
    expect(mcpAuthStatus({ auth_type: 'oauth', auth_status: 'authorized' }).label).toBe('已授权，待检测');
  });
  it('最新检测失败不沿用旧成功标签', () => {
    expect(mcpAuthStatus({ auth_type: 'bearer', has_credential: true, catalog: { checked_at: 'old', probe_status: 'failed' } }))
      .toMatchObject({ label: '认证已配置', ready: false });
  });
});
