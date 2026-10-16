import React from 'react';

const RETRIEVAL_MODES = [
  { value: 'on_demand', label: '按需调用', hint: '由模型判断是否查知识库，闲聊不检索；效果取决于知识库描述是否写清楚。' },
  { value: 'auto', label: '自动检索', hint: '每轮对话都先检索知识库再回答，引用稳定，适合客服、问答。' },
];

/** 知识库检索方式：自动检索（每轮先查）/ 按需调用（知识库作为工具，由模型决定）。 */
export function RetrievalModeSetting({ mode, disabled = false, missingDescriptions = [], onChange }) {
  const active = RETRIEVAL_MODES.find((item) => item.value === mode) || RETRIEVAL_MODES[1];
  return (
    <div className="retrieval-mode-setting">
      <div className="retrieval-mode-options" role="radiogroup" aria-label="检索方式">
        <span className="retrieval-mode-label">检索方式</span>
        {RETRIEVAL_MODES.map((item) => (
          <label key={item.value} className={item.value === active.value ? 'active' : ''}>
            <input
              type="radio"
              name="retrieval-mode"
              value={item.value}
              checked={item.value === active.value}
              disabled={disabled}
              onChange={() => onChange(item.value)}
            />
            {item.label}
          </label>
        ))}
      </div>
      <small className="retrieval-mode-hint">{active.hint}</small>
      {active.value === 'on_demand' && missingDescriptions.length > 0 && (
        <small className="retrieval-mode-warning">
          以下知识库没有描述，模型可能不知道何时该查：{missingDescriptions.join('、')}。建议到知识库页补充描述。
        </small>
      )}
    </div>
  );
}
