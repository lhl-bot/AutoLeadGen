import { apiFetch } from '@/lib/utils';
import type { components } from './openapi.generated';
import {
  sampleAnalytics,
  sampleCampaigns,
  sampleConversations,
  sampleCustomers,
  sampleOpportunities,
  sampleRuntime,
  sampleWork,
} from './demo-data';
import type {
  AnalyticsSnapshot,
  Campaign,
  CampaignAuthoringSnapshot,
  CampaignContactOption,
  CampaignRevisionSummary,
  CampaignStage,
  ChannelAccount,
  Company,
  CompanyWorkspace,
  Contact,
  Conversation,
  CustomerList,
  CustomerSnapshot,
  DataEnvelope,
  EmailAccountBindingPreview,
  Opportunity,
  OpportunityWorkspace,
  OwnerMigrationPreview,
  OwnerMigrationState,
  OutcomeMetric,
  ProductSettingSection,
  ProductSettingSnapshot,
  ReadinessCheck,
  RevisionImpactPreview,
  ReplyIntent,
  RuntimeService,
  RuntimeSnapshot,
  RuntimeState,
  SpendMetric,
  WorkSnapshot,
  WorkTask,
} from './types';

type CompanyRead = components['schemas']['CompanyRead'];
type CompanyWorkspaceRead = components['schemas']['CompanyWorkspaceRead'];
type CompanyUpdatePayload = components['schemas']['CompanyUpdate'];
type ContactRead = components['schemas']['ContactRead'];
type ContactUpdatePayload = components['schemas']['ContactUpdate'];
type AudienceListRead = components['schemas']['AudienceListRead'];
type CampaignRead = components['schemas']['CampaignRead'];
type CampaignReadiness = components['schemas']['CampaignReadiness'];
type CampaignRevisionRead = components['schemas']['CampaignRevisionRead'];
type EnrollmentRead = components['schemas']['EnrollmentRead'];
type ConversationRead = components['schemas']['ConversationRead'];
type OpportunityRead = components['schemas']['OpportunityRead'];
type TaskRead = components['schemas']['TaskRead'];
type WorkerHeartbeatRead = components['schemas']['WorkerHeartbeatRead'];
type StageRuntimeRead = components['schemas']['StageRuntimeRead'];
type ReplyAssessmentRead = components['schemas']['ReplyAssessmentRead'];
type JobAccepted = components['schemas']['JobAccepted'];
type TaskUpdate = components['schemas']['TaskUpdate'];
type OpportunityStageUpdate = components['schemas']['OpportunityStageUpdate'];
type OpportunityConfirm = components['schemas']['OpportunityConfirm'];
type CampaignCreatePayload = components['schemas']['CampaignCreate'];
type CampaignReadPayload = components['schemas']['CampaignRead'];
type CampaignRevisionPayload = components['schemas']['CampaignRevisionRead'];
type EnrollmentCreatePayload = components['schemas']['EnrollmentCreate'];
type OwnerMigrationPreviewRead = components['schemas']['OwnerMigrationPreview'];
type OwnerMigrationPreviewRequest = components['schemas']['OwnerMigrationPreviewRequest'];
type OwnerMigrationStateRead = components['schemas']['OwnerMigrationStateRead'];
type OwnerMigrationSwitch = components['schemas']['OwnerMigrationSwitch'];
type ChannelAccountSummaryRead = components['schemas']['ChannelAccountSummary'];
type EmailAccountBindingPreviewRead = components['schemas']['EmailAccountBindingPreview'];
type EmailAccountBindingDraft = components['schemas']['EmailAccountBindingDraft'];
type EmailAccountBindingApply = components['schemas']['EmailAccountBindingApply'];
export type AcquisitionRunRead = components['schemas']['AcquisitionRunRead'];
export type AcquisitionSearchCreate = components['schemas']['AcquisitionSearchCreate'];
export type AcquisitionVerifyRequest = components['schemas']['AcquisitionVerifyRequest'];
export type AcquisitionCommitRequest = components['schemas']['AcquisitionCommitRequest'];
export type ActivationRead = components['schemas']['ActivationRead'];
export type ActivationLaunchDraft = components['schemas']['ActivationLaunchDraft'];
export type ActivationLaunchPreview = components['schemas']['ActivationLaunchPreview'];
export type ActivationLaunchRequest = components['schemas']['ActivationLaunchRequest'];
export type RouteProposalRead = components['schemas']['RouteProposalRead'];
export type ReviewBatchRead = components['schemas']['ReviewBatchRead'];
export type ReviewBatchItemUpdate = components['schemas']['ReviewBatchItemUpdate'];

type CampaignRevisionDiffResponse = components['schemas']['CampaignRevisionDiff'];
type CampaignRevisionPublishInput = components['schemas']['CampaignRevisionPublish'];

export type CampaignChannel = components['schemas']['Channel'];

export interface CampaignDraftRevisionInput {
  icp_definition: Record<string, unknown>;
  audience_definition: Record<string, unknown>;
  quality_gates: Record<string, unknown>;
  budget_definition: Record<string, unknown>;
  stop_conditions: Record<string, unknown>;
  sequence_steps: Array<{
    position: number;
    channel: CampaignChannel;
    wait_minutes: number;
    template_version: string;
    subject_template?: string;
    body_template?: string;
    conditions?: Record<string, unknown>;
    stop_conditions?: Record<string, unknown>;
  }>;
}

export type V2Path = `/api/v2/${string}`;
export type V2MutationMethod = 'POST' | 'PATCH' | 'PUT' | 'DELETE';

export class V2MutationError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = 'V2MutationError';
  }
}

interface V2MutationOptions<TBody> {
  method?: V2MutationMethod;
  body?: TBody;
  signal?: AbortSignal;
  asyncCommand?: boolean;
  idempotencyKey?: string;
}

type OutcomeResponse = {
  north_star: { qualified_opportunities: number };
  outcomes: { won: number; positive_replies: number };
  diagnostics: { successful_attempts: number };
};

type ProviderUsageResponse = {
  native: Array<{ provider: string; unit: string; units: number | string; results: number }>;
  normalized: Array<{ currency: string; amount: number | string }>;
};

const now = () => new Date().toISOString();
const revisionDiffChecksumPattern = /^[0-9a-f]{64}$/;

