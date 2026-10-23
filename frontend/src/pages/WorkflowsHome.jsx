import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Plus, Trash2, Workflow as WorkflowIcon } from 'lucide-react';
import { api, errorMessage } from '../utils.js';
import { useUnsavedNavigation } from '../components/UnsavedChanges.jsx';

const WorkflowCanvas = lazy(() => import('../components/WorkflowCanvas.jsx').then((module) => ({ default: module.WorkflowCanvas })));

/**
 * 独立工作流：左侧一级资源，自成入口。
 *
 * 与智能体内置流程的区别是「归属于谁」——这里的工作流属于工作区，
 * 可以被多个智能体通过 `workflow_id` 引用；内置流程仍是智能体的一个配置字段。
 */
export function WorkflowsHome({ token, agents = [], models = [], userModels = [], workflows: initialWorkflows, reloadWorkflows, canEdit = true, onChatWorkflow }) {
  const [workflows, setWorkflows] = useState(initialWorkflows || []);
  const [activeId, setActiveId] = useState(null);
  // 画布有未保存改动时，切换/新建/删除都先确认，避免改动静默丢失
  const confirmNavigation = useUnsavedNavigation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function reload() {
    setLoading(true);
    try {
      const data = await api('/api/workflows', { token });
      setWorkflows(data.items || []);
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [token]);

  async function createWorkflow() {
    try {
      const data = await api('/api/workflows', { token, method: 'POST', body: { name: '未命名工作流' } });
      await reload();
      reloadWorkflows?.();
      setActiveId(data.workflow.id);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function renameWorkflow(id, patch) {
    try {
      const data = await api(`/api/workflows/${id}`, { token, method: 'PATCH', body: patch });
      setWorkflows((current) => current.map((item) => (item.id === id ? data.workflow : item)));
      reloadWorkflows?.();
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function removeWorkflow(item) {
    if (!window.confirm(`删除工作流「${item.name}」？引用它的智能体会退回内置流程。`)) return;
    try {
      await api(`/api/workflows/${item.id}`, { token, method: 'DELETE' });
      if (activeId === item.id) setActiveId(null);
      await reload();
      reloadWorkflows?.();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const active = workflows.find((item) => item.id === activeId);
  // 必须是稳定引用：画布的读取 effect 依赖它，内联函数会让每次重渲染都重新拉取、冲掉未保存的编辑
  const loadActive = useCallback(() => api(`/api/workflows/${activeId}`, { token }), [activeId, token]);
  // 全局刷新函数每次渲染都是新引用，放进 ref，避免 saveActive 跟着变
  const reloadGlobal = useRef(reloadWorkflows);
  reloadGlobal.current = reloadWorkflows;
  const saveActive = useCallback(async (_id, nodes) => {
    const data = await api(`/api/workflows/${activeId}`, { token, method: 'PATCH', body: { nodes } });
    // 节点数变了：本页列表与智能体编辑页的下拉框都要跟上
    setWorkflows((current) => current.map((item) => (item.id === data.workflow.id ? data.workflow : item)));
    reloadGlobal.current?.();
    return data;
  }, [activeId, token]);

  return (
    <div className="content-page workflow-page">
      <header className="page-heading">
        <div>
          <h1>工作流</h1>
          <p>编排流程在这里独立维护，智能体通过引用使用它。</p>
        </div>
        <button className="primary" type="button" disabled={!canEdit} onClick={() => confirmNavigation(createWorkflow)}>
          <Plus size={16} />新建工作流
        </button>
      </header>

      {error && <p className="workflow-editor-error">{error}</p>}

      <div className="workflow-layout">
        <aside className="workflow-list">
          {loading && <p className="workflow-editor-empty">正在读取…</p>}
          {!loading && workflows.length === 0 && <p className="workflow-editor-empty">还没有工作流，先新建一个。</p>}
          {workflows.map((item) => (
            <div key={item.id} className={`workflow-list-row ${item.id === activeId ? 'active' : ''}`}>
              <button type="button" className="workflow-list-main" onClick={() => item.id !== activeId && confirmNavigation(() => setActiveId(item.id))}>
                <WorkflowIcon size={15} />
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.node_count} 个节点</small>
                </span>
              </button>
              <button type="button" aria-label="删除工作流" disabled={!canEdit} onClick={() => confirmNavigation(() => removeWorkflow(item))}>
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </aside>

        <section className="workflow-stage">
          {active ? (
            <>
              <WorkflowMeta key={active.id} workflow={active} canEdit={canEdit} onSave={renameWorkflow} />
              <WorkflowRuntime
                key={`runtime-${active.id}`} workflow={active} token={token} canEdit={canEdit}
                models={models} userModels={userModels} onSaved={renameWorkflow}
                onChat={onChatWorkflow ? () => onChatWorkflow(active.id) : undefined}
              />
              <Suspense fallback={<p className="workflow-editor-empty">正在加载画布…</p>}>
                <WorkflowCanvas
                  key={active.id}
                  agentId={active.id}
                  token={token}
                  canEdit={canEdit}
                  agents={agents}
                  loadWorkflow={loadActive}
                  saveWorkflow={saveActive}
                />
              </Suspense>
              <WorkflowTestPanel workflowId={active.id} token={token} agents={agents} />
            </>
          ) : (
            <p className="workflow-editor-empty">从左边选一个工作流开始编辑。</p>
          )}
        </section>
      </div>
    </div>
  );
}

/**
 * 试运行面板：借一个智能体的配置跑当前工作流。
 *
 * 为什么必须选智能体：工作流只描述流程，模型、知识库、工具都挂在智能体上。
 * 试运行不落正式会话，跑完不污染会话列表。
 */
function WorkflowTestPanel({ workflowId, token, agents = [] }) {
  const [agentId, setAgentId] = useState('');
  const [message, setMessage] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setResult(null);
    setError('');
  }, [workflowId]);

  async function run() {
    if (!agentId || !message.trim()) return;
    setRunning(true);
    setError('');
    try {
      const data = await api(`/api/workflows/${workflowId}/test`, {
        token,
        method: 'POST',
        body: { agent_id: Number(agentId), message: message.trim() },
      });
      setResult(data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="workflow-test">
      <header>
        <strong>试运行</strong>
        <small>借一个智能体的配置跑这条流程</small>
      </header>
      <div className="workflow-test-row">
        <select value={agentId} onChange={(event) => setAgentId(event.target.value)}>
          <option value="">选择智能体（提供模型/知识库/工具）</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>{agent.name}</option>
          ))}
        </select>
        <input
          value={message}
          placeholder="输入一句话试试"
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') run(); }}
        />
        <button type="button" className="primary" disabled={running || !agentId || !message.trim()} onClick={run}>
          {running ? '运行中…' : '运行'}
        </button>
      </div>
      {error && <p className="workflow-editor-error">{error}</p>}
      {result && (
        <div className="workflow-test-result">
          <div className="workflow-test-steps">
            {(result.steps || []).map((step, index) => (
              <span key={`${step.node_id}-${index}`} className={`workflow-test-step ${step.status}`}>
                {step.node_id} · {step.node_type}
              </span>
            ))}
          </div>
          <div className="workflow-test-answer markdown-content">
            {result.answer ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown> : '（没有输出）'}
          </div>
        </div>
      )}
    </div>
  );
}

/** 名称与描述：失焦或回车即保存，不另设保存按钮 */
function WorkflowMeta({ workflow, canEdit, onSave }) {
  const [name, setName] = useState(workflow.name);
  const [description, setDescription] = useState(workflow.description || '');

  function commit() {
    const trimmed = name.trim();
    if (!trimmed) { setName(workflow.name); return; }
    if (trimmed === workflow.name && description === (workflow.description || '')) return;
    onSave(workflow.id, { name: trimmed, description });
  }

  return (
    <header className="workflow-stage-head">
      <input
        className="workflow-name-input" aria-label="工作流名称" value={name} disabled={!canEdit} maxLength={160}
        onChange={(event) => setName(event.target.value)} onBlur={commit}
        onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
      />
      <input
        className="workflow-desc-input" aria-label="工作流描述" value={description} disabled={!canEdit}
        placeholder="一句话说明这条流程做什么（可选）"
        onChange={(event) => setDescription(event.target.value)} onBlur={commit}
        onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
      />
    </header>
  );
}

/**
 * 运行配置：模型与提示词。
 *
 * 工作流自己没有"智能体"这一层，但对话必须有模型和系统提示词，
 * 所以这几项直接挂在工作中作为它的默认运行配置（后端写进宿主智能体）。
 */
function WorkflowRuntime({ workflow, token, canEdit, models = [], userModels = [], onSaved, onChat }) {
  const [open, setOpen] = useState(false);
  const [selection, setSelection] = useState(
    workflow.user_model_config_id ? `user:${workflow.user_model_config_id}`
      : (workflow.system_prompt != null && workflow.model ? '' : ''),
  );
  const [prompt, setPrompt] = useState(workflow.system_prompt || '');
  const [temperature, setTemperature] = useState(workflow.temperature ?? 0.4);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    setSelection(workflow.user_model_config_id ? `user:${workflow.user_model_config_id}` : '');
    setPrompt(workflow.system_prompt || '');
    setTemperature(workflow.temperature ?? 0.4);
    setMessage('');
    setError('');
  }, [workflow.id]);

  async function save() {
    const [scope, id] = String(selection || '').split(':');
    const patch = { system_prompt: prompt, temperature: Number(temperature) || 0.4 };
    if (scope === 'user' && id) {
      patch.user_model_config_id = Number(id);
      patch.model_id = null;
    } else if (scope === 'system' && id) {
      patch.model_id = Number(id);
      patch.user_model_config_id = null;
    }
    setSaving(true); setError(''); setMessage('');
    try {
      const data = await api(`/api/workflows/${workflow.id}`, { token, method: 'PATCH', body: patch });
      setMessage('已保存，直接对话就会用这套配置。');
      onSaved?.(data.workflow);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="workflow-runtime">
      <header>
        <button type="button" className="workflow-runtime-toggle" onClick={() => setOpen((value) => !value)}>
          <strong>运行配置</strong>
          <small>{workflow.user_model_config_id
            ? (userModels.find((m) => m.id === workflow.user_model_config_id)?.display_name || `我的模型 #${workflow.user_model_config_id}`)
            : '未设置模型'}</small>
        </button>
        {onChat && (
          <button type="button" className="primary" onClick={onChat}>去对话</button>
        )}
      </header>
      {open && (
        <div className="workflow-runtime-body">
          <label>
            <span>对话模型</span>
            <select value={selection} disabled={!canEdit} onChange={(event) => setSelection(event.target.value)}>
              <option value="">选择模型</option>
              {userModels.filter((model) => model.enabled).length > 0 && (
                <optgroup label="我的模型">
                  {userModels.filter((model) => model.enabled).map((model) => (
                    <option key={`user-${model.id}`} value={`user:${model.id}`}>{model.display_name || model.chat_model}</option>
                  ))}
                </optgroup>
              )}
              <optgroup label="平台模型">
                {models.map((model) => (
                  <option key={`system-${model.id}`} value={`system:${model.id}`}>{model.display_name || model.model_name}</option>
                ))}
              </optgroup>
            </select>
          </label>
          <label>
            <span>系统提示词（可选）</span>
            <textarea rows={4} value={prompt} disabled={!canEdit} placeholder="这条流程整体的角色与约束"
              onChange={(event) => setPrompt(event.target.value)} />
          </label>
          <label className="workflow-runtime-temp">
            <span>温度</span>
            <input type="number" min={0} max={2} step={0.1} value={temperature} disabled={!canEdit}
              onChange={(event) => setTemperature(event.target.value)} />
          </label>
          {canEdit && <button type="button" className="primary" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存运行配置'}</button>}
          {message && <p className="workflow-editor-success">{message}</p>}
          {error && <p className="workflow-editor-error">{error}</p>}
        </div>
      )}
    </div>
  );
}
