import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { Plus, Trash2, Workflow as WorkflowIcon } from 'lucide-react';
import { api, errorMessage } from '../utils.js';

const WorkflowCanvas = lazy(() => import('../components/WorkflowCanvas.jsx').then((module) => ({ default: module.WorkflowCanvas })));

/**
 * 独立工作流：左侧一级资源，自成入口。
 *
 * 与智能体内置流程的区别是「归属于谁」——这里的工作流属于工作区，
 * 可以被多个智能体通过 `workflow_id` 引用；内置流程仍是智能体的一个配置字段。
 */
export function WorkflowsHome({ token, agents = [], workflows: initialWorkflows, reloadWorkflows, canEdit = true }) {
  const [workflows, setWorkflows] = useState(initialWorkflows || []);
  const [activeId, setActiveId] = useState(null);
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
        <button className="primary" type="button" disabled={!canEdit} onClick={createWorkflow}>
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
              <button type="button" className="workflow-list-main" onClick={() => setActiveId(item.id)}>
                <WorkflowIcon size={15} />
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.node_count} 个节点</small>
                </span>
              </button>
              <button type="button" aria-label="删除工作流" disabled={!canEdit} onClick={() => removeWorkflow(item)}>
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </aside>

        <section className="workflow-stage">
          {active ? (
            <>
              <header className="workflow-stage-head">
                <strong>{active.name}</strong>
                <small>{active.description || '在画布里连线编排流程'}</small>
              </header>
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
          <p className="workflow-test-answer">{result.answer || '（没有输出）'}</p>
        </div>
      )}
    </div>
  );
}
