import React, { useState } from 'react';
import { useUnsavedForm } from './UnsavedChanges.jsx';
import { Check, X } from 'lucide-react';

export function SecretInputDialog({ label = '密钥', message, onCancel, onSubmit, placeholder = '只提交一次，不会回显', saving, submitLabel = '保存', title }) {
  const [value, setValue] = useState('');
  const guard = useUnsavedForm({ value, label: title || '密钥配置', busy: saving,
    onSave: () => value.trim() ? onSubmit(value) : false });
  const close = () => guard.confirmLeave(onCancel);

  function submit(event) {
    event.preventDefault();
    onSubmit(value);
  }

  return (
    <div className="profile-dialog-backdrop">
      <section className="resource-form-dialog secret-input-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭密钥表单" onClick={close} disabled={saving}>
          <X size={16} />
        </button>
        <header className="model-dialog-heading">
          <h3>{title}</h3>
          <p>{message}</p>
        </header>
        <form className="dialog-form" onSubmit={submit}>
          <label className="field-stack">
            <span>{label}</span>
            <input type="password" value={value} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} autoComplete="off" autoFocus />
          </label>
          <footer className="dialog-actions">
            <button type="button" onClick={close} disabled={saving}>取消</button>
            <button className="primary-model-action" type="submit" disabled={saving || !value.trim()}>
              <Check size={15} />{saving ? '保存中...' : submitLabel}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

