import React, { useEffect, useRef, useState } from 'react';
import { LogIn, Sparkles } from 'lucide-react';
import { api, errorMessage } from '../lib/api.js';

export function AuthPage({ token = '', inviteToken = '', onAuthenticated, onCancelInvite, onLogout, initialError = '' }) {
  const [mode, setMode] = useState(() => localStorage.getItem('lingshu_last_email') ? 'login' : 'register');
  const [form, setForm] = useState({ email: '', name: '', password: '' });
  const [invite, setInvite] = useState(null);
  const [inviteError, setInviteError] = useState('');
  const [error, setError] = useState(initialError);
  const [session, setSession] = useState(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setInvite(null);
    setInviteError('');
    if (inviteToken) api(`/api/workspaces/invites/${encodeURIComponent(inviteToken)}`)
      .then((data) => {
        if (cancelled) return;
        setInvite(data);
        setForm((current) => ({ ...current, email: data.email }));
      })
      .catch((err) => {
        if (!cancelled) setInviteError(err.status === 410 ? '这条邀请链接已过期，请让管理员重新邀请。' : '这条邀请链接无效、已被使用或邀请功能已关闭。');
      });
    return () => { cancelled = true; };
  }, [inviteToken]);

  useEffect(() => {
    let cancelled = false;
    setSession(null);
    if (token && inviteToken) api('/api/auth/account', { token })
      .then((data) => { if (!cancelled) setSession({ token, email: data.user.email }); })
      .catch((err) => { if (!cancelled) setError(errorMessage(err)); });
    return () => { cancelled = true; };
  }, [token, inviteToken]);

  const mismatch = Boolean(session && invite && session.email.toLowerCase() !== invite.email.toLowerCase());
  const blocked = busy || (inviteToken && (!invite || inviteError || mismatch || (token && !session)));

  async function submit(event) {
    event.preventDefault();
    if (blocked || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      let current = session;
      if (!current) {
        const data = await api(`/api/auth/${mode}`, {
          method: 'POST',
          body: mode === 'register'
            ? { ...form, ...(inviteToken ? { invite_token: inviteToken } : {}) }
            : { email: form.email, password: form.password },
        });
        current = { token: data.access_token, email: data.user.email };
        if (mode === 'register' || !inviteToken) {
          onAuthenticated(current.token, current.email);
          return;
        }
        setSession(current);
      }
      const accepted = await api('/api/workspaces/invites/accept', {
        method: 'POST', token: current.token, body: { token: inviteToken },
      });
      onAuthenticated(accepted.access_token, current.email);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  function cancelInvite() {
    if (session && !token) onAuthenticated(session.token, session.email);
    else onCancelInvite();
  }

  return <main className="auth-shell"><section className="auth-panel">
    <div className="brand-mark"><Sparkles size={22} /> Lingshu Agent</div>
    <h1>{inviteToken ? '加入工作区' : mode === 'register' ? '创建你的智能体工作台' : '欢迎回来'}</h1>
    {!inviteToken && <p>注册后拥有自己的工作区，通过邀请与同事协作。</p>}
    {invite && <p className="invite-banner">邀请你加入工作区「{invite.workspace_name}」，请使用 <strong>{invite.email}</strong> {session ? '接受邀请。' : '登录或注册。'}</p>}
    {inviteToken && !invite && !inviteError && <p role="status">正在检查邀请…</p>}
    {inviteError && <p role="alert" className="error">{inviteError}</p>}
    {mismatch && <p role="alert" className="error">当前账号与邀请邮箱不一致，请切换账号。</p>}
    <form onSubmit={submit} className="auth-form">
      {!session && !token && <>
        <div className="auth-toggle">
          <button type="button" disabled={busy} className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>注册</button>
          <button type="button" disabled={busy} className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>登录</button>
        </div>
        <input type="email" required autoComplete="email" aria-label="邮箱" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="邮箱" readOnly={!!inviteToken} disabled={busy} />
        {mode === 'register' && <input required aria-label="姓名" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="姓名" disabled={busy} />}
        <input type="password" required minLength={8} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="密码，至少 8 位" disabled={busy} />
      </>}
      {session && <p>当前账号：{session.email}</p>}
      <button className="primary" type="submit" disabled={!!blocked}><LogIn size={18} />{busy ? '处理中…' : inviteToken ? session || token ? '接受邀请并加入' : mode === 'login' ? '登录并加入' : '注册并加入' : '进入工作台'}</button>
    </form>
    {error && <p role="alert" className="error">{error}</p>}
    {inviteToken && <div className="auth-toggle">
      {(session || token) && <button type="button" disabled={busy} onClick={() => { setSession(null); setMode('login'); setError(''); onLogout?.(); }}>切换账号</button>}
      <button type="button" disabled={busy} onClick={cancelInvite}>暂不加入</button>
    </div>}
  </section></main>;
}
