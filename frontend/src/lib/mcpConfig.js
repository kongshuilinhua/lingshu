export function parseStdioConfigs(text) {
  let data;
  try { data = JSON.parse(text); } catch { throw new Error('MCP 配置不是有效的 JSON。'); }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('请提供 MCP 配置对象。');
  const entries = data.mcpServers ? Object.entries(data.mcpServers) : [[data.name || '本地 MCP', data]];
  if (!entries.length || entries.length > 50) throw new Error('配置中应包含 1 到 50 个 MCP 服务。');
  return entries.map(([name, config]) => {
    if (!config || typeof config.command !== 'string' || !config.command.trim()) throw new Error(`${name} 缺少 stdio 启动命令。`);
    const args = config.args || [];
    const env = config.env || {};
    if (!Array.isArray(args) || args.some((arg) => typeof arg !== 'string')) throw new Error(`${name} 的 args 必须是字符串数组。`);
    if (typeof env !== 'object' || Array.isArray(env) || Object.values(env).some((value) => typeof value !== 'string')) throw new Error(`${name} 的 env 必须是字符串键值对象。`);
    return { name, command: config.command.trim(), args, env };
  });
}
