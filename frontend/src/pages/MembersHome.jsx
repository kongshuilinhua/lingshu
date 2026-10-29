import React, { useCallback, useEffect, useState } from 'react';
import { Copy, Mail, Trash2, UserPlus } from 'lucide-react';
import { UserAvatar } from '../components/AgentAvatar.jsx';
import { api, errorMessage, roleLabel } from '../utils.js';

/**
 * 成员：看谁在这个工作区里，并管理邀请与角色。
 *
 * 只有管理员能进这一页（后端 require_manager 把关），所以这里不做额外的权限判断。
 * 但「不能动自己」和「owner 不可改」两条仍要在界面上先拦一次——
 * 点下去弹 400 比按钮直接灰掉更难理解。
 */
export function MembersHome({ members, token, me, loadMembers, notify }) {
  const [invites, setInvites] = useState([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('user');
  const [inviteError, setInviteError] = useState('');
  const [inviteBusy, setInviteBusy] = useState(false);
  const [freshInvite, setFreshInvite] = useState(null);

  const reloadInvites = useCallback(async () => {
    try {
      const data = await api('/api/workspaces/invites', { token });
      setInvites(data.items || []);
    } catch {
      // 邀请开关关着时这里必然 404。整个邀请面板随之隐藏，不需要额外提示。
      setInvites([]);
    }
  }, [token]);

  useEffect(() => { reloadInvites(); }, [reloadInvites]);

  async function submitInvite(event) {
    event.preventDefault();
    setInviteError('');
    setInviteBusy(true);
    try {
      const data = await api('/api/workspaces/invites', {
        token,
        method: 'POST',
        body: { email: inviteEmail.trim(), role: inviteRole },
      });
      setFreshInvite(data.invite);
      setInviteEmail('');
      await reloadInvites();
    } catch (err) {
      setInviteError(errorMessage(err));
    } finally {
      setInviteBusy(false);
    }
  }

  async function copyLink(inviteToken) {
    const url = `${window.location.origin}${window.location.pathname}?invite=${inviteToken}`;
    try {
      await navigator.clipboard.writeText(url);
      notify?.('邀请链接已复制。');
    } catch {
      // 非 HTTPS 或用户拒绝授权时 clipboard 不可用，退回让用户手动复制
      window.prompt('复制下面的邀请链接发给对方：', url);
    }
  }

  async function changeRole(member, role) {
    try {
      await api(`/api/workspaces/members/${member.id}`, { token, method: 'PATCH', body: { role } });
      notify?.('角色已更新。');
      await loadMembers?.();
    } catch (err) {
      notify?.(errorMessage(err));
    }
  }

  async function removeOne(member) {
    const label = member.user?.name || member.user?.email;
    if (!window.confirm(`把 ${label} 移出工作区？他会立刻失去访问权限，但创建的内容仍留在工作区。`)) return;
    try {
      await api(`/api/workspaces/members/${member.id}`, { token, method: 'DELETE' });
      notify?.('成员已移除。');
      await loadMembers?.();
    } catch (err) {
      notify?.(errorMessage(err));
    }
  }

  const pendingInvites = invites.filter((invite) => !invite.accepted_at);

  return (
    <div className="content-page">
      <header className="page-heading">
        <div>
          <h1>成员</h1>
          <p>邀请同事加入这个工作区，给他们普通用户或管理员角色。</p>
        </div>
      </header>

      <section className="member-invite">
        <h2><UserPlus size={18} /> 邀请成员</h2>
        <form onSubmit={submitInvite} className="member-invite-form">
          <input
            type="email"
            required
            value={inviteEmail}
            onChange={(event) => setInviteEmail(event.target.value)}
            placeholder="同事的邮箱"
          />
          <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}>
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
          <button className="primary" type="submit" disabled={inviteBusy || !inviteEmail.trim()}>
            <Mail size={16} /> {inviteBusy ? '生成中…' : '生成邀请链接'}
          </button>
        </form>
        {inviteError && <p className="error">{inviteError}</p>}
        {freshInvite && (
          <div className="member-invite-result">
            <p>把链接发给 <strong>{freshInvite.email}</strong>，对方打开后即可注册并加入：</p>
            <div className="member-invite-link">
              <code>{`${window.location.origin}${window.location.pathname}?invite=${freshInvite.token}`}</code>
              <button type="button" onClick={() => copyLink(freshInvite.token)}><Copy size={15} /> 复制</button>
            </div>
            <small>链接仅对上面这个邮箱有效，7 天过期，用过一次即失效。重新邀请会作废旧链接。</small>
          </div>
        )}
      </section>

      <div className="member-list">
        {members.map((member) => {
          const isSelf = member.user?.id != null && member.user.id === me?.id;
          const isOwner = member.role === 'owner';
          const locked = isSelf || isOwner;
          return (
            <article className="member-card" key={member.id}>
              <UserAvatar user={member.user} className="account-avatar" />
              <div>
                <h3>
                  {member.user?.name || member.user?.email}
                  {isSelf && <span className="member-self">（你）</span>}
                </h3>
                <p>{member.user?.email}</p>
              </div>
              <div className="member-actions">
                {isOwner || isSelf ? (
                  <span className="status-pill">{roleLabel(member.role)}</span>
                ) : (
                  <>
                    <select
                      value={member.role}
                      onChange={(event) => changeRole(member, event.target.value)}
                      disabled={locked}
                    >
                      <option value="user">普通用户</option>
                      <option value="admin">管理员</option>
                    </select>
                    <button
                      type="button"
                      className="danger-action"
                      onClick={() => removeOne(member)}
                      disabled={locked}
                      title={locked ? '不能移除自己' : '移出工作区'}
                    >
                      <Trash2 size={16} />
                    </button>
                  </>
                )}
              </div>
            </article>
          );
        })}
        {members.length === 0 && <p className="empty-state">暂无成员。</p>}
      </div>

      {pendingInvites.length > 0 && (
        <section className="member-invite">
          <h2>待接受的邀请</h2>
          <div className="member-pending">
            {pendingInvites.map((invite) => (
              <div key={invite.id} className="member-pending-row">
                <span>{invite.email}</span>
                <span className="status-pill">{roleLabel(invite.role)}</span>
              </div>
            ))}
          </div>
          <small>出于安全考虑，链接本身只在创建时显示一次。忘了就重新邀请，旧链接会作废。</small>
        </section>
      )}
    </div>
  );
}
