import Link from 'next/link';
import AnalyticsPage from '@/features/v2/pages/analytics-page';
import OpportunitiesPage from '@/features/v2/pages/opportunities-page';

export default async function ResultsPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const { view } = await searchParams;
  const isAnalytics = view === 'analytics';
  return (
    <div className="space-y-5">
      <nav aria-label="结果页面" className="inline-flex rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
        <Link href="/dashboard/results?view=opportunities" aria-current={!isAnalytics ? 'page' : undefined} className={`rounded-md px-4 py-2 text-sm font-semibold ${!isAnalytics ? 'bg-slate-950 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>商机</Link>
        <Link href="/dashboard/results?view=analytics" aria-current={isAnalytics ? 'page' : undefined} className={`rounded-md px-4 py-2 text-sm font-semibold ${isAnalytics ? 'bg-slate-950 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>分析</Link>
      </nav>
      {isAnalytics ? <AnalyticsPage /> : <OpportunitiesPage />}
    </div>
  );
}
