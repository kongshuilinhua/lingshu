import React, { useEffect, useState } from 'react';
import { Database, FileText, Plus, Search, Wand2 } from 'lucide-react';
import { PromptTemplateDialog } from '../components/PromptTemplateDialog.jsx';
import {
  defaultPromptTemplateForm,
  errorMessage,
  filterPromptTemplates,
  filterResourceItems,
  formFromPromptTemplate,
  insertPromptIntoAgent,
  promptTemplateFormPayload,
} from '../utils.js';

export function ResourceLibraryHome({
  embedded = false,
  activeAgentId,
  agentForm,
  copyBuiltinPromptTemplate,
  createPromptTemplate,
  deletePromptTemplate,
  knowledgeBases,
  openBuilder,
  promptTemplates,
  requestDeleteConfirm,
  resourceTab,
  setActiveNav,
  setResourceTab,
  setAgentForm,
  setProfileError,
  setView,
  tools,
  updatePromptTemplate,
}) {
  const tab = resourceTab;
  const setTab = setResourceTab;
  const [query, setQuery] = useState('');
  const [selectedTemplate, setSelectedTemplate] = useState(promptTemplates[0] || null);
  const [editingTemplate, setEditingTemplate] = useState(null);
  const [form, setForm] = useState(() => defaultPromptTemplateForm());
  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (!selectedTemplate && promptTemplates.length) {
      setSelectedTemplate(promptTemplates[0]);
    } else if (selectedTemplate && !promptTemplates.some((item) => item.id === selectedTemplate.id)) {
      setSelectedTemplate(promptTemplates[0] || null);
    }
  }, [promptTemplates, selectedTemplate?.id]);

  const filteredTemplates = filterPromptTemplates(promptTemplates, query);
  const filteredTools = filterResourceItems(tools, query, (tool) => `${tool.label || ''} ${tool.name || ''} ${tool.description || ''}`);
  const filteredKnowledge = filterResourceItems(knowledgeBases, query, (kb) => `${kb.name || ''} ${kb.description || ''}`);
  const showPrompts = tab === 'all' || tab === 'prompts';
  const showTools = tab === 'all' || tab === 'tools';
  const showKnowledge = tab === 'all' || tab === 'knowledge';

  function insertTemplate(template) {
    if (!template?.content) return;
    insertPromptIntoAgent(setAgentForm, template.content);
    setSelectedTemplate(template);
    setNotice('模板已插入当前智能体 Prompt。');
  }

  function openCreate(template = null) {
    setEditingTemplate(null);
    setForm(template ? formFromPromptTemplate(template, { title: `${template.title} 副本` }) : defaultPromptTemplateForm());
    setNotice('');
    setFormOpen(true);
  }

  function openEdit(template) {
    setEditingTemplate(template);
    setForm(formFromPromptTemplate(template));
    setSelectedTemplate(template);
    setNotice('');
    setFormOpen(true);
  }

  function closeTemplateForm() {
    if (saving) return;
    setFormOpen(false);
    setEditingTemplate(null);
    setForm(defaultPromptTemplateForm());
  }

  async function saveTemplate(event) {
    event.preventDefault();
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      const payload = promptTemplateFormPayload(form);
      const saved = editingTemplate?.db_id
        ? await updatePromptTemplate(editingTemplate.db_id, payload)
        : await createPromptTemplate(payload);
      setEditingTemplate(null);
      setForm(defaultPromptTemplateForm());
      setFormOpen(false);
      setSelectedTemplate(saved);
      setNotice('提示词模板已保存。');
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function copyBuiltin(template) {
    if (!template?.id) return;
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      const copied = await copyBuiltinPromptTemplate({
        builtin_id: template.id.replace('builtin:', ''),
        title: `${template.title} 副本`,
      });
      setSelectedTemplate(copied);
      setNotice('已复制为我的模板。');
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function removeTemplate(template) {
    if (!template?.db_id) return;
    const confirmed = await requestDeleteConfirm({
      title: '删除提示词模板',
      message: `删除「${template.title}」？`,
      detail: '删除后，我的资源和 Builder 模板区都不再显示该模板。',
      confirmLabel: '删除模板',
    });
    if (!confirmed) return;
    setSaving(true);
    setNotice('');
    setProfileError('');
    try {
      await deletePromptTemplate(template.db_id);
      setSelectedTemplate(null);
      setNotice('模板已删除。');
    } catch (err) {
      setProfileError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={embedded ? 'market-resource-panel' : 'content-page resource-page'}>
      <header className="page-heading resource-heading">
        {!embedded && <div>
          <h1>资源库</h1>
          <p>管理工作区的插件、知识库和提示词，并把需要的资源交给智能体。</p>
        </div>}
        <div className="resource-actions">
          <label className="resource-search">
            <Search size={16} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索资源" />
          </label>
          {showPrompts && <button className="primary" type="button" onClick={() => openCreate()}><Plus size={15} />新建提示词</button>}
        </div>
      </header>

      {!embedded && <div className="resource-tabs">
        {[
          ['all', '全部'],
          ['tools', '插件'],
          ['knowledge', '知识库'],
          ['prompts', '提示词'],
        ].map(([key, label]) => (
          <button key={key} type="button" className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>}

      <div className="resource-layout">
        <section className="resource-list-panel">
          {showPrompts && (
            <ResourceSection
              title="提示词"
              count={filteredTemplates.length}
              emptyText="暂无提示词模板"
            >
              {filteredTemplates.map((template) => (
                <ResourceRow
                  key={template.id}
                  icon={<FileText size={17} />}
                  title={template.title}
                  desc={template.description || template.content}
                  type={template.source === 'builtin' ? '预置提示词' : '我的提示词'}
                  meta={template.category || 'general'}
                  active={selectedTemplate?.id === template.id}
                  onClick={() => setSelectedTemplate(template)}
                  actions={
                    <>
                      <button type="button" onClick={(event) => { event.stopPropagation(); setSelectedTemplate(template); }}>预览</button>
                      <button type="button" onClick={(event) => { event.stopPropagation(); insertTemplate(template); }}>插入</button>
                      {template.source === 'builtin' && <button type="button" disabled={saving} onClick={(event) => { event.stopPropagation(); copyBuiltin(template); }}>复制</button>}
                      {template.editable && <button type="button" disabled={saving} onClick={(event) => { event.stopPropagation(); openEdit(template); }}>编辑</button>}
                      {template.editable && <button type="button" disabled={saving} onClick={(event) => { event.stopPropagation(); removeTemplate(template); }}>删除</button>}
                    </>
                  }
                />
              ))}
            </ResourceSection>
          )}

          {showTools && (
            <ResourceSection title="插件" count={filteredTools.length} emptyText="暂无插件">
              {filteredTools.map((tool) => (
                <ResourceRow
                  key={`tool-${tool.id}`}
                  icon={<Wand2 size={17} />}
                  title={tool.label || tool.name}
                  desc={tool.description || tool.name}
                  type="插件"
                  meta={`${toolType(tool)} · ${tool.enabled === false ? '停用' : '启用'}`}
                  actions={<button type="button" onClick={() => setActiveNav('tools')}>管理</button>}
                />
              ))}
            </ResourceSection>
          )}

          {showKnowledge && (
            <ResourceSection title="知识库" count={filteredKnowledge.length} emptyText="暂无知识库">
              {filteredKnowledge.map((kb) => (
                <ResourceRow
                  key={`kb-${kb.id}`}
                  icon={<Database size={17} />}
                  title={kb.name}
                  desc={kb.description || `${kb.document_count || 0} 个文档`}
                  type="知识库"
                  meta={`${kb.document_count || 0} 文档`}
                  actions={<button type="button" onClick={() => setActiveNav('knowledge')}>管理</button>}
                />
              ))}
            </ResourceSection>
          )}
        </section>

        {showPrompts && <aside className="resource-detail-panel">
          <PromptTemplatePreview
            activeAgentId={activeAgentId}
            template={selectedTemplate}
            onInsert={insertTemplate}
            onCopy={copyBuiltin}
            onEdit={openEdit}
            onDelete={removeTemplate}
            saving={saving}
          />
          <section className="resource-side-actions">
            <button type="button" onClick={() => { setView('builder'); openBuilder(); }}>打开 Builder</button>
            <button className="primary-model-action" type="button" onClick={() => openCreate()}><Plus size={15} />新建模板</button>
          </section>
        </aside>}
      </div>
      {notice && <p className="model-row-warning floating-notice">{notice}</p>}
      {formOpen && (
        <PromptTemplateDialog
          editingTemplate={editingTemplate}
          form={form}
          onCancel={closeTemplateForm}
          onChange={setForm}
          onSubmit={saveTemplate}
          saving={saving}
        />
      )}
    </div>
  );
}

// ── 原有 utils.js 导入（逐步迁移到 lib/ 后删除）──

function ResourceSection({ children, count, emptyText, title }) {
  return (
    <div className="resource-section">
      <div className="resource-section-title">
        <strong>{title}</strong>
        <span>{count}</span>
      </div>
      <div className="resource-rows">
        {children}
        {count === 0 && <p className="muted">{emptyText}</p>}
      </div>
    </div>
  );
}

function ResourceRow({ active = false, actions, desc, icon, meta, onClick, title, type }) {
  return (
    <article className={`resource-row ${active ? 'active' : ''}`} onClick={onClick || undefined}>
      <span className="resource-icon">{icon}</span>
      <div>
        <strong>{title}</strong>
        <small>{desc}</small>
      </div>
      <span className="resource-type">{type}</span>
      <span className="resource-meta">{meta}</span>
      <div className="resource-row-actions">{actions}</div>
    </article>
  );
}

function PromptTemplatePreview({ activeAgentId, onCopy, onDelete, onEdit, onInsert, saving, template }) {
  if (!template) {
    return (
      <section className="prompt-preview-panel">
        <h3>模板预览</h3>
        <p className="muted">选择一个提示词模板后在这里预览和插入。</p>
      </section>
    );
  }
  return (
    <section className="prompt-preview-panel">
      <div className="prompt-preview-head">
        <div>
          <span>{template.source === 'builtin' ? '平台预置' : '我的模板'}</span>
          <h3>{template.title}</h3>
          <p>{template.description || '暂无描述'}</p>
        </div>
        <span className="soft-pill">{template.category || 'general'}</span>
      </div>
      <pre>{template.content}</pre>
      <div className="prompt-preview-actions">
        <button className="primary" type="button" disabled={!activeAgentId} onClick={() => onInsert(template)}>插入到当前智能体</button>
        {template.source === 'builtin' && <button type="button" disabled={saving} onClick={() => onCopy(template)}>复制为我的模板</button>}
        {template.editable && <button type="button" disabled={saving} onClick={() => onEdit(template)}>编辑</button>}
        {template.editable && <button type="button" disabled={saving} onClick={() => onDelete(template)}>删除</button>}
      </div>
    </section>
  );
}


function toolType(tool) {
  return tool?.type || (tool?.name === 'builtin_search' ? 'builtin_search' : 'http');
}