function assertRevisionDiffChecksum(value: string): string {
  if (!revisionDiffChecksumPattern.test(value)) {
    throw new Error('Campaign revision diff checksum is invalid');
  }
  return value;
}

async function getJson<T>(path: V2Path, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(path, { method: 'GET', signal });
  if (!response.ok) throw new Error(`GET ${path} failed (${response.status})`);
  return response.json() as Promise<T>;
}

async function getAllPages<T>(path: V2Path, signal?: AbortSignal): Promise<T[]> {
  const pageSize = 500;
  const rows: T[] = [];
  let offset = 0;
  while (true) {
    const separator = path.includes('?') ? '&' : '?';
    const page = await getJson<T[]>(`${path}${separator}limit=${pageSize}&offset=${offset}` as V2Path, signal);
    rows.push(...page);
    if (page.length < pageSize) return rows;
    offset += page.length;
  }
}

function assertV2Path(path: string): asserts path is V2Path {
  if (!path.startsWith('/api/v2/')) {
    throw new Error(`Product V2 mutations may only target /api/v2/* (received ${path})`);
  }
}

function newIdempotencyKey(): string {
  const value = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `ui-v2-${value}`;
}

/** The only write primitive used by Product V2 pages. */
export async function mutateV2Json<TResponse, TBody = unknown>(
  path: V2Path,
  options: V2MutationOptions<TBody> = {},
): Promise<TResponse> {
  assertV2Path(path);
  const headers = new Headers();
  if (options.asyncCommand || options.idempotencyKey) {
    headers.set('Idempotency-Key', options.idempotencyKey ?? newIdempotencyKey());
  }
  const response = await apiFetch(path, {
    method: options.method ?? 'POST',
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    headers,
    signal: options.signal,
  });
  const payload = response.status === 204
    ? undefined
    : await response.json().catch(() => undefined) as unknown;
  if (!response.ok) {
    const wrapper = payload && typeof payload === 'object' ? payload as { detail?: unknown } : undefined;
    const detail = wrapper?.detail ?? payload;
    const structured = detail && typeof detail === 'object'
      ? detail as { code?: string; message?: string }
      : undefined;
    const message = structured?.message
      ?? (typeof detail === 'string' ? detail : `V2 mutation failed (${response.status})`);
    throw new V2MutationError(message, response.status, structured?.code, detail);
  }
  return payload as TResponse;
}

export type CampaignCommandAction = 'start' | 'pause' | 'complete';

async function campaignCommand(campaignId: string, action: CampaignCommandAction, confirmWarnings = false) {
  return mutateV2Json<JobAccepted, { confirm_warnings: boolean }>(
    `/api/v2/campaigns/${campaignId}/${action}`,
    { body: { confirm_warnings: confirmWarnings }, asyncCommand: true },
  );
}

async function createCampaign(payload: CampaignCreatePayload) {
  return mutateV2Json<CampaignReadPayload, CampaignCreatePayload>('/api/v2/campaigns', {
    body: payload,
  });
}

/** A revision proposal is always persisted as DRAFT; publication is a separate reviewed command. */
async function createDraftRevision(campaignId: string, payload: CampaignDraftRevisionInput) {
  return mutateV2Json<CampaignRevisionPayload, CampaignDraftRevisionInput>(
    `/api/v2/campaigns/${campaignId}/revisions`,
    { body: payload },
  );
}

async function revisionDiff(campaignId: string, revisionId: string): Promise<RevisionImpactPreview> {
  const value = await getJson<CampaignRevisionDiffResponse>(
    `/api/v2/campaigns/${campaignId}/revisions/${revisionId}/diff`,
  );
  return {
    campaignId: String(value.campaign_id),
    baseRevisionId: value.base_revision_id == null ? null : String(value.base_revision_id),
    proposedRevisionId: String(value.proposed_revision_id),
    diff: value.diff as unknown as Record<string, unknown>,
    diffChecksum: assertRevisionDiffChecksum(value.checksum),
  };
}

async function publishRevision(reviewedPreview: RevisionImpactPreview) {
  const reviewedDiffChecksum = assertRevisionDiffChecksum(reviewedPreview.diffChecksum);
  const baseRevisionId = reviewedPreview.baseRevisionId == null
    ? null
    : Number(reviewedPreview.baseRevisionId);
  if (baseRevisionId !== null && !Number.isSafeInteger(baseRevisionId)) {
    throw new Error('Reviewed Campaign base revision id is invalid');
  }

  return mutateV2Json<CampaignRevisionPayload, CampaignRevisionPublishInput>(
    `/api/v2/campaigns/${reviewedPreview.campaignId}/revisions/${reviewedPreview.proposedRevisionId}/publish`,
    {
      body: {
        base_revision_id: baseRevisionId,
        reviewed_diff_checksum: reviewedDiffChecksum,
        human_confirmed: true,
      },
      idempotencyKey: newIdempotencyKey(),
    },
  );
}

async function enrollContact(campaignId: string, payload: EnrollmentCreatePayload) {
  return mutateV2Json<JobAccepted, EnrollmentCreatePayload>(
    `/api/v2/campaigns/${campaignId}/enrollments`,
    { body: payload, asyncCommand: true },
  );
}

async function completeTask(taskId: string) {
  return updateTask(taskId, { status: 'completed' });
}

async function updateTask(taskId: string, payload: TaskUpdate) {
  return mutateV2Json<TaskRead, TaskUpdate>(`/api/v2/tasks/${taskId}`, {
    method: 'PATCH',
    body: payload,
  });
}

async function approveTask(taskId: string, reviewed?: { subject?: string; body: string }) {
  return updateTask(taskId, {
    status: 'completed',
    ...(reviewed ? { review_subject: reviewed.subject, review_body: reviewed.body } : {}),
  });
}

async function dismissTask(taskId: string) {
  return updateTask(taskId, { status: 'dismissed' });
}

async function routeProposals(signal?: AbortSignal): Promise<RouteProposalRead[]> {
  return getJson<RouteProposalRead[]>('/api/v2/route-proposals?limit=100', signal);
}

async function reviewBatches(signal?: AbortSignal): Promise<ReviewBatchRead[]> {
  return getJson<ReviewBatchRead[]>('/api/v2/review-batches?limit=100', signal);
}

