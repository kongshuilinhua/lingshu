import React, { useCallback, useEffect, useState } from 'react';
import { Clock, Plus, Radio, Trash2, Zap } from 'lucide-react';
import { api, errorMessage } from '../utils.js';

/**
 * 触发器：让智能体/工作流在没人说话时也能被唤起。
 *
 * 两种形态共用一套表：
 *   webhook  外部系统 POST 一个带 token 的地址，body 的字段能填进消息模板
 *   schedule 按 cron 定时唤起，由平台内调度线程扫描
 * 触发一次跑的就是普通对话，结果会出现在「会话」里，能直接看运行详情。
 */
export function TriggersHome({ token, canEdit = true }) {
  const [triggers, setTriggers] = useState([]);
  const [targets, setTargets] = useState({ agents: [], workflows: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState('');

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api('/api/triggers', { token });
      setTriggers(data.items || []);
      setTargets(data.targets || { agents: [], workflows: [] });
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  async function create(form) {
    try {
      await api('/api/triggers', { token, method: 'POST', body: form });
      setCreating(false);
      setNotice('触发器已创建。');
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function patch(id, body) {
    try {
      await api(`/api/triggers/${id}`, { token, method: 'PATCH', body });
      setNotice('');
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function remove(item) {
    if (!window.confirm(`删除触发器「${item.name}」？外部系统再调用它的地址会失效。`)) return;
    try {
      await api(`/api/triggers/${item.id}`, { token, method: 'DELETE' });
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function runNow(item) {
    setNotice(`正在执行「${item.name}」…`);
    try {
      const data = await api(`/api/triggers/${item.id}/run`, { token, method: 'POST' });
      setNotice(`执行完成：${data.answer || '（没有输出）'}`);
      await reload();
    } catch (err) {
      setNotice('');
      setError(errorMessage(err));
    }
  }

  async function copyUrl(item) {
    try {
      await navigator.clipboard.writeText(item.webhook_url);
      setNotice('调用地址已复制。');
    } catch {
      setNotice(item.webhook_url);
    }
  }

  return (
    <div className="content-page trigger-page">
      <header className="page-heading">
        <div>
          <h1>触发器</h1>
          <p>让智能体或工作流在没人说话的时候也能被唤起：外部系统调用，或按 cron 定时。</p>
        </div>
        <button className="primary" type="button" disabled={!canEdit || creating} onClick={() => setCreating(true)}>
          <Plus size={16} />新建触发器
        </button>
      </header>

      {error && <p className="workflow-editor-error">{error}</p>}
      {notice && <p className="workflow-editor-success">{notice}</p>}

      {creating && (
        <TriggerForm targets={targets} onCancel={() => setCreating(false)} onSubmit={create} />
      )}

      {loading && <p className="workflow-editor-empty">正在读取…</p>}
      {!loading && triggers.length === 0 && !creating && (
        <p className="workflow-editor-empty">还没有触发器。新建一个，外部系统就能唤起你的智能体。</p>
      )}

      <div className="trigger-list">
        {triggers.map((item) => (
          <article className={`trigger-card ${item.enabled ? '' : 'disabled'}`} key={item.id}>
            <header>
              <span className="trigger-kind">
                {item.kind === 'schedule' ? <Clock size={14} /> : <Radio size={14} />}
                {item.kind === 'schedule' ? '定时' : 'Webhook'}
              </span>
              <strong>{item.name}</strong>
              <small>
                {item.target_type === 'workflow' ? '工作流' : '智能体'} · {targetName(item, targets)}
              </small>
              <label className="trigger-toggle">
                <input
                  type="checkbox" checked={item.enabled} disabled={!canEdit}
                  onChange={(event) => patch(item.id, { enabled: event.target.checked })}
                />
                启用
              </label>
            </header>

            {item.kind === 'webhook' ? (
              <div className="trigger-hook">
                <code>{item.webhook_url}</code>
                <button type="button" onClick={() => copyUrl(item)}>复制</button>
              </div>
            ) : (
              <div className="trigger-hook">
                <code>{item.cron}</code>
                <small>{item.timezone} · 下次 {formatTime(item.next_run_at)}</small>
              </div>
            )}

            {item.message_template && <p className="trigger-template">消息模板：{item.message_template}</p>}

            <footer>
              <small>上次触发 {formatTime(item.last_fired_at)}</small>
              <button type="button" disabled={!canEdit} onClick={() => runNow(item)}><Zap size={13} />立即执行</button>
              <button type="button" className="danger-light" disabled={!canEdit} onClick={() => remove(item)}>
                <Trash2 size={13} />删除
              </button>
            </footer>
          </article>
        ))}
      </div>
    </div>
  );
}

function targetName(item, targets) {
  const pool = item.target_type === 'workflow' ? targets.workflows : targets.agents;
  return pool.find((entry) => entry.id === item.target_id)?.name || `#${item.target_id}`;
}

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function TriggerForm({ targets, onCancel, onSubmit }) {
  const [name, setName] = useState('订单提醒');
  const [targetType, setTargetType] = useState('agent');
  const [targetId, setTargetId] = useState('');
  const [kind, setKind] = useState('webhook');
  const [cron, setCron] = useState('0 9 * * *');
  const [messageTemplate, setMessageTemplate] = useState('');

  const pool = targetType === 'workflow' ? targets.workflows : targets.agents;
  const submitting = !name.trim() || !targetId;

  function submit() {
    if (submitting) return;
    onSubmit({
      name: name.trim(),
      target_type: targetType,
      target_id: Number(targetId),
      kind,
      cron: kind === 'schedule' ? cron : '',
      message_template: messageTemplate,
    });
  }

  return (
    <div className="trigger-form">
      <div className="trigger-form-grid">
        <label>
          <span>名称</span>
          <input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} />
        </label>
        <label>
          <span>目标类型</span>
          <select value={targetType} onChange={(event) => { setTargetType(event.target.value); setTargetId(''); }}>
            <option value="agent">智能体</option>
            <option value="workflow">工作流</option>
          </select>
        </label>
        <label>
          <span>执行哪个</span>
          <select value={targetId} onChange={(event) => setTargetId(event.target.value)}>
            <option value="">选择…</option>
            {pool.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>触发方式</span>
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="webhook">Webhook（外部调用）</option>
            <option value="schedule">定时（cron）</option>
          </select>
        </label>
        {kind === 'schedule' && (
          <label>
            <span>cron 表达式</span>
            <input value={cron} onChange={(event) => setCron(event.target.value)} placeholder="0 9 * * *" />
          </label>
        )}
      </div>
      <label className="trigger-form-wide">
        <span>消息模板（可用 {'{{字段}}'} 接住 webhook body）</span>
        <textarea
          rows={2} value={messageTemplate}
          placeholder="订单 {{order}} 到了吗"
          onChange={(event) => setMessageTemplate(event.target.value)}
        />
      </label>
      <div className="trigger-form-actions">
        <button type="button" onClick={onCancel}>取消</button>
        <button type="button" className="primary" disabled={submitting} onClick={submit}>创建</button>
      </div>
    </div>
  );
}
