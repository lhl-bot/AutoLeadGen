import { expect, test, type Page } from '@playwright/test';

type AcquisitionSource = 'csv' | 'ai';

interface FakeActivationState {
  source: AcquisitionSource;
  runCreated: boolean;
  runStatus: 'ready' | 'processing' | 'verified' | 'committed';
  pollCount: number;
  phase: 'idle' | 'search' | 'verify';
  launched: boolean;
  sent: boolean;
}

function candidate(id: number, state: FakeActivationState) {
  const committed = state.runStatus === 'committed';
  const verified = state.runStatus === 'verified' || committed;
  return {
    id,
    run_id: 41,
    row_number: id + 1,
    status: committed ? 'committed' : verified ? 'selected' : 'ready',
    selected: verified,
    company_name: `${state.source === 'csv' ? 'CSV' : 'AI'} 客户 ${id}`,
    normalized_domain: `customer-${id}.example`,
    first_name: `Buyer${id}`,
    last_name: null,
    full_name: `Buyer ${id}`,
    job_title: '采购负责人',
    email: verified ? `buyer${id}@customer-${id}.example` : null,
    source_url: `https://customer-${id}.example/about`,
    evidence: { source: state.source === 'csv' ? 'csv' : 'fake-search', snippet: `客户 ${id} 的公开来源证据` },
    confidence: 1,
    verification_status: verified ? 'valid' : 'unverified',
    verification_source: verified ? 'fake-verifier' : null,
    verification_checked_at: verified ? '2026-07-20T04:00:00Z' : null,
    rejection_reason: null,
    committed_company_id: committed ? 100 + id : null,
    committed_contact_id: committed ? 200 + id : null,
    committed_contact_point_id: committed ? 300 + id : null,
  };
}

function acquisitionRun(state: FakeActivationState) {
  return {
    id: 41,
    source: state.source,
    status: state.runStatus,
    name: state.source === 'csv' ? 'CSV 首批客户' : 'AI 首批客户',
    criteria: {},
    column_mapping: state.source === 'csv' ? { company_name: '公司名称', email: '邮箱' } : {},
    provider: state.source === 'csv' ? 'csv-import' : 'fake-search',
    estimated_units: 5,
    price_version: 'fake-v1',
    last_error: null,
    committed_at: state.runStatus === 'committed' ? '2026-07-20T04:01:00Z' : null,
    created_at: '2026-07-20T04:00:00Z',
    updated_at: '2026-07-20T04:00:00Z',
    candidates: Array.from({ length: 5 }, (_, index) => candidate(index + 1, state)),
    job_id: state.runStatus === 'processing' ? 91 : null,
  };
}

function activationSnapshot(state: FakeActivationState) {
  const completed = [true, true, state.runStatus === 'committed', state.launched, state.sent];
  const currentStep = Math.max(1, completed.findIndex(value => !value) + 1 || 5);
  const labels = ['描述产品与理想客户', '确认发件邮箱', '准备首批客户', '生成并审核邮件', '发出第一封邮件'];
  return {
    activated: state.sent,
    current_step: currentStep,
    started_at: '2026-07-20T04:00:00Z',
    first_sent_at: state.sent ? '2026-07-20T04:05:00Z' : null,
    steps: labels.map((label, index) => ({ key: ['icp', 'mailbox', 'customers', 'plan', 'send'][index], label, completed: completed[index], detail: completed[index] ? '已完成' : '等待处理', href: `/dashboard/get-started?step=${index + 1}` })),
    blockers: [],
    latest_run_id: state.runCreated ? 41 : null,
    campaign_id: state.launched ? 51 : null,
    review_tasks_open: state.launched && !state.sent ? 5 : 0,
  };
}

