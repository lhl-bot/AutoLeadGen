export interface DashboardKpis {
  active_workflows: number
  total_leads: number
  emails_sent: number
  total_replies: number
}

export interface DashboardTrend {
  date: string
  leads_found: number
  emails_sent: number
}

export interface TodayReport {
  leads_found_today: number
  emails_sent_today: number
  high_intent_replies: number
  top_leads: Array<{
    id: number
    company_name: string
    email: string
    reply_snippet: string
  }>
  active_workflow_names: string[]
}

export interface EmailAccount {
  id: number
  email: string
  display_name?: string | null
  smtp_host: string
  smtp_port: number
  smtp_user: string
  use_tls: boolean
  use_ssl: boolean
  imap_host?: string | null
  imap_port: number
  created_at: string
}

export interface ClientPool {
  id: number
  name: string
  description?: string | null
  excluded_domains?: string | null
  total_leads?: number
  contacted_leads?: number
  replied_leads?: number
  workflow_count?: number
}

export type CsvLeadField =
  | 'company_name'
  | 'domain'
  | 'email'
  | 'first_name'
  | 'last_name'
  | 'job_title'
  | 'linkedin_url'
  | 'whatsapp_number'

export interface CsvImportPreviewRow {
  row_number: number
  status: 'valid' | 'duplicate' | 'invalid'
  reason?: string | null
  normalized: Partial<Record<CsvLeadField, string | null>>
  raw: Record<string, string>
}

export interface CsvImportPreview {
  filename?: string | null
  encoding: string
  delimiter: string
  headers: string[]
  fields: CsvLeadField[]
  suggested_mapping: Record<CsvLeadField, string | null>
  mapping: Record<CsvLeadField, string | null>
  mapping_required: boolean
  total_rows: number
  counts: {
    valid: number
    duplicate: number
    invalid: number
  }
  preview_rows: CsvImportPreviewRow[]
}

export interface CsvImportResult {
  filename?: string | null
  pool_id: number
  total_rows: number
  imported: number
  duplicates: number
  invalid: number
}

