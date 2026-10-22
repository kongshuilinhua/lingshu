import React from 'react';
import { Plus } from 'lucide-react';
import { AgentAvatar } from '../components/AgentAvatar.jsx';
import { statusLabel } from '../utils.js';

export function AgentsHome({ agents, activeAgentId, canManage, createAgent, deleteAgent, me, openBuilder, setActiveAgentId }) {
  return (
    <div className="content-page">
      <header className="page-heading">
        <div>
          <h1>智能体</h1>
          <p>创建、选择和编辑你的智能体。</p>
        </div>
        <button className="primary" type="button" onClick={() => createAgent(true)}><Plus size={16} />创建智能体</button>
      </header>
      <div className="agent-grid">
        {agents.map((agent) => (
          <article className={`agent-card ${agent.id === activeAgentId ? 'active' : ''}`} key={agent.id}>
            <AgentAvatar value={agent.avatar} />
            <h3>{agent.name}</h3>
            <p>{agent.description || '暂无简介'}</p>
            <small className={`status-pill ${agent.status}`}>{statusLabel(agent.status)}</small>
            <div>
              <button type="button" onClick={() => setActiveAgentId(agent.id)}>设为当前</button>
              <button type="button" disabled={!canManage && agent.created_by !== me?.id} onClick={() => openBuilder(agent.id)}>编辑</button>
              {!agent.is_template && (
                <button className="danger-light" type="button" disabled={!canManage && agent.created_by !== me?.id}
                  onClick={() => deleteAgent(agent).catch((err) => console.error(err))}>删除</button>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}