async function installActivationApi(page: Page, source: AcquisitionSource) {
  const state: FakeActivationState = { source, runCreated: false, runStatus: 'ready', pollCount: 0, phase: 'idle', launched: false, sent: false };
  await page.route('**/api/v2/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });

    if (path === '/api/v2/activation' && method === 'GET') return json(activationSnapshot(state));
    if (path === '/api/v2/settings/icp_playbook') return json({ section: 'icp_playbook', version: 1, values: { summary: '14 天快速家纺打样', target_industries: ['家纺零售'], target_roles: ['采购负责人'], evidence_requirements: ['官网'], playbook_notes: '', proposal_status: 'published' }, updated_at: null, updated_by_user_id: 1, effective_locks: {} });
    if (path === '/api/v2/settings/channels_integrations') return json({ section: 'channels_integrations', version: 1, values: { email_enabled: true, linkedin_enabled: false, whatsapp_enabled: false, public_unsubscribe_url: 'https://pilot.example/unsubscribe', review_before_send: true, integration_notes: '' }, updated_at: null, updated_by_user_id: 1, effective_locks: {} });
    if (path === '/api/v2/channel-accounts') return json([{ id: 1, owner_id: 1, channel: 'email', provider: 'fake-email', provider_account_id: 'pilot@example.test', address: 'pilot@example.test', display_name: 'Pilot sender', enabled: true, health_status: 'healthy', health_checked_at: '2026-07-20T04:00:00Z', daily_limit: 10, timezone: 'Asia/Shanghai', smtp_host: null, smtp_port: null, imap_host: null, imap_port: null, transport: 'fake', credentials_configured: false, legacy_email_account_id: null, last_error: null }]);

    if (path === '/api/v2/acquisition-runs/import/preview' && method === 'POST') {
      state.runCreated = true;
      state.runStatus = 'ready';
      return json(acquisitionRun(state), 201);
    }
    if (path === '/api/v2/acquisition-runs/search' && method === 'POST') {
      state.runCreated = true;
      state.runStatus = 'processing';
      state.pollCount = 0;
      state.phase = 'search';
      return json(acquisitionRun(state), 202);
    }
    if (path === '/api/v2/acquisition-runs/41/verify' && method === 'POST') {
      state.runStatus = 'processing';
      state.pollCount = 0;
      state.phase = 'verify';
      return json(acquisitionRun(state), 202);
    }
    if (path === '/api/v2/acquisition-runs/41/commit' && method === 'POST') {
      state.runStatus = 'committed';
      return json(acquisitionRun(state));
    }
    if (path === '/api/v2/acquisition-runs/41' && method === 'GET') {
      state.pollCount += 1;
      if (state.runStatus === 'processing' && state.pollCount >= 1) {
        state.runStatus = state.phase === 'search' ? 'ready' : 'verified';
        state.phase = 'idle';
      }
      return json(acquisitionRun(state));
    }
    if (path === '/api/v2/activation/launch-preview' && method === 'POST') return json({ checksum: 'a'.repeat(64), effects: ['创建逐封审核的触达计划', '发送前重新检查全部硬门槛'], blockers: [], candidate_count: 5, estimated_send_count: 5 });
    if (path === '/api/v2/activation/launch' && method === 'POST') { state.launched = true; return json({ job_id: 99, status: 'pending' }, 202); }

    if (path === '/api/v2/runtime/heartbeats') return json([]);
    if (path === '/api/v2/runtime/stages') return json([]);
    if (path === '/api/v2/campaigns') return json([]);
    if (path === '/api/v2/analytics/outcomes') return json({ north_star: { qualified_opportunities: 0 }, outcomes: { won: 0, positive_replies: 0 }, diagnostics: { successful_attempts: state.sent ? 1 : 0 } });
    if (path === '/api/v2/providers/usage') return json({ native: [], normalized: [] });
    if (path === '/api/v2/tasks' && method === 'GET') return json(state.launched && !state.sent ? [{ id: 81, task_type: 'draft_review', status: 'open', priority: 'high', title: '审核首封客户邮件', description: '逐封检查并确认', assignee_user_id: null, due_at: null, company_id: 101, contact_id: 201, campaign_id: 51, enrollment_id: 61, conversation_id: null, opportunity_id: null, attempt_id: 71, metadata_json: { attempt_id: 71, channel: 'email', recipient: 'b***@customer-1.example', subject: '原始主题', body: '原始正文', template_version: 'activation-99' }, created_at: '2026-07-20T04:04:00Z' }] : []);
    if (path === '/api/v2/tasks/81' && method === 'PATCH') { state.sent = true; return json({ id: 81, task_type: 'draft_review', status: 'completed', priority: 'high', title: '审核首封客户邮件', description: '逐封检查并确认', assignee_user_id: null, due_at: null, company_id: 101, contact_id: 201, campaign_id: 51, enrollment_id: 61, conversation_id: null, opportunity_id: null, attempt_id: 71, metadata_json: {}, created_at: '2026-07-20T04:04:00Z' }); }
    return json({ detail: `Unhandled fake V2 route: ${method} ${path}` }, 500);
  });
  return state;
}

test.beforeEach(async ({ page }) => {
  // GIVEN: An invited owner with an authenticated, isolated browser session.
  await page.addInitScript(() => {
    window.localStorage.setItem('auth_token', 'activation-e2e-token');
    window.localStorage.setItem('auth_user', JSON.stringify({ username: 'pilot-owner', is_admin: false }));
    window.localStorage.removeItem('v2_activation_run_id');
    window.localStorage.removeItem('v2_activation_step');
  });
  await page.route('**/api/auth/me', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ username: 'pilot-owner', is_admin: false }) }));
  await page.route('**/api/notifications**', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
});

