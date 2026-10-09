import React from 'react';
import { useUnsavedForm } from './UnsavedChanges.jsx';
import { X, Plus, Save } from 'lucide-react';

export function KnowledgeBaseDialog({
  form,
  onCancel,
  onChange,
  onSubmit,
  saving,
  title = "新建知识库",
  description = "创建后可以上传 TXT、MD、CSV、PDF、DOCX 文件，或写入粘贴文本用于 RAG 检索。",
  submitText = "创建知识库",
  savingText = "创建中...",
  isEdit = false
}) {
  const guard = useUnsavedForm({ value: form, label: title, busy: saving,
    onSave: async () => form.name.trim() ? onSubmit({ preventDefault() {} }) : false });
  const close = () => guard.confirmLeave(onCancel);
  return (
    <div className="profile-dialog-backdrop">
      <section className="resource-form-dialog knowledge-base-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <button className="profile-dialog-close" type="button" title="关闭" aria-label="关闭知识库表单" onClick={close} disabled={saving}>
          <X size={16} />
        </button>
        <header className="model-dialog-heading">
          <h3>{title}</h3>
          <p>{description}</p>
        </header>
        <form className="dialog-form" onSubmit={onSubmit}>
          <label className="field-stack">
            <span>名称</span>
            <input value={form.name} onChange={(event) => onChange({ ...form, name: event.target.value })} placeholder="例如：产品资料库" autoFocus />
          </label>
          <label className="field-stack">
            <span>描述</span>
            <textarea value={form.description} onChange={(event) => onChange({ ...form, description: event.target.value })} placeholder="说明知识库内容、适用智能体或维护范围" />
          </label>
          <footer className="dialog-actions">
            <button type="button" onClick={close} disabled={saving}>取消</button>
            <button className="primary-model-action" type="submit" disabled={saving || !form.name.trim()}>
              {isEdit ? <Save size={15} /> : <Plus size={15} />}
              {saving ? savingText : submitText}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
