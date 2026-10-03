/**
 * prop 链契约测试。
 *
 * 之前的线上崩溃是"子组件解构了某个 prop，但上游组装 props 时漏传"——JS 不会报错，
 * 拿到的是 undefined，直到用户点击才抛异常。挂载整个 BuilderView 需要 mock 掉大量
 * 网络与懒加载依赖，性价比低；这里直接比对两侧的标识符集合，把漏传挡在构建期。
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

/** 取出 `export function X(props) { const { ... } = props;` 里解构的顶层 prop 名。 */
function destructuredProps(source, componentName) {
  const start = source.indexOf(`function ${componentName}(props)`);
  expect(start, `未找到 ${componentName} 的定义`).toBeGreaterThan(-1);
  const open = source.indexOf('{', source.indexOf('const {', start));
  const close = source.indexOf('} = props', open);
  expect(close, `未找到 ${componentName} 的解构块`).toBeGreaterThan(open);
  return new Set(
    source
      .slice(open + 1, close)
      .split('\n')
      .map((line) => line.replace(/\/\/.*$/, '').trim())
      .filter(Boolean)
      .map((line) => line.replace(/[,}]/g, '').split(':')[0].split('=')[0].trim())
      .filter((name) => /^[A-Za-z_$][\w$]*$/.test(name)),
  );
}

/**
 * 取出 main.jsx 里 `const builderProps = { ... }` 之类对象字面量提供的键。
 * builderProps 由 `...shellProps` 展开加自有键组成，因此要递归跟进展开来源，
 * 否则继承来的那批键会被误判为漏传。
 */
function providedKeys(source, objectName, seen = new Set()) {
  if (seen.has(objectName)) return new Set();
  seen.add(objectName);
  const start = source.indexOf(`const ${objectName} = {`);
  expect(start, `未找到 ${objectName} 的定义`).toBeGreaterThan(-1);
  const open = source.indexOf('{', start);
  let depth = 0;
  let end = open;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const body = source.slice(open + 1, end);
  const keys = new Set();
  for (const line of body.split('\n')) {
    const cleaned = line.replace(/\/\/.*$/, '').trim();
    // 只收顶层 `name,` 与 `name: value` 形式；嵌套对象内部的键不算顶层供给。
    const match = cleaned.match(/^([A-Za-z_$][\w$]*)\s*[,:]/);
    if (match) keys.add(match[1]);

    const spread = cleaned.match(/^\.\.\.([A-Za-z_$][\w$]*)\s*,?$/);
    if (spread) {
      for (const inherited of providedKeys(source, spread[1], seen)) keys.add(inherited);
    }
  }
  return keys;
}

const mainSource = readFileSync(join(SRC, 'main.jsx'), 'utf8');
const builderSource = readFileSync(join(SRC, 'views/BuilderView.jsx'), 'utf8');

describe('prop 链契约', () => {
  it('会话重命名相关的四项必须由 main.jsx 传下去', () => {
    const provided = providedKeys(mainSource, 'builderProps');
    // 这四项曾在页面拆分时整组漏传，是回归的重点。
    for (const name of ['activeSessionId', 'sessionTitleDraft', 'setSessionTitleDraft', 'renameSession']) {
      expect(provided, `builderProps 缺少 ${name}`).toContain(name);
    }
  });

  it('BuilderView 解构的每个 prop 都有上游供给', () => {
    const required = destructuredProps(builderSource, 'BuilderView');
    const provided = providedKeys(mainSource, 'builderProps');
    expect(required.size).toBeGreaterThan(20);

    const missing = [...required].filter((name) => !provided.has(name));
    expect(missing, `builderProps 未提供：${missing.join(', ')}`).toEqual([]);
  });
});
