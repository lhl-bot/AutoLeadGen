import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DataEnvelope, WorkSnapshot, WorkTask } from '../types';

const { queryState, refreshMock } = vi.hoisted(() => ({
  queryState: { value: {} as unknown },
  refreshMock: vi.fn(),
}));

vi.mock('../api', () => ({
  v2Api: { work: vi.fn(), completeTask: vi.fn(), approveTask: vi.fn(), dismissTask: vi.fn() },
}));

vi.mock('../use-v2-query', () => ({
  useV2Query: () => queryState.value,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import WorkPage from './work-page';
import { v2Api } from '../api';

function task(id: string, type: WorkTask['type']): WorkTask {
  return {
    id,
    title: `${type} task`,
    detail: 'Persisted V2 task',
    type,
    priority: 'high',
    href: type === 'handoff' ? '/dashboard/opportunities' : '/dashboard/campaigns',
  };
}

function workEnvelope(tasks: WorkTask[]): DataEnvelope<WorkSnapshot> {
  return {
    data: {
      runtime: { services: [], activeCampaigns: 0, recentMessages: 0, timestamp: '2026-07-15T08:00:00Z' },
      metrics: [],
      tasks,
      campaigns: [],
    },
    source: 'live',
    observedAt: '2026-07-15T08:00:00Z',
  };
}

describe('Product V2 task actions', () => {
  beforeEach(() => {
    refreshMock.mockReset();
    vi.mocked(v2Api.approveTask).mockReset();
    vi.mocked(v2Api.dismissTask).mockReset();
  });

  it('blocks approval without a preview but still allows the safe reject-and-cancel path', async () => {
    // GIVEN: Tasks whose completion requires a dedicated Product V2 workflow.
    queryState.value = {
      result: workEnvelope([
        task('1', 'handoff'),
        task('2', 'reply'),
        task('3', 'approval'),
        task('4', 'readiness'),
      ]),
      loading: false,
      refresh: refreshMock,
    };

    // WHEN: Today's work is rendered.
    const user = userEvent.setup();
    render(<WorkPage />);

    // THEN: sales_handoff, reply_triage, draft_review, and campaign_readiness cannot bypass their workflow.
    expect(screen.getAllByRole('link', { name: '处理' })).toHaveLength(4);
    expect(screen.queryByRole('button', { name: '完成' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批准发送' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '拒绝' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '拒绝' }));
    expect(screen.getByRole('dialog', { name: '确认拒绝草稿' })).toHaveTextContent('仅允许拒绝并取消');
    await user.click(screen.getByRole('button', { name: '确认拒绝并取消' }));
    await waitFor(() => expect(v2Api.dismissTask).toHaveBeenCalledWith('3'));
  });

  it('keeps inline completion for ordinary operational tasks', () => {
    // GIVEN: A generic exception Task that has no dedicated confirmation flow.
    queryState.value = {
      result: workEnvelope([task('5', 'exception')]),
      loading: false,
      refresh: refreshMock,
    };

    // WHEN / THEN: The normal Task completion control remains available.
    render(<WorkPage />);
    expect(screen.getByRole('link', { name: '处理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '完成' })).toBeEnabled();
  });

  it('filters large migration work queues by customer-data task type', async () => {
    queryState.value = {
      result: workEnvelope([task('6', 'data'), task('7', 'exception')]),
      loading: false,
      refresh: refreshMock,
    };
    const user = userEvent.setup();
    render(<WorkPage />);

    await user.selectOptions(screen.getByRole('combobox', { name: '筛选待办任务类型' }), 'data');

    expect(screen.getByText('data task')).toBeInTheDocument();
    expect(screen.queryByText('exception task')).not.toBeInTheDocument();
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
  });

  it('previews a REVIEW draft and requires an explicit accessible confirmation before approval', async () => {
    // GIVEN: A live draft_review Task with the exact immutable Attempt preview fields.
    const approval = {
      ...task('9', 'approval'),
      draftReview: {
        attemptId: '301',
        channel: 'email',
        recipient: 'buyer@example.com',
        subject: 'A reviewed introduction',
        body: 'Hello buyer,\nThis is the exact reviewed body.',
        templateVersion: 'cold-email-v3',
      },
    } satisfies WorkTask;
    queryState.value = {
      result: workEnvelope([approval]),
      loading: false,
      refresh: refreshMock,
    };
    vi.mocked(v2Api.approveTask).mockResolvedValue({} as never);
    const user = userEvent.setup();

    // WHEN: The rep reviews the impact, opens approval, and explicitly confirms.
    render(<WorkPage />);
    expect(screen.getByLabelText('发送影响预览')).toHaveTextContent('buyer@example.com');
    expect(screen.getByText('This is the exact reviewed body.', { exact: false })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '批准发送' }));
    const dialog = screen.getByRole('dialog', { name: '确认批准发送' });
    expect(dialog).toHaveTextContent('同意状态');
    expect(v2Api.approveTask).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认批准并重新入队' }));

    // THEN: Only the dedicated approval helper is invoked and the Task list refreshes.
    await waitFor(() => expect(v2Api.approveTask).toHaveBeenCalledWith('9', {
      subject: 'A reviewed introduction',
      body: 'Hello buyer,\nThis is the exact reviewed body.',
    }));
    expect(v2Api.dismissTask).not.toHaveBeenCalled();
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it('keeps REVIEW writes disabled for mixed data even when preview metadata exists', () => {
    // GIVEN: A page where Task data is live but another panel fell back, so the composite source is mixed.
    const envelope = workEnvelope([{
      ...task('10', 'approval'),
      draftReview: { attemptId: '302', channel: 'linkedin', recipient: 'profile/302', body: 'Connect note' },
    }]);
    envelope.source = 'mixed';
    queryState.value = { result: envelope, loading: false, refresh: refreshMock };

    // WHEN / THEN: Neither approval nor rejection can mutate from a non-live page.
    render(<WorkPage />);
    expect(screen.getByRole('button', { name: '批准发送' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '拒绝' })).toBeDisabled();
  });
});
