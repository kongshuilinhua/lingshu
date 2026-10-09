import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

const fallback = { register: () => () => {}, confirmLeave: async (action) => { await action(); return true; } };
const Context = createContext(fallback);
const isDirty = (guard) => guard.current.isDirty ? guard.current.isDirty() : guard.current.dirty;

export function UnsavedChangesProvider({ children }) {
  const registry = useRef(new Set());
  const pending = useRef(null);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [error, setError] = useState('');
  const register = useCallback((guard) => {
    registry.current.add(guard);
    return () => registry.current.delete(guard);
  }, []);
  const confirmLeave = useCallback(async (action, only) => {
    if (pending.current) return false;
    const guards = [...registry.current].filter((guard) => !only || guard === only)
      .sort((a, b) => (a.current.saveOrder || 0) - (b.current.saveOrder || 0));
    const labels = guards.filter(isDirty).map((guard) => guard.current.label);
    if (!labels.length) { await action(); return true; }
    return new Promise((resolve) => {
      pending.current = { guards, labels, action, resolve, focus: document.activeElement };
      setError('');
      setDialog(pending.current);
    });
  }, []);

  function stay() {
    if (savingRef.current) return;
    const current = pending.current;
    pending.current = null;
    setDialog(null);
    current?.resolve(false);
    current?.focus?.focus?.();
  }

  async function leave(save) {
    const current = pending.current;
    if (!current || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError('');
    try {
      if (current.guards.some((guard) => registry.current.has(guard) && isDirty(guard) && guard.current.busy)) {
        throw new Error('当前配置正在保存或加载，请稍后再试。');
      }
      for (const guard of current.guards) {
        if (!registry.current.has(guard) || !isDirty(guard)) continue;
        if (guard.current.busy) throw new Error('当前配置正在保存或加载，请稍后再试。');
        if (save) {
          if (!guard.current.onSave || await guard.current.onSave() !== true) {
            throw new Error('保存未完成，请继续编辑并检查必填项或错误提示。');
          }
        } else {
          await guard.current.onDiscard?.();
        }
        guard.current.dirty = false;
      }
      pending.current = null;
      setDialog(null);
      await current.action();
      current.resolve(true);
    } catch (err) {
      if (!pending.current) { pending.current = current; setDialog(current); }
      setError(err.message || '保存失败，修改已保留。');
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  useEffect(() => {
    function beforeUnload(event) {
      if ([...registry.current].some(isDirty)) {
        event.preventDefault();
        event.returnValue = '';
      }
    }
    function keyDown(event) {
      if (!pending.current) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); stay(); }
      if (event.key === 'Tab') {
        const buttons = [...document.querySelectorAll('.unsaved-dialog button:not(:disabled)')];
        const first = buttons[0];
        const last = buttons.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }
    window.addEventListener('beforeunload', beforeUnload);
    window.addEventListener('keydown', keyDown, true);
    return () => { window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('keydown', keyDown, true); };
  }, []);

  return <Context.Provider value={{ register, confirmLeave }}>
    {children}
    {dialog && <div className="confirm-dialog-backdrop unsaved-backdrop">
      <section className="confirm-dialog unsaved-dialog" role="dialog" aria-modal="true" aria-labelledby="unsaved-title" aria-describedby="unsaved-description">
        <header><div><h2 id="unsaved-title">有未保存的修改</h2><p id="unsaved-description">{dialog.labels.join('、')}尚未保存，退出前要保存吗？</p></div></header>
        {error && <p role="alert" className="error">{error}</p>}
        <footer>
          <button type="button" disabled={saving} autoFocus onClick={stay}>继续编辑</button>
          <button type="button" disabled={saving} onClick={() => leave(false)}>放弃修改</button>
          <button type="button" className="primary-model-action" disabled={saving} onClick={() => leave(true)}>{saving ? '保存中…' : '保存并退出'}</button>
        </footer>
      </section>
    </div>}
  </Context.Provider>;
}

export function useUnsavedNavigation() { return useContext(Context).confirmLeave; }

export function useUnsavedChanges(options) {
  const context = useContext(Context);
  const guard = useRef(options);
  guard.current = options;
  useEffect(() => context.register(guard), [context.register]);
  return useCallback((action) => context.confirmLeave(action, guard), [context.confirmLeave]);
}

export function useUnsavedForm({ enabled = true, value, onSave, onDiscard, label, busy = false, resetOnEnable = true, saveOrder = 0, getCurrentValue }) {
  const signature = JSON.stringify(value);
  const baseline = useRef(signature);
  const suppressed = useRef(false);
  const previousSignature = useRef(signature);
  if (previousSignature.current !== signature) { suppressed.current = false; previousSignature.current = signature; }
  const wasEnabled = useRef(false);
  if (resetOnEnable && enabled && !wasEnabled.current) baseline.current = signature;
  wasEnabled.current = enabled;
  const dirty = Boolean(enabled && signature !== baseline.current);
  const options = useRef(null);
  const markSaved = useCallback((next) => {
    baseline.current = next === undefined ? JSON.stringify(options.current.getCurrentValue?.() ?? options.current.value) : JSON.stringify(next);
    options.current.dirty = false;
  }, []);
  const guardedOptions = { dirty, label, busy, saveOrder,
    onSave: async () => { const saved = await onSave(); if (saved === true) markSaved(); return saved; },
    onDiscard: async () => { await onDiscard?.(JSON.parse(baseline.current)); suppressed.current = true; options.current.dirty = false; },
  };
  options.current = { ...guardedOptions, value, getCurrentValue,
    isDirty: () => Boolean(enabled && !suppressed.current && JSON.stringify(getCurrentValue?.() ?? value) !== baseline.current) };
  // Keep this reference shared so a successful save clears the guard before
  // a parent navigation callback runs, even before React renders again.
  const context = useContext(Context);
  useEffect(() => context.register(options), [context.register]);
  const confirmLeave = useCallback((action) => context.confirmLeave(action, options), [context.confirmLeave]);
  return { dirty, markSaved, confirmLeave };
}
