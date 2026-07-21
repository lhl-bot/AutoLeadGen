import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { v2Api } from '../api';
import type { Campaign, CampaignAuthoringSnapshot, DataEnvelope } from '../types';
import { CampaignActionControls, CampaignAuthoringPanel } from './campaigns-page';

afterEach(() => vi.restoreAllMocks());

const campaign: Campaign = {
  id: '12',
  name: 'Blocked campaign',
  lifecycle: 'ready',
  mode: 'review',
  priority: 100,
  budgetLimit: 10,
  enrollments: 3,
  positiveSignals: 0,
  stages: [],
  readiness: [{
    key: 'worker_outbound',
    label: '外发执行器',
    severity: 'blocker',
    passed: false,
    detail: '没有有效 heartbeat',
  }],
};

function authoringEnvelope(source: DataEnvelope<CampaignAuthoringSnapshot>['source']): DataEnvelope<CampaignAuthoringSnapshot> {
  return {
    source,
    observedAt: '2026-07-15T00:00:00Z',
    data: {
      campaigns: [campaign],
      revisionsByCampaign: {
        '12': [
          {
            id: '31',
            campaignId: '12',
            revisionNumber: 2,
            status: 'draft',
            createdAt: '2026-07-15T00:00:00Z',
            icpDefinition: { summary: 'Apparel' },
            audienceDefinition: { description: 'Sourcing leads' },
            qualityGates: { min_fit_score: 70 },
            budgetDefinition: { native_limit: 10, native_unit: 'fake_calls' },
            stopConditions: { public_unsubscribe_url: 'http://127.0.0.1:8000/unsubscribe' },
          },
          {
            id: '30',
            campaignId: '12',
            revisionNumber: 1,
            status: 'published',
            createdAt: '2026-07-14T00:00:00Z',
            icpDefinition: {},
            audienceDefinition: {},
            qualityGates: {},
            budgetDefinition: {},
            stopConditions: {},
          },
        ],
      },
      contacts: [{ id: '19', companyId: '4', label: 'Ada', company: 'Example Co', contactPoints: ['email: ada@example.com · valid/available'] }],
    },
  };
}

