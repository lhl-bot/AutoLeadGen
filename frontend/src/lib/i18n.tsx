"use client";

import React, { createContext, useContext, useState, useEffect } from 'react';

export type Language = 'en' | 'zh';

interface LanguageContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string) => string;
}

const LanguageContext = createContext<LanguageContextType | undefined>(undefined);

// Core translation dictionary
export const translations: Record<Language, Record<string, string>> = {
  en: {
    // Navigation / Sidebar
    'WORKSPACE': 'WORKSPACE',
    'ASSISTANT': 'ASSISTANT',
    'REPORTS': 'REPORTS',
    'Overview': 'Overview',
    'Client Pools': 'Client Pools',
    'Personas': 'Personas',
    'Workflows': 'Workflows',
    'Email Config': 'Email Config',
    'Omnichannel': 'Omnichannel Settings',
    'Users': 'Users',
    'AI Sandbox': 'AI Sandbox',
    'AI Agent': 'AI Agent',
    'Replies': 'Replies',
    'Email Logs': 'Email Logs',
    'New Chat': 'New Chat',
    'System Online': 'System Online',
    'Workers and API ready': 'Workers and API ready',
    'Log out': 'Log out',
    'AI outbound console': 'AI outbound console',

    // Login Page
    'Welcome Back': 'Welcome Back',
    'Sign in description': 'Sign in to your AutoLeadGen workspace',
    'Username': 'Username',
    'Password': 'Password',
    'Sign In': 'Sign In',
    'No account': "Don't have an account?",
    'Contact Sales': 'Contact Sales',

    // Landing Page Navbar
    'Product': 'Product',
    'Solutions': 'Solutions',
    'Pricing': 'Pricing',
    'Log in': 'Log in',
    'Get Started': 'Get Started',

    // Landing Page Hero
    'Hero Title Line 1': 'The AI SDR That',
    'Hero Title Line 2': 'Never Sleeps.',
    'Hero Desc': 'Automate your entire B2B sales development process. From deep account research to omnichannel outreach across Email, LinkedIn, and WhatsApp.',
    'Free Trial': 'Start your free trial',
    'Watch Demo': 'Watch Demo',
    'No CC': 'No credit card required',
    '14 Days': '14-day free trial',

    // Landing Page Showcase
    'Omnichannel Sequences': 'Omnichannel Sequences',
    'Showcase Desc': 'Reach your prospects wherever they are. If they don\'t reply to an email, we automatically send a connection request on LinkedIn, and follow up via WhatsApp.',
    'Day 1 Email': 'Day 1: Highly Personalized Email',
    'Day 1 Detail': 'Based on deep AI research',
    'Day 3 LinkedIn': 'Day 3: LinkedIn Connection',
    'Day 3 Detail': '"Saw your recent funding round..."',
    'Day 5 WhatsApp': 'Day 5: WhatsApp Follow-up',
    'Day 5 Detail': 'Quick ping to stay top of mind',

    // Landing Page Features
    'Features Title': 'Everything you need to scale outbound',
    'Features Desc': 'AutoLeadGen replaces 5 different tools with one cohesive AI agent.',
    'Lead Sourcing': 'Lead Sourcing',
    'Lead Sourcing Desc': 'Find verified B2B contacts that perfectly match your Ideal Customer Profile.',
    'Deep Research': 'Deep Research',
    'Deep Research Desc': 'AI scans company websites and news to find hyper-relevant outreach angles.',
    'Multichannel': 'Multichannel',
    'Multichannel Desc': 'Native integration with Email, LinkedIn, and WhatsApp via Unipile.',
    'Auto-Drafting': 'Auto-Drafting',
    'Auto-Drafting Desc': 'Dynamic copy generation that doesn\'t sound like a robot wrote it.',
    'Unified Inbox': 'Unified Inbox',
    'Unified Inbox Desc': 'Manage all replies across all channels in one single inbox.',
    '24/7 Execution': '24/7 Execution',
    '24/7 Execution Desc': 'Your agent works around the clock, sending messages at optimal times globally.',

    // Landing Page Call to Action & Footer
    'CTA Title': 'Ready to clone your best SDR?',
    'CTA Desc': 'Join forward-thinking sales teams automating their outbound engine.',
    'CTA Button': 'Get Started for Free',
    'Footer Rights': '© 2026 AutoLeadGen Inc. All rights reserved.',

    // Reports & Dashboard & Replies Pages
    'Reports': 'Reports',
    'Client Replies': 'Client Replies',
    'Track all incoming responses from your leads across channels.': 'Track all incoming responses from your leads across channels.',
    'Refresh': 'Refresh',
    'Loading replies...': 'Loading replies...',
    'No replies received yet. Make sure your workflows are active.': 'No replies received yet. Make sure your workflows are active.',
    'Unknown lead': 'Unknown lead',
    'Unknown company': 'Unknown company',
    'Unknown time': 'Unknown time',
    'No reply snippet captured yet.': 'No reply snippet captured yet.',
    'Generate AI Response': 'Generate AI Response',
    'Workflow': 'Workflow',
    'Handoff': 'Handoff',
    'Welcome to AutoLeadGen': 'Welcome to AutoLeadGen',
    'Your AI-powered outbound sales engine is ready.': 'Your AI-powered outbound sales engine is ready.',
    'Loading analytics...': 'Loading analytics...',
    "Today's AI Work Report": "Today's AI Work Report",
    "Today's Report": "Today's Report",
    'High-value Leads Found': 'High-value Leads Found',
    'Emails Sent': 'Emails Sent',
    'High-intent Replies': 'High-intent Replies',
    'Follow up': 'Follow up',
    'Active Workflows Running': 'Active Workflows Running',
    'Active Workflows': 'Active Workflows',
    'Leads Sourced': 'Leads Sourced',
    'Performance Trends (14 Days)': 'Performance Trends (14 Days)',
    'System Status': 'System Status',
    'Outbound Engine': 'Outbound Engine',
    'Background workers': 'Background workers',
    'Online': 'Online',
    'Research Agent': 'Research Agent',
    'DeepSeek LLM Models': 'DeepSeek LLM Models',
    'Unipile Integration': 'Unipile Integration',
    'Omnichannel webhooks': 'Omnichannel webhooks',
    'Connected': 'Connected',
    'Quick Start': 'Quick Start',
    'Configure emails & personas': 'Configure emails & personas',
    'Create a workflow': 'Create a workflow',
    'Let the AI start hunting': 'Let the AI start hunting'
  },
  zh: {
    // Navigation / Sidebar
    'WORKSPACE': '工作区',
    'ASSISTANT': 'AI 助手',
    'REPORTS': '数据报表',
    'Overview': '数据概览',
    'Client Pools': '客户池',
    'Personas': '客户画像',
    'Workflows': '工作流',
    'Email Config': '发信邮箱配置',
    'Omnichannel': '全渠道设置',
    'Users': '用户管理',
    'AI Sandbox': 'AI 沙盒',
    'AI Agent': 'AI 智能体',
    'Replies': '回复管理',
    'Email Logs': '发信日志',
    'New Chat': '新建对话',
    'System Online': '系统在线',
    'Workers and API ready': '后台工作线程及 API 已就绪',
    'Log out': '退出登录',
    'AI outbound console': 'AI 外发控制台',

    // Login Page
    'Welcome Back': '欢迎回来',
    'Sign in description': '登录您的 AutoLeadGen 控制台以继续工作',
    'Username': '用户名',
    'Password': '密码',
    'Sign In': '登录',
    'No account': '还没有账号？',
    'Contact Sales': '联系销售',

    // Landing Page Navbar
    'Product': '产品功能',
    'Solutions': '解决方案',
    'Pricing': '价格体系',
    'Log in': '登录',
    'Get Started': '立即体验',

    // Landing Page Hero
    'Hero Title Line 1': '永不停歇的',
    'Hero Title Line 2': 'AI 销售代表。',
    'Hero Desc': '全自动化的 B2B 客户开发流程。从深度的企业调研到邮件、领英、WhatsApp 全渠道自动外发获客。',
    'Free Trial': '开始免费试用',
    'Watch Demo': '观看演示视频',
    'No CC': '无需提供信用卡',
    '14 Days': '14天免费试用',

    // Landing Page Showcase
    'Omnichannel Sequences': '全渠道自动化序列',
    'Showcase Desc': '在客户活跃的渠道上触达他们。如果邮件没有回复，系统会自动在领英上发送加好友申请，并之后在 WhatsApp 上跟进。',
    'Day 1 Email': '第 1 天：高个性化开发信',
    'Day 1 Detail': '基于深度的 AI 调研与个性化钩子',
    'Day 3 LinkedIn': '第 3 天：领英加好友并留言',
    'Day 3 Detail': '“关注到你们最近的业务扩张……”',
    'Day 5 WhatsApp': '第 5 天：WhatsApp 消息跟进',
    'Day 5 Detail': '轻量化问候，保持品牌心智占领',

    // Landing Page Features
    'Features Title': '规模化开发客户所需的一切功能',
    'Features Desc': 'AutoLeadGen 用一个连贯的 AI 销售代表代替了 5 种不同的工具。',
    'Lead Sourcing': '客户搜索定位',
    'Lead Sourcing Desc': '寻找符合您理想客户画像（ICP）的已验证 B2B 联系人。',
    'Deep Research': '深度背景调研',
    'Deep Research Desc': 'AI 自动扫描公司官网及最新动态，提炼高吸引力的切入点。',
    'Multichannel': '全渠道触达',
    'Multichannel Desc': '原生集成邮件、领英、WhatsApp 等，统一通过 Unipile 处理。',
    'Auto-Drafting': '智能自动撰写',
    'Auto-Drafting Desc': '动态生成个性化的正文与问候，读起来完全不像是机器人写的。',
    'Unified Inbox': '统一收件箱',
    'Unified Inbox Desc': '在一个统一的视图中，集中管理所有渠道的往来回复。',
    '24/7 Execution': '24/7 全天候执行',
    '24/7 Execution Desc': '您的 AI 代表全天候工作，在最符合对方时区的时间段发信。',

    // Landing Page Call to Action & Footer
    'CTA Title': '准备好复制你最顶尖的销售代表了吗？',
    'CTA Desc': '加入领先的销售团队，开启全自动的外发销售引擎。',
    'CTA Button': '开始免费体验',
    'Footer Rights': '© 2026 AutoLeadGen 公司。版权所有。',

    // Reports & Dashboard & Replies Pages
    'Reports': '数据报表',
    'Client Replies': '回复管理',
    'Track all incoming responses from your leads across channels.': '在一个视图中，集中管理所有渠道收到的客户回复。',
    'Refresh': '刷新',
    'Loading replies...': '正在加载回复列表...',
    'No replies received yet. Make sure your workflows are active.': '暂无收到回复，请确保您的工作流处于激活状态。',
    'Unknown lead': '未知联系人',
    'Unknown company': '未知公司',
    'Unknown time': '未知时间',
    'No reply snippet captured yet.': '暂无捕获到回复片段。',
    'Generate AI Response': '智能生成 AI 回复',
    'Workflow': '工作流',
    'Handoff': '转交人工',
    'Welcome to AutoLeadGen': '欢迎使用 AutoLeadGen',
    'Your AI-powered outbound sales engine is ready.': '您的 AI 智能外发销售引擎已准备就绪。',
    'Loading analytics...': '正在加载分析数据...',
    "Today's AI Work Report": '今日 AI 工作汇报',
    "Today's Report": '今日简报',
    'High-value Leads Found': '高价值客户挖掘',
    'Emails Sent': '精准触达',
    'High-intent Replies': '意向客户回复',
    'Follow up': '跟进',
    'Active Workflows Running': '正在运行',
    'Active Workflows': '活跃工作流',
    'Leads Sourced': '已挖掘客户数',
    'Performance Trends (14 Days)': '业绩趋势 (14天)',
    'System Status': '系统状态',
    'Outbound Engine': '自动外发引擎',
    'Background workers': '后台工作线程',
    'Online': '在线',
    'Research Agent': '背景调研智能体',
    'DeepSeek LLM Models': 'DeepSeek 大语言模型',
    'Unipile Integration': 'Unipile 全渠道集成',
    'Omnichannel webhooks': '全渠道实时回调',
    'Connected': '已连接',
    'Quick Start': '快速开始',
    'Configure emails & personas': '配置发信邮箱与客户画像',
    'Create a workflow': '创建客户开发工作流',
    'Let the AI start hunting': '启动 AI 开始自动开发客户'
  }
};

export const LanguageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [language, setLanguageState] = useState<Language>('zh');

  useEffect(() => {
    const saved = localStorage.getItem('app_language') as Language;
    if (saved === 'en' || saved === 'zh') {
      setLanguageState(saved);
    } else {
      const browserLang = navigator.language.toLowerCase();
      if (browserLang.startsWith('zh')) {
        setLanguageState('zh');
      } else {
        setLanguageState('en');
      }
    }
  }, []);

  const setLanguage = (lang: Language) => {
    setLanguageState(lang);
    localStorage.setItem('app_language', lang);
  };

  const t = (key: string): string => {
    return translations[language][key] || translations['en'][key] || key;
  };

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </LanguageContext.Provider>
  );
};

export const useTranslation = () => {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error('useTranslation must be used within a LanguageProvider');
  }
  return context;
};
