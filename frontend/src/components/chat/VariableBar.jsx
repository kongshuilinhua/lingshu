/**
 * 智能体自定义变量的运行时输入条。
 *
 * 🎯 为什么单独成文件：对话页和编排页各有一处渲染，原先只是对话页里一个
 *    未导出的局部函数，编排页直接照抄名字引用，运行时 ReferenceError 把整页打成白屏。
 *    变量的读写语义只有一份，就不该有两份实现。
 */
export function VariableBar({ variables, values, onChange }) {
  return (
    <div className="chat-variable-bar">
      {variables.map((variable) => (
        <label key={variable.key}>
          {variable.label || variable.key}
          {variable.type === 'boolean' ? (
            <select
              value={String(values[variable.key] ?? variable.default_value ?? false)}
              onChange={(e) => onChange(variable.key, e.target.value === 'true')}
            >
              <option value="false">false</option>
              <option value="true">true</option>
            </select>
          ) : (
            <input
              type={variable.type === 'number' ? 'number' : 'text'}
              value={values[variable.key] ?? variable.default_value ?? ''}
              onChange={(e) => onChange(variable.key, e.target.value)}
            />
          )}
        </label>
      ))}
    </div>
  );
}