async function previewReviewBatch(input: {
  routeProposalIds: number[];
  approvalId: string;
  priceVersion: string;
  estimatedCost?: number;
  batch?: ReviewBatchRead;
}) {
  return mutateV2Json<ReviewBatchRead, components['schemas']['ReviewBatchPreviewRequest']>(
    '/api/v2/review-batches/preview',
    {
      body: {
        route_proposal_ids: input.routeProposalIds,
        idempotency_key: input.batch?.idempotency_key ?? newIdempotencyKey(),
        approval_id: input.approvalId,
        price_version: input.priceVersion,
        estimated_cost: input.estimatedCost ?? 0,
        batch_id: input.batch?.id,
      },
    },
  );
}

async function editReviewBatchItem(batchId: number, itemId: number, payload: ReviewBatchItemUpdate) {
  return mutateV2Json<ReviewBatchRead, ReviewBatchItemUpdate>(
    `/api/v2/review-batches/${batchId}/items/${itemId}`,
    { method: 'PATCH', body: payload },
  );
}

async function approveReviewBatch(batch: ReviewBatchRead) {
  if (!batch.preview_checksum) throw new Error('请先重新预览本批次');
  return mutateV2Json<ReviewBatchRead, components['schemas']['ReviewBatchApprove']>(
    `/api/v2/review-batches/${batch.id}/approve`,
    {
      body: {
        preview_checksum: batch.preview_checksum,
        approval_id: batch.approval_id,
        human_confirmed: true,
      },
    },
  );
}

async function rejectReviewBatch(batchId: number, reason: string) {
  return mutateV2Json<ReviewBatchRead, components['schemas']['ReviewBatchReject']>(
    `/api/v2/review-batches/${batchId}/reject`,
    { body: { reason } },
  );
}

const positiveReplyIntents = new Set<ReplyIntent>(['interested', 'more_info', 'referral', 'meeting']);

export function isPositiveReplyIntent(intent: ReplyIntent): boolean {
  return positiveReplyIntents.has(intent);
}

async function confirmReplyAssessment(assessmentId: string, intent: ReplyIntent) {
  return mutateV2Json<TaskRead, { intent: ReplyIntent; is_positive: boolean }>(
    `/api/v2/reply-assessments/${assessmentId}/confirm`,
    { body: { intent, is_positive: isPositiveReplyIntent(intent) } },
  );
}

async function updateOpportunityStage(opportunityId: string, payload: OpportunityStageUpdate) {
  return mutateV2Json<OpportunityRead, OpportunityStageUpdate>(
    `/api/v2/opportunities/${opportunityId}/stage`,
    { method: 'PATCH', body: payload },
  );
}

async function confirmOpportunity(payload: OpportunityConfirm) {
  return mutateV2Json<OpportunityRead, OpportunityConfirm>('/api/v2/opportunities', {
    body: payload,
  });
}

function sampleEnvelope<T>(data: T, warning = '本地 V2 API 不可用，当前显示已明确标记的示例数据。'): DataEnvelope<T> {
  return { data, source: 'sample', observedAt: now(), warning };
}

export function explicitDemoModeEnabled(): boolean {
  if (process.env.NEXT_PUBLIC_V2_DEMO_MODE === 'true') return true;
  if (typeof window === 'undefined') return false;
  try { return window.localStorage.getItem('v2_demo_mode') === '1'; } catch { return false; }
}

function demoFallbackOrThrow<T>(error: unknown, data: T, warning?: string): DataEnvelope<T> {
  if (explicitDemoModeEnabled()) return sampleEnvelope(data, warning);
  throw error instanceof Error ? error : new Error(warning ?? 'V2 API 数据加载失败');
}

function liveEnvelope<T>(data: T, observedAt = now(), warning?: string): DataEnvelope<T> {
  return { data, source: 'live', observedAt, warning };
}

