export function mcpAuthStatus(server) {
  let state = server.auth_status;
  if (!state) {
    state = server.auth_type === 'none' ? 'not_required'
      : server.catalog?.checked_at && server.has_credential && server.catalog?.probe_status !== 'failed' ? 'verified'
        : server.auth_type === 'oauth' ? 'pending_authorization' : server.has_credential ? 'configured' : 'missing';
  }
  const labels = { not_required: '无需认证', verified: '认证已验证', configured: '认证已配置', authorized: '已授权，待检测',
    pending_authorization: '待授权', missing: '未配置认证', invalid: '认证配置失效' };
  return { label: labels[state] || '待验证认证', ready: state === 'verified', state };
}
