import React from 'react';
import { Plus, X } from 'lucide-react';

/**
 * 元数据过滤条件编辑器：召回测试与工作流的知识检索节点共用。
 *
 * 条件格式与后端 `resolve_metadata_filter` 一致：
 *   { logic: 'and' | 'or', conditions: [{ key, op, value }] }
 */
export const METADATA_OPERATORS = [
  { value: 'eq', label: '等于' },
  { value: 'ne', label: '不等于' },
  { value: 'contains', label: '包含' },
  { value: 'not_contains', label: '不包含' },
  { value: 'empty', label: '为空' },
  { value: 'not_empty', label: '不为空' },
];

const NO_VALUE = new Set(['empty', 'not_empty']);

export function emptyFilter() {
  return { logic: 'and', conditions: [] };
}

/** 去掉没填字段名的行；全空时返回 null，调用方据此决定是否带上过滤 */
export function cleanFilter(filter) {
  const conditions = (filter?.conditions || []).filter((item) => item.key?.trim());
  return conditions.length ? { logic: filter.logic || 'and', conditions } : null;
}

export function MetadataFilterEditor({ value, onChange, keys = [], disabled = false, valueHint = '' }) {
  const filter = value || emptyFilter();
  const conditions = filter.conditions || [];
  const listId = React.useId();

  function update(next) {
    onChange({ ...filter, ...next });
  }

  function updateRow(index, patch) {
    update({ conditions: conditions.map((item, i) => (i === index ? { ...item, ...patch } : item)) });
  }

  return (
    <div className="metadata-filter">
      {conditions.length > 1 && (
        <label className="metadata-filter-logic">
          满足
          <select value={filter.logic || 'and'} disabled={disabled} onChange={(event) => update({ logic: event.target.value })}>
            <option value="and">全部条件</option>
            <option value="or">任一条件</option>
          </select>
        </label>
      )}
      {keys.length > 0 && (
        <datalist id={listId}>
          {keys.map((key) => <option key={key} value={key} />)}
        </datalist>
      )}
      {conditions.map((item, index) => (
        <div className="metadata-filter-row" key={index}>
          <input
            aria-label="元数据字段" placeholder="字段，如 产品" list={keys.length ? listId : undefined}
            value={item.key || ''} disabled={disabled}
            onChange={(event) => updateRow(index, { key: event.target.value })}
          />
          <select aria-label="比较方式" value={item.op || 'eq'} disabled={disabled}
            onChange={(event) => updateRow(index, { op: event.target.value })}>
            {METADATA_OPERATORS.map((op) => <option key={op.value} value={op.value}>{op.label}</option>)}
          </select>
          {!NO_VALUE.has(item.op) && (
            <input
              aria-label="元数据取值" placeholder={valueHint || '取值'}
              value={item.value ?? ''} disabled={disabled}
              onChange={(event) => updateRow(index, { value: event.target.value })}
            />
          )}
          <button type="button" aria-label="删除条件" disabled={disabled}
            onClick={() => update({ conditions: conditions.filter((_, i) => i !== index) })}>
            <X size={13} />
          </button>
        </div>
      ))}
      <button type="button" className="metadata-filter-add" disabled={disabled}
        onClick={() => update({ conditions: [...conditions, { key: keys[0] || '', op: 'eq', value: '' }] })}>
        <Plus size={13} />添加过滤条件
      </button>
    </div>
  );
}