async function approveFirstDraftAndObserveSuccess(page: Page) {
  await page.getByRole('link', { name: '前往今日工作审核邮件' }).click();
  await expect(page.getByRole('heading', { name: '今日工作' })).toBeVisible();
  await page.getByRole('button', { name: '批准发送' }).click();
  await page.getByLabel('主题').fill('销售逐封编辑后的主题');
  await page.getByLabel('正文').fill('销售逐封编辑后的正文。\n\nhttps://pilot.example/unsubscribe');
  await page.getByRole('button', { name: '确认批准并重新入队' }).click();
  await page.goto('/dashboard/get-started?step=5');
  await expect(page.getByRole('heading', { name: '第一封邮件已成功发送' })).toBeVisible();
}

async function launchCommittedCandidates(page: Page) {
  await page.goto('/dashboard/get-started?step=4');
  await expect(page.getByRole('heading', { name: '4. 创建逐封审核计划' })).toBeVisible();
  await page.getByRole('button', { name: '生成启动预检' }).click();
  await expect(page.getByRole('heading', { name: '启动影响' })).toBeVisible();
  await page.getByRole('checkbox', { name: /我确认创建 5 人试跑/ }).check();
  await page.getByRole('button', { name: '创建试跑计划' }).click();
  await expect(page.getByRole('heading', { name: '试跑正在等待逐封审核' })).toBeVisible();
}

test('CSV 导入五名客户 → 验证 → 逐封批准 → fake 首封成功', async ({ page }) => {
  // GIVEN: The owner chooses the CSV path in the shared candidate workspace.
  await installActivationApi(page, 'csv');
  await page.goto('/dashboard/find-customers');
  await page.getByLabel('CSV 文件（最大 2 MB）').setInputFiles({ name: 'customers.csv', mimeType: 'text/csv', buffer: Buffer.from('公司名称,邮箱\nA,a@a.example') });

  // WHEN: Five rows are previewed, explicitly verified, and committed.
  await page.getByRole('button', { name: '安全预览' }).click();
  await expect(page.getByText('已识别字段：')).toBeVisible();
  await page.getByRole('checkbox', { name: /我确认验证所选邮箱/ }).check();
  await page.getByRole('button', { name: '验证 5 人' }).click();
  await expect(page.getByText('验证有效').first()).toBeVisible({ timeout: 5_000 });
  await page.getByRole('checkbox', { name: /我已核对公司、联系人、邮箱和证据/ }).check();
  await page.getByRole('button', { name: '确认入库 5 人' }).click();

  // THEN: The REVIEW plan sends only after one exact draft is edited and approved.
  await launchCommittedCandidates(page);
  await approveFirstDraftAndObserveSuccess(page);
});

test('AI 搜索 → 选择公司 → 补全验证 → 逐封批准 → fake 首封成功', async ({ page }) => {
  // GIVEN: The owner starts AI search with explicit cost confirmation.
  await installActivationApi(page, 'ai');
  await page.goto('/dashboard/find-customers');
  await page.getByRole('tab', { name: 'AI 找客户' }).click();
  await page.getByLabel('产品与客户价值').fill('帮助家纺零售商缩短新品打样周期');
  await page.getByLabel('地区').fill('北欧');
  await page.getByRole('checkbox', { name: /我确认本次付费动作/ }).check();

  // WHEN: Evidence-first companies return and five are selected before enrichment.
  await page.getByRole('button', { name: '查找候选客户' }).click();
  await expect(page.getByText('客户 1 的公开来源证据')).toBeVisible({ timeout: 5_000 });
  for (let id = 1; id <= 5; id += 1) await page.getByRole('checkbox', { name: `选择 AI 客户 ${id}` }).check();
  await page.getByRole('checkbox', { name: /我确认验证所选邮箱/ }).check();
  await page.getByRole('button', { name: '验证 5 人' }).click();
  await expect(page.getByText('验证有效').first()).toBeVisible({ timeout: 5_000 });
  await page.getByRole('checkbox', { name: /我已核对公司、联系人、邮箱和证据/ }).check();
  await page.getByRole('button', { name: '确认入库 5 人' }).click();

  // THEN: The same REVIEW-only launch and exact-copy approval reaches first send.
  await launchCommittedCandidates(page);
  await approveFirstDraftAndObserveSuccess(page);
});
