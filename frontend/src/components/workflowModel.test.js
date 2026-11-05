import { describe, expect, it } from 'vitest';
import {
  deriveEdges, hasEdge, reaches, removeNodeAt, validateWorkflow,
} from './workflowModel.js';

const defaultNodes = [
  { id: 'start', type: 'Start', config: {} },
  { id: 'knowledge', type: 'Knowledge', config: { top_k: 4 } },
  { id: 'tool', type: 'Tool', config: { tools: [] } },
  { id: 'llm', type: 'LLM', config: {} },
  { id: 'answer', type: 'Answer', config: {} },
];

describe('工作流图结构推导', () => {
  it('未显式配置跳转时按数组顺序连成一条链', () => {
    const edges = deriveEdges(defaultNodes);
    expect(edges.map((edge) => `${edge.from}->${edge.to}`)).toEqual([
      'start->knowledge', 'knowledge->tool', 'tool->llm', 'llm->answer',
    ]);
    expect(edges.every((edge) => edge.implicit)).toBe(true);
  });

  it('显式 next 优先于顺序，且不再画兜底边', () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {}, next: 'llm' },
      { id: 'knowledge', type: 'Knowledge', config: {} },
      { id: 'llm', type: 'LLM', config: {} },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    const edges = deriveEdges(nodes);
    expect(edges.some((edge) => edge.from === 'start' && edge.to === 'llm' && !edge.implicit)).toBe(true);
    expect(edges.some((edge) => edge.from === 'start' && edge.to === 'knowledge')).toBe(false);
    // knowledge 没有 next，仍会顺序连到 llm
    expect(edges.some((edge) => edge.from === 'knowledge' && edge.to === 'llm' && edge.implicit)).toBe(true);
  });

  it('条件节点把分支和否则都展开成边，并带上条件描述', () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {} },
      {
        id: 'cond', type: 'Condition', config: {
          branches: [{ when: { field: 'rag_status.matched_chunks', gte: 1 }, next: 'llm' }],
          default: 'answer',
        },
      },
      { id: 'llm', type: 'LLM', config: {} },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    const edges = deriveEdges(nodes).filter((edge) => edge.from === 'cond');
    expect(edges).toHaveLength(2);
    expect(edges[0].label).toBe('rag_status.matched_chunks 大于等于 1');
    expect(edges[0].to).toBe('llm');
    expect(edges[1].label).toBe('否则');
    expect(edges[1].to).toBe('answer');
  });

  it('输出回答节点不画出边', () => {
    const edges = deriveEdges(defaultNodes);
    expect(edges.some((edge) => edge.from === 'answer')).toBe(false);
  });
});

describe('连线校验', () => {
  it('能识别成环，阻止执行器永远跑不到的图', () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {}, next: 'llm' },
      { id: 'llm', type: 'LLM', config: {}, next: 'answer' },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    // answer 已经能到 llm 吗？不能；llm 能到 answer 吗？能
    expect(reaches(nodes, 'llm', 'answer')).toBe(true);
    expect(reaches(nodes, 'answer', 'llm')).toBe(false);
    // 若从 answer 连回 llm 就成环
    expect(reaches([...nodes, { id: 'x', type: 'LLM', config: {}, next: 'llm' }], 'x', 'start')).toBe(false);
  });

  it('同一对节点重复连线会被识别', () => {
    expect(hasEdge(defaultNodes, 'start', 'knowledge')).toBe(true);
    expect(hasEdge(defaultNodes, 'start', 'llm')).toBe(false);
  });
});

describe('节点删除与校验', () => {
  it('删除节点时清掉指向它的 next 和条件分支', () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {}, next: 'llm' },
      { id: 'llm', type: 'LLM', config: {} },
      { id: 'cond', type: 'Condition', config: { branches: [{ when: { field: 'x', gte: 1 }, next: 'llm' }], default: 'llm' } },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    const next = removeNodeAt(nodes, 1);
    expect(next.map((node) => node.id)).toEqual(['start', 'cond', 'answer']);
    expect(next[0].next).toBeUndefined();
    expect(next[1].config.branches).toEqual([]);
    expect(next[1].config.default).toBe('');
  });

  it('缺少开始或输出回答时报错', () => {
    expect(validateWorkflow([{ id: 'llm', type: 'LLM', config: {} }])).toEqual(
      expect.arrayContaining(['缺少「开始」节点', '缺少「输出回答」节点']),
    );
    expect(validateWorkflow(defaultNodes)).toEqual([]);
  });

  it('条件分支缺字段或指向不存在的节点时报错', () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {} },
      { id: 'cond', type: 'Condition', config: { branches: [{ when: { field: '' }, next: 'ghost' }], default: '' } },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    expect(validateWorkflow(nodes)).toEqual(
      expect.arrayContaining(['条件分支缺少判断字段', '条件分支的目标节点不存在']),
    );
  });
});

describe('循环节点', () => {
  it('循环体不能包含开始或输出回答', () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {} },
      { id: 'loop', type: 'Loop', config: { body: [{ id: 'step1', type: 'Answer' }], max_iterations: 3 } },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    expect(validateWorkflow(nodes)).toEqual(expect.arrayContaining(['循环体内不能包含开始或输出回答节点']));
  });

  it('空循环体报错，正常循环体通过', () => {
    const base = [
      { id: 'start', type: 'Start', config: {} },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    expect(validateWorkflow([...base, { id: 'loop', type: 'Loop', config: { body: [] } }]))
      .toEqual(expect.arrayContaining(['循环节点缺少循环体']));
    expect(validateWorkflow([...base, {
      id: 'loop', type: 'Loop', config: { body: [{ id: 'step1', type: 'LLM' }], max_iterations: 3 },
    }])).toEqual([]);
  });

  it('循环节点按顺序连到下一个节点', () => {
    const nodes = [
      { id: 'start', type: 'Start', config: {} },
      { id: 'loop', type: 'Loop', config: { body: [{ id: 'step1', type: 'LLM' }] } },
      { id: 'answer', type: 'Answer', config: {} },
    ];
    const edges = deriveEdges(nodes);
    expect(edges.some((edge) => edge.from === 'loop' && edge.to === 'answer')).toBe(true);
  });
});
