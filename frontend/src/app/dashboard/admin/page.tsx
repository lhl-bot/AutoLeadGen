import Link from 'next/link';
import { Database, Megaphone, Plug, Settings, ShieldCheck } from 'lucide-react';

const adminEntries = [
  { href: '/dashboard/settings/icp-playbook', label: '客户画像与话术', detail: '目标客户、证据要求和沟通规则', icon: Settings },
  { href: '/dashboard/settings/channels', label: '渠道账号', detail: 'Email、LinkedIn、WhatsApp 账号与健康状态', icon: Plug },
  { href: '/dashboard/admin/plans', label: '活动计划', detail: '计划版本、受众和运行方式', icon: Megaphone },
  { href: '/dashboard/settings/providers', label: '外部服务与费用', detail: '搜索、验证、消息服务和费用对账', icon: ShieldCheck },
  { href: '/dashboard/leads', label: '历史数据与迁移', detail: '只读历史数据、迁移任务和数据治理', icon: Database },
];

export default function AdminPage() {
  return (
    <section>
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-700">仅管理员</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">系统管理</h1>
      <p className="mt-2 text-sm text-slate-600">销售日常不需要进入这里。账号、费用、运行状态和历史迁移集中管理。</p>
      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {adminEntries.map(entry => <Link key={entry.href} href={entry.href} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-indigo-300 hover:shadow-md"><entry.icon className="h-5 w-5 text-indigo-700" /><h2 className="mt-4 font-semibold text-slate-950">{entry.label}</h2><p className="mt-1 text-sm leading-6 text-slate-600">{entry.detail}</p></Link>)}
      </div>
    </section>
  );
}
