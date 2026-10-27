/**
 * 工作流编排的共享模型：节点类型、默认配置、条件谓词、图结构推导。
 *
 * 表单编辑器与画布视图都从这里取，保证两种视图对同一份 JSON 的理解完全一致。
 * 执行语义以 core/runtime/graph.py 为准，本文件只是它的前端镜像。
 */

export const NODE_TYPES = [
  { type: 'Start', label: '开始', hint: '接收用户输入，必须且只能有一个' },
  { type: 'Knowledge', label: '知识检索', hint: '从绑定的知识库召回片段' },
  { type: 'Tool', label: '工具', hint: '让模型决定是否调用已绑定的工具' },
  { type: 'LLM', label: '模型生成', hint: '基于上文生成回答草稿' },
  { type: 'HTTP', label: 'HTTP 请求', hint: '调用外部 HTTPS 接口，结果写入上下文' },
  { type: 'Code', label: '代码执行', hint: '在 Docker 沙箱里跑 Python / JavaScript' },
  { type: 'VariableAssign', label: '变量赋值', hint: '把上游结果存成变量供后续引用' },
  { type: 'VariableAggregate', label: '变量聚合', hint: '多条分支汇合：取第一个有值的结果存成一个变量' },
  { type: 'ParameterExtract', label: '参数提取', hint: '用模型把自然语言抽成结构化字段' },
  { type: 'Classifier', label: '问题分类', hint: '用模型判断问题属于哪一类并跳转' },
  { type: 'Condition', label: '条件分支', hint: '按上下文取值决定下一跳' },
  { type: 'HumanApproval', label: '人工审批', hint: '暂停运行，等待人工放行或驳回' },
  { type: 'Loop', label: '循环 / 迭代', hint: '重复执行一组节点；遍历数组时可并行，每轮结果收成数组' },
  { type: 'Agent', label: '智能体', hint: '在这一步调用另一个已发布的智能体' },
  { type: 'Answer', label: '输出回答', hint: '把草稿作为最终回答输出' },
];

export const TYPE_LABELS = Object.fromEntries(NODE_TYPES.map((item) => [item.type, item.label]));

export const PREDICATES = [
  { op: 'eq', label: '等于' },
  { op: 'ne', label: '不等于' },
  { op: 'gt', label: '大于' },
  { op: 'gte', label: '大于等于' },
  { op: 'lt', label: '小于' },
  { op: 'lte', label: '小于等于' },
  { op: 'exists', label: '存在' },
];

// 常用字段：给个候选，但允许手填（上下文里任何字段都能作为条件）
export const COMMON_FIELDS = [
  'rag_status.matched_chunks',
  'rag_enabled',
  'search_enabled',
  'tool_stats.total_calls',
  'rag_refusal',
];

export function newId(type, nodes) {
  const base = type.toLowerCase();
  let index = 1;
  while (nodes.some((node) => node.id === `${base}${index}`)) index += 1;
  return `${base}${index}`;
}

export function defaultConfig(type) {
  if (type === 'Knowledge') return { top_k: 4 };
  if (type === 'Tool') return { tools: [] };
  if (type === 'Condition') return { branches: [], default: '' };
  if (type === 'HumanApproval') return { prompt: '请确认是否继续执行' };
  if (type === 'HTTP') {
    return { method: 'GET', url: '', headers: {}, body: '', response_path: '$', timeout_seconds: 10 };
  }
  if (type === 'Code') return { language: 'python', code: '' };
  if (type === 'VariableAssign') return { assignments: [] };
  if (type === 'VariableAggregate') return { sources: ['', ''], output_var: 'result' };
  if (type === 'ParameterExtract') return { instruction: '', source: '{{input}}', schema: {} };
  if (type === 'Loop') return { body: [], items: '', loop_var: 'item', max_iterations: 3, stop_when: null };
  if (type === 'Agent') return { agent_id: null, input: '{{input}}' };
  if (type === 'Classifier') return { classes: [], default: '' };
  return {};
}

export function predicateOf(when) {
  if (!when) return 'gte';
  for (const item of PREDICATES) {
    if (item.op === 'exists' && 'exists' in when) return 'exists';
    if (item.op !== 'exists' && item.op in when) return item.op;
  }
  return 'gte';
}

/** 条件的人类可读描述：`rag_status.matched_chunks 大于等于 1` */
export function describeCondition(when) {
  if (!when?.field) return '未设置条件';
  const op = predicateOf(when);
  if (op === 'exists') return `${when.field} ${when.exists ? '存在' : '不存在'}`;
  const label = PREDICATES.find((item) => item.op === op)?.label || op;
  return `${when.field} ${label} ${when[op]}`;
}

/**
 * 把节点列表展开成有向边。
 *
 * 三种来源：
 * 1. Condition 节点：config.branches 的每条（带条件描述）+ config.default（「否则」）；
 * 2. 普通节点：显式 next；
 * 3. 都没有时，按数组顺序连到下一个节点 —— 执行器就是这么跑的，
 *    画布上必须画出来，否则默认工作流会显示成五个孤立节点。
 *
 * `implicit` 标记用于区分「用户显式配置的跳转」和「顺序兜底」，
 * 画布上画成虚线，且不能被删（删了也还是按顺序跑，容易误导）。
 */
