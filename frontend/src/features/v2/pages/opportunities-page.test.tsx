import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DataEnvelope, OpportunityWorkspace, SalesHandoff } from '../types';

const { confirmOpportunityMock, refreshMock, queryState, updateOpportunityStageMock } = vi.hoisted(() => ({
  confirmOpportunityMock: vi.fn(),
  refreshMock: vi.fn(),
  queryState: { value: {} as unknown },
  updateOpportunityStageMock: vi.fn(),
}));

vi.mock('../api', () => ({
  v2Api: {
    opportunityWorkspace: vi.fn(),
    confirmOpportunity: confirmOpportunityMock,
    updateOpportunityStage: updateOpportunityStageMock,
  },
}));

vi.mock('../use-v2-query', () => ({
  useV2Query: () => queryState.value,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import OpportunitiesPage from './opportunities-page';

const handoff: SalesHandoff = {
  id: '71',
  replyAssessmentId: '83',
  status: 'open',
  priority: 'urgent',
  title: 'Confirm qualified sales opportunity',
  detail: 'Please send the pricing deck and arrange a discovery call.',
  companyId: '9',
  company: 'Acme Manufacturing',
  contactId: '14',
  contact: 'Ada Chen',
  conversationId: '22',
  conversation: 'email · Re: automation proposal',
  channel: 'email',
};

function workspaceEnvelope(source: DataEnvelope<OpportunityWorkspace>['source'] = 'live'): DataEnvelope<OpportunityWorkspace> {
  return {
    data: { handoffs: [handoff], opportunities: [] },
    source,
    observedAt: '2026-07-15T08:00:00Z',
  };
}

describe('Sales handoff opportunity confirmation', () => {
  beforeEach(() => {
    confirmOpportunityMock.mockReset();
    confirmOpportunityMock.mockResolvedValue({ id: 101 });
    updateOpportunityStageMock.mockReset();
    refreshMock.mockReset();
    queryState.value = { result: workspaceEnvelope(), loading: false, refresh: refreshMock };
  });

  it('shows the persisted handoff evidence and requires an impact preview before the V2 write', async () => {
    // GIVEN: An open sales_handoff Task with a linked assessment, company, contact, and conversation.
    const user = userEvent.setup();
    render(<OpportunitiesPage />);

    // THEN: Sales can inspect the evidence before entering qualification fields.
    expect(screen.getByRole('heading', { name: 'Acme Manufacturing' })).toBeInTheDocument();
    expect(screen.getByText('Ada Chen')).toBeInTheDocument();
    expect(screen.getByText('email · Re: automation proposal')).toBeInTheDocument();
    expect(screen.getByText('Reply assessment #83')).toBeInTheDocument();

    // WHEN: The salesperson confirms Fit and supplies ownership plus a concrete next step.
    await user.type(screen.getByRole('spinbutton', { name: '负责人用户 ID' }), '12');
    await user.type(screen.getByRole('textbox', { name: '下一步动作' }), '发送报价并预约 discovery');
    await user.type(screen.getByLabelText('下一步到期时间'), '2026-07-20T09:30');
    await user.type(screen.getByRole('spinbutton', { name: '预估金额' }), '25000');
    await user.type(screen.getByLabelText('预计成交日期'), '2026-08-31');
    await user.click(screen.getByRole('checkbox', { name: '确认符合已发布 ICP' }));
    await user.click(screen.getByRole('button', { name: '预览确认影响' }));

    // THEN: No write occurs until the explicit impact confirmation.
    expect(confirmOpportunityMock).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: '影响确认' })).toBeInTheDocument();
    expect(screen.getAllByText(/Task #71/)).toHaveLength(2);

    // WHEN: The salesperson confirms the preview.
    await user.click(screen.getByRole('button', { name: '确认创建合格商机' }));

    // THEN: The exact Task and assessment evidence are posted without inventing a fit override.
    await waitFor(() => expect(confirmOpportunityMock).toHaveBeenCalledTimes(1));
    const payload = confirmOpportunityMock.mock.calls[0][0];
    expect(payload).toEqual({
      reply_assessment_id: 83,
      source_task_id: 71,
      assignee_user_id: 12,
      next_action: '发送报价并预约 discovery',
      next_action_due_at: new Date('2026-07-20T09:30').toISOString(),
      fit_confirmed: true,
      value_amount: 25000,
      currency: 'USD',
      expected_close_date: '2026-08-31',
    });
    expect(payload).not.toHaveProperty('fit_override_id');
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it.each(['sample', 'mixed'] as const)('blocks qualification writes when the data source is %s', source => {
    // GIVEN: The page is not composed exclusively from persisted live V2 data.
    queryState.value = { result: workspaceEnvelope(source), loading: false, refresh: refreshMock };

    // WHEN: The handoff is rendered.
    render(<OpportunitiesPage />);

    // THEN: The mutation entry point is disabled, even though sample evidence is visible.
    expect(screen.getByRole('button', { name: '预览确认影响' })).toBeDisabled();
    expect(confirmOpportunityMock).not.toHaveBeenCalled();
  });

  it('keeps a rejected qualification visible as an actionable error', async () => {
    // GIVEN: The backend rejects the persisted handoff at its final qualification gate.
    const user = userEvent.setup();
    confirmOpportunityMock.mockRejectedValue(new Error('Published ICP fit is no longer valid'));
    render(<OpportunitiesPage />);
    await user.type(screen.getByRole('spinbutton', { name: '负责人用户 ID' }), '12');
    await user.type(screen.getByRole('textbox', { name: '下一步动作' }), '人工复核并跟进');
    await user.type(screen.getByLabelText('下一步到期时间'), '2026-07-20T09:30');
    await user.click(screen.getByRole('checkbox', { name: '确认符合已发布 ICP' }));
    await user.click(screen.getByRole('button', { name: '预览确认影响' }));

    // WHEN: The final confirmation fails.
    await user.click(screen.getByRole('button', { name: '确认创建合格商机' }));

    // THEN: The failure is announced in-page and no success refresh hides the Task.
    expect(await screen.findByRole('alert')).toHaveTextContent('Published ICP fit is no longer valid');
    expect(refreshMock).not.toHaveBeenCalled();
  });
});