function numeric(value: string | number | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function heartbeatState(item: WorkerHeartbeatRead): RuntimeState {
  if (!item.lease_expires_at) return 'offline';
  const leaseExpiresAt = Date.parse(item.lease_expires_at);
  if (!Number.isFinite(leaseExpiresAt) || leaseExpiresAt <= Date.now()) return 'offline';
  return item.status;
}

const workerLabels: Record<string, string> = {
  prospecting: '客户发现',
  research: '研究与补全',
  outbound: '外发执行',
  inbox: '收件箱监听',
  omnichannel: '全渠道同步',
};

export function mapRuntimeSnapshot(rows: WorkerHeartbeatRead[]): RuntimeSnapshot {
  const newestByType = new Map<string, WorkerHeartbeatRead>();
  for (const row of rows) {
    const current = newestByType.get(row.worker_type);
    if (!current || Date.parse(row.last_seen_at) > Date.parse(current.last_seen_at)) newestByType.set(row.worker_type, row);
  }
  const services: RuntimeService[] = Object.entries(workerLabels).map(([id, label]) => {
    const row = newestByType.get(id);
    return row
      ? { id, label, state: heartbeatState(row), detail: row.worker_name, lastSeenAt: row.last_seen_at }
      : { id, label, state: 'unknown', detail: '没有持久化心跳' };
  });
  const timestamp = rows.reduce((latest, row) => Date.parse(row.last_seen_at) > Date.parse(latest) ? row.last_seen_at : latest, new Date(0).toISOString());
  return { services, activeCampaigns: 0, recentMessages: 0, timestamp };
}

const readinessLinks: Record<string, string> = {
  audience: '/dashboard/customers',
  valid_audience: '/dashboard/customers',
  contact_point: '/dashboard/customers',
  email_account: '/dashboard/settings/channels',
  channel_accounts: '/dashboard/settings/channels',
  worker_outbound: '/dashboard/work',
  worker_inbox: '/dashboard/work',
  sequence: '/dashboard/campaigns',
  published_revision: '/dashboard/campaigns',
  safety_lock: '/dashboard/work',
  public_unsubscribe_url: '/dashboard/settings/channels',
  budget: '/dashboard/analytics',
};

export function mapReadiness(value: CampaignReadiness): ReadinessCheck[] {
  return [...value.blockers, ...value.warnings].map(item => ({
    key: item.code,
    label: item.code.replaceAll('_', ' '),
    severity: item.severity === 'blocker' || item.severity === 'warning' ? item.severity : 'info',
    passed: item.passed,
    detail: item.message,
    remediationHref: readinessLinks[item.code],
  }));
}

export function resolveCompositeSource(sources: DataEnvelope<unknown>['source'][]): DataEnvelope<unknown>['source'] {
  const distinctSources = new Set(sources);
  if (distinctSources.size === 1) return sources[0] ?? 'sample';
  return 'mixed';
}

const stageDefinitions: Array<[CampaignStage['key'], string, string[]]> = [
  ['discover', '发现', ['discover', 'prospecting']],
  ['research', '研究', ['research', 'enrichment']],
  ['draft', '草稿', ['draft']],
  ['send', '发送', ['send', 'outbound']],
  ['reply', '回复', ['reply', 'inbox']],
];

function mapStages(campaignId: number, rows: StageRuntimeRead[]): CampaignStage[] {
  const own = rows.filter(row => row.campaign_id === campaignId);
  return stageDefinitions.map(([key, label, aliases]) => {
    const row = own.find(item => aliases.includes(item.stage_name.toLowerCase()));
    return {
      key,
      label,
      state: row?.status ?? 'unknown',
      detail: row?.reason || (row ? `更新于 ${new Date(row.updated_at).toLocaleString('zh-CN')}` : '未上报阶段状态'),
    };
  });
}

function companyName(companies: Map<number, CompanyRead>, id: number): string {
  return companies.get(id)?.name ?? `公司 #${id}`;
}

function contactName(contacts: Map<number, ContactRead>, id: number): string {
  return contacts.get(id)?.full_name ?? `联系人 #${id}`;
}

function mapCustomerContact(row: ContactRead, companyById: Map<number, CompanyRead>): Contact {
  const contactPoints = row.contact_points ?? [];
  const primary = contactPoints.find(point => point.is_primary) ?? contactPoints[0];
  const email = contactPoints.find(point => point.channel === 'email');
  return {
    id: String(row.id),
    companyId: String(row.company_id),
    name: row.full_name,
    company: companyName(companyById, row.company_id),
    domain: companyById.get(row.company_id)?.normalized_domain ?? '',
    email: email?.value ?? '未提供',
    title: row.job_title ?? '未提供',
    status: primary ? `${primary.verification_status} / ${primary.availability_status}` : '无联系点',
    verified: Boolean(email?.verification_status === 'valid' && email.availability_status === 'available'),
    channels: Array.from(new Set(contactPoints.map(point => point.channel))),
  };
}

function mapCustomerCompany(row: CompanyRead, contacts: Contact[]): Company {
  const own = contacts.filter(contact => contact.companyId === String(row.id));
  return {
    id: String(row.id),
    name: row.name,
    domain: row.normalized_domain ?? '',
    industry: row.industry ?? '未提供',
    region: row.region ?? row.country ?? '未提供',
    contacts: own.length,
    verifiedContacts: own.filter(contact => contact.verified).length,
  };
}

function taskType(type: TaskRead['task_type']): WorkTask['type'] {
  if (type === 'campaign_readiness') return 'readiness';
  if (type === 'research_required' || type === 'contact_enrichment_required') return 'data';
  if (type === 'draft_review') return 'approval';
  if (type === 'reply_triage') return 'reply';
  if (type === 'provider_budget_alert') return 'budget';
  if (type === 'sales_handoff') return 'handoff';
  return 'exception';
}

function taskHref(type: WorkTask['type']): string {
  if (type === 'reply') return '/dashboard/inbox';
  if (type === 'handoff') return '/dashboard/opportunities';
  if (type === 'data') return '/dashboard/customers';
  if (type === 'budget') return '/dashboard/analytics';
  return '/dashboard/campaigns';
}

async function runtime(signal?: AbortSignal): Promise<DataEnvelope<RuntimeSnapshot>> {
  try {
    const rows = await getJson<WorkerHeartbeatRead[]>('/api/v2/runtime/heartbeats', signal);
    const data = mapRuntimeSnapshot(rows);
    return liveEnvelope(data, data.timestamp === new Date(0).toISOString() ? now() : data.timestamp, rows.length ? undefined : 'V2 API 可用，但尚无 worker 心跳。');
  } catch (error) {
    return demoFallbackOrThrow(error, sampleRuntime, '未取得 V2 heartbeat；运行状态不可判定。');
  }
}

async function campaigns(signal?: AbortSignal): Promise<DataEnvelope<Campaign[]>> {
  try {
    const [campaignRows, stageRows] = await Promise.all([
      getJson<CampaignRead[]>('/api/v2/campaigns', signal),
      getJson<StageRuntimeRead[]>('/api/v2/runtime/stages', signal),
    ]);
    const bundles = await Promise.all(campaignRows.map(async campaign => {
      const [readiness, enrollments, revisions] = await Promise.all([
        getJson<CampaignReadiness>(`/api/v2/campaigns/${campaign.id}/readiness`, signal),
        getJson<EnrollmentRead[]>(`/api/v2/campaigns/${campaign.id}/enrollments`, signal),
        getJson<CampaignRevisionRead[]>(`/api/v2/campaigns/${campaign.id}/revisions`, signal),
      ]);
      const published = revisions.find(revision => revision.status === 'published') ?? revisions[0];
      const budget = published?.budget_definition as Record<string, unknown> | undefined;
      const budgetLimit = budget && (budget.native_limit ?? budget.daily_limit);
      return {
        id: String(campaign.id),
        name: campaign.name,
        lifecycle: campaign.lifecycle,
        mode: campaign.run_mode,
        priority: campaign.priority,
        budgetLimit: budgetLimit === undefined ? null : numeric(budgetLimit as number | string),
        enrollments: enrollments.length,
        positiveSignals: enrollments.filter(item => Boolean(item.positive_signal_at)).length,
        stages: mapStages(campaign.id, stageRows),
        readiness: mapReadiness(readiness),
      } satisfies Campaign;
    }));
    return liveEnvelope(bundles);
  } catch (error) {
    const detail = error instanceof Error ? error.message : '未知错误';
    return demoFallbackOrThrow(error, sampleCampaigns, `V2 Campaign 数据加载失败：${detail}`);
  }
}

function mapRevision(row: CampaignRevisionRead): CampaignRevisionSummary {
  return {
    id: String(row.id),
    campaignId: String(row.campaign_id),
    revisionNumber: row.revision_number,
    status: row.status,
    createdAt: row.created_at,
    icpDefinition: row.icp_definition as unknown as Record<string, unknown>,
    audienceDefinition: row.audience_definition as unknown as Record<string, unknown>,
    qualityGates: row.quality_gates as unknown as Record<string, unknown>,
    budgetDefinition: row.budget_definition as unknown as Record<string, unknown>,
    stopConditions: row.stop_conditions as unknown as Record<string, unknown>,
  };
}

async function campaignAuthoring(signal?: AbortSignal): Promise<DataEnvelope<CampaignAuthoringSnapshot>> {
  const campaignResult = await campaigns(signal);
  if (campaignResult.source !== 'live') {
    return {
      data: { campaigns: campaignResult.data, revisionsByCampaign: {}, contacts: [] },
      source: campaignResult.source,
      observedAt: campaignResult.observedAt,
      warning: campaignResult.warning ?? '未取得完整 V2 编排数据，所有写操作已锁定。',
    };
  }
  try {
    const [companyRows, contactRows, revisionRows] = await Promise.all([
      getAllPages<CompanyRead>('/api/v2/companies', signal),
      getAllPages<ContactRead>('/api/v2/contacts', signal),
      Promise.all(campaignResult.data.map(campaign =>
        getJson<CampaignRevisionRead[]>(`/api/v2/campaigns/${campaign.id}/revisions`, signal),
      )),
    ]);
    const companyById = new Map(companyRows.map(row => [row.id, row]));
    const contacts: CampaignContactOption[] = contactRows.map(row => ({
      id: String(row.id),
      companyId: String(row.company_id),
      label: row.full_name,
      company: companyName(companyById, row.company_id),
      contactPoints: (row.contact_points ?? []).map(point =>
        `${point.channel}: ${point.value} · ${point.verification_status}/${point.availability_status}`,
      ),
    }));
    const revisionsByCampaign = Object.fromEntries(campaignResult.data.map((campaign, index) => [
      campaign.id,
      (revisionRows[index] ?? []).map(mapRevision),
    ]));
    return liveEnvelope({ campaigns: campaignResult.data, revisionsByCampaign, contacts });
  } catch (error) {
    return demoFallbackOrThrow(
      error,
      { campaigns: campaignResult.data, revisionsByCampaign: {}, contacts: [] },
      'Campaign 概览来自 V2，但 Revision 或 Contact 数据不完整；所有写操作已锁定。',
    );
  }
}

async function customers(signal?: AbortSignal): Promise<DataEnvelope<CustomerSnapshot>> {
  try {
    const [companyRows, contactRows, listRows] = await Promise.all([
      getAllPages<CompanyRead>('/api/v2/companies', signal),
      getAllPages<ContactRead>('/api/v2/contacts', signal),
      getJson<AudienceListRead[]>('/api/v2/lists', signal),
    ]);
    const companyById = new Map(companyRows.map(row => [row.id, row]));
    const contacts = contactRows.map(row => mapCustomerContact(row, companyById));
    const companies = companyRows.map(row => mapCustomerCompany(row, contacts));
    const lists: CustomerList[] = listRows.map(row => ({ id: String(row.id), name: row.name, description: row.description ?? undefined, total: null }));
    return liveEnvelope({ contacts, companies, lists });
  } catch (error) {
    return demoFallbackOrThrow(error, sampleCustomers);
  }
}

async function companyWorkspace(companyId: string, signal?: AbortSignal): Promise<CompanyWorkspace> {
  const row = await getJson<CompanyWorkspaceRead>(`/api/v2/companies/${companyId}/workspace`, signal);
  const companyById = new Map([[row.company.id, row.company]]);
  const contacts = row.contacts.map(contact => mapCustomerContact(contact, companyById));
  return {
    company: mapCustomerCompany(row.company, contacts),
    contacts,
    evidence: row.evidence_snapshots.map(snapshot => ({
      id: String(snapshot.id),
      source: snapshot.source,
      sourceUrl: snapshot.source_url ?? undefined,
      confidence: numeric(snapshot.confidence),
      capturedAt: snapshot.captured_at,
      evidence: snapshot.evidence,
    })),
    outreach: {
      enrollmentCount: row.outreach.enrollment_count,
      sentCount: row.outreach.sent_count,
      replyCount: row.outreach.reply_count,
      lastContactAt: row.outreach.last_contact_at ?? undefined,
    },
  };
}

async function updateCompany(companyId: string, payload: CompanyUpdatePayload) {
  return mutateV2Json<CompanyRead, CompanyUpdatePayload>(`/api/v2/companies/${companyId}`, {
    method: 'PATCH',
    body: payload,
  });
}

async function updateContact(contactId: string, payload: ContactUpdatePayload) {
  return mutateV2Json<ContactRead, ContactUpdatePayload>(`/api/v2/contacts/${contactId}`, {
    method: 'PATCH',
    body: payload,
  });
}

async function inbox(signal?: AbortSignal): Promise<DataEnvelope<Conversation[]>> {
  try {
    const [conversationRows, companyRows, contactRows] = await Promise.all([
      getJson<ConversationRead[]>('/api/v2/conversations?limit=200', signal),
      getAllPages<CompanyRead>('/api/v2/companies', signal),
      getAllPages<ContactRead>('/api/v2/contacts', signal),
    ]);
    const companiesById = new Map(companyRows.map(row => [row.id, row]));
    const contactsById = new Map(contactRows.map(row => [row.id, row]));
    const assessmentRows = await Promise.all(conversationRows.map(row =>
      getJson<ReplyAssessmentRead[]>(`/api/v2/conversations/${row.id}/assessments`, signal),
    ));
    return liveEnvelope(conversationRows.map((row, index) => {
      const assessment = assessmentRows[index]?.[0];
      return {
        id: String(row.id),
        contactName: contactName(contactsById, row.contact_id),
        company: companyName(companiesById, row.company_id),
        channel: row.channel,
        subject: row.subject ?? '无主题',
        snippet: row.latest_reply_body ?? '尚无回复正文',
        lastReplyAt: row.last_message_at ?? undefined,
        intent: assessment?.intent,
        assessment: assessment ? {
          id: String(assessment.id),
          intent: assessment.intent,
          isPositive: assessment.is_positive,
          confidence: assessment.confidence == null ? undefined : numeric(assessment.confidence),
          status: assessment.status,
          rationale: assessment.rationale ?? undefined,
        } : undefined,
        handoffRecommended: Boolean(assessment?.is_positive),
        status: row.status,
      } satisfies Conversation;
    }));
  } catch (error) {
    return demoFallbackOrThrow(error, sampleConversations);
  }
}

async function opportunities(signal?: AbortSignal): Promise<DataEnvelope<Opportunity[]>> {
  try {
    const [rows, companyRows, contactRows] = await Promise.all([
      getJson<OpportunityRead[]>('/api/v2/opportunities', signal),
      getAllPages<CompanyRead>('/api/v2/companies', signal),
      getAllPages<ContactRead>('/api/v2/contacts', signal),
    ]);
    const companiesById = new Map(companyRows.map(row => [row.id, row]));
    const contactsById = new Map(contactRows.map(row => [row.id, row]));
    return liveEnvelope(rows.map(row => ({
      id: String(row.id),
      name: `${companyName(companiesById, row.company_id)} · ${row.stage}`,
      company: companyName(companiesById, row.company_id),
      contact: contactName(contactsById, row.contact_id),
      stage: row.stage,
      nextStep: row.next_action,
      nextActionDueAt: row.next_action_due_at,
      ownerId: String(row.assignee_user_id),
      value: row.value_amount == null ? undefined : numeric(row.value_amount),
      currency: row.currency ?? undefined,
      source: 'opportunity',
    })));
  } catch (error) {
    return demoFallbackOrThrow(error, sampleOpportunities);
  }
}

function metadataInteger(metadata: TaskRead['metadata_json'], key: string): string | undefined {
  if (!metadata || typeof metadata !== 'object') return undefined;
  const value = Number((metadata as Record<string, unknown>)[key]);
  return Number.isInteger(value) && value > 0 ? String(value) : undefined;
}

function metadataString(metadata: TaskRead['metadata_json'], key: string): string | undefined {
  if (!metadata || typeof metadata !== 'object') return undefined;
  const value = (metadata as Record<string, unknown>)[key];
  return typeof value === 'string' && value.trim() ? value : undefined;
}

export function mapDraftReviewPreview(
  row: Pick<TaskRead, 'task_type' | 'attempt_id' | 'metadata_json'>,
): WorkTask['draftReview'] {
  if (row.task_type !== 'draft_review') return undefined;
  const attemptId = row.attempt_id == null
    ? metadataInteger(row.metadata_json, 'attempt_id')
    : String(row.attempt_id);
  const channel = metadataString(row.metadata_json, 'channel');
  const recipient = metadataString(row.metadata_json, 'recipient');
  const body = metadataString(row.metadata_json, 'body');
  if (!attemptId || !channel || !recipient || !body) return undefined;
  return {
    attemptId,
    channel,
    recipient,
    subject: metadataString(row.metadata_json, 'subject'),
    body,
    templateVersion: metadataString(row.metadata_json, 'template_version'),
  };
}

async function opportunityWorkspace(signal?: AbortSignal): Promise<DataEnvelope<OpportunityWorkspace>> {
  try {
    const [opportunityResult, openTasks, inProgressTasks, companyRows, contactRows, conversationRows] = await Promise.all([
      opportunities(signal),
      getAllPages<TaskRead>('/api/v2/tasks?status=open', signal),
      getAllPages<TaskRead>('/api/v2/tasks?status=in_progress', signal),
      getAllPages<CompanyRead>('/api/v2/companies', signal),
      getAllPages<ContactRead>('/api/v2/contacts', signal),
      getJson<ConversationRead[]>('/api/v2/conversations?limit=500', signal),
    ]);
    if (opportunityResult.source !== 'live') {
      return sampleEnvelope({ handoffs: [], opportunities: opportunityResult.data }, opportunityResult.warning);
    }
    const companiesById = new Map(companyRows.map(row => [row.id, row]));
    const contactsById = new Map(contactRows.map(row => [row.id, row]));
    const conversationsById = new Map(conversationRows.map(row => [row.id, row]));
    const handoffs = [...openTasks, ...inProgressTasks]
      .filter(task => task.task_type === 'sales_handoff')
      .map(task => {
        const conversation = task.conversation_id == null ? undefined : conversationsById.get(task.conversation_id);
        const companyId = task.company_id ?? conversation?.company_id;
        const contactId = task.contact_id ?? conversation?.contact_id;
        const conversationLabel = conversation
          ? `${conversation.channel} · ${conversation.subject ?? '无主题'}`
          : task.conversation_id == null ? '未关联会话' : `会话 #${task.conversation_id}`;
        return {
          id: String(task.id),
          replyAssessmentId: metadataInteger(task.metadata_json, 'reply_assessment_id'),
          status: task.status as 'open' | 'in_progress',
          priority: task.priority,
          title: task.title,
          detail: task.description ?? conversation?.latest_reply_body ?? '无回复摘要',
          companyId: companyId == null ? undefined : String(companyId),
          company: companyId == null ? '未关联公司' : companyName(companiesById, companyId),
          contactId: contactId == null ? undefined : String(contactId),
          contact: contactId == null ? '未关联联系人' : contactName(contactsById, contactId),
          conversationId: task.conversation_id == null ? undefined : String(task.conversation_id),
          conversation: conversationLabel,
          channel: conversation?.channel,
          assigneeUserId: task.assignee_user_id == null ? undefined : String(task.assignee_user_id),
          dueAt: task.due_at ?? undefined,
        };
      });
    return liveEnvelope({ handoffs, opportunities: opportunityResult.data });
  } catch (error) {
    return demoFallbackOrThrow(error, { handoffs: [], opportunities: sampleOpportunities });
  }
}

async function analytics(signal?: AbortSignal): Promise<DataEnvelope<AnalyticsSnapshot>> {
  try {
    const [outcomes, usage] = await Promise.all([
      getJson<OutcomeResponse>('/api/v2/analytics/outcomes', signal),
      getJson<ProviderUsageResponse>('/api/v2/providers/usage', signal),
    ]);
    const qualified = outcomes.north_star.qualified_opportunities;
    const positive = outcomes.outcomes.positive_replies;
    const sent = outcomes.diagnostics.successful_attempts;
    const metrics: OutcomeMetric[] = [
      { key: 'qualified', label: '合格商机', value: qualified, detail: '北极星指标' },
      { key: 'positive', label: '人工确认正向信号', value: positive, detail: '跨渠道确认' },
      { key: 'won', label: 'Won', value: outcomes.outcomes.won, detail: '已填写金额与成交日期' },
      { key: 'sent', label: '成功触达', value: sent, detail: '诊断指标，不作为北极星' },
    ];
    const spend: SpendMetric[] = usage.native.map(item => ({
      key: `${item.provider}:${item.unit}`,
      label: item.provider,
      units: numeric(item.units),
      unit: item.unit,
      results: item.results,
    }));
    return liveEnvelope({
      outcomes: metrics,
      funnel: [
        { key: 'sent', label: '成功触达', count: sent },
        { key: 'positive', label: '正向信号', count: positive },
        { key: 'qualified', label: '合格商机', count: qualified },
        { key: 'won', label: 'Won', count: outcomes.outcomes.won },
      ],
      spend,
      replyRate: sent ? (positive / sent) * 100 : 0,
      normalizedSpend: usage.normalized.map(item => ({ currency: item.currency, amount: numeric(item.amount) })),
    }, now(), usage.normalized.length ? undefined : '未配置价格版本时只展示供应商原生计费单位。');
  } catch (error) {
    return demoFallbackOrThrow(error, sampleAnalytics);
  }
}

async function work(signal?: AbortSignal): Promise<DataEnvelope<WorkSnapshot>> {
  try {
    const [runtimeResult, campaignResult, analyticsResult, taskRows] = await Promise.all([
      runtime(signal),
      campaigns(signal),
      analytics(signal),
      getAllPages<TaskRead>('/api/v2/tasks?status=open', signal),
    ]);
    const campaignNames = new Map(campaignResult.data.map(campaign => [campaign.id, campaign.name]));
    const tasks: WorkTask[] = taskRows.map(row => {
      const type = taskType(row.task_type);
      return {
        id: String(row.id),
        title: row.title,
        detail: row.description ?? row.task_type.replaceAll('_', ' '),
        type,
        priority: row.priority,
        href: taskHref(type),
        campaign: row.campaign_id == null ? undefined : campaignNames.get(String(row.campaign_id)),
        dueAt: row.due_at ?? undefined,
        draftReview: mapDraftReviewPreview(row),
      };
    });
    const liveRuntime = {
      ...runtimeResult.data,
      activeCampaigns: campaignResult.data.filter(item => item.lifecycle === 'running').length,
    };
    const source = resolveCompositeSource([
      runtimeResult.source,
      campaignResult.source,
      analyticsResult.source,
      'live', // The task request above completed against V2.
    ]);
    const warnings = [
      source === 'mixed' ? '部分区块来自实时 V2 API，部分区块是示例回退；请勿将整页视为实时数据。' : undefined,
      runtimeResult.warning,
      campaignResult.warning,
      analyticsResult.warning,
    ].filter(Boolean).join(' ') || undefined;
    return {
      data: { runtime: liveRuntime, metrics: analyticsResult.data.outcomes, tasks, campaigns: campaignResult.data },
      source,
      observedAt: runtimeResult.observedAt,
      warning: warnings,
    };
  } catch (error) {
    return demoFallbackOrThrow(error, sampleWork);
  }
}

async function acquisitionRun(runId: number, signal?: AbortSignal): Promise<AcquisitionRunRead> {
  return getJson<AcquisitionRunRead>(`/api/v2/acquisition-runs/${runId}`, signal);
}

async function importAcquisitionCsv(file: File, name = 'CSV 首批客户'): Promise<AcquisitionRunRead> {
  const form = new FormData();
  form.set('file', file);
  form.set('name', name);
  const response = await apiFetch('/api/v2/acquisition-runs/import/preview', {
    method: 'POST',
    body: form,
    headers: { 'Idempotency-Key': newIdempotencyKey() },
  });
  const payload = await response.json().catch(() => undefined) as unknown;
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' ? (payload as { detail?: unknown }).detail : payload;
    const structured = detail && typeof detail === 'object' ? detail as { code?: string; message?: string } : undefined;
    throw new V2MutationError(structured?.message ?? 'CSV 预览失败', response.status, structured?.code, detail);
  }
  return payload as AcquisitionRunRead;
}

