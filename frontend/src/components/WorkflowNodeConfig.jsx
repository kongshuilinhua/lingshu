import React from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { MetadataFilterEditor } from './MetadataFilterEditor.jsx';
import { COMMON_FIELDS, NODE_TYPES, PREDICATES, TYPE_LABELS, predicateOf } from './workflowModel.js';

/**
 * 节点参数面板：表单编辑器与画布共用。
 *
 * 只负责「编辑当前节点的 config」，不关心自己嵌在列表里还是画布右侧的检查器里——
 * 两种视图必须产出完全相同的 JSON，否则同一份工作流换个视图就会变样。
 * 因此这里改用函数式导入而非内联定义。
 */
export function NodeConfig({ node, index, nodes, nodeIds, tools, agents = [], canEdit, saving, onUpdate, onUpdateConfig, onAddBranch, onUpdateBranch, onRemoveBranch }) {
  const disabled = !canEdit || saving;
  const config = node.config || {};

  return (
    <>
      {node.type === 'Agent' && (
        <div className="workflow-field">
          <label>
            <span>目标智能体（只能用已发布的）</span>
            <select
              value={config.agent_id ?? ''} disabled={disabled}
              onChange={(event) => onUpdateConfig(index, { agent_id: event.target.value ? Number(event.target.value) : null })}
            >
              <option value="">请选择</option>
              {agents
                .filter((agent) => agent.status === 'published')
                .map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
            </select>
          </label>
          <label>
            <span>交给它的输入（支持 {'{{变量}}'} 模板）</span>
            <input
              value={config.input ?? '{{input}}'} disabled={disabled} placeholder="{{input}}"
              onChange={(event) => onUpdateConfig(index, { input: event.target.value })}
            />
          </label>
          <small className="workflow-note">
            这一步会完整跑一次该智能体（含它自己的知识库和工具），结果写入 {'{{agent_result}}'}。串多个智能体会叠加耗时与 token。
          </small>
        </div>
      )}

      {node.type === 'LLM' && (
        <label className="workflow-field">
          <span>这一步的提示词（可选，支持 {'{{变量}}'}；留空则直接回答用户原话）</span>
          <textarea
            rows={3} value={config.prompt || ''} disabled={disabled}
            placeholder="例如：把「{{variables.item}}」翻译成英文"
            onChange={(event) => onUpdateConfig(index, { prompt: event.target.value })}
          />
        </label>
      )}

      {node.type === 'Knowledge' && (
        <>
          <label className="workflow-field">
            <span>召回片段数</span>
            <input
              type="number" min={1} max={20} value={config.top_k ?? 4} disabled={disabled}
              onChange={(event) => onUpdateConfig(index, { top_k: Number(event.target.value) || 1 })}
            />
          </label>
          <div className="workflow-field">
            <span>元数据过滤（只在满足条件的文档里检索）</span>
            <MetadataFilterEditor
              value={config.metadata_filter} disabled={disabled} valueHint="取值，可写 {{variables.产品}}"
              onChange={(next) => onUpdateConfig(index, { metadata_filter: next })}
            />
          </div>
        </>
      )}

      {node.type === 'HumanApproval' && (
        <label className="workflow-field">
          <span>审批提示</span>
          <input
            value={config.prompt || ''} disabled={disabled}
            placeholder="请确认是否继续执行"
            onChange={(event) => onUpdateConfig(index, { prompt: event.target.value })}
          />
        </label>
      )}

      {node.type === 'Tool' && (
        <div className="workflow-field">
          <span>限定可用工具（不选表示不限制）</span>
          <div className="workflow-tool-options">
            {tools.length === 0 && <small>这个智能体还没有绑定工具。</small>}
            {tools.map((tool) => {
              const selected = (config.tools || []).includes(tool.name);
              return (
                <label key={tool.id || tool.name} className="workflow-check">
                  <input
                    type="checkbox" checked={selected} disabled={disabled}
                    onChange={() => {
                      const current = config.tools || [];
                      onUpdateConfig(index, {
                        tools: selected ? current.filter((name) => name !== tool.name) : [...current, tool.name],
                      });
                    }}
                  />
                  {tool.label || tool.name}
                </label>
              );
            })}
          </div>
        </div>
      )}

      {node.type === 'Loop' && (
        <div className="workflow-field">
          <label>
            <span>遍历数组（留空表示固定次数）</span>
            <input value={typeof config.items === 'string' ? config.items : JSON.stringify(config.items || '')}
              disabled={disabled} placeholder="{{variables.list}} 或 {{http.json.items}}"
              onChange={(event) => {
                const raw = event.target.value;
                const trimmed = raw.trim();
                // 以 {{ 开头的是模板，保留字符串；其余尝试解析成数组字面量
                if (!trimmed || trimmed.startsWith('{{')) {
                  onUpdateConfig(index, { items: raw });
                  return;
                }
                try {
                  const parsed = JSON.parse(trimmed);
                  onUpdateConfig(index, { items: Array.isArray(parsed) ? parsed : raw });
                } catch {
                  onUpdateConfig(index, { items: raw });
                }
              }} />
          </label>
          <div className="workflow-row">
            <label>
              <span>循环变量名</span>
              <input value={config.loop_var || 'item'} disabled={disabled}
                onChange={(event) => onUpdateConfig(index, { loop_var: event.target.value })} />
            </label>
            <label>
              <span>最多迭代次数</span>
              <input type="number" min={1} max={20} value={config.max_iterations ?? 3} disabled={disabled}
                onChange={(event) => onUpdateConfig(index, { max_iterations: Number(event.target.value) || 1 })} />
            </label>
          </div>
          <div className="workflow-row">
            <label>
              <span>并行数（遍历数组时）</span>
              <input type="number" min={1} max={10} value={config.parallel ?? 1} disabled={disabled}
                aria-label="并行数"
                onChange={(event) => onUpdateConfig(index, { parallel: Math.min(10, Math.max(1, Number(event.target.value) || 1)) })} />
            </label>
            <label>
              <span>某一项失败时</span>
              <select value={config.on_error || 'stop'} disabled={disabled} aria-label="失败策略"
                onChange={(event) => onUpdateConfig(index, { on_error: event.target.value })}>
                <option value="stop">整个节点失败</option>
                <option value="skip">跳过该项</option>
                <option value="null">该项结果置空</option>
              </select>
            </label>
          </div>
          <div className="workflow-row">
            <label>
              <span>每轮收集的字段</span>
              <input value={config.output_key || 'draft'} disabled={disabled} placeholder="draft"
                onChange={(event) => onUpdateConfig(index, { output_key: event.target.value })} />
            </label>
            <label>
              <span>结果数组写入变量</span>
              <input value={config.result_var || 'results'} disabled={disabled} placeholder="results"
                onChange={(event) => onUpdateConfig(index, { result_var: event.target.value })} />
            </label>
          </div>
          <small className="workflow-note">
            每轮的 {`{{${config.output_key || 'draft'}}}`} 按原顺序收成数组，下游用 {`{{variables.${config.result_var || 'results'}}}`} 取。
            并行数大于 1 时各项互不可见，「提前停止」条件不生效。
          </small>
          <span>循环体（按顺序执行的节点）</span>
          <div className="workflow-branches">
            {(config.body || []).map((item, itemIndex) => (
              <div className="workflow-branch" key={item.id || itemIndex}>
                <span className="workflow-branch-index">{itemIndex + 1}</span>
                <select value={item.type || 'LLM'} disabled={disabled}
                  aria-label={`循环体第 ${itemIndex + 1} 个节点类型`}
                  onChange={(event) => {
                    const body = (config.body || []).map((row, position) => (
                      position === itemIndex ? { ...row, type: event.target.value } : row
                    ));
                    onUpdateConfig(index, { body });
                  }}>
                  {NODE_TYPES.filter((option) => option.type !== 'Start' && option.type !== 'Answer' && option.type !== 'Loop')
                    .map((option) => <option key={option.type} value={option.type}>{option.label}</option>)}
                </select>
                <input className="workflow-branch-value-grow" value={item.id || ''} disabled={disabled}
                  placeholder="节点 id" aria-label={`循环体第 ${itemIndex + 1} 个节点 id`}
                  onChange={(event) => {
                    const body = (config.body || []).map((row, position) => (
                      position === itemIndex ? { ...row, id: event.target.value } : row
                    ));
                    onUpdateConfig(index, { body });
                  }} />
                <button type="button" aria-label="删除循环体节点" disabled={disabled}
                  onClick={() => onUpdateConfig(index, {
                    body: (config.body || []).filter((_, position) => position !== itemIndex),
                  })}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <button type="button" className="workflow-add-branch" disabled={disabled}
              onClick={() => {
                const body = config.body || [];
                let counter = body.length + 1;
                while (body.some((row) => row.id === `step${counter}`)) counter += 1;
                onUpdateConfig(index, { body: [...body, { id: `step${counter}`, type: 'LLM', config: {} }] });
              }}>
              <Plus size={13} />添加循环体节点
            </button>
          </div>
          <small className="workflow-note">
            循环体内可用 {'{{loop.item}}'}、{'{{loop.iteration}}'}；最多 20 轮、120 个节点步、300 秒。
            循环里的 LLM 节点走非流式，最终回答由循环之后的节点输出。
          </small>
        </div>
      )}

      {node.type === 'HTTP' && (
        <div className="workflow-field">
          <div className="workflow-row">
            <label>
              <span>方法</span>
              <select value={config.method || 'GET'} disabled={disabled}
                onChange={(event) => onUpdateConfig(index, { method: event.target.value })}>
                {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <label>
              <span>超时（秒）</span>
              <input type="number" min={1} max={30} value={config.timeout_seconds ?? 10} disabled={disabled}
                onChange={(event) => onUpdateConfig(index, { timeout_seconds: Number(event.target.value) || 10 })} />
            </label>
          </div>
          <label>
            <span>URL（支持 {'{{变量}}'} 模板）</span>
            <input value={config.url || ''} disabled={disabled} placeholder="https://api.example.com/search?q={{input}}"
              onChange={(event) => onUpdateConfig(index, { url: event.target.value })} />
          </label>
          <label>
            <span>响应路径（留 $ 表示原样返回）</span>
            <input value={config.response_path ?? '$'} disabled={disabled} placeholder="$.data.items"
              onChange={(event) => onUpdateConfig(index, { response_path: event.target.value })} />
          </label>
          {['POST', 'PUT', 'PATCH'].includes(config.method || 'GET') && (
            <label>
              <span>请求体（JSON，支持 {'{{变量}}'} 模板）</span>
              <textarea rows={4} value={typeof config.body === 'string' ? config.body : JSON.stringify(config.body || {}, null, 2)}
                disabled={disabled} placeholder={'{"query": "{{input}}"}'}
                onChange={(event) => onUpdateConfig(index, { body: event.target.value })} />
            </label>
          )}
          <KeyValueRows
            label="请求头" rows={config.headers || {}} disabled={disabled} keyPlaceholder="Header 名" valuePlaceholder="值"
            onChange={(headers) => onUpdateConfig(index, { headers })}
          />
          <small className="workflow-note">只允许公网 HTTPS 地址；不会跟随重定向。</small>
        </div>
      )}

      {node.type === 'Code' && (
        <div className="workflow-field">
          <label>
            <span>语言</span>
            <select value={config.language || 'python'} disabled={disabled}
              onChange={(event) => onUpdateConfig(index, { language: event.target.value })}>
              <option value="python">Python</option>
              <option value="javascript">JavaScript</option>
            </select>
          </label>
          <label>
            <span>代码</span>
            <textarea rows={8} className="workflow-code" value={config.code || ''} disabled={disabled} spellCheck={false}
              placeholder={'# 从 /skill/scripts/input.json 读取上下文\n# 向 stdout 输出 JSON 作为节点结果'}
              onChange={(event) => onUpdateConfig(index, { code: event.target.value })} />
          </label>
          <small className="workflow-note">在 Docker 沙箱中执行：禁网、只读、60 秒上限。需要配置 SKILL_SANDBOX_IMAGE。</small>
        </div>
      )}

      {node.type === 'VariableAssign' && (
        <div className="workflow-field">
          <span>赋值（右侧支持 {'{{变量}}'} 模板）</span>
          <div className="workflow-branches">
            {(config.assignments || []).map((item, itemIndex) => (
              <div className="workflow-branch" key={itemIndex}>
                <input className="workflow-branch-field" value={item.name || ''} disabled={disabled} placeholder="变量名"
                  onChange={(event) => {
                    const assignments = (config.assignments || []).map((row, position) => (
                      position === itemIndex ? { ...row, name: event.target.value } : row
                    ));
                    onUpdateConfig(index, { assignments });
                  }} />
                <input className="workflow-branch-value-grow" value={item.value ?? ''} disabled={disabled} placeholder="{{http.json.title}}"
                  onChange={(event) => {
                    const assignments = (config.assignments || []).map((row, position) => (
                      position === itemIndex ? { ...row, value: event.target.value } : row
                    ));
                    onUpdateConfig(index, { assignments });
                  }} />
                <button type="button" aria-label="删除赋值" disabled={disabled}
                  onClick={() => onUpdateConfig(index, {
                    assignments: (config.assignments || []).filter((_, position) => position !== itemIndex),
                  })}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <button type="button" className="workflow-add-branch" disabled={disabled}
              onClick={() => onUpdateConfig(index, { assignments: [...(config.assignments || []), { name: '', value: '' }] })}>
              <Plus size={13} />添加变量
            </button>
          </div>
        </div>
      )}

      {node.type === 'VariableAggregate' && (
        <div className="workflow-field">
          <label>
            <span>输出变量名（下游用 {`{{variables.${config.output_var || 'result'}}}`} 取）</span>
            <input value={config.output_var || ''} disabled={disabled} placeholder="result"
              onChange={(event) => onUpdateConfig(index, { output_var: event.target.value })} />
          </label>
          <span>来源（按顺序取第一个有值的；通常每条分支各填一个）</span>
          <div className="workflow-branches">
            {(config.sources || []).map((item, itemIndex) => (
              <div className="workflow-branch" key={itemIndex}>
                <span className="workflow-branch-index">{itemIndex + 1}</span>
                <input className="workflow-branch-value-grow" value={item ?? ''} disabled={disabled}
                  placeholder="{{variables.分支结果}}" aria-label={`聚合来源 ${itemIndex + 1}`}
                  onChange={(event) => onUpdateConfig(index, {
                    sources: (config.sources || []).map((row, position) => (position === itemIndex ? event.target.value : row)),
                  })} />
                <button type="button" aria-label="删除来源" disabled={disabled}
                  onClick={() => onUpdateConfig(index, { sources: (config.sources || []).filter((_, position) => position !== itemIndex) })}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <button type="button" className="workflow-add-branch" disabled={disabled}
              onClick={() => onUpdateConfig(index, { sources: [...(config.sources || []), ''] })}>
              <Plus size={13} />添加来源
            </button>
          </div>
        </div>
      )}

      {node.type === 'ParameterExtract' && (
        <div className="workflow-field">
          <label>
            <span>提取说明（可选）</span>
            <input value={config.instruction || ''} disabled={disabled} placeholder="从用户问题里抽取订单信息"
              onChange={(event) => onUpdateConfig(index, { instruction: event.target.value })} />
          </label>
          <label>
            <span>提取来源</span>
            <input value={config.source || '{{input}}'} disabled={disabled} placeholder="{{input}}"
              onChange={(event) => onUpdateConfig(index, { source: event.target.value })} />
          </label>
          <span>字段</span>
          <div className="workflow-branches">
            {Object.entries(config.schema || {}).map(([name, spec]) => (
              <div className="workflow-branch" key={name}>
                <input className="workflow-branch-field" value={name} disabled
                  aria-label={`字段 ${name}`} />
                <input className="workflow-branch-value-grow" value={spec?.description || ''} disabled={disabled}
                  placeholder="字段说明"
                  onChange={(event) => {
                    const schema = { ...(config.schema || {}), [name]: { description: event.target.value } };
                    onUpdateConfig(index, { schema });
                  }} />
                <button type="button" aria-label={`删除字段 ${name}`} disabled={disabled}
                  onClick={() => {
                    const schema = { ...(config.schema || {}) };
                    delete schema[name];
                    onUpdateConfig(index, { schema });
                  }}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <button type="button" className="workflow-add-branch" disabled={disabled}
              onClick={() => {
                let name = 'field1';
                let counter = 1;
                while ((config.schema || {})[name]) { counter += 1; name = `field${counter}`; }
                onUpdateConfig(index, { schema: { ...(config.schema || {}), [name]: { description: '' } } });
              }}>
              <Plus size={13} />添加字段
            </button>
          </div>
          <small className="workflow-note">结果写入上下文 {`{{extracted.字段名}}`}，可用于后续模板与条件。</small>
        </div>
      )}

      {node.type === 'Classifier' && (
        <div className="workflow-field">
          <span>分类项（模型从中选一个）</span>
          <div className="workflow-branches">
            {(config.classes || []).map((item, itemIndex) => (
              <div className="workflow-branch" key={itemIndex}>
                <input className="workflow-branch-field" value={item.label || ''} disabled={disabled} placeholder="分类名"
                  aria-label={`分类 ${itemIndex + 1} 名称`}
                  onChange={(event) => {
                    const classes = (config.classes || []).map((row, position) => (
                      position === itemIndex ? { ...row, label: event.target.value } : row
                    ));
                    onUpdateConfig(index, { classes });
                  }} />
                <input className="workflow-branch-value-grow" value={item.description || ''} disabled={disabled}
                  placeholder="分类说明（帮助模型判断）"
                  aria-label={`分类 ${itemIndex + 1} 说明`}
                  onChange={(event) => {
                    const classes = (config.classes || []).map((row, position) => (
                      position === itemIndex ? { ...row, description: event.target.value } : row
                    ));
                    onUpdateConfig(index, { classes });
                  }} />
                <select value={item.next || ''} disabled={disabled} aria-label={`分类 ${itemIndex + 1} 目标`}
                  onChange={(event) => {
                    const classes = (config.classes || []).map((row, position) => (
                      position === itemIndex ? { ...row, next: event.target.value } : row
                    ));
                    onUpdateConfig(index, { classes });
                  }}>
                  <option value="">跳转到…</option>
                  {nodes.filter((row) => row.id !== node.id).map((row) => (
                    <option key={row.id} value={row.id}>{TYPE_LABELS[row.type] || row.type}（{row.id}）</option>
                  ))}
                </select>
                <button type="button" aria-label="删除分类" disabled={disabled}
                  onClick={() => onUpdateConfig(index, {
                    classes: (config.classes || []).filter((_, position) => position !== itemIndex),
                  })}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <button type="button" className="workflow-add-branch" disabled={disabled}
              onClick={() => onUpdateConfig(index, {
                classes: [...(config.classes || []), { label: '', description: '', next: '' }],
              })}>
              <Plus size={13} />添加分类
            </button>
          </div>
          <label className="workflow-field workflow-default-branch">
            <span>无法判断时跳转到</span>
            <select value={config.default || ''} disabled={disabled}
              onChange={(event) => onUpdateConfig(index, { default: event.target.value })}>
              <option value="">顺序执行下一个节点</option>
              {nodes.filter((row) => row.id !== node.id).map((row) => (
                <option key={row.id} value={row.id}>{TYPE_LABELS[row.type] || row.type}（{row.id}）</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {node.type === 'Condition' && (
        <div className="workflow-field">
          <span>分支条件（按顺序判断，命中即跳转）</span>
          <div className="workflow-branches">
            {(config.branches || []).map((branch, branchIndex) => (
              <div className="workflow-branch" key={branchIndex}>
                <input
                  className="workflow-branch-field"
                  list="workflow-field-options"
                  placeholder="字段，如 rag_status.matched_chunks"
                  value={branch.when?.field || ''}
                  disabled={disabled}
                  onChange={(event) => onUpdateBranch(index, branchIndex, {
                    when: { ...(branch.when || {}), field: event.target.value },
                  })}
                />
                <select
                  value={predicateOf(branch.when)} disabled={disabled}
                  onChange={(event) => {
                    const op = event.target.value;
                    const when = { field: branch.when?.field || '' };
                    if (op === 'exists') when.exists = true;
                    else when[op] = branch.when?.[op] ?? 1;
                    onUpdateBranch(index, branchIndex, { when });
                  }}
                >
                  {PREDICATES.map((item) => <option key={item.op} value={item.op}>{item.label}</option>)}
                </select>
                {predicateOf(branch.when) !== 'exists' && (
                  <input
                    className="workflow-branch-value"
                    value={branch.when?.[predicateOf(branch.when)] ?? ''}
                    disabled={disabled}
                    onChange={(event) => {
                      const op = predicateOf(branch.when);
                      const raw = event.target.value;
                      const value = raw === '' ? '' : Number.isNaN(Number(raw)) ? raw : Number(raw);
                      onUpdateBranch(index, branchIndex, { when: { ...(branch.when || {}), [op]: value } });
                    }}
                  />
                )}
                <select
                  value={branch.next || ''} disabled={disabled}
                  onChange={(event) => onUpdateBranch(index, branchIndex, { next: event.target.value })}
                >
                  <option value="">跳转到…</option>
                  {nodes.filter((item) => item.id !== node.id).map((item) => (
                    <option key={item.id} value={item.id}>{TYPE_LABELS[item.type] || item.type}（{item.id}）</option>
                  ))}
                </select>
                <button type="button" aria-label="删除分支" disabled={disabled} onClick={() => onRemoveBranch(index, branchIndex)}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <datalist id="workflow-field-options">
              {COMMON_FIELDS.map((field) => <option key={field} value={field} />)}
            </datalist>
            <button type="button" className="workflow-add-branch" disabled={disabled} onClick={() => onAddBranch(index)}>
              <Plus size={13} />添加分支
            </button>
          </div>
          <label className="workflow-field workflow-default-branch">
            <span>都不命中时跳转到</span>
            <select
              value={config.default || ''} disabled={disabled}
              onChange={(event) => onUpdateConfig(index, { default: event.target.value })}
            >
              <option value="">顺序执行下一个节点</option>
              {nodes.filter((item) => item.id !== node.id).map((item) => (
                <option key={item.id} value={item.id}>{TYPE_LABELS[item.type] || item.type}（{item.id}）</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {node.type !== 'Condition' && node.type !== 'Classifier' && node.type !== 'Answer' && (
        <label className="workflow-field">
          <span>执行后跳转到</span>
          <select
            value={node.next || ''} disabled={disabled}
            onChange={(event) => onUpdate(index, { next: event.target.value || undefined })}
          >
            <option value="">顺序执行下一个节点</option>
            {nodeIds.filter((id) => id !== node.id).map((id) => (
              <option key={id} value={id}>{TYPE_LABELS[nodes.find((item) => item.id === id)?.type] || id}（{id}）</option>
            ))}
          </select>
        </label>
      )}
    </>
  );
}

function KeyValueRows({ label, rows, disabled, keyPlaceholder, valuePlaceholder, onChange }) {
  const entries = Object.entries(rows || {});
  return (
    <div className="workflow-field">
      <span>{label}</span>
      <div className="workflow-branches">
        {entries.map(([key, value], rowIndex) => (
          <div className="workflow-branch" key={`${key}-${rowIndex}`}>
            <input className="workflow-branch-field" value={key} disabled={disabled} placeholder={keyPlaceholder}
              onChange={(event) => {
                const next = {};
                entries.forEach(([itemKey, itemValue], position) => {
                  next[position === rowIndex ? event.target.value : itemKey] = itemValue;
                });
                onChange(next);
              }} />
            <input className="workflow-branch-value-grow" value={String(value ?? '')} disabled={disabled}
              placeholder={valuePlaceholder}
              onChange={(event) => onChange({ ...Object.fromEntries(entries), [key]: event.target.value })} />
            <button type="button" aria-label="删除这一行" disabled={disabled}
              onClick={() => {
                const next = { ...Object.fromEntries(entries) };
                delete next[key];
                onChange(next);
              }}>
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        <button type="button" className="workflow-add-branch" disabled={disabled}
          onClick={() => onChange({ ...Object.fromEntries(entries), [`header${entries.length + 1}`]: '' })}>
          <Plus size={13} />添加
        </button>
      </div>
    </div>
  );
}
