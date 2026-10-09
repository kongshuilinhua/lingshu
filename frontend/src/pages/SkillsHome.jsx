import React, { useEffect, useRef, useState } from 'react';
import { useUnsavedForm } from '../components/UnsavedChanges.jsx';
import { Plus, Search, Sparkles, X } from 'lucide-react';
import { api } from '../lib/api.js';
import './SkillsHome.css';

const TEMPLATE = '---\nname: my-skill\ndescription: 描述此技能的用途和适用场景\n---\n\n# 操作说明\n\n1. 描述完成任务的步骤。\n';
const empty = () => ({ name: '', category: '通用', instructions: TEMPLATE, package_base64: '', enabled: true, is_listed: false, scripts_approved: false });

export function SkillsHome({ token, canManage, mode, onManage, requestDeleteConfirm }) {
  const [items, setItems] = useState([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [drawer, setDrawer] = useState(null);
  const [form, setForm] = useState(empty());
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState([]);
  const [packageName, setPackageName] = useState('');
  const editFormRef = useRef(null);
  const formGuard = useUnsavedForm({ enabled: Boolean(drawer?.edit), value: form, label: 'Skill 配置', busy,
    onSave: () => editFormRef.current?.reportValidity() ? save() : false,
    onDiscard: (saved) => { setForm(saved); setPackageName(''); } });
  const closeForm = () => formGuard.confirmLeave(() => setDrawer(null));

  async function refresh() {
    const data = await api('/api/skills', { token });
    setItems(data.items || []);
  }
  useEffect(() => {
    let live = true;
    api('/api/skills', { token }).then((data) => { if (live) setItems(data.items || []); })
      .catch((err) => live && setError(err.message)).finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [token]);

  function create() {
    if (mode === 'discover') onManage?.();
    setError(''); setForm(empty()); setFiles([]); setPackageName(''); setDrawer({ id: null, edit: true });
  }

  async function open(skill, versionId = skill.current_version_id, edit = false) {
    setError('');
    try {
      const data = await api(`/api/skills/${skill.id}/versions/${versionId}`, { token });
      const nextForm = { ...empty(), name: skill.name, category: skill.category, enabled: skill.enabled,
        is_listed: skill.is_listed, instructions: data.version.source, scripts_approved: data.version.scripts_approved };
      formGuard.markSaved(nextForm);
      setForm(nextForm);
      setFiles(data.version.files || []); setPackageName('');
      setDrawer({ id: skill.id, versionId, edit: edit && skill.can_edit, skill });
    } catch (err) { setError(err.message); }
  }

  async function upload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('导入文件不能超过 10 MB。');
      if (file.name.toLowerCase().endsWith('.zip')) {
        const encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = reject; reader.readAsDataURL(file);
        });
        setForm((current) => ({ ...current, name: current.name || file.name.replace(/\.zip$/i, ''), package_base64: encoded, instructions: '' }));
        setPackageName(file.name);
      } else {
        const instructions = await file.text();
        setForm((current) => ({ ...current, name: current.name || file.name.replace(/\.md$/i, ''), instructions, package_base64: '' }));
        setPackageName('');
      }
      setError('');
    } catch (err) { setError(err.message || '读取文件失败。'); }
    event.target.value = '';
  }

  async function save(event) {
    event?.preventDefault(); setBusy(true); setError('');
    try {
      const body = { ...form, name: form.name.trim() };
      if (!canManage) body.scripts_approved = false;
      if (!body.package_base64) delete body.package_base64;
      await api(drawer.id ? `/api/skills/${drawer.id}` : '/api/skills', { token, method: drawer.id ? 'PATCH' : 'POST', body });
      formGuard.markSaved(); setDrawer(null); setNotice(drawer.id ? '新版本已保存。已绑定的旧版本保持固定，可在智能体中切换。' : 'Skill 已创建，可在智能体中绑定并按需加载。');
      await refresh().catch((err) => setError(`Skill 已保存，目录刷新失败：${err.message}`));
      return true;
    } catch (err) { setError(err.message); return false; } finally { setBusy(false); }
  }

  async function change(skill, body) {
    setError('');
    try { await api(`/api/skills/${skill.id}`, { token, method: 'PATCH', body }); await refresh(); }
    catch (err) { setError(err.message); }
  }

  async function remove(skill) {
    if (!requestDeleteConfirm || !await requestDeleteConfirm({ title: '移除 Skill', message: `确定移除「${skill.name}」及其版本吗？`, detail: '被智能体引用的 Skill 需先解除绑定。' })) return;
    try { await api(`/api/skills/${skill.id}`, { token, method: 'DELETE' }); await refresh(); }
    catch (err) { setError(err.message); }
  }

  async function download(file) {
    try {
      const path = file.path.split('/').map(encodeURIComponent).join('/');
      const data = await api(`/api/skills/${drawer.id}/versions/${drawer.versionId}/files/${path}`, { token });
      const bytes = Uint8Array.from(atob(data.content_base64), (character) => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }));
      const link = document.createElement('a'); link.href = url; link.download = file.path.split('/').pop();
      link.click(); URL.revokeObjectURL(url);
    } catch (err) { setError(err.message); }
  }

  const visible = items.filter((item) => `${item.name} ${item.slug} ${item.description}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="skills-home mcp-market">
    <header className="mcp-resource-header"><div><h2>{mode === 'mine' ? '我的 Skill' : '工作区 Skill'}</h2><p>{mode === 'discover' ? '浏览技能用途和说明，进入“我的资源”管理版本、共享和启停。' : '启动时只提供元数据，模型需要时加载说明和引用文件。'}</p></div><button type="button" className="mcp-primary" onClick={create}><Plus size={16} />创建 / 导入 Skill</button></header>
    {error && <p className="mcp-alert" role="alert">{error}</p>}{notice && <p className="mcp-notice">{notice}</p>}
    <div className="mcp-catalog-heading"><h3>可用的技能</h3><label className="mcp-search"><Search size={16} /><input aria-label="搜索 Skill" placeholder="搜索名称或适用场景" value={query} onChange={(event) => setQuery(event.target.value)} /></label></div>
    {loading ? <p>正在读取 Skill…</p> : !visible.length ? <div className="mcp-empty"><Sparkles size={28} /><strong>还没有匹配的 Skill</strong><span>创建 SKILL.md，或导入包含说明、参考文件和模板的 ZIP。</span></div> : <div className="mcp-grid">
      {visible.map((skill) => <article className="mcp-card" key={skill.id}>
        <div className="mcp-card-top"><span className="mcp-card-icon"><Sparkles size={23} /></span><span className="mcp-transport">SKILL · {skill.enabled ? '可用' : '已停用'}</span></div>
        <div className="mcp-card-body"><small>{skill.category} · {skill.is_listed ? '工作区共享' : '私有'}</small><h3>{skill.name}</h3><p>{skill.description}</p><small>{skill.slug} · {skill.versions?.length || 0} 个版本</small></div>
        <div className="mcp-card-actions"><button type="button" onClick={() => open(skill)}>{mode === 'discover' ? '查看说明' : '查看版本'}</button>
          {skill.can_edit && (mode === 'discover' ? <button type="button" onClick={() => onManage?.()}>管理 Skill</button> : <><button type="button" onClick={() => open(skill, skill.current_version_id, true)}>编辑</button><button type="button" onClick={() => change(skill, { enabled: !skill.enabled })}>{skill.enabled ? '停用' : '启用'}</button></>)}
        </div>
        {mode !== 'discover' && skill.can_edit && <div className="mcp-manage-actions"><button type="button" onClick={() => change(skill, { is_listed: !skill.is_listed })}>{skill.is_listed ? '设为私有' : '共享到工作区'}</button><button type="button" onClick={() => remove(skill)}>移除</button></div>}
      </article>)}
    </div>}
    {drawer && <div className="mcp-drawer-backdrop"><aside className="mcp-drawer" role="dialog" aria-modal="true" aria-label="Skill 配置"><div className="mcp-drawer-head"><h2>{drawer.edit ? drawer.id ? '保存 Skill 新版本' : '创建 Skill' : 'Skill 版本内容'}</h2><button type="button" aria-label="关闭 Skill 配置" disabled={busy} onClick={closeForm}><X size={20} /></button></div><form ref={editFormRef} className="mcp-form" onSubmit={save}>
      {drawer.skill && <label>查看版本<select value={drawer.versionId} disabled={busy} onChange={(event) => {
        const versionId = Number(event.target.value);
        formGuard.confirmLeave(() => open(drawer.skill, versionId, drawer.edit));
      }}>{drawer.skill.versions.map((item) => <option key={item.id} value={item.id}>版本 {item.version}</option>)}</select></label>}
      <label>显示名称<input required readOnly={!drawer.edit} maxLength={120} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label>分类<input readOnly={!drawer.edit} value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} /></label>
      {drawer.edit && <label>导入 Markdown / ZIP<input type="file" accept=".md,.zip" onChange={upload} /></label>}
      {packageName ? <p className="mcp-notice">已选择 {packageName}，保存时解析包中的 SKILL.md 和资源文件。</p> : <label>SKILL.md<textarea className="skill-source" required readOnly={!drawer.edit} value={form.instructions} onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></label>}
      {files.length > 0 && <div className="skill-file-list"><strong>附属文件（按需加载）</strong>{files.map((file) => <button type="button" key={file.path} onClick={() => download(file)}>{file.path} · {file.size} 字节 · 下载</button>)}</div>}
      {drawer.edit && <><label className="mcp-check"><input type="checkbox" checked={form.is_listed} onChange={(event) => setForm({ ...form, is_listed: event.target.checked })} />工作区共享</label>{canManage && <label className="mcp-check"><input type="checkbox" checked={form.scripts_approved} onChange={(event) => setForm({ ...form, scripts_approved: event.target.checked })} />批准此版本的脚本在已配置的沙箱中运行</label>}<p className="mcp-form-hint">导入和加载不会执行脚本。执行需要管理员授权、Docker 和部署者配置的沙箱镜像。</p></>}
      {error && <p className="mcp-alert">{error}</p>}<div className="mcp-form-actions"><button type="button" disabled={busy} onClick={closeForm}>关闭</button>{drawer.edit && <button type="submit" disabled={busy} className="mcp-primary">{busy ? '保存中…' : drawer.id ? '保存新版本' : '创建 Skill'}</button>}</div>
    </form></aside></div>}
  </div>;
}
