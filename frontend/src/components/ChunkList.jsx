import React, { useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { api, errorMessage } from '../utils.js';

/**
 * 文档的切片列表，可逐条改、删、补。
 *
 * 召回测试发现某条切片切坏了或内容不对，直接在这里修，不必改原文再整篇重切。
 * 每次改动后端都会重新向量化并刷新检索缓存，返回最新的切片列表。
 */
export function ChunkList({ kbId, documentId, chunks, loading, canEdit, token, onChange, notify }) {
  const [editingId, setEditingId] = useState(null);
  const [draft, setDraft] = useState('');
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const base = `/api/knowledge-bases/${kbId}/documents/${documentId}/chunks`;

  async function submit(request) {
    setSaving(true);
    try {
      const data = await request();
      onChange?.(data.chunks || [], data.document);
      setEditingId(null);
      setAdding(false);
      setDraft('');
    } catch (err) {
      notify?.(`保存切片失败：${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  function startEdit(chunk) {
    setAdding(false);
    setEditingId(chunk.id);
    setDraft(chunk.text);
  }

  function remove(chunk) {
    if (!window.confirm(`删除切片 #${chunk.chunk_index}？检索将不再命中这段内容。`)) return;
    submit(() => api(`${base}/${chunk.id}`, { token, method: 'DELETE' }));
  }

  const editor = (onSave) => (
    <div className="chunk-editor">
      <textarea rows={5} value={draft} autoFocus onChange={(event) => setDraft(event.target.value)} />
      <div className="chunk-editor-actions">
        <button type="button" disabled={saving} onClick={() => { setEditingId(null); setAdding(false); }}>取消</button>
        <button type="button" className="primary" disabled={saving || !draft.trim()} onClick={onSave}>
          {saving ? '保存中…' : '保存并重新索引'}
        </button>
      </div>
    </div>
  );

  return (
    <div className="chunk-list-section">
      <div className="chunk-list-head">
        <h5>分块预览 ({chunks.length} 个)</h5>
        {canEdit && !adding && (
          <button type="button" onClick={() => { setEditingId(null); setDraft(''); setAdding(true); }}>
            <Plus size={13} />补一条
          </button>
        )}
      </div>
      <div className="chunk-card-stream">
        {adding && editor(() => submit(() => api(base, { token, method: 'POST', body: { text: draft } })))}
        {loading ? (
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
                {canEdit && editingId !== chunk.id && (
                  <span className="chunk-card-actions">
                    <button type="button" aria-label="编辑切片" onClick={() => startEdit(chunk)}><Pencil size={12} /></button>
                    <button type="button" aria-label="删除切片" onClick={() => remove(chunk)}><Trash2 size={12} /></button>
                  </span>
                )}
              </div>
              {editingId === chunk.id
                ? editor(() => submit(() => api(`${base}/${chunk.id}`, { token, method: 'PATCH', body: { text: draft } })))
                : <div className="chunk-text-content">{chunk.text}</div>}
            </div>
          ))
        )}
        {!loading && chunks.length === 0 && !adding && (
          <p className="muted empty-chunks">此文档暂无分块，请重新切片或检查索引状态。</p>
        )}
      </div>
    </div>
  );
}