async function searchAcquisition(payload: AcquisitionSearchCreate): Promise<AcquisitionRunRead> {
  return mutateV2Json<AcquisitionRunRead, AcquisitionSearchCreate>('/api/v2/acquisition-runs/search', {
    body: payload,
    asyncCommand: true,
  });
}

async function verifyAcquisition(runId: number, payload: AcquisitionVerifyRequest): Promise<AcquisitionRunRead> {
  return mutateV2Json<AcquisitionRunRead, AcquisitionVerifyRequest>(`/api/v2/acquisition-runs/${runId}/verify`, {
    body: payload,
    asyncCommand: true,
  });
}

async function commitAcquisition(runId: number, payload: AcquisitionCommitRequest): Promise<AcquisitionRunRead> {
  return mutateV2Json<AcquisitionRunRead, AcquisitionCommitRequest>(`/api/v2/acquisition-runs/${runId}/commit`, {
    body: payload,
    idempotencyKey: newIdempotencyKey(),
  });
}

async function activation(signal?: AbortSignal): Promise<ActivationRead> {
  return getJson<ActivationRead>('/api/v2/activation', signal);
}

async function previewActivationLaunch(payload: ActivationLaunchDraft): Promise<ActivationLaunchPreview> {
  return mutateV2Json<ActivationLaunchPreview, ActivationLaunchDraft>('/api/v2/activation/launch-preview', { body: payload });
}

