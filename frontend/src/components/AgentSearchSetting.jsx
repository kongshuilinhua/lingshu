import React from 'react';

export function AgentSearchSetting({ policy = {}, onChange, disabled = false }) {
  const enabled = Boolean(policy.web_search_enabled);
  function change(value) {
    const names = policy.allowed_tool_names || [];
    onChange({ ...policy, web_search_enabled: value,
      allowed_tool_names: value && names.length ? [...new Set([...names, 'web_search'])] : names });
  }
  return <section className="mcp-binding-panel">
    <div className="mcp-binding-header"><strong>联网搜索</strong></div>
    <label className="mcp-binding-tool"><input aria-label="允许联网搜索" type="checkbox" checked={enabled} disabled={disabled} onChange={(event) => change(event.target.checked)} /><span><strong>允许联网搜索 · {enabled ? '开启' : '关闭'}</strong><small>开启后模型可按需使用平台搜索。保存到当前 Agent，发布后按发布版本生效。</small></span></label>
  </section>;
}
