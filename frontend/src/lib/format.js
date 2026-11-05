/**
 * 格式化与标签工具函数。
 * 从 utils.js 提取 —— 角色标签、状态标签、日期格式化。
 */

function roleLabel(role) {
  return isAdminRole(role) ? '管理员' : '普通用户';
}

function avatarInitial(user) {
  const value = user?.name || user?.email || 'U';
  return value.trim().slice(0, 2).toUpperCase();
}

function isAdminRole(role) {
  return role === 'admin' || role === 'owner';
}

function statusLabel(status) {
  const labels = { draft: '草稿', published: '已发布' };
  return labels[status] || status || '草稿';
}

function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function capabilityCheckLabel(name) {
  return { chat: 'chat', image: 'image' }[name] || name;
}

export { roleLabel, avatarInitial, isAdminRole, statusLabel, formatDateTime, capabilityCheckLabel };