async function launchActivation(payload: ActivationLaunchRequest): Promise<JobAccepted> {
  return mutateV2Json<JobAccepted, ActivationLaunchRequest>('/api/v2/activation/launch', {
    body: payload,
    asyncCommand: true,
  });
}

async function productSetting(
  section: ProductSettingSection,
  signal?: AbortSignal,
): Promise<ProductSettingSnapshot> {
  return getJson<ProductSettingSnapshot>(`/api/v2/settings/${section}`, signal);
}

async function updateProductSetting(
  section: ProductSettingSection,
  snapshot: Pick<ProductSettingSnapshot, 'version' | 'values'>,
): Promise<ProductSettingSnapshot> {
  return mutateV2Json<ProductSettingSnapshot, {
    values: Record<string, unknown>;
    expected_version: number;
    impact_preview_confirmed: true;
  }>(`/api/v2/settings/${section}`, {
    method: 'PUT',
    idempotencyKey: newIdempotencyKey(),
    body: {
      values: snapshot.values,
      expected_version: snapshot.version,
      impact_preview_confirmed: true,
    },
  });
}

function mapChannelAccount(row: ChannelAccountSummaryRead): ChannelAccount {
  return {
    id: row.id,
    ownerId: row.owner_id,
    channel: row.channel,
    provider: row.provider,
    address: row.address,
    displayName: row.display_name ?? undefined,
    enabled: row.enabled,
    healthStatus: row.health_status,
    healthCheckedAt: row.health_checked_at ?? undefined,
    dailyLimit: row.daily_limit ?? undefined,
    timezone: row.timezone,
    smtpHost: row.smtp_host ?? undefined,
    smtpPort: row.smtp_port ?? undefined,
    imapHost: row.imap_host ?? undefined,
    imapPort: row.imap_port ?? undefined,
    transport: row.transport,
    credentialsConfigured: row.credentials_configured,
    legacyEmailAccountId: row.legacy_email_account_id ?? undefined,
    lastError: row.last_error ?? undefined,
  };
}

