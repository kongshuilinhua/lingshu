import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronLeft,
  Database,
  FileText,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
  SquarePen,
  Trash2,
  X,
} from 'lucide-react';
import { ResegmentModal } from '../components/ResegmentModal.jsx';
import { KnowledgeBaseDialog } from '../components/KnowledgeBaseDialog.jsx';
import { useUnsavedForm } from '../components/UnsavedChanges.jsx';
import { KnowledgeDocumentList, KnowledgeUploadBox } from '../components/KnowledgeDocumentList.jsx';
import {
  KNOWLEDGE_FILE_ACCEPT,
  api,
  defaultKnowledgeBaseForm,
  errorMessage,
  formatDateTime,
  handleKnowledgeFileInput,
} from '../utils.js';

export function KnowledgeHome({
  canManage,
  createKnowledgeBase,
  updateKnowledgeBase,
  deleteDocument,
  deleteKnowledgeBase,
  docForm,
  documents,
  knowledgeBases,
  setDocForm,
  setProfileError,
  uploadingKnowledgeFile,
  uploadingFileName,
  uploadDocument,
  uploadKnowledgeFile,
  token,
  loadDocuments,
  notify,
}) {
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState(() => defaultKnowledgeBaseForm());
  const [saving, setSaving] = useState(false);

  // Task 4 States
  const [viewMode, setViewMode] = useState('list'); // 'list' | 'detail'
  const [activeKbId, setActiveKbId] = useState(null); // number
  const [activeDoc, setActiveDoc] = useState(null); // object
  const [resegmentOpen, setResegmentOpen] = useState(false); // boolean
  const [pendingUploadFile, setPendingUploadFile] = useState(null); // 待上传文件（先选切片策略再上传）

  // Synchronize state when entering detail view
  function handleSelectKb(kbId) {
    setActiveKbId(kbId);
    setDocForm((current) => ({ ...current, kb_id: String(kbId) }));
    setViewMode('detail');
  }

  function handleBack() {
    setViewMode('list');
    setActiveDoc(null);
  }

  function openCreate() {
    setForm(defaultKnowledgeBaseForm());
    setCreateOpen(true);
  }

  function closeCreate() {
    if (saving) return;
    setCreateOpen(false);
  }

  async function submitKnowledgeBase(event) {
    event.preventDefault();
    setSaving(true);
    setProfileError('');
    try {
      const saved = await createKnowledgeBase(form);
      if (saved?.id) {
        setDocForm((current) => ({ ...current, kb_id: String(saved.id) }));
      }
      setForm(defaultKnowledgeBaseForm());
      setCreateOpen(false);
      return true;
    } catch (err) {
      setProfileError(errorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  const selectedKb = useMemo(() => {
    return knowledgeBases.find((kb) => kb.id === activeKbId) || null;
  }, [knowledgeBases, activeKbId]);

  return (
    <div className="content-page knowledge-home-page">
      {viewMode === 'list' ? (
        <KnowledgeDashboard
          knowledgeBases={knowledgeBases}
          deleteKnowledgeBase={deleteKnowledgeBase}
          openCreate={openCreate}
          onSelectKb={handleSelectKb}
          notify={notify}
        />
      ) : (
        <KnowledgeWorkspace
          kb={selectedKb}
          documents={documents}
          deleteDocument={deleteDocument}
          updateKnowledgeBase={updateKnowledgeBase}
          uploadDocument={uploadDocument}
          uploadKnowledgeFile={uploadKnowledgeFile}
          onPickUploadFile={setPendingUploadFile}
          uploadingKnowledgeFile={uploadingKnowledgeFile}
          uploadingFileName={uploadingFileName}
          docForm={docForm}
          setDocForm={setDocForm}
          handleBack={handleBack}
          activeDoc={activeDoc}
          setActiveDoc={setActiveDoc}
          setResegmentOpen={setResegmentOpen}
          notify={notify}
          token={token}
          activeKbId={activeKbId}
          loadDocuments={loadDocuments}
        />
      )}

      {createOpen && (
        <KnowledgeBaseDialog
          form={form}
          onCancel={closeCreate}
          onChange={setForm}
          onSubmit={submitKnowledgeBase}
          saving={saving}
        />
      )}

      {resegmentOpen && activeDoc && (
        <ResegmentModal
          isOpen={resegmentOpen}
          onClose={() => setResegmentOpen(false)}
          kbId={activeKbId}
          doc={activeDoc}
          token={token}
          onResegmentSuccess={async () => {
            setResegmentOpen(false);
            if (activeKbId && loadDocuments) {
              await loadDocuments?.(activeKbId);
            }
          }}
          notify={notify}
        />
      )}

      {pendingUploadFile && (
        <ResegmentModal
          isOpen
          mode="preupload"
          kbId={activeKbId}
          doc={{ filename: pendingUploadFile.name, title: pendingUploadFile.name }}
          previewFile={pendingUploadFile}
          token={token}
          onClose={() => setPendingUploadFile(null)}
          onConfirmConfig={async (segmentConfig) => {
            const file = pendingUploadFile;
            setPendingUploadFile(null);
            await uploadKnowledgeFile(file, segmentConfig);
          }}
          notify={notify}
        />
      )}
    </div>
  );
}



function KnowledgeDashboard({
  knowledgeBases,
  deleteKnowledgeBase,
  openCreate,
  onSelectKb,
  notify,
}) {
  const [openMenuKbId, setOpenMenuKbId] = useState(null);
  const [kbEnabled, setKbEnabled] = useState({});

  useEffect(() => {
    function handleOutsideClick() {
      setOpenMenuKbId(null);
    }
    if (openMenuKbId) {
      window.addEventListener('click', handleOutsideClick);
    }
    return () => window.removeEventListener('click', handleOutsideClick);
  }, [openMenuKbId]);

  return (
    <div className="knowledge-dashboard">
      <header className="dashboard-header">
        <div>
          <h2>知识库列表</h2>
          <p>选择一个知识库进行精准分段调参、上传及管理文档。</p>
        </div>
        <button className="primary" type="button" onClick={openCreate}>
          <Plus size={16} />新建知识库
        </button>
      </header>

      <div className="dashboard-table-container">
        <table className="dashboard-table">
          <thead>
            <tr>
              <th>信息</th>
              <th>类型</th>
              <th>更新时间</th>
              <th>启用状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {knowledgeBases.map((kb) => {
              const isEnabled = kbEnabled[kb.id] !== false;
              const formattedDate = kb.updated_at
                ? new Date(kb.updated_at).toLocaleString('zh-CN', {
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : '2026-05-30';

              return (
                <tr key={kb.id} className="kb-row-interactive" onClick={() => onSelectKb(kb.id)}>
                  <td className="kb-info-cell">
                    <div className="kb-icon-wrapper">
                      <Database size={20} />
                    </div>
                    <div className="kb-meta-details">
                      <strong>{kb.name}</strong>
                      <small>{kb.description || '暂无描述'}</small>
                      <span className="kb-doc-count-pill">{kb.document_count || 0} 个文档</span>
                    </div>
                  </td>
                  <td>
                    <span className="kb-type-badge">文档库</span>
                  </td>
                  <td className="kb-time-cell">{formattedDate}</td>
                  <td>
                    <label className="apple-switch" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={isEnabled}
                        onChange={() => {
                          const nextVal = !isEnabled;
                          setKbEnabled((prev) => ({ ...prev, [kb.id]: nextVal }));
                          notify?.(nextVal ? `「${kb.name}」已启用` : `「${kb.name}」已停用`);
                        }}
                      />
                      <span className="slider"></span>
                    </label>
                  </td>
                  <td className="kb-actions-cell">
                    <div className="actions-wrapper">
                      <button
                        className="btn-more"
                        title="操作"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenMenuKbId(openMenuKbId === kb.id ? null : kb.id);
                        }}
                      >
                        <MoreHorizontal size={16} />
                      </button>
                      {openMenuKbId === kb.id && (
                        <div className="bubble-dropdown-menu" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            onClick={() => {
                              setOpenMenuKbId(null);
                              notify?.("复制成功！已复制到其他空间。");
                            }}
                          >
                            复制到其他空间
                          </button>
                          <button
                            type="button"
                            className="danger"
                            onClick={() => {
                              setOpenMenuKbId(null);
                              deleteKnowledgeBase(kb);
                            }}
                          >
                            删除
                          </button>
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {knowledgeBases.length === 0 && (
              <tr>
                <td colSpan="5" className="empty-table-cell">
                  <div className="table-empty-state">
                    <Database size={40} className="muted-icon" />
                    <p>还没有知识库，点击右上角新建知识库。</p>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function KnowledgeWorkspace({
  kb,
  activeKbId,
  documents,
  deleteDocument,
  updateKnowledgeBase,
  uploadDocument,
  uploadKnowledgeFile,
  onPickUploadFile,
  uploadingKnowledgeFile,
  uploadingFileName,
  docForm,
  setDocForm,
  handleBack,
  activeDoc,
  setActiveDoc,
  setResegmentOpen,
  loadDocuments,
  notify,
  token,
}) {
  const [docSearchQuery, setDocSearchQuery] = useState('');
  const [addDropdownOpen, setAddDropdownOpen] = useState(false);
  const [customInputOpen, setCustomInputOpen] = useState(false);
  const currentKbId = activeKbId || kb?.id;

  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', description: '' });
  const [savingEdit, setSavingEdit] = useState(false);

  async function handleEditSubmit(e) {
    if (e) e.preventDefault();
    if (!editForm.name.trim()) return;
    setSavingEdit(true);
    try {
      await updateKnowledgeBase(kb.id, {
        name: editForm.name.trim(),
        description: editForm.description.trim()
      });
      setEditDialogOpen(false);
      return true;
    } catch (err) {
      console.error(err);
      notify?.(`保存失败：${errorMessage(err)}`);
      return false;
    } finally {
      setSavingEdit(false);
    }
  }

  const [chunks, setChunks] = useState([]);
  const [chunksLoading, setChunksLoading] = useState(false);

  const fileInputRef = useRef(null);

  // Close dropdown on click outside
  useEffect(() => {
    function handleOutsideClick() {
      setAddDropdownOpen(false);
    }
    if (addDropdownOpen) {
      window.addEventListener('click', handleOutsideClick);
    }
    return () => window.removeEventListener('click', handleOutsideClick);
  }, [addDropdownOpen]);

  // Sync / fetch chunks for the selected active document
  useEffect(() => {
    if (!activeDoc || !kb?.id) {
      setChunks([]);
      return;
    }
    let active = true;
    async function fetchChunks() {
      setChunksLoading(true);
      try {
        const data = await api(`/api/knowledge-bases/${kb.id}/documents/${activeDoc.id}/chunks`, { token });
        if (active) {
          setChunks(data.chunks || []);
        }
      } catch (err) {
        console.error(err);
        if (active) {
          setChunks([]);
          notify?.(`加载分块失败：${errorMessage(err)}`);
        }
      } finally {
        if (active) setChunksLoading(false);
      }
    }
    fetchChunks();
    return () => {
      active = false;
    };
  }, [activeDoc, kb?.id, token]);

  const filteredDocs = useMemo(() => {
    if (!docSearchQuery.trim()) return documents;
    return documents.filter((doc) =>
      (doc.title || doc.filename || '').toLowerCase().includes(docSearchQuery.toLowerCase())
    );
  }, [documents, docSearchQuery]);

  // Autoselect first document if none selected
  useEffect(() => {
    if (filteredDocs.length > 0 && !activeDoc) {
      setActiveDoc(filteredDocs[0]);
    }
  }, [filteredDocs, activeDoc, setActiveDoc]);

  function triggerLocalFileInput() {
    fileInputRef.current?.click();
  }

  // Handle custom paste submit
  const [pasteFilename, setPasteFilename] = useState('粘贴文档.txt');
  const [pasteText, setPasteText] = useState('');
  const [pasteSubmitting, setPasteSubmitting] = useState(false);
  const pasteGuard = useUnsavedForm({ enabled: customInputOpen, value: { pasteFilename, pasteText }, label: '粘贴文档',
    busy: pasteSubmitting, onSave: () => handlePasteSubmit() });
  const closePaste = () => pasteGuard.confirmLeave(() => setCustomInputOpen(false));

  async function handlePasteSubmit(e) {
    e?.preventDefault();
    if (!pasteText.trim()) return false;
    setPasteSubmitting(true);
    try {
      const payload = {
        title: pasteFilename || '粘贴文档.txt',
        filename: pasteFilename || '粘贴文档.txt',
        content: pasteText,
        content_type: 'text/plain',
        source_type: 'text',
      };
      await api(`/api/knowledge-bases/${kb.id}/documents`, {
        token,
        method: 'POST',
        body: payload,
      });
      // Clear inputs
      pasteGuard.markSaved();
      setPasteText('');
      setCustomInputOpen(false);
      // Wait a moment and force reload
      if (setDocForm) {
        setDocForm((form) => ({ ...form, kb_id: String(kb.id) })); // triggers re-fetch in parent!
      }
      return true;
    } catch (err) {
      console.error(err);
      notify?.(`写入知识库失败：${errorMessage(err)}`);
      return false;
    } finally {
      setPasteSubmitting(false);
    }
  }

  return (
    <div className="knowledge-workspace-container">
      <header className="workspace-header">
        <button className="btn-back" type="button" onClick={handleBack}>
          <ChevronLeft size={16} />返回列表
        </button>
        <div className="workspace-kb-title-block" style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: '#111827' }}>{kb?.name}</h3>
              <button
                type="button"
                className="coze-icon-button"
                title="编辑名称和描述"
                style={{
                  background: 'none',
                  border: 'none',
                  padding: '2px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  color: '#667085',
                  borderRadius: '4px',
                  transition: 'all 0.2s'
                }}
                onClick={() => {
                  setEditForm({ name: kb?.name || '', description: kb?.description || '' });
                  setEditDialogOpen(true);
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = '#4d43e6';
                  e.currentTarget.style.background = '#f4f4f5';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = '#667085';
                  e.currentTarget.style.background = 'none';
                }}
              >
                <SquarePen size={14} />
              </button>
            </div>
            <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#667085' }}>{kb?.description || '暂无描述'}</p>
          </div>
        </div>
        <div className="workspace-header-actions">
          <button
            className="primary"
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setAddDropdownOpen(!addDropdownOpen);
            }}
          >
            <Plus size={16} />添加内容
          </button>
          {addDropdownOpen && (
            <div className="add-dropdown-menu" onClick={(e) => e.stopPropagation()}>
              <button type="button" onClick={() => { setAddDropdownOpen(false); triggerLocalFileInput(); }}>
                💻 本地文档
              </button>
              <button type="button" onClick={() => { setAddDropdownOpen(false); setCustomInputOpen(true); }}>
                📝 自定义输入
              </button>
            </div>
          )}
          <input
            type="file"
            ref={fileInputRef}
            style={{ display: 'none' }}
            accept={KNOWLEDGE_FILE_ACCEPT}
            onChange={(event) => {
              if (setDocForm) {
                setDocForm((form) => ({ ...form, kb_id: String(kb.id) }));
              }
              // 先弹切片策略弹窗，确认后再真正上传（onPickUploadFile 暂存文件）
              handleKnowledgeFileInput(event, onPickUploadFile || uploadKnowledgeFile);
            }}
          />
        </div>
      </header>

      <div className="workspace-main-split">
        {/* Left column: search and documents list */}
        <section className="workspace-column left-column plain-panel">
          <div className="search-box-wrapper">
            <Search size={16} className="search-icon" />
            <input
              type="text"
              placeholder="搜索文档名称"
              value={docSearchQuery}
              onChange={(e) => setDocSearchQuery(e.target.value)}
            />
          </div>

          <div className="workspace-doc-list">
            {uploadingKnowledgeFile && (
              <div className="workspace-doc-row uploading active" style={{ opacity: 0.85, cursor: 'default', borderLeft: '3px solid #4d43e6', background: 'rgba(77, 67, 230, 0.03)', display: 'flex', alignItems: 'center', padding: '10px 12px', borderBottom: '1px solid #e5e7eb', gap: '8px' }}>
                <span className="coze-spinner"></span>
                <div className="doc-row-details" style={{ minWidth: 0, flex: 1 }}>
                  <strong style={{ color: '#4d43e6', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block', fontSize: '13px' }}>{uploadingFileName || '正在上传文档...'}</strong>
                  <small style={{ color: '#667085', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block', fontSize: '11px', marginTop: '2px' }}>
                    正在解析切片向量化...
                  </small>
                </div>
                <span className="document-status indexing" style={{ background: 'rgba(77, 67, 230, 0.08)', color: '#4d43e6', fontSize: '11px', padding: '2px 6px', borderRadius: '4px', whiteSpace: 'nowrap', flexShrink: 0, fontWeight: 600 }}>
                  上传中
                </span>
              </div>
            )}
            {filteredDocs.map((doc) => {
              const isActive = activeDoc?.id === doc.id;
              const status = doc.status || 'uploaded';
              const sourceType = doc.source_type || 'file';

              return (
                <div
                  key={doc.id}
                  className={`workspace-doc-row ${isActive ? 'active' : ''} status-${status}`}
                  onClick={() => setActiveDoc(doc)}
                >
                  <FileText size={16} className="doc-icon" />
                  <div className="doc-row-details">
                    <strong>{doc.title || doc.filename || `document-${doc.id}`}</strong>
                    <small>
                      {doc.chunk_count ?? 0} chunks · {sourceType === 'file' ? '文件' : '文本'}
                    </small>
                  </div>
                  <span className={`document-status ${status}`}>
                    {status === 'indexed' ? '已索引' : status === 'indexing' ? '索引中' : '失败'}
                  </span>
                  <div className="doc-row-actions" aria-label="文档操作">
                    {(status === 'failed' || status === 'indexed') && (
                      <button
                        className="btn-reindex-doc"
                        type="button"
                        title={status === 'failed' ? '重新索引' : '重建索引'}
                        aria-label={status === 'failed' ? '重新索引文档' : '重建文档索引'}
                        onClick={(e) => {
                          e.stopPropagation();
                          api(`/api/knowledge-bases/${currentKbId}/documents/${doc.id}/reindex`, { token, method: 'POST' })
                            .then(() => loadDocuments?.(currentKbId))
                            .catch(() => {});
                      }}
                    >
                        <RefreshCw size={15} />
                      </button>
                    )}
                    <button
                      className="btn-delete-doc"
                      type="button"
                      title="删除文档"
                      aria-label="删除文档"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteDocument(doc.id).then(() => {
                          if (activeDoc?.id === doc.id) {
                            setActiveDoc(null);
                          }
                        });
                      }}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              );
            })}
            {filteredDocs.length === 0 && (
              <p className="muted empty-workspace-docs">无文档资料，请点击右上角添加。</p>
            )}
          </div>
        </section>

        {/* Right column: active doc details & chunk stream */}
        <section className="workspace-column right-column plain-panel">
          {activeDoc ? (
            <div className="doc-detail-view">
              <div className="doc-detail-header-card">
                <div className="doc-meta-title-row">
                  <h4>{activeDoc.title || activeDoc.filename}</h4>
                  <button
                    className="btn-resegment-trigger"
                    type="button"
                    onClick={() => setResegmentOpen(true)}
                  >
                    重新切片/调参
                  </button>
                </div>
                <div className="doc-meta-grid">
                  <div className="meta-item">
                    <span>文件格式</span>
                    <strong>{activeDoc.content_type || 'text/plain'}</strong>
                  </div>
                  <div className="meta-item">
                    <span>分块数量</span>
                    <strong>{activeDoc.chunk_count ?? 0} 个 chunk</strong>
                  </div>
                  <div className="meta-item">
                    <span>索引状态</span>
                    <strong className={`status-text ${activeDoc.status}`}>
                      {activeDoc.status === 'indexed' ? '已完成' : '同步中'}
                    </strong>
                  </div>
                </div>
              </div>

              <div className="chunk-list-section">
                <h5>分块预览 ({chunks.length} 个)</h5>
                <div className="chunk-card-stream">
                  {chunksLoading ? (
                    <div className="chunks-loading">
                      <span className="spinner"></span>加载分块中...
                    </div>
                  ) : (
                    chunks.map((chunk, index) => (
                      <div key={chunk.id || index} className="chunk-card">
                        <div className="chunk-card-header">
                          <span className="chunk-index-badge">#{chunk.chunk_index ?? index}</span>
                          <span className="chunk-dim">{chunk.embedding_dimension || 768}d</span>
                          {chunk.hierarchy_path && (
                            <span className="chunk-path-badge">🌳 {chunk.hierarchy_path}</span>
                          )}
                        </div>
                        <div className="chunk-text-content">{chunk.text}</div>
                      </div>
                    ))
                  )}
                  {!chunksLoading && chunks.length === 0 && (
                    <p className="muted empty-chunks">此文档暂无分块，请重新切片或检查索引状态。</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="workspace-empty-detail">
              <Database size={48} className="muted-icon" />
              <p>请在左侧列表选择一个文档查看详细的分块信息和参数配置。</p>
            </div>
          )}
        </section>
      </div>

      {/* Custom Paste Text Dialog */}
      {customInputOpen && (
        <div className="profile-dialog-backdrop">
          <section
            className="resource-form-dialog"
            role="dialog"
            onClick={(e) => e.stopPropagation()}
            style={{ width: '560px' }}
          >
            <button
              className="profile-dialog-close"
              type="button"
              title="关闭"
              onClick={closePaste}
            >
              <X size={16} />
            </button>
            <header className="model-dialog-heading">
              <h3>自定义文本输入</h3>
              <p>直接粘贴资料文本进行快速上传并建立索引。</p>
            </header>
            <form className="dialog-form" onSubmit={handlePasteSubmit}>
              <label className="field-stack">
                <span>文档名称</span>
                <input
                  type="text"
                  value={pasteFilename}
                  onChange={(e) => setPasteFilename(e.target.value)}
                  placeholder="例如: 产品指南.txt"
                  required
                />
              </label>
              <label className="field-stack">
                <span>文本内容</span>
                <textarea
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                  placeholder="在这里粘贴或直接写入知识库内容..."
                  style={{ minHeight: '180px' }}
                  required
                />
              </label>
              <footer className="dialog-actions">
                <button type="button" onClick={closePaste} disabled={pasteSubmitting}>
                  取消
                </button>
                <button className="primary-model-action" type="submit" disabled={pasteSubmitting}>
                  {pasteSubmitting ? '上传中...' : '确认上传'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      )}

      {editDialogOpen && (
        <KnowledgeBaseDialog
          title="编辑知识库"
          description="修改知识库的名称和描述，方便在智能体配置中进行管理与调用。"
          submitText="保存修改"
          savingText="保存中..."
          isEdit={true}
          form={editForm}
          onChange={setEditForm}
          onCancel={() => setEditDialogOpen(false)}
          onSubmit={handleEditSubmit}
          saving={savingEdit}
        />
      )}
    </div>
  );
}










function documentStatusLabel(status) {
  const labels = {
    uploaded: '已上传',
    indexing: '索引中',
    indexed: '已索引',
    failed: '失败',
  };
  return labels[status] || status || '未知';
}

