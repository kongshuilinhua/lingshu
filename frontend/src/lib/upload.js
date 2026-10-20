/**
 * 文件上传工具函数。
 * 从 utils.js 提取 —— Base64 转换、文件类型推断、附件处理。
 */

const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;
const MAX_KNOWLEDGE_BYTES = 30 * 1024 * 1024; // 知识库文档单独上限（文献类 PDF 较大），与后端 upload_max_bytes 保持一致
const KNOWLEDGE_FILE_ACCEPT = '.txt,.md,.markdown,.csv,.pdf,.docx,.xlsx,.pptx';
const KNOWLEDGE_FILE_EXTENSIONS = ['txt', 'md', 'markdown', 'csv', 'pdf', 'docx', 'xlsx', 'pptx'];

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.onerror = () => reject(new Error('文件读取失败'));
    reader.readAsDataURL(file);
  });
}

function guessContentType(filename) {
  const suffix = filename.toLowerCase().split('.').pop();
  const types = { txt: 'text/plain', md: 'text/markdown', markdown: 'text/markdown', csv: 'text/csv', pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' };
  return types[suffix] || 'application/octet-stream';
}

function uploadTypeFromContentType(contentType = '') {
  return String(contentType).startsWith('image/') ? 'image' : 'document';
}

function validateKnowledgeFile(file) {
  if (file.size > MAX_KNOWLEDGE_BYTES) throw new Error('知识库文件不能超过 30MB');
  const suffix = String(file.name || '').toLowerCase().split('.').pop();
  if (!KNOWLEDGE_FILE_EXTENSIONS.includes(suffix)) throw new Error('知识库仅支持 TXT、MD、CSV、PDF、DOCX、XLSX、PPTX');
}

function filesFromList(fileList) {
  return Array.from(fileList || []).filter(Boolean);
}

function filesFromClipboard(clipboardData) {
  const files = filesFromList(clipboardData?.files);
  if (files.length) return files;
  return Array.from(clipboardData?.items || []).filter((item) => item.kind === 'file').map((item) => item.getAsFile()).filter(Boolean);
}

function hasTransferFiles(dataTransfer) {
  return Array.from(dataTransfer?.types || []).includes('Files');
}

async function uploadAttachmentFiles(files, uploadChatAttachment) {
  for (const file of files) await uploadChatAttachment(file);
}

async function handleAttachmentInput(event, uploadChatAttachment) {
  const files = filesFromList(event.target.files);
  event.target.value = '';
  await uploadAttachmentFiles(files, uploadChatAttachment);
}

async function handleAttachmentPaste(event, uploadChatAttachment) {
  const files = filesFromClipboard(event.clipboardData);
  if (!files.length) return;
  event.preventDefault();
  await uploadAttachmentFiles(files, uploadChatAttachment);
}

async function handleAttachmentDrop(files, uploadChatAttachment) {
  await uploadAttachmentFiles(filesFromList(files), uploadChatAttachment);
}

async function handleKnowledgeFileInput(event, uploadKnowledgeFile) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  await uploadKnowledgeFile(file);
}

export {
  MAX_UPLOAD_BYTES,
  MAX_KNOWLEDGE_BYTES,
  KNOWLEDGE_FILE_ACCEPT,
  KNOWLEDGE_FILE_EXTENSIONS,
  fileToBase64,
  guessContentType,
  uploadTypeFromContentType,
  validateKnowledgeFile,
  filesFromList,
  filesFromClipboard,
  hasTransferFiles,
  uploadAttachmentFiles,
  handleAttachmentInput,
  handleAttachmentPaste,
  handleAttachmentDrop,
  handleKnowledgeFileInput,
};