function mapEmailBindingPreview(row: EmailAccountBindingPreviewRead): EmailAccountBindingPreview {
  return {
    legacyEmailAccountId: row.legacy_email_account_id,
    currentChannelAccountId: row.current_channel_account_id ?? undefined,
    address: row.address,
    dailyLimit: row.daily_limit,
    timezone: row.timezone,
    previewChecksum: row.preview_checksum,
    effects: row.effects,
    warnings: row.warnings,
  };
}

async function channelAccounts(signal?: AbortSignal): Promise<ChannelAccount[]> {
  const rows = await getJson<ChannelAccountSummaryRead[]>('/api/v2/channel-accounts', signal);
  return rows.map(mapChannelAccount);
}

async function previewEmailAccountBinding(input: {
  legacyEmailAccountId: number;
  dailyLimit: number;
  timezone: string;
}): Promise<EmailAccountBindingPreview> {
  const payload: EmailAccountBindingDraft = {
    legacy_email_account_id: input.legacyEmailAccountId,
    daily_limit: input.dailyLimit,
    timezone: input.timezone,
  };
  return mapEmailBindingPreview(await mutateV2Json<EmailAccountBindingPreviewRead, EmailAccountBindingDraft>(
    '/api/v2/channel-accounts/email-bindings/preview',
    { body: payload },
  ));
}