export function deriveEdges(nodes) {
  const edges = [];
  nodes.forEach((node, index) => {
    if (node.type === 'Answer') return;
    if (node.type === 'Condition' || node.type === 'Classifier') {
      const config = node.config || {};
      (config.branches || []).forEach((branch, branchIndex) => {
        if (!branch.next) return;
        edges.push({
          id: `${node.id}->${branch.next}#b${branchIndex}`,
          from: node.id,
          to: branch.next,
          label: node.type === 'Classifier'
            ? (branch.label || describeCondition(branch.when))
            : describeCondition(branch.when),
          implicit: false,
          branchIndex,
        });
      });
      if (config.default) {
        edges.push({
          id: `${node.id}->${config.default}#default`,
          from: node.id,
          to: config.default,
          label: '否则',
          implicit: false,
          defaultBranch: true,
        });
      } else {
        const next = nodes[index + 1];
        if (next) {
          edges.push({
            id: `${node.id}->${next.id}#implicit`,
            from: node.id, to: next.id, label: '否则（顺序）', implicit: true,
          });
        }
      }
      return;
    }
    if (node.next) {
      edges.push({ id: `${node.id}->${node.next}`, from: node.id, to: node.next, implicit: false });
      return;
    }
    const next = nodes[index + 1];
    if (next) {
      edges.push({ id: `${node.id}->${next.id}#implicit`, from: node.id, to: next.id, implicit: true });
    }
  });
  return edges;
}

/** 与后端 validate_graph 对齐的本地校验 */
export function validateWorkflow(nodes) {
  const problems = [];
  const ids = nodes.map((node) => node.id);
  if (new Set(ids).size !== ids.length) problems.push('节点 id 不能重复');
  const types = new Set(nodes.map((node) => node.type));
  if (!types.has('Start')) problems.push('缺少「开始」节点');
  if (!types.has('Answer')) problems.push('缺少「输出回答」节点');
  for (const node of nodes) {
    if (node.next && !ids.includes(node.next)) problems.push(`「${TYPE_LABELS[node.type] || node.type}」的跳转目标不存在`);
    if (node.type === 'Condition') {
      const config = node.config || {};
      for (const branch of config.branches || []) {
        if (!branch.when?.field) problems.push('条件分支缺少判断字段');
        if (!branch.next) problems.push('条件分支缺少目标节点');
        else if (!ids.includes(branch.next)) problems.push('条件分支的目标节点不存在');
      }
      if (config.default && !ids.includes(config.default)) problems.push('条件分支的「否则」目标不存在');
    }
    if (node.type === 'Classifier') {
      const config = node.config || {};
      if (!(config.classes || []).length) problems.push('问题分类至少需要一个分类');
      for (const item of config.classes || []) {
        if (!item.label) problems.push('问题分类缺少分类名');
        if (item.next && !ids.includes(item.next)) problems.push('分类「' + item.label + '」的目标节点不存在');
      }
    }
    if (node.type === 'Loop') {
      const config = node.config || {};
      const body = config.body || [];
      if (!body.length) problems.push('循环节点缺少循环体');
      if (body.some((item) => item.type === 'Start' || item.type === 'Answer')) {
        problems.push('循环体内不能包含开始或输出回答节点');
      }
      if (config.items && !Array.isArray(config.items) && !String(config.items).trim().startsWith('{{')) {
        problems.push('循环的数组来源需要是数组或 {{变量}} 模板');
      }
    }
    if (node.type === 'HTTP' && !node.config?.url) problems.push('HTTP 节点缺少 url');
    if (node.type === 'Code' && !node.config?.code) problems.push('代码节点缺少代码');
    if (node.type === 'VariableAssign' && !(node.config?.assignments || []).length) problems.push('变量赋值至少需要一项');
    if (node.type === 'VariableAggregate' && !(node.config?.sources || []).some((item) => String(item || '').trim())) problems.push('变量聚合至少需要一个来源');
    if (node.type === 'VariableAggregate' && !String(node.config?.output_var || '').trim()) problems.push('变量聚合需要输出变量名');
    if (node.type === 'ParameterExtract' && !Object.keys(node.config?.schema || {}).length) problems.push('参数提取至少需要一个字段');
    if (node.type === 'Agent' && !node.config?.agent_id) problems.push('智能体节点需要选择目标智能体');
  }
  return problems;
}

/** 删除节点时清理所有指向它的跳转，避免留下悬空边 */
export function removeNodeAt(nodes, index) {
  const removed = nodes[index];
  const next = nodes.filter((_, position) => position !== index);
  return next.map((node) => {
    const cleaned = { ...node };
    if (cleaned.next === removed.id) delete cleaned.next;
    if (cleaned.type === 'Condition') {
      const config = { ...(cleaned.config || {}) };
      config.branches = (config.branches || []).filter((branch) => branch.next !== removed.id);
      if (config.default === removed.id) config.default = '';
      cleaned.config = config;
    }
    if (cleaned.type === 'Classifier') {
      const config = { ...(cleaned.config || {}) };
      config.classes = (config.classes || []).map((item) => (
        item.next === removed.id ? { ...item, next: '' } : item
      ));
      if (config.default === removed.id) config.default = '';
      cleaned.config = config;
    }
    return cleaned;
  });
}

/** 判断两节点之间是否已存在任意边（显式或顺序兜底） */
export function hasEdge(nodes, fromId, toId) {
  return deriveEdges(nodes).some((edge) => edge.from === fromId && edge.to === toId);
}

/** 从 from 出发是否能到达 to（用于阻止连线成环） */
export function reaches(nodes, fromId, toId, seen = new Set()) {
  if (fromId === toId) return true;
  if (seen.has(fromId)) return false;
  seen.add(fromId);
  return deriveEdges(nodes)
    .filter((edge) => edge.from === fromId)
    .some((edge) => reaches(nodes, edge.to, toId, seen));
}
