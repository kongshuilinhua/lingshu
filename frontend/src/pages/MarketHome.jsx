import React from 'react';
import { Bot, Boxes, Database, FileText, Sparkles, Wand2 } from 'lucide-react';
import { AgentAvatar } from '../components/AgentAvatar.jsx';
import { McpResourceCatalog } from './McpResourceCatalog.jsx';
import { ResourceLibraryHome } from './ResourceLibraryHome.jsx';
import { SkillsHome } from './SkillsHome.jsx';
import './MarketHome.css';
import { useUnsavedNavigation } from '../components/UnsavedChanges.jsx';

const MARKET_SECTIONS = [
  { id: 'agents', label: '智能体', description: '复用已发布的智能体', icon: Bot, scopes: ['discover'] },
  { id: 'mcp', label: 'MCP 服务', description: '外部服务和工具连接', icon: Boxes, scopes: ['discover', 'mine'] },
  { id: 'tools', label: '工具', description: '工作区可调用的工具', icon: Wand2, scopes: ['mine'] },
  { id: 'knowledge', label: '知识库', description: '用于检索的文档资料', icon: Database, scopes: ['mine'] },
  { id: 'prompts', label: '提示词', description: '创建和复用提示词模板', icon: FileText, scopes: ['mine'] },
  { id: 'skills', label: 'Skill', description: '按需加载的任务技能', icon: Sparkles, scopes: ['discover', 'mine'] },
];

export function MarketHome({ agents, canManage, copyMarketAgent, marketTab, setMarketTab,
  marketScope = 'discover', setMarketScope, resourcesProps, token }) {
  const confirmNavigation = useUnsavedNavigation();
  function changeScope(scope) {
    setMarketScope(scope);
    if (!MARKET_SECTIONS.some((item) => item.id === marketTab && item.scopes.includes(scope))) {
      setMarketTab(scope === 'mine' ? 'mcp' : 'agents');
    }
  }

  return (
    <div className="content-page market-page">
      <header className="market-heading">
        <h1>市场</h1>
        <p>发现可用能力，接入工作区，再统一管理自己的资源。</p>
      </header>

      <nav className="market-scopes" aria-label="资源视图">
        <button type="button" className={marketScope === 'discover' ? 'active' : ''} aria-pressed={marketScope === 'discover'} onClick={() => marketScope !== 'discover' && confirmNavigation(() => changeScope('discover'))}>发现</button>
        <button type="button" className={marketScope === 'mine' ? 'active' : ''} aria-pressed={marketScope === 'mine'} onClick={() => marketScope !== 'mine' && confirmNavigation(() => changeScope('mine'))}>我的资源</button>
      </nav>

      <nav className="market-sections" aria-label="市场分类">
        {MARKET_SECTIONS.filter((item) => item.scopes.includes(marketScope)).map(({ id, label, description, icon: Icon, disabled }) => (
          <button
            key={id}
            type="button"
            className={`market-section ${marketTab === id ? 'active' : ''}`}
            aria-current={marketTab === id ? 'page' : undefined}
            disabled={disabled}
            onClick={() => marketTab !== id && confirmNavigation(() => setMarketTab(id))}
          >
            <span className="market-section-icon"><Icon size={19} strokeWidth={1.8} /></span>
            <span className="market-section-copy"><strong>{label}</strong><small>{disabled ? '即将开放' : description}</small></span>
          </button>
        ))}
      </nav>

      {marketTab === 'skills' ? <SkillsHome token={token} canManage={canManage} mode={marketScope} onManage={() => setMarketScope('mine')} requestDeleteConfirm={resourcesProps?.requestDeleteConfirm} /> : marketTab === 'mcp' ? (
        <McpResourceCatalog token={token} canManage={canManage}
          mode={marketScope === 'mine' ? 'connections' : 'discover'}
          onOpenDiscovery={() => setMarketScope('discover')}
          onOpenResources={() => setMarketScope('mine')}
          onConnected={() => setMarketScope('mine')} />
      ) : marketScope === 'mine' ? (
        <ResourceLibraryHome key={marketTab} {...resourcesProps} embedded resourceTab={marketTab} setResourceTab={setMarketTab} />
      ) : (
        <section className="market-agents" aria-label="智能体市场">
          <div className="market-agents-heading">
            <div><h2>智能体</h2></div>
            <p>审核通过后上架；复制到工作区即可继续配置。</p>
          </div>
          <div className="agent-grid">
            {agents.map((agent) => (
              <article className="agent-card" key={agent.id}>
                <AgentAvatar value={agent.avatar} />
                <h3>{agent.name}</h3>
                <p>{agent.description || '暂无简介'}</p>
                <small className="status-pill published">版本 {agent.version || '-'}</small>
                <div>
                  <button type="button" onClick={() => copyMarketAgent(agent.id).catch((err) => console.error(err))}>复制使用</button>
                </div>
              </article>
            ))}
            {agents.length === 0 && <p className="empty-state">市场里还没有审核通过的智能体。</p>}
          </div>
        </section>
      )}
    </div>
  );
}