async function applyEmailAccountBinding(
  preview: EmailAccountBindingPreview,
): Promise<ChannelAccount> {
  const payload: EmailAccountBindingApply = {
    legacy_email_account_id: preview.legacyEmailAccountId,
    daily_limit: preview.dailyLimit,
    timezone: preview.timezone,
    preview_checksum: preview.previewChecksum,
    human_confirmed: true,
  };
  const row = await mutateV2Json<ChannelAccountSummaryRead, EmailAccountBindingApply>(
    '/api/v2/channel-accounts/email-bindings',
    { body: payload, idempotencyKey: newIdempotencyKey() },
  );
  return mapChannelAccount(row);
}

function mapOwnerMigrationState(row: OwnerMigrationStateRead): OwnerMigrationState {
  return {
    ownerId: row.owner_id,
    currentPath: row.current_path,
    version: row.version,
    explicit: row.explicit,
    switchedAt: row.switched_at ?? undefined,
  };
}

function mapOwnerMigrationPreview(row: OwnerMigrationPreviewRead): OwnerMigrationPreview {
  return {
    ownerId: row.owner_id,
    currentPath: row.current_path,
    targetPath: row.target_path,
    expectedVersion: row.expected_version,
    previewChecksum: row.preview_checksum,
    effects: row.effects,
    blockers: (row.blockers ?? []).map(blocker => ({
      code: typeof blocker.code === 'string' ? blocker.code : 'OWNER_PATH_BLOCKED',
      message: typeof blocker.message === 'string' ? blocker.message : '当前写入路径不可切换。',
      counts: blocker.counts && typeof blocker.counts === 'object'
        ? Object.fromEntries(Object.entries(blocker.counts).filter((entry): entry is [string, number] => typeof entry[1] === 'number'))
        : undefined,
    })),
  };
}

async function ownerMigrationState(signal?: AbortSignal): Promise<OwnerMigrationState> {
  return mapOwnerMigrationState(await getJson<OwnerMigrationStateRead>('/api/v2/migration-state', signal));
}

async function previewOwnerV2Migration(): Promise<OwnerMigrationPreview> {
  const payload: OwnerMigrationPreviewRequest = { target_path: 'v2' };
  const row = await mutateV2Json<OwnerMigrationPreviewRead, OwnerMigrationPreviewRequest>(
    '/api/v2/migration-state/preview',
    { method: 'POST', body: payload },
  );
  return mapOwnerMigrationPreview(row);
}

async function activateOwnerV2Migration(preview: OwnerMigrationPreview): Promise<OwnerMigrationState> {
  const payload: OwnerMigrationSwitch = {
    target_path: 'v2',
    expected_version: preview.expectedVersion,
    preview_checksum: preview.previewChecksum,
    impact_preview_confirmed: true,
  };
  const row = await mutateV2Json<OwnerMigrationStateRead, OwnerMigrationSwitch>(
    '/api/v2/migration-state',
    { method: 'PUT', body: payload, idempotencyKey: newIdempotencyKey() },
  );
  return mapOwnerMigrationState(row);
}

export const v2Api = {
  runtime,
  campaigns,
  campaignAuthoring,
  customers,
  companyWorkspace,
  updateCompany,
  updateContact,
  inbox,
  opportunities,
  opportunityWorkspace,
  analytics,
  work,
  campaignCommand,
  createCampaign,
  createDraftRevision,
  revisionDiff,
  publishRevision,
  enrollContact,
  updateTask,
  completeTask,
  approveTask,
  dismissTask,
  routeProposals,
  reviewBatches,
  previewReviewBatch,
  editReviewBatchItem,
  approveReviewBatch,
  rejectReviewBatch,
  confirmReplyAssessment,
  updateOpportunityStage,
  confirmOpportunity,
  productSetting,
  updateProductSetting,
  channelAccounts,
  previewEmailAccountBinding,
  applyEmailAccountBinding,
  ownerMigrationState,
  previewOwnerV2Migration,
  activateOwnerV2Migration,
  acquisitionRun,
  importAcquisitionCsv,
  searchAcquisition,
  verifyAcquisition,
  commitAcquisition,
  activation,
  previewActivationLaunch,
  launchActivation,
};
