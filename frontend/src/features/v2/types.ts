export type DataSource = 'live' | 'mixed' | 'sample';

export interface DataEnvelope<T> {
  data: T;
  source: DataSource;
  observedAt: string;
  warning?: string;
}

export type RuntimeState =
  | 'idle'
  | 'running'
  | 'backoff'
  | 'blocked'
  | 'failed'
  | 'disabled'
  | 'offline'
  | 'unknown';

export interface RuntimeService {
  id: string;
  label: string;
  state: RuntimeState;
  detail: string;
  lastSeenAt?: string;
}

export interface RuntimeSnapshot {
  services: RuntimeService[];
  activeCampaigns: number;
  recentMessages: number;
  timestamp: string;
}

export type TaskPriority = 'urgent' | 'high' | 'normal' | 'low';

export interface DraftReviewPreview {
  attemptId: string;
  channel: string;
  recipient: string;
  subject?: string;
  body: string;
  templateVersion?: string;
}

export interface WorkTask {
  id: string;
  title: string;
  detail: string;
  type: 'approval' | 'data' | 'reply' | 'exception' | 'budget' | 'handoff' | 'readiness';
  priority: TaskPriority;
  href: string;
  campaign?: string;
  dueAt?: string;
  draftReview?: DraftReviewPreview;
}

export interface OutcomeMetric {
  key: string;
  label: string;
  value: number | string;
  detail: string;
}

export interface Contact {
  id: string;
  companyId: string;
  name: string;
  company: string;
  domain: string;
  email: string;
  title: string;
  status: string;
  verified: boolean;
  channels: string[];
}

export interface Company {
  id: string;
  name: string;
  domain: string;
  industry: string;
  region: string;
  contacts: number;
  verifiedContacts: number;
}

export interface CustomerList {
  id: string;
  name: string;
  description?: string;
  total: number | null;
}

export interface CompanyEvidenceSnapshot {
  id: string;
  source: string;
  sourceUrl?: string;
  confidence: number;
  capturedAt: string;
  evidence: Record<string, unknown>;
}

export interface CompanyOutreachSummary {
  enrollmentCount: number;
  sentCount: number;
  replyCount: number;
  lastContactAt?: string;
}

export interface CompanyWorkspace {
  company: Company;
  contacts: Contact[];
  evidence: CompanyEvidenceSnapshot[];
  outreach: CompanyOutreachSummary;
}

export type OwnerWritePath = 'legacy' | 'v2';

export interface OwnerMigrationState {
  ownerId: number;
  currentPath: OwnerWritePath;
  version: number;
  explicit: boolean;
  switchedAt?: string;
}

export interface OwnerMigrationBlocker {
  code: string;
  message: string;
  counts?: Record<string, number>;
}

export interface OwnerMigrationPreview {
  ownerId: number;
  currentPath: OwnerWritePath;
  targetPath: OwnerWritePath;
  expectedVersion: number;
  previewChecksum: string;
  effects: Record<string, unknown>;
  blockers: OwnerMigrationBlocker[];
}

export type CheckSeverity = 'blocker' | 'warning' | 'info';

export interface ReadinessCheck {
  key: string;
  label: string;
  severity: CheckSeverity;
  passed: boolean;
  detail: string;
  remediationHref?: string;
}

export interface CampaignStage {
  key: 'discover' | 'research' | 'draft' | 'send' | 'reply';
  label: string;
  state: RuntimeState;
  detail: string;
}

export interface Campaign {
  id: string;
  name: string;
  lifecycle: 'draft' | 'ready' | 'running' | 'paused' | 'completed' | 'archived';
  mode: 'shadow' | 'review' | 'auto';
  priority: number;
  budgetLimit: number | null;
  enrollments: number;
  positiveSignals: number;
  stages: CampaignStage[];
  readiness: ReadinessCheck[];
}

export type CampaignRevisionStatus = 'draft' | 'published' | 'superseded';

export interface CampaignRevisionSummary {
  id: string;
  campaignId: string;
  revisionNumber: number;
  status: CampaignRevisionStatus;
  createdAt: string;
  icpDefinition: Record<string, unknown>;
  audienceDefinition: Record<string, unknown>;
  qualityGates: Record<string, unknown>;
  budgetDefinition: Record<string, unknown>;
  stopConditions: Record<string, unknown>;
}

export interface CampaignContactOption {
  id: string;
  companyId: string;
  label: string;
  company: string;
  contactPoints: string[];
}

