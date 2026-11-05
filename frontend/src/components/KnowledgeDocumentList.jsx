import React from 'react';
import { UploadCloud, Database, FileText, FileX2 } from 'lucide-react';
import { handleKnowledgeFileInput, KNOWLEDGE_FILE_ACCEPT } from '../utils.js';

function documentStatusLabel(status) {
  const labels = {
    uploaded: '已上传',
    indexing: '索引中',
    indexed: '已索引',
    failed: '失败',
  };
  return labels[status] || status || '未知';
}

export function KnowledgeUploadBox({ docForm, setDocForm, uploadDocument, uploadKnowledgeFile, uploadingKnowledgeFile }) {
  return (
    <div className="knowledge-upload-box">
      <label className={`knowledge-file-drop ${uploadingKnowledgeFile ? 'loading' : ''}`}>
        <UploadCloud size={18} />
        <span>
          <strong>{uploadingKnowledgeFile ? '文件上传中...' : '上传文件到知识库'}</strong>
          <small>支持 TXT / MD / CSV / PDF / DOCX / XLSX / PPTX，单文件不超过 30MB</small>
        </span>
        <input
          type="file"
          accept={KNOWLEDGE_FILE_ACCEPT}
          disabled={uploadingKnowledgeFile}
          onChange={(event) => handleKnowledgeFileInput(event, uploadKnowledgeFile)}
        />
      </label>
      <div className="knowledge-or-line"><span>或粘贴文本</span></div>
      <input value={docForm.filename} onChange={(e) => setDocForm({ ...docForm, filename: e.target.value })} placeholder="guide.txt" />
      <textarea value={docForm.text} onChange={(e) => setDocForm({ ...docForm, text: e.target.value })} placeholder="粘贴资料文本" />
      <button type="button" onClick={uploadDocument} disabled={uploadingKnowledgeFile || !String(docForm.text || '').trim()}>
        <Database size={15} />上传并索引
      </button>
    </div>
  );
}

export function KnowledgeDocumentList({ deleteDocument, documents, expandedChunks, onToggleChunks, wide = false }) {
  return (
    <div className={`document-list ${wide ? 'wide' : ''}`}>
      {documents.map((document) => (
        <DocumentRow document={document} key={document.id} deleteDocument={deleteDocument} expandedChunks={expandedChunks} onToggleChunks={onToggleChunks} />
      ))}
      {documents.length === 0 && <p className="muted">当前知识库还没有文档。</p>}
    </div>
  );
}

export function DocumentRow({ deleteDocument, document, expandedChunks, onToggleChunks }) {
  const status = document.status || 'uploaded';
  const sourceType = document.source_type || (document.content_type === 'text/plain' ? 'text' : 'file');
  const isExpanded = expandedChunks?.id === document.id;
  const isLoading = expandedChunks?.loading && isExpanded;
  return (
    <div className={`document-row status-${status}`} key={document.id} style={{ cursor: 'pointer' }} onClick={() => onToggleChunks?.(document.id)}>
      <FileText size={15} />
      <span>
        <strong>{document.title || document.filename || `document-${document.id}`}</strong>
        <small>{document.chunk_count ?? 0} chunks · {document.content_type || 'text/plain'} · {sourceType}</small>
        {document.text_preview && <em>{document.text_preview}</em>}
        {document.error_message && <b>{document.error_message}</b>}
      </span>
      <i className={`document-status ${status}`}>{documentStatusLabel(status)}</i>
      <button type="button" title="删除文档" onClick={(e) => { e.stopPropagation(); deleteDocument(document.id).catch((err) => console.error(err)); }}>
        <FileX2 size={14} />
      </button>
      {isExpanded && expandedChunks?.items && (
        <div style={{ gridColumn: '1 / -1', borderTop: '1px solid #eef0f5', paddingTop: 8, marginTop: 4 }}>
          {isLoading ? <span className="muted">加载中...</span> : expandedChunks.items.map((chunk, idx) => (
            <div key={chunk.id || idx} style={{ border: '1px solid #eef0f5', borderRadius: 8, padding: '8px 10px', marginBottom: 6, fontSize: 12, lineHeight: 1.55 }}>
              <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
                <span className="document-status indexed" style={{ fontSize: 10 }}>#{chunk.chunk_index}</span>
                <small style={{ color: '#667085' }}>{chunk.chunk_id}</small>
                <small style={{ color: '#98a2b3' }}>{chunk.embedding_dimension}d</small>
              </div>
              <div style={{ color: '#344054', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{chunk.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
