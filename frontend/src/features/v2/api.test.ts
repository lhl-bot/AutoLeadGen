import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiFetchMock } = vi.hoisted(() => ({ apiFetchMock: vi.fn() }));

vi.mock('@/lib/utils', () => ({ apiFetch: apiFetchMock }));

import { mapDraftReviewPreview, mutateV2Json, type V2Path, v2Api } from './api';

describe('Product V2 mutations', () => {
  beforeEach(() => apiFetchMock.mockReset());

  it('rejects every mutation outside the V2 namespace before making a request', async () => {
    // GIVEN: A caller bypasses the compile-time V2 path type with a legacy path.
    const legacyPath = '/api/workflows/18/start' as V2Path;

    // WHEN: The shared Product V2 mutation primitive receives that path.
    const result = mutateV2Json(legacyPath, { body: {} });

    // THEN: It fails closed and never reaches the authenticated fetch layer.
    await expect(result).rejects.toThrow('may only target /api/v2/*');
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('loads every customer page instead of silently truncating at 500 rows', async () => {
    const companyRows = Array.from({ length: 500 }, (_, index) => ({
      id: index + 1,
      owner_id: 13,
      name: `Company ${index + 1}`,
      normalized_domain: `company-${index + 1}.example`,
      website: null,
      industry: null,
      region: null,
      country: null,
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
      archived_at: null,
    }));
    apiFetchMock.mockImplementation(async (path: string) => ({
      ok: true,
      status: 200,
      json: async () => {
        if (path === '/api/v2/companies?limit=500&offset=0') return companyRows;
        if (path === '/api/v2/companies?limit=500&offset=500') return [];
        if (path === '/api/v2/contacts?limit=500&offset=0') return [];
        if (path === '/api/v2/lists') return [];
        throw new Error(`Unexpected test path: ${path}`);
      },
    }));

    const result = await v2Api.customers();

    expect(result.source).toBe('live');
    expect(result.data.companies).toHaveLength(500);
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/v2/companies?limit=500&offset=500',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('adds a unique idempotency header to asynchronous Campaign commands', async () => {
    // GIVEN: The V2 API accepts an asynchronous Campaign start command.
    apiFetchMock.mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ job_id: 41, status: 'queued' }),
    });

    // WHEN: The UI submits the command through the shared command helper.
    await v2Api.campaignCommand('17', 'start', true);

    // THEN: It only writes V2, sends confirmation, and automatically supplies an idempotency key.
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = apiFetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe('/api/v2/campaigns/17/start');
    expect(options.method).toBe('POST');
    expect(JSON.parse(String(options.body))).toEqual({ confirm_warnings: true });
    expect(new Headers(options.headers).get('Idempotency-Key')).toMatch(/^ui-v2-.{8,}$/);
  });

  it('previews and applies a credential-free V2 email binding through dedicated endpoints', async () => {
    const previewResponse = {
      legacy_email_account_id: 9,
      current_channel_account_id: 7,
      address: 'info@example.com',
      daily_limit: 20,
      timezone: 'UTC',
      preview_checksum: 'b'.repeat(64),
      effects: { credential_copy_count: 0, message_send_count: 0 },
      warnings: [],
    };
    apiFetchMock
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => previewResponse })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          id: 7,
          owner_id: 13,
          channel: 'email',
          provider: 'smtp',
          address: 'info@example.com',
          display_name: 'Sales',
          enabled: true,
          health_status: 'unknown',
          health_checked_at: null,
          daily_limit: 20,
          timezone: 'UTC',
          smtp_host: 'smtp.example.com',
          smtp_port: 465,
          imap_host: 'imap.example.com',
          imap_port: 993,
          transport: 'smtps',
          credentials_configured: true,
          legacy_email_account_id: 9,
          last_error: null,
        }),
      });

    const preview = await v2Api.previewEmailAccountBinding({
      legacyEmailAccountId: 9,
      dailyLimit: 20,
      timezone: 'UTC',
    });
    await v2Api.applyEmailAccountBinding(preview);

    const [previewPath, previewOptions] = apiFetchMock.mock.calls[0] as [string, RequestInit];
    expect(previewPath).toBe('/api/v2/channel-accounts/email-bindings/preview');
    expect(JSON.parse(String(previewOptions.body))).toEqual({
      legacy_email_account_id: 9,
      daily_limit: 20,
      timezone: 'UTC',
    });
    const [applyPath, applyOptions] = apiFetchMock.mock.calls[1] as [string, RequestInit];
    expect(applyPath).toBe('/api/v2/channel-accounts/email-bindings');
    expect(new Headers(applyOptions.headers).get('Idempotency-Key')).toMatch(/^ui-v2-.{8,}$/);
    expect(JSON.parse(String(applyOptions.body))).toEqual({
      legacy_email_account_id: 9,
      daily_limit: 20,
      timezone: 'UTC',
      preview_checksum: 'b'.repeat(64),
      human_confirmed: true,
    });
  });

  it('maps only the safe REVIEW preview allowlist and fails closed when required evidence is missing', () => {
    // GIVEN: A Task metadata payload containing preview evidence plus an unrelated internal field.
    const row = {
      task_type: 'draft_review',
      attempt_id: 87,
      metadata_json: {
        channel: 'email',
        recipient: 'buyer@example.com',
        subject: 'Subject',
        body: 'Exact body',
        template_version: 'intro-v4',
        provider_secret: 'must-never-reach-the-view-model',
      },
    } as Parameters<typeof mapDraftReviewPreview>[0];

    // WHEN / THEN: The mapper preserves the approved preview contract and drops everything else.
    expect(mapDraftReviewPreview(row)).toEqual({
      attemptId: '87',
      channel: 'email',
      recipient: 'buyer@example.com',
      subject: 'Subject',
      body: 'Exact body',
      templateVersion: 'intro-v4',
    });
    expect(mapDraftReviewPreview({ ...row, metadata_json: { channel: 'email', recipient: 'buyer@example.com' } })).toBeUndefined();
  });

  it('uses distinct Task statuses for REVIEW approval and rejection', async () => {
    // GIVEN: The Task endpoint accepts two human review decisions.
    apiFetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: 91 }) });

    // WHEN: The rep approves one Task and dismisses another.
    await v2Api.approveTask('91');
    await v2Api.dismissTask('92');

    // THEN: Approval completes/requeues while rejection dismisses/cancels through V2 PATCH only.
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/v2/tasks/91', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ status: 'completed' }),
    }));
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/v2/tasks/92', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ status: 'dismissed' }),
    }));
  });

  it('creates a qualified opportunity only through the V2 opportunity endpoint', async () => {
    // GIVEN: A human-confirmed handoff payload backed by a persisted assessment and Task.
    apiFetchMock.mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: 55, stage: 'qualified_reply' }),
    });
    const payload = {
      reply_assessment_id: 8,
      source_task_id: 13,
      assignee_user_id: 21,
      next_action: 'Book discovery',
      next_action_due_at: '2026-07-20T01:30:00.000Z',
      fit_confirmed: true,
    };

    // WHEN: The opportunity confirmation helper is called.
    await v2Api.confirmOpportunity(payload);

    // THEN: It writes the exact evidence to /api/v2 and never invents an override.
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [path, options] = apiFetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe('/api/v2/opportunities');
    expect(options.method).toBe('POST');
    expect(JSON.parse(String(options.body))).toEqual(payload);
    expect(JSON.parse(String(options.body))).not.toHaveProperty('fit_override_id');
  });

  it('creates a revision proposal without the forbidden legacy publish field', async () => {
    // GIVEN: The authoring form has a complete structured revision proposal.
    apiFetchMock.mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: 31, revision_number: 2, status: 'draft' }),
    });

    // WHEN: The Product V2 draft helper saves the proposal.
    await v2Api.createDraftRevision('7', {
      icp_definition: { summary: 'EU apparel brands', industries: ['apparel'] },
      audience_definition: { description: 'Sourcing directors' },
      quality_gates: { min_fit_score: 70, require_verified_contact_point: true },
      budget_definition: { native_limit: 25, native_unit: 'fake_calls' },
      stop_conditions: { public_unsubscribe_url: 'http://127.0.0.1:8000/unsubscribe' },
      sequence_steps: [{ position: 1, channel: 'email', wait_minutes: 0, template_version: 'cold-v1' }],
    });

    // THEN: Creation is V2-only and publication cannot be smuggled into this helper.
    const [path, options] = apiFetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe('/api/v2/campaigns/7/revisions');
    expect(options.method).toBe('POST');
    expect(JSON.parse(String(options.body))).toMatchObject({
      quality_gates: { min_fit_score: 70, require_verified_contact_point: true },
      budget_definition: { native_limit: 25, native_unit: 'fake_calls' },
      sequence_steps: [{ position: 1, channel: 'email', template_version: 'cold-v1' }],
    });
    expect(JSON.parse(String(options.body))).not.toHaveProperty('publish');
    expect(JSON.parse(String(options.body)).quality_gates).not.toHaveProperty('minimum_fit_score');
  });

  it('stores the server checksum on the exact revision diff preview', async () => {
    // GIVEN: The server returns a checksum over one concrete base/proposed diff.
    apiFetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        campaign_id: 7,
        base_revision_id: null,
        proposed_revision_id: 31,
        diff: { added: [{ path: 'sequence_steps.0' }] },
        checksum: 'd'.repeat(64),
      }),
    });

    // WHEN: The UI loads the review preview.
    const preview = await v2Api.revisionDiff('7', '31');

    // THEN: The checksum and mandatory null base survive the mapping unchanged.
    expect(preview).toEqual({
      campaignId: '7',
      baseRevisionId: null,
      proposedRevisionId: '31',
      diff: { added: [{ path: 'sequence_steps.0' }] },
      diffChecksum: 'd'.repeat(64),
    });
  });

  it('echoes the exact reviewed diff contract and uses idempotency for publication and Enrollment', async () => {
    // GIVEN: The reviewed DRAFT and selected Contact are accepted by V2.
    apiFetchMock
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 31, revision_number: 2, status: 'published' }) })
      .mockResolvedValueOnce({ ok: true, status: 202, json: async () => ({ job_id: 88, status: 'queued' }) });

    // WHEN: The user publishes and then enrolls through dedicated V2 commands.
    await v2Api.publishRevision({
      campaignId: '7',
      baseRevisionId: '29',
      proposedRevisionId: '31',
      diff: { changed: [{ path: 'quality_gates.min_fit_score', before: 60, after: 70 }] },
      diffChecksum: 'a'.repeat(64),
    });
    await v2Api.enrollContact('7', { contact_id: 19, scheduled_at: null });

    // THEN: Both endpoint-specific commands carry non-empty idempotency keys.
    const [publishPath, publishOptions] = apiFetchMock.mock.calls[0] as [string, RequestInit];
    const [enrollPath, enrollOptions] = apiFetchMock.mock.calls[1] as [string, RequestInit];
    expect(publishPath).toBe('/api/v2/campaigns/7/revisions/31/publish');
    expect(new Headers(publishOptions.headers).get('Idempotency-Key')).toMatch(/^ui-v2-.{8,}$/);
    expect(JSON.parse(String(publishOptions.body))).toEqual({
      base_revision_id: 29,
      reviewed_diff_checksum: 'a'.repeat(64),
      human_confirmed: true,
    });
    expect(enrollPath).toBe('/api/v2/campaigns/7/enrollments');
    expect(new Headers(enrollOptions.headers).get('Idempotency-Key')).toMatch(/^ui-v2-.{8,}$/);
    expect(JSON.parse(String(enrollOptions.body))).toEqual({ contact_id: 19, scheduled_at: null });
  });
});