export interface CampaignAuthoringSnapshot {
  campaigns: Campaign[];
  revisionsByCampaign: Record<string, CampaignRevisionSummary[]>;
  contacts: CampaignContactOption[];
}

export interface RevisionImpactPreview {
  campaignId: string;
  baseRevisionId: string | null;
  proposedRevisionId: string;
  diff: Record<string, unknown>;
  diffChecksum: string;
}

export type ReplyIntent =
  | 'interested'
  | 'more_info'
  | 'referral'
  | 'meeting'
  | 'not_interested'
  | 'unsubscribe'
  | 'out_of_office'
  | 'bounce'
  | 'other';

export interface Conversation {
  id: string;
  contactName: string;
  company: string;
  channel: string;
  subject: string;
  snippet: string;
  intent?: ReplyIntent;
  assessment?: ReplyAssessment;
  lastReplyAt?: string;
  handoffRecommended: boolean;
  status: 'open' | 'waiting_on_us' | 'waiting_on_contact' | 'closed';
}

export interface ReplyAssessment {
  id: string;
  intent: ReplyIntent;
  isPositive: boolean;
  confidence?: number;
  status: 'proposed' | 'confirmed' | 'rejected';
  rationale?: string;
}

export type OpportunityStage =
  | 'qualified_reply'
  | 'discovery'
  | 'sample_or_quote'
  | 'negotiation'
  | 'won'
  | 'lost';

export interface Opportunity {
  id: string;
  name: string;
  company: string;
  contact: string;
  stage: OpportunityStage;
  nextStep: string;
  nextActionDueAt: string;
  ownerId: string;
  value?: number;
  currency?: string;
  source: 'opportunity';
}

export interface SalesHandoff {
  id: string;
  replyAssessmentId?: string;
  status: 'open' | 'in_progress';
  priority: TaskPriority;
  title: string;
  detail: string;
  companyId?: string;
  company: string;
  contactId?: string;
  contact: string;
  conversationId?: string;
  conversation: string;
  channel?: string;
  assigneeUserId?: string;
  dueAt?: string;
}

export interface OpportunityWorkspace {
  handoffs: SalesHandoff[];
  opportunities: Opportunity[];
}

export interface FunnelStage {
  key: string;
  label: string;
  count: number;
}

export interface SpendMetric {
  key: string;
  label: string;
  units: number;
  unit: string;
  results: number;
}

export interface AnalyticsSnapshot {
  outcomes: OutcomeMetric[];
  funnel: FunnelStage[];
  spend: SpendMetric[];
  replyRate: number;
  normalizedSpend: Array<{ currency: string; amount: number }>;
}

export interface WorkSnapshot {
  runtime: RuntimeSnapshot;
  metrics: OutcomeMetric[];
  tasks: WorkTask[];
  campaigns: Campaign[];
}

export interface CustomerSnapshot {
  contacts: Contact[];
  companies: Company[];
  lists: CustomerList[];
}

export interface ActionPreview {
  id: string;
  title: string;
  target: string;
  effects: string[];
  risks: string[];
}

export type ProductSettingSection =
  | 'icp_playbook'
  | 'channels_integrations'
  | 'providers'
  | 'permissions';

export interface ProductSettingSnapshot {
  section: ProductSettingSection;
  version: number;
  values: Record<string, unknown>;
  updated_at: string | null;
  updated_by_user_id: number | null;
  effective_locks: {
    environment?: string;
    connector_mode?: string;
    outbound_hard_pause?: boolean;
    real_external_calls_allowed?: boolean;
    credentials_accepted_here?: boolean;
    [key: string]: unknown;
  };
}

export interface ChannelAccount {
  id: number;
  ownerId: number;
  channel: string;
  provider: string;
  address: string;
  displayName?: string;
  enabled: boolean;
  healthStatus: 'unknown' | 'healthy' | 'degraded' | 'unhealthy';
  healthCheckedAt?: string;
  dailyLimit?: number;
  timezone: string;
  smtpHost?: string;
  smtpPort?: number;
  imapHost?: string;
  imapPort?: number;
  transport: string;
  credentialsConfigured: boolean;
  legacyEmailAccountId?: number;
  lastError?: string;
}

export interface EmailAccountBindingPreview {
  legacyEmailAccountId: number;
  currentChannelAccountId?: number;
  address: string;
  dailyLimit: number;
  timezone: string;
  previewChecksum: string;
  effects: Record<string, unknown>;
  warnings: Array<{ code: string; message: string }>;
}
