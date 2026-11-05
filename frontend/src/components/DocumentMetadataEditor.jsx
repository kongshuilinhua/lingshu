import React, { useEffect, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { api, errorMessage } from '../utils.js';

/**
 * 文档元数据编辑：给文档打「产品 / 版本 / 部门」这类标签，检索时可以按它圈定范围。
 * 整份覆盖保存，空字段名的行直接丢弃。
 */
export function DocumentMetadataEditor({ kbId, document, token, notify, onSaved }) {
  const [rows, setRows] = useState(() => toRows(document?.metadata));
  const [saving, setSaving] = useState(false);

  useEffect(() => { setRows(toRows(document?.metadata)); }, [document?.id, document?.metadata]);

  const dirty = JSON.stringify(fromRows(rows)) !== JSON.stringify(document?.metadata || {});

  async function save() {
    setSaving(true);
    try {
      const data = await api(`/api/knowledge-bases/${kbId}/documents/${document.id}/metadata`, {
        token, method: 'PUT', body: { metadata: fromRows(rows) },
      });
      onSaved?.(data.document);
      notify?.('元数据已保存');
    } catch (err) {
      notify?.(`保存元数据失败：${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="doc-metadata">
      <div className="doc-metadata-head">
        <span>元数据</span>
        <small>检索时可按这些字段圈定文档范围</small>
      </div>
      {rows.map((row, index) => (
        <div className="doc-metadata-row" key={index}>
          <input aria-label="字段名" placeholder="字段，如 产品" value={row.key}
            onChange={(event) => setRows(rows.map((item, i) => (i === index ? { ...item, key: event.target.value } : item)))} />
          <input aria-label="字段值" placeholder="取值，如 扫地机" value={row.value}
            onChange={(event) => setRows(rows.map((item, i) => (i === index ? { ...item, value: event.target.value } : item)))} />
          <button type="button" aria-label="删除字段" onClick={() => setRows(rows.filter((_, i) => i !== index))}><X size={13} /></button>
        </div>
      ))}
      <div className="doc-metadata-actions">
        <button type="button" onClick={() => setRows([...rows, { key: '', value: '' }])}><Plus size={13} />添加字段</button>
        {dirty && <button type="button" className="primary" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存'}</button>}
      </div>
    </div>
  );
}

function toRows(metadata) {
  return Object.entries(metadata || {}).map(([key, value]) => ({ key, value: String(value ?? '') }));
}

function fromRows(rows) {
  return Object.fromEntries(rows.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.value.trim()]));
}
