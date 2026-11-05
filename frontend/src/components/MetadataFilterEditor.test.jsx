import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { cleanFilter } from './MetadataFilterEditor.jsx';
import { RetrievalTestPanel } from './RetrievalTestPanel.jsx';

describe('元数据过滤', () => {
  it('cleanFilter 丢掉没填字段名的行，全空时不带过滤', () => {
    expect(cleanFilter({ logic: 'and', conditions: [{ key: ' ', op: 'eq', value: 'x' }] })).toBeNull();
    expect(cleanFilter({ logic: 'or', conditions: [{ key: '产品', op: 'eq', value: 'A' }, { key: '', op: 'eq' }] }))
      .toEqual({ logic: 'or', conditions: [{ key: '产品', op: 'eq', value: 'A' }] });
  });

  it('召回测试带上过滤条件，字段名候选来自文档元数据', async () => {
    const run = vi.fn(async () => ({ status: {}, items: [], latency_ms: 1 }));
    render(<RetrievalTestPanel kbId={1} runRetrievalTest={run} metadataKeys={['产品']} onClose={() => {}} />);
    fireEvent.click(screen.getByText('添加过滤条件'));
    expect(screen.getByLabelText('元数据字段')).toHaveValue('产品');
    fireEvent.change(screen.getByLabelText('元数据取值'), { target: { value: '洗地机' } });
    fireEvent.change(screen.getByLabelText('测试问题'), { target: { value: '保修多久' } });
    fireEvent.click(screen.getByText('测试'));
    await waitFor(() => expect(run).toHaveBeenCalledWith(1, expect.objectContaining({
      metadata_filter: { logic: 'and', conditions: [{ key: '产品', op: 'eq', value: '洗地机' }] },
    })));
  });
});
