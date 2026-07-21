import Link from 'next/link';
import ActivationPage from '@/features/v2/pages/activation-page';
import CustomersPage from '@/features/v2/pages/customers-page';

export default async function CustomerHubPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const { view } = await searchParams;
  const isFinding = view === 'find';
  return (
    <div className="space-y-5">
      <nav aria-label="客户页面" className="inline-flex rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
        <Link href="/dashboard/customers" aria-current={!isFinding ? 'page' : undefined} className={`rounded-md px-4 py-2 text-sm font-semibold ${!isFinding ? 'bg-slate-950 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>客户库</Link>
        <Link href="/dashboard/customers?view=find" aria-current={isFinding ? 'page' : undefined} className={`rounded-md px-4 py-2 text-sm font-semibold ${isFinding ? 'bg-slate-950 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>导入 / AI 找客</Link>
      </nav>
      {isFinding ? <ActivationPage acquisitionOnly /> : <CustomersPage />}
    </div>
  );
}
