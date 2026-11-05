import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { ChunkList } from './ChunkList.jsx';

const apiMock = vi.hoisted(() => vi.fn());
vi.mock('../utils.js', () => ({
  api: (...args) => apiMock(...args),
  errorMessage: (value) => String(value?.message ?? value ?? ''),
}));

const CHUNKS = [
  { id: 11, chunk_index: 0, text: '营业时间九点到八点', embedding_dimension: 1024 },
  { id: 12, chunk_index: 1, text: '退货七天内', embedding_dimension: 1024 },
];

function renderList(onChange = vi.fn()) {
  render(<ChunkList kbId={1} documentId={2} chunks={CHUNKS} loading={false} canEdit token="t" onChange={onChange} />);
  return onChange;
}

describe('切片逐条维护', () => {
  beforeEach(() => apiMock.mockReset());

  it('编辑一条切片走 PATCH，并把后端返回的列表交给上层', async () => {
    const next = [{ ...CHUNKS[0], text: '改后的内容' }, CHUNKS[1]];
    apiMock.mockResolvedValue({ chunks: next });
    const onChange = renderList();

    fireEvent.click(screen.getAllByLabelText('编辑切片')[0]);
    fireEvent.change(screen.getByDisplayValue('营业时间九点到八点'), { target: { value: '改后的内容' } });
    fireEvent.click(screen.getByText('保存并重新索引'));

    await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
      '/api/knowledge-bases/1/documents/2/chunks/11',
      expect.objectContaining({ method: 'PATCH', body: { text: '改后的内容' } }),
    ));
    expect(onChange).toHaveBeenCalledWith(next, undefined);
  });

  it('补一条走 POST，空内容不能提交', async () => {
    apiMock.mockResolvedValue({ chunks: CHUNKS });
    renderList();
    fireEvent.click(screen.getByText('补一条'));
    expect(screen.getByText('保存并重新索引')).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '客服电话 400' } });
    fireEvent.click(screen.getByText('保存并重新索引'));
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
      '/api/knowledge-bases/1/documents/2/chunks',
      expect.objectContaining({ method: 'POST', body: { text: '客服电话 400' } }),
    ));
  });

  it('删除前确认，取消则不发请求', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderList();
    fireEvent.click(screen.getAllByLabelText('删除切片')[1]);
    expect(confirm).toHaveBeenCalled();
    expect(apiMock).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});
