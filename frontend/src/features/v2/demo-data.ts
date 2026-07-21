import type {
  AnalyticsSnapshot,
  Campaign,
  Conversation,
  CustomerSnapshot,
  Opportunity,
  RuntimeSnapshot,
  WorkSnapshot,
} from './types';

export const sampleRuntime: RuntimeSnapshot = {
  services: [
    { id: 'database', label: '数据库', state: 'unknown', detail: '未连接本地 API' },
    { id: 'prospecting', label: '客户发现', state: 'unknown', detail: '等待心跳' },
    { id: 'outbound', label: '邮件发送', state: 'unknown', detail: '等待心跳' },
    { id: 'inbox', label: '收件箱监听', state: 'unknown', detail: '等待心跳' },
  ],
  activeCampaigns: 0,
  recentMessages: 0,
  timestamp: new Date(0).toISOString(),
};

export const sampleCampaigns: Campaign[] = [
  {
    id: 'sample-campaign',
    name: '欧洲家纺买手开发（示例）',
    lifecycle: 'draft',
    mode: 'review',
    priority: 100,
    budgetLimit: 25,
    enrollments: 18,
    positiveSignals: 0,
    stages: [
      { key: 'discover', label: '发现', state: 'idle', detail: '示例阶段' },
      { key: 'research', label: '研究', state: 'blocked', detail: '9 个联系人待验证' },
      { key: 'draft', label: '草稿', state: 'blocked', detail: '等待研究证据' },
      { key: 'send', label: '发送', state: 'disabled', detail: 'fake connector 未启动' },
      { key: 'reply', label: '回复', state: 'idle', detail: '暂无新回复' },
    ],
    readiness: [
      { key: 'email', label: '发件账号', severity: 'blocker', passed: false, detail: '示例数据未绑定发件账号', remediationHref: '/dashboard/emails' },
      { key: 'verification', label: '邮箱验证', severity: 'warning', passed: false, detail: '9 个联系人待验证' },
    ],
  },
];

export const sampleCustomers: CustomerSnapshot = {
  contacts: [
    { id: 'sample-contact', companyId: 'sample-company', name: 'Sofia Weber', company: 'Nordic Living', domain: 'nordic.example', email: 'sofia@nordic.example', title: 'Category Buyer', status: 'unverified / available', verified: false, channels: ['email'] },
  ],
  companies: [{ id: 'sample-company', name: 'Nordic Living', domain: 'nordic.example', industry: '家居零售', region: '北欧', contacts: 1, verifiedContacts: 0 }],
  lists: [{ id: 'sample-list', name: '欧洲家纺买手（示例）', description: '只用于本地界面预览', total: null }],
};

export const sampleConversations: Conversation[] = [
  { id: 'sample-conversation', contactName: 'Sofia Weber', company: 'Nordic Living', channel: 'email', subject: '样品与起订量', snippet: '希望了解起订量和样品周期（示例）', intent: 'more_info', handoffRecommended: true, status: 'waiting_on_us' },
];

export const sampleOpportunities: Opportunity[] = [
  { id: 'sample-opportunity', name: '样品与报价跟进（示例）', company: 'Nordic Living', contact: 'Sofia Weber', stage: 'qualified_reply', nextStep: '人工核对需求', nextActionDueAt: new Date(0).toISOString(), ownerId: '未分配', source: 'opportunity' },
];

export const sampleAnalytics: AnalyticsSnapshot = {
  outcomes: [
    { key: 'qualified', label: '合格客户', value: 0, detail: '暂无实时数据' },
    { key: 'verified', label: '已验证联系人', value: 0, detail: '暂无实时数据' },
    { key: 'sent', label: '首触达', value: 0, detail: '暂无实时数据' },
    { key: 'positive', label: '正向回复', value: 0, detail: '暂无实时数据' },
  ],
  funnel: [
    { key: 'found', label: '已发现', count: 0 },
    { key: 'with_email', label: '有邮箱', count: 0 },
    { key: 'sent', label: '已发送', count: 0 },
    { key: 'replied', label: '已回复', count: 0 },
  ],
  spend: [{ key: 'unavailable', label: '供应商成本', units: 0, unit: '未配置', results: 0 }],
  replyRate: 0,
  normalizedSpend: [],
};

export const sampleWork: WorkSnapshot = {
  runtime: sampleRuntime,
  metrics: sampleAnalytics.outcomes,
  tasks: [
    { id: 'sample-task', title: '完成开发计划就绪检查', detail: '连接本地 API 后显示实时待办', type: 'readiness', priority: 'high', href: '/dashboard/campaigns', campaign: '示例计划' },
  ],
  campaigns: sampleCampaigns,
};