describe('Campaign lifecycle controls', () => {
  it('disables start when readiness contains an unresolved blocker', () => {
    // GIVEN: A ready Campaign whose outbound heartbeat readiness check is blocked.
    // WHEN: Lifecycle controls are rendered.
    render(<CampaignActionControls campaign={campaign} onComplete={vi.fn()} />);

    // THEN: Start is visibly disabled and explains the hard readiness gate.
    expect(screen.getByRole('button', { name: '启动' })).toBeDisabled();
    expect(screen.getByText('有 blocker，启动已禁用')).toBeInTheDocument();
  });

  it.each(['sample', 'mixed'] as const)('locks every authoring write when source data is %s', async source => {
    // GIVEN: At least one Campaign row is fallback or only partially live.
    const user = userEvent.setup();
    const createSpy = vi.spyOn(v2Api, 'createCampaign');
    const publishSpy = vi.spyOn(v2Api, 'publishRevision');
    const enrollSpy = vi.spyOn(v2Api, 'enrollContact');

    // WHEN: The authoring surface is rendered and the user tries the visible actions.
    render(<CampaignAuthoringPanel envelope={authoringEnvelope(source)} onRefresh={vi.fn()} />);

    // THEN: Product V2 fails closed before any write can leave the browser.
    expect(screen.getByRole('alert')).toHaveTextContent('写操作锁定');
    const createButton = screen.getByRole('button', { name: '创建 draft Campaign' });
    const draftButton = screen.getByRole('button', { name: '保存 DRAFT 并读取 diff' });
    const enrollButton = screen.getByRole('button', { name: '创建 Enrollment 任务' });
    expect(createButton).toBeDisabled();
    expect(draftButton).toBeDisabled();
    expect(enrollButton).toBeDisabled();
    await user.click(createButton);
    expect(createSpy).not.toHaveBeenCalled();
    expect(publishSpy).not.toHaveBeenCalled();
    expect(enrollSpy).not.toHaveBeenCalled();
  });

  it('only offers the production-supported Email channel', () => {
    render(<CampaignAuthoringPanel envelope={authoringEnvelope('live')} onRefresh={vi.fn()} />);

    const channel = screen.getByLabelText('渠道');
    expect(channel).toHaveValue('email');
    expect(within(channel).getAllByRole('option')).toHaveLength(1);
    expect(within(channel).getByRole('option', { name: 'Email（当前生产版本）' })).toBeInTheDocument();
    expect(screen.getByText(/LinkedIn 与 WhatsApp 连接器通过独立生产验收后才会开放/)).toBeInTheDocument();
  });

  it('requires a server diff preview and explicit human review before publishing', async () => {
    // GIVEN: Complete live V2 data with one published base and one DRAFT proposal.
    const user = userEvent.setup();
    vi.spyOn(v2Api, 'revisionDiff').mockResolvedValue({
      campaignId: '12',
      baseRevisionId: '30',
      proposedRevisionId: '31',
      diff: { added: [{ path: 'budget_definition.native_limit', value: 10 }], changed: [], removed: [] },
      diffChecksum: 'b'.repeat(64),
    });
    const publishSpy = vi.spyOn(v2Api, 'publishRevision').mockResolvedValue({
      id: 31,
      campaign_id: 12,
      revision_number: 2,
      status: 'published',
      icp_definition: {},
      audience_definition: {},
      quality_gates: {},
      budget_definition: {},
      stop_conditions: {},
      published_at: '2026-07-15T00:05:00Z',
      created_at: '2026-07-15T00:00:00Z',
    });

    // WHEN: The user requests /diff but has not yet confirmed its impact.
    render(<CampaignAuthoringPanel envelope={authoringEnvelope('live')} onRefresh={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: '预览 Revision #2' }));
    const publishButton = await screen.findByRole('button', { name: '人工确认并发布 DRAFT' });

    // THEN: Publish stays locked until the review acknowledgement is explicit.
    expect(v2Api.revisionDiff).toHaveBeenCalledWith('12', '31');
    expect(publishButton).toBeDisabled();
    expect(publishSpy).not.toHaveBeenCalled();
    await user.click(screen.getByRole('checkbox', { name: /diff 及其受众/ }));
    await user.click(publishButton);
    expect(publishSpy).toHaveBeenCalledWith({
      campaignId: '12',
      baseRevisionId: '30',
      proposedRevisionId: '31',
      diff: { added: [{ path: 'budget_definition.native_limit', value: 10 }], changed: [], removed: [] },
      diffChecksum: 'b'.repeat(64),
    });
  });

  it('serializes the Fit gate with the backend-consumed min_fit_score key', async () => {
    // GIVEN: A sales operator authors a complete revision from live V2 data.
    const user = userEvent.setup();
    const draftSpy = vi.spyOn(v2Api, 'createDraftRevision').mockResolvedValue({
      id: 32,
      campaign_id: 12,
      revision_number: 3,
      status: 'draft',
      icp_definition: {},
      audience_definition: {},
      quality_gates: {},
      budget_definition: {},
      stop_conditions: {},
      sequence_steps: [],
      published_at: null,
      created_at: '2026-07-15T01:00:00Z',
    });
    vi.spyOn(v2Api, 'revisionDiff').mockResolvedValue({
      campaignId: '12',
      baseRevisionId: '30',
      proposedRevisionId: '32',
      diff: { added: [], changed: [], removed: [] },
      diffChecksum: 'c'.repeat(64),
    });

    render(<CampaignAuthoringPanel envelope={authoringEnvelope('live')} onRefresh={vi.fn()} />);
    await user.type(screen.getByLabelText('ICP 简述'), 'EU apparel brands');
    await user.type(screen.getByLabelText('行业（逗号分隔）'), 'apparel');
    await user.type(screen.getByLabelText('受众说明'), 'Sourcing directors');
    await user.type(screen.getByLabelText('Provider 预算 native_limit'), '25');
    await user.type(
      screen.getByLabelText('公共退订 URL'),
      'https://app.example.com/api/unsubscribe/v2',
    );
    await user.type(screen.getByLabelText('模板版本'), 'cold-email-v1');
    await user.type(screen.getByLabelText('主题模板（Email）'), 'Hello buyer');
    await user.type(screen.getByLabelText('正文模板'), 'A note for the buyer.');

    // WHEN: The DRAFT is saved through the authoring form.
    await user.click(screen.getByRole('button', { name: '保存 DRAFT 并读取 diff' }));

    // THEN: The payload uses the exact gate key consumed by backend readiness.
    await waitFor(() => expect(draftSpy).toHaveBeenCalledTimes(1));
    const [, payload] = draftSpy.mock.calls[0];
    expect(payload.quality_gates).toEqual({
      min_fit_score: 70,
      require_verified_contact_point: true,
    });
    expect(payload.quality_gates).not.toHaveProperty('minimum_fit_score');
  });
});
