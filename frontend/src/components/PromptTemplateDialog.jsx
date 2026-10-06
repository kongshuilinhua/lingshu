import React from 'react';
import { X, Check } from 'lucide-react';

export function PromptTemplateDialog({ editingTemplate, form, onCancel, onChange, onSubmit, saving }) {
  const title = editingTemplate ? '编辑我的模板' : '新建我的模板';
  const submitLabel = editingTemplate ? '保存修改' : '保存模板';

  return (
    <div className="profile-dialog-backdrop">
      <section className="resource-form-dialog prompt-template-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭模板表单" onClick={onCancel} disabled={saving}>
          <X size={16} />
        </button>
        <header className="model-dialog-heading">
          <h3>{title}</h3>
          <p>模板会保存到“我的资源”，并可在 Builder 中选择使用。</p>
        </header>
        <form className="prompt-template-form dialog-form" onSubmit={onSubmit}>
          <div className="resource-form-grid two">
            <label className="field-stack">
              <span>标题</span>
              <input value={form.title} onChange={(event) => onChange({ ...form, title: event.target.value })} placeholder="例如：售前客服模板" autoFocus />
            </label>
            <label className="field-stack">
              <span>分类</span>
              <input value={form.category} onChange={(event) => onChange({ ...form, category: event.target.value })} placeholder="general" />
            </label>
          </div>
          <label className="field-stack">
            <span>描述</span>
            <input value={form.description} onChange={(event) => onChange({ ...form, description: event.target.value })} placeholder="适用场景" />
          </label>
          <label className="field-stack">
            <span>标签</span>
            <input value={form.tagsText} onChange={(event) => onChange({ ...form, tagsText: event.target.value })} placeholder="客服, 售前" />
          </label>
          <label className="field-stack">
            <span>模板正文</span>
            <textarea value={form.content} onChange={(event) => onChange({ ...form, content: event.target.value })} placeholder="写入提示词模板内容" />
          </label>
          <label className="inline-check">
            <input type="checkbox" checked={form.enabled} onChange={(event) => onChange({ ...form, enabled: event.target.checked })} />
            启用
          </label>
          <footer className="dialog-actions">
            <button type="button" onClick={onCancel} disabled={saving}>取消</button>
            <button className="primary-model-action" type="submit" disabled={saving || !form.title.trim() || !form.content.trim()}>
              <Check size={15} />{saving ? '保存中...' : submitLabel}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
