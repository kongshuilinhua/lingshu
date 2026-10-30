import React, { useEffect, useState } from 'react';
import { api, errorMessage } from '../lib/api.js';

export function WorkspaceSwitcher({ token, workspace, onSwitch, confirmNavigation }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    api('/api/workspaces', { token }).then((data) => {
      if (!cancelled) setItems(data.items);
    }).catch((err) => { if (!cancelled) setError(errorMessage(err)); });
    return () => { cancelled = true; };
  }, [token]);

  async function switchTo(id) {
    if (busy || String(workspace?.id) === id) return;
    setBusy(true);
    setError('');
    try {
      const result = await api(`/api/workspaces/${id}/switch`, { method: 'POST', token });
      await onSwitch(result.access_token);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return <div className="workspace-switcher">
    <label>当前工作区
      <select aria-label="切换工作区" value={workspace?.id ?? ''} disabled={busy || items.length < 2} onChange={(event) => {
        const id = event.target.value;
        confirmNavigation(() => switchTo(id));
      }}>
        {items.length ? items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>) : <option value={workspace?.id ?? ''}>{workspace?.name || '加载中…'}</option>}
      </select>
    </label>
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
