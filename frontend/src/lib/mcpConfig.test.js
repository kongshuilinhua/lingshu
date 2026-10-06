import { describe, expect, it } from 'vitest';
import { parseStdioConfigs } from './mcpConfig.js';

describe('stdio JSON 导入', () => {
  it('读取多个 MCP 配置，保留带空格路径、参数和环境变量', () => {
    const configs = parseStdioConfigs(JSON.stringify({ mcpServers: {
      one: { command: 'C:/Program Files/Python/python.exe', args: ['server.py', 'a b'], env: { TOKEN: 'test' } },
      two: { command: 'uvx', args: ['server'] },
    } }));
    expect(configs).toHaveLength(2);
    expect(configs[0].args).toEqual(['server.py', 'a b']);
    expect(configs[0].env).toEqual({ TOKEN: 'test' });
  });
  it('拒绝无效 JSON、命令和参数', () => {
    expect(() => parseStdioConfigs('{')).toThrow('有效的 JSON');
    expect(() => parseStdioConfigs('{}')).toThrow('启动命令');
    expect(() => parseStdioConfigs('{"command":"python","args":"server.py"}')).toThrow('字符串数组');
  });
});