export interface Lead {
  id: number
  workflow_id?: number | null
  client_pool_id?: number | null
  domain: string
  company_name?: string | null
  email?: string | null
  first_name?: string | null
  last_name?: string | null
  job_title?: string | null
  linkedin_url?: string | null
  status: string
  ai_draft?: string | null
  followup_count?: number
  last_reply_at?: string | null
  reply_snippet?: string | null
  // Feedback & verification
  user_rating?: string | null
  email_verified?: boolean
  email_validation_status?: string | null
  timezone?: string | null
  fit_score?: number | null
  fit_grade?: string | null
  qualification_notes?: string | null
  handoff_recommended?: boolean
  source_channel?: string | null
  data_sources?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export type ReviewQueueKey = 'drafted' | 'needs_email' | 'send_failed' | 'high_intent'

export interface ReviewCenterData {
  counts: Record<ReviewQueueKey, number>
  queues: Record<ReviewQueueKey, Lead[]>
}

export interface BulkLeadResult {
  lead_id: number
  ok: boolean
  status?: string
  message?: string
}

export interface BulkLeadResponse {
  requested: number
  succeeded: number
  failed: number
  results: BulkLeadResult[]
}

export interface CustomerPersona {
  id: number
  name: string
  target_industry?: string | null
  target_countries?: string | null
  target_keywords?: string | null
  negative_keywords?: string | null
  target_roles?: string | null
  ai_prompt_template?: string | null
  customer_types?: string | null
  product_categories?: string | null
  company_size?: string | null
  evidence_sources?: string | null
  qualification_rules?: string | null
  disqualification_rules?: string | null
  cultural_notes?: string | null
  positive_examples?: string | null
  negative_examples?: string | null
  created_at: string
}

export interface FollowupStep {
  day_offset: number
  instruction?: string | null
}

export interface Workflow {
  id: number
  name: string
  status: string
  search_keywords: string
  target_positions: string
  ai_prompt?: string | null
  email_signature?: string | null
  client_pool_id?: number | null
  persona_id?: number | null
  pilot_goal?: string | null
  target_customer_type?: string | null
  target_region?: string | null
  product_focus?: string | null
  manual_handoff_triggers?: string | null
  search_sources?: string | null
  competitor_names?: string | null
  trade_show_names?: string | null
  daily_limit: number
  send_interval_min: number
  send_interval_max: number
  auto_followup: boolean
  max_followups: number
  followup_steps?: FollowupStep[] | null
  template_id?: number | null
  search_offset: number
  enable_linkedin: boolean
  enable_whatsapp: boolean
  linkedin_invite_message?: string | null
  whatsapp_message_template?: string | null
  linkedin_daily_limit: number
  emails?: EmailAccount[]
  leads_count?: number
  contactable_count?: number
  needs_email_count?: number
  replied_count?: number
  bounced_count?: number
  low_score_count?: number
  outbound_count?: number
  bounce_rate?: number
  email_paused?: boolean
  avg_fit_score?: number
  handoff_count?: number
  client_pool_name?: string | null
  persona_name?: string | null
  playbook_type?: string
}

export interface PlaybookPreset {
  key: string
  name: string
  description: string
  icon: string
  defaults: {
    name_prefix: string
    daily_limit: number
    send_interval_min?: number
    send_interval_max?: number
    auto_followup?: boolean
    max_followups?: number
    search_keywords?: string
    target_positions?: string
    target_customer_type?: string
    target_region?: string
    product_focus?: string
    pilot_goal?: string
    manual_handoff_triggers?: string
    search_sources?: string
    competitor_names?: string
    trade_show_names?: string
    enable_linkedin?: boolean
    enable_whatsapp?: boolean
    linkedin_daily_limit?: number
    ai_prompt: string
  }
}

export interface ChannelAccount {
  id: number
  account_type: string
  name?: string | null
  status: string
  unipile_account_id: string
}

export interface ReplyLead extends Lead {
  last_reply_at?: string | null
  reply_snippet?: string | null
}

export interface EmailLog {
  id: number
  direction: string
  from_email: string
  to_email: string
  subject?: string | null
  body?: string | null
  sent_at?: string | null
  message_id?: string | null
  lead_company?: string | null
  lead_name?: string | null
}

export interface DeliverabilitySummary {
  status_counts: Record<string, number>
  outbound_count: number
  risk_domains: Array<{
    domain: string
    failures: number
    sent: number
  }>
}

export interface ResearchResult {
  company_overview: string
  pain_points: string
  recent_news?: string
  value_proposition_alignment: string
}

export interface WorkflowPilotReport {
  workflow_id: number
  leads_total: number
  matched_leads: number
  match_rate: number
  email_valid_rate: number
  reply_rate: number
  handoff_count: number
  high_intent_count: number
  avg_fit_score: number
  top_channels: string[]
}

export interface User {
  id: number
  username: string
  display_name?: string | null
  is_admin: boolean
  is_active: boolean
  credit_balance?: number
  created_at?: string | null
}

export interface CreditSummary {
  user_id: number
  balance: number
  lifetime_granted: number
  lifetime_used: number
  pricing: Record<string, number>
  credits_enabled: boolean
  updated_at?: string | null
}

export interface CreditTransaction {
  id: number
  user_id: number
  amount: number
  balance_after: number
  transaction_type: string
  action: string
  description?: string | null
  reference_type?: string | null
  reference_id?: string | null
  created_by_user_id?: number | null
  created_at: string
}

export interface LeadBrief {
  id: number
  lead_id: number
  company_overview?: string | null
  recent_news?: string | null
  pain_points?: string | null
  value_proposition_alignment?: string | null
  specific_products?: string | null
  recent_activity?: string | null
  personalization_hook?: string | null
  created_at: string
  updated_at: string
}

export interface ApiUsageProvider {
  key: string
  name: string
  category: string
  configured: boolean
  status: 'ok' | 'warning' | 'missing' | 'error'
  balance_label: string
  balance_value?: number | null
  balance_unit?: string | null
  usage_30d: number
  usage_label: string
  details: Record<string, string | number | boolean | null | undefined>
  error?: string | null
  docs_url?: string | null
}

export interface ApiUsageSummary {
  updated_at: string
  window_days: number
  totals: {
    configured_providers: number
    ok_providers: number
    warning_providers: number
    known_balance_providers: number
    local_events_30d: number
  }
  providers: ApiUsageProvider[]
  local_usage: Array<{
    key: string
    label: string
    count: number
  }>
}

export interface OnboardingStep {
  key: 'persona' | 'email' | 'pool' | 'workflow' | 'leads' | 'review'
  done: boolean
  count: number
}

export interface OnboardingStatus {
  steps: OnboardingStep[]
  completed: number
  total: number
  all_done: boolean
}

export interface EmailTemplate {
  id: number
  user_id: number
  name: string
  category: string
  ab_group?: string | null
  variant_label: string
  subject?: string | null
  body: string
  weight: number
  is_active: boolean
  created_at: string
  sent_count?: number
  replied_count?: number
  reply_rate?: number
}
