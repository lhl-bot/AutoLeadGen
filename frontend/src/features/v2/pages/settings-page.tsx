'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Eye, LockKeyhole, MailCheck, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { cn, isAbortError } from '@/lib/utils';
import { V2MutationError, v2Api } from '../api';
import { LoadingState, ProductPageShell } from '../components/product-ui';
import type { ChannelAccount, EmailAccountBindingPreview, ProductSettingSection, ProductSettingSnapshot } from '../types';
import type { OwnerMigrationPreview, OwnerMigrationState } from '../types';

const sectionLinks: Array<{ section: ProductSettingSection; href: string; label: string }> = [
  { section: 'icp_playbook', href: '/dashboard/settings/icp-playbook', label: 'ICP / Playbook' },
  { section: 'channels_integrations', href: '/dashboard/settings/channels', label: '渠道与集成' },
  { section: 'providers', href: '/dashboard/settings/providers', label: '供应商与预算' },
  { section: 'permissions', href: '/dashboard/settings/permissions', label: '权限策略' },
];

type SettingsForm = {
  summary: string;
  targetIndustries: string;
  targetRoles: string;
  evidenceRequirements: string;
  playbookNotes: string;
  proposalStatus: 'draft' | 'published';
  emailEnabled: boolean;
  linkedinEnabled: boolean;
  whatsappEnabled: boolean;
  publicUnsubscribeUrl: string;
  reviewBeforeSend: boolean;
  integrationNotes: string;
  globalBudgetLimit: string;
  currency: string;
  priceVersion: string;
  paidMissRequiresReview: boolean;
  providerPolicyNotes: string;
  rolePolicyNotes: string;
};

type SettingsImpactPreview = {
  baseVersion: number;
  changedKeys: string[];
  values: Record<string, unknown>;
  fingerprint: string;
};

const emptyForm: SettingsForm = {
  summary: '',
  targetIndustries: '',
  targetRoles: '',
  evidenceRequirements: '',
  playbookNotes: '',
  proposalStatus: 'draft',
  emailEnabled: false,
  linkedinEnabled: false,
  whatsappEnabled: false,
  publicUnsubscribeUrl: '',
  reviewBeforeSend: true,
  integrationNotes: '',
  globalBudgetLimit: '0',
  currency: 'USD',
  priceVersion: 'local-unpriced',
  paidMissRequiresReview: true,
  providerPolicyNotes: '',
  rolePolicyNotes: '',
};

function stringValue(values: Record<string, unknown>, key: string, fallback = ''): string {
  return typeof values[key] === 'string' ? String(values[key]) : fallback;
}

function booleanValue(values: Record<string, unknown>, key: string, fallback: boolean): boolean {
  return typeof values[key] === 'boolean' ? Boolean(values[key]) : fallback;
}

function listValue(values: Record<string, unknown>, key: string): string {
  return Array.isArray(values[key]) ? (values[key] as unknown[]).map(String).join(', ') : '';
}

function snapshotToForm(snapshot: ProductSettingSnapshot): SettingsForm {
  const values = snapshot.values;
  return {
    ...emptyForm,
    summary: stringValue(values, 'summary'),
    targetIndustries: listValue(values, 'target_industries'),
    targetRoles: listValue(values, 'target_roles'),
    evidenceRequirements: listValue(values, 'evidence_requirements'),
    playbookNotes: stringValue(values, 'playbook_notes'),
    proposalStatus: values.proposal_status === 'published' ? 'published' : 'draft',
    emailEnabled: booleanValue(values, 'email_enabled', false),
    linkedinEnabled: false,
    whatsappEnabled: false,
    publicUnsubscribeUrl: stringValue(values, 'public_unsubscribe_url'),
    reviewBeforeSend: booleanValue(values, 'review_before_send', true),
    integrationNotes: stringValue(values, 'integration_notes'),
    globalBudgetLimit: String(values.global_budget_limit ?? 0),
    currency: stringValue(values, 'currency', 'USD'),
    priceVersion: stringValue(values, 'price_version', 'local-unpriced'),
    paidMissRequiresReview: booleanValue(values, 'paid_miss_requires_review', true),
    providerPolicyNotes: stringValue(values, 'provider_policy_notes'),
    rolePolicyNotes: stringValue(values, 'role_policy_notes'),
  };
}

function commaList(value: string): string[] {
  return Array.from(new Set(value.split(',').map(item => item.trim()).filter(Boolean)));
}

function formToValues(section: ProductSettingSection, form: SettingsForm): Record<string, unknown> {
  if (section === 'icp_playbook') {
    return {
      summary: form.summary.trim(),
      target_industries: commaList(form.targetIndustries),
      target_roles: commaList(form.targetRoles),
      evidence_requirements: commaList(form.evidenceRequirements),
      playbook_notes: form.playbookNotes.trim(),
      proposal_status: form.proposalStatus,
    };
  }
  if (section === 'channels_integrations') {
    return {
      email_enabled: form.emailEnabled,
      linkedin_enabled: false,
      whatsapp_enabled: false,
      public_unsubscribe_url: form.publicUnsubscribeUrl.trim(),
      review_before_send: form.reviewBeforeSend,
      integration_notes: form.integrationNotes.trim(),
    };
  }
  if (section === 'providers') {
    return {
      global_budget_limit: Number(form.globalBudgetLimit || 0),
      currency: form.currency.trim().toUpperCase(),
      price_version: form.priceVersion.trim(),
      paid_miss_requires_review: form.paidMissRequiresReview,
      provider_policy_notes: form.providerPolicyNotes.trim(),
    };
  }
  return {
    paid_actions_require_confirmation: true,
    bulk_mutations_require_confirmation: true,
    opportunity_requires_human_confirmation: true,
    review_mode_send_requires_confirmation: true,
    role_policy_notes: form.rolePolicyNotes.trim(),
  };
}

const fieldLabels: Record<string, string> = {
  summary: 'ICP 简述',
  target_industries: '目标行业',
  target_roles: '目标岗位',
  evidence_requirements: '证据要求',
  playbook_notes: 'Playbook',
  proposal_status: '发布状态',
  email_enabled: 'Email 策略',
  linkedin_enabled: 'LinkedIn 策略',
  whatsapp_enabled: 'WhatsApp 策略',
  public_unsubscribe_url: '公共退订 URL',
  review_before_send: '发送前审核',
  integration_notes: '集成说明',
  global_budget_limit: '全局预算上限',
  currency: '币种',
  price_version: '价格版本',
  paid_miss_requires_review: '付费 miss 审核',
  provider_policy_notes: '供应商策略',
  role_policy_notes: '角色策略',
};

function policyCheckbox({
  id,
  label,
  checked,
  onChange,
  disabled = false,
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label htmlFor={id} className={cn('flex min-h-11 items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800', disabled && 'bg-slate-100')}>
      <input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange?.(event.target.checked)} className="h-5 w-5 rounded border-slate-300 accent-indigo-700" />
      <span className="flex-1">{label}</span>
      {disabled ? <LockKeyhole className="h-4 w-4 text-slate-500" aria-hidden="true" /> : null}
    </label>
  );
}

function EmailAccountBindings() {
  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [drafts, setDrafts] = useState<Record<number, { dailyLimit: string; timezone: string }>>({});
  const [preview, setPreview] = useState<EmailAccountBindingPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  function loadAccounts(signal?: AbortSignal) {
    setPending(true);
    setMessage('');
    return v2Api.channelAccounts(signal)
      .then(rows => {
        setAccounts(rows);
        setDrafts(Object.fromEntries(rows.map(account => [account.id, {
          dailyLimit: String(account.dailyLimit ?? 20),
          timezone: account.timezone || 'UTC',
        }])));
      })
      .catch(error => {
        if (signal?.aborted) return;
        setMessage(error instanceof Error ? error.message : '邮箱账户读取失败');
      })
      .finally(() => {
        if (!signal?.aborted) setPending(false);
      });
  }

  useEffect(() => {
    const controller = new AbortController();
    void loadAccounts(controller.signal);
    return () => controller.abort();
  }, []);

  function updateDraft(accountId: number, field: 'dailyLimit' | 'timezone', value: string) {
    setDrafts(current => ({
      ...current,
      [accountId]: { ...current[accountId], [field]: value },
    }));
    setPreview(null);
    setConfirmed(false);
    setMessage('账户策略已更改，请重新预览。');
  }

  async function previewBinding(account: ChannelAccount) {
    const draft = drafts[account.id];
    const dailyLimit = Number(draft?.dailyLimit);
    if (!account.legacyEmailAccountId || !Number.isInteger(dailyLimit) || dailyLimit < 1 || dailyLimit > 100 || !draft?.timezone.trim()) {
      setMessage('每日上限必须是 1–100 的整数，Timezone 不能为空。');
      return;
    }
    setPending(true);
    setMessage('');
    setConfirmed(false);
    try {
      const next = await v2Api.previewEmailAccountBinding({
        legacyEmailAccountId: account.legacyEmailAccountId,
        dailyLimit,
        timezone: draft.timezone.trim(),
      });
      setPreview(next);
      setMessage('账户接管影响已生成；不会复制密码、调用外部服务或发送邮件。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '账户接管预览失败');
    } finally {
      setPending(false);
    }
  }

  async function applyBinding() {
    if (!preview || !confirmed) return;
    setPending(true);
    setMessage('');
    try {
      const updated = await v2Api.applyEmailAccountBinding(preview);
      setAccounts(current => current.map(account => account.id === updated.id ? updated : account));
      setPreview(null);
      setConfirmed(false);
      setMessage('V2 邮箱绑定和每日上限已保存；真实外发暂停状态未改变。');
    } catch (error) {
      const detail = error instanceof V2MutationError && error.code
        ? `${error.code}: ${error.message}`
        : error instanceof Error ? error.message : '账户接管失败';
      setMessage(detail);
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="email-accounts-heading" className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="email-accounts-heading" className="flex items-center gap-2 text-lg font-semibold text-slate-950"><MailCheck className="h-5 w-5" aria-hidden="true" />V2 邮箱账户</h2>
          <p className="mt-1 text-sm leading-6 text-slate-600">只保存旧邮箱账户的 ID 与公开地址，不复制、不显示密码。健康状态只能由生产环境无发送 SMTP/IMAP 探测更新。</p>
        </div>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">{accounts.length} 个绑定</span>
      </div>

      {accounts.length ? (
        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          {accounts.map(account => {
            const draft = drafts[account.id] ?? { dailyLimit: String(account.dailyLimit ?? 20), timezone: account.timezone || 'UTC' };
            const isHealthy = account.healthStatus === 'healthy';
            return (
              <article key={account.id} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-semibold text-slate-950">{account.displayName || account.address}</h3>
                    <p className="mt-1 break-all text-sm text-slate-700">{account.address}</p>
                  </div>
                  <span className={cn('rounded-full border px-2.5 py-1 text-xs font-semibold', isHealthy ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : account.healthStatus === 'unhealthy' ? 'border-rose-200 bg-rose-50 text-rose-800' : 'border-amber-200 bg-amber-50 text-amber-900')}>
                    {isHealthy ? '健康' : account.healthStatus === 'unhealthy' ? '不可用' : '待无发送探测'}
                  </span>
                </div>
                <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                  <div><dt className="text-xs text-slate-500">SMTP</dt><dd className="mt-1 font-medium text-slate-900">{account.smtpHost ? `${account.smtpHost}:${account.smtpPort ?? '—'}` : '未配置'}</dd></div>
                  <div><dt className="text-xs text-slate-500">IMAP</dt><dd className="mt-1 font-medium text-slate-900">{account.imapHost ? `${account.imapHost}:${account.imapPort ?? '—'}` : '未配置'}</dd></div>
                  <div><dt className="text-xs text-slate-500">传输加密</dt><dd className="mt-1 font-medium text-slate-900">{account.transport.toUpperCase()}</dd></div>
                  <div><dt className="text-xs text-slate-500">Credential</dt><dd className="mt-1 font-medium text-slate-900">{account.credentialsConfigured ? '已配置（不可见）' : '缺失'}</dd></div>
                </dl>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2"><Label htmlFor={`account-limit-${account.id}`}>每日硬上限</Label><Input id={`account-limit-${account.id}`} type="number" min="1" max="100" step="1" value={draft.dailyLimit} onChange={event => updateDraft(account.id, 'dailyLimit', event.target.value)} /></div>
                  <div className="space-y-2"><Label htmlFor={`account-timezone-${account.id}`}>Timezone</Label><Input id={`account-timezone-${account.id}`} value={draft.timezone} onChange={event => updateDraft(account.id, 'timezone', event.target.value)} /></div>
                </div>
                <Button type="button" variant="outline" className="mt-4" disabled={pending || !account.legacyEmailAccountId} onClick={() => previewBinding(account)}><Eye className="mr-2 h-4 w-4" />预览账户接管</Button>
              </article>
            );
          })}
        </div>
      ) : pending ? <p className="mt-5 text-sm text-slate-600">正在读取邮箱绑定…</p> : <p className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">当前账号没有 V2 邮箱绑定；真实发信保持锁定。</p>}

      {preview ? (
        <div className="mt-5 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
          <p className="font-semibold text-indigo-950">接管预览 · {preview.address}</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-indigo-900">
            <li>每日硬上限：{preview.dailyLimit}</li>
            <li>Timezone：{preview.timezone}</li>
            <li>复制密码：0；外部调用：0；发送邮件：0</li>
            <li>绑定后仍需通过生产无发送 SMTP/IMAP 探测才会显示健康。</li>
          </ul>
          {preview.warnings.length ? <div role="alert" className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{preview.warnings.map(item => <p key={item.code}><strong>{item.code}</strong>：{item.message}</p>)}</div> : null}
          <label htmlFor="email-binding-confirm" className="mt-4 flex min-h-11 items-center gap-3 text-sm font-medium text-indigo-950"><input id="email-binding-confirm" type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} className="h-5 w-5 accent-indigo-700" />我已核对邮箱身份、每日上限和影响，确认由 V2 接管</label>
          <Button type="button" className="mt-3" disabled={!confirmed || pending} onClick={applyBinding}>{pending ? '保存中…' : '确认接管邮箱账户'}</Button>
        </div>
      ) : null}
      {message ? <p aria-live="polite" className="mt-4 text-sm font-medium text-slate-800">{message}</p> : null}
    </section>
  );
}

function OwnerV2WritePathControl() {
  const [state, setState] = useState<OwnerMigrationState | null>(null);
  const [preview, setPreview] = useState<OwnerMigrationPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    v2Api.ownerMigrationState(controller.signal)
      .then(setState)
      .catch(error => {
        if (!controller.signal.aborted && !isAbortError(error)) setMessage(error instanceof Error ? error.message : '写入路径读取失败');
      });
    return () => controller.abort();
  }, []);

  const loadPreview = async () => {
    setPending(true);
    setMessage('');
    setConfirmed(false);
    try {
      setPreview(await v2Api.previewOwnerV2Migration());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '写入路径预览失败');
    } finally {
      setPending(false);
    }
  };

  const activate = async () => {
    if (!preview || preview.blockers.length || !confirmed) return;
    setPending(true);
    setMessage('');
    try {
      const next = await v2Api.activateOwnerV2Migration(preview);
      setState(next);
      setPreview(null);
      setConfirmed(false);
      setMessage('V2 已成为当前账号唯一可写路径；Legacy 页面继续只读。');
    } catch (error) {
      const detail = error instanceof V2MutationError && error.code
        ? `${error.code}: ${error.message}`
        : error instanceof Error ? error.message : 'V2 写入路径启用失败';
      setMessage(detail);
    } finally {
      setPending(false);
    }
  };

  return (
    <section aria-labelledby="owner-write-path-heading" className="rounded-lg border border-indigo-200 bg-indigo-50/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="owner-write-path-heading" className="text-sm font-semibold text-slate-950">账号写入路径</h3>
          <p className="mt-1 text-xs leading-5 text-slate-700">切换只决定新旧产品谁能写数据库，不会启动执行器、解除发信硬暂停或开启真实外部调用。</p>
        </div>
        <span className="rounded-full border border-indigo-200 bg-white px-3 py-1 text-xs font-semibold text-indigo-900">
          {state ? (state.currentPath === 'v2' ? 'V2 唯一写入' : 'Legacy 写入') : '读取中…'}
        </span>
      </div>

      {state?.currentPath === 'v2' ? (
        <p className="mt-4 flex gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />V2 写入路径已显式启用，版本 {state.version}。Legacy 仅用于对账。</p>
      ) : (
        <div className="mt-4">
          <Button type="button" variant="outline" disabled={!state || pending} onClick={loadPreview}><Eye className="mr-2 h-4 w-4" />{pending ? '检查中…' : '预览启用 V2 写入'}</Button>
          {preview ? (
            <div className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
              <p className="text-sm font-semibold text-slate-950">影响预览：Legacy → V2</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-slate-700">
                <li>V2 成为当前账号唯一可写路径。</li>
                <li>Legacy 表单和写接口保持禁用，读取回退不变。</li>
                <li>此动作本身发送邮件数为 0，外部 Provider 调用数为 0。</li>
              </ul>
              {preview.blockers.length ? (
                <div role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3">
                  <p className="text-sm font-semibold text-rose-900">存在 {preview.blockers.length} 个阻断项，不能切换：</p>
                  <ul className="mt-2 space-y-2 text-xs leading-5 text-rose-800">{preview.blockers.map(blocker => <li key={`${blocker.code}-${blocker.message}`}><strong>{blocker.code}</strong>：{blocker.message}</li>)}</ul>
                </div>
              ) : (
                <>
                  <label htmlFor="owner-v2-impact-confirm" className="mt-4 flex min-h-11 items-center gap-3 text-sm font-medium text-slate-900"><input id="owner-v2-impact-confirm" type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} className="h-5 w-5 accent-indigo-700" />我已核对影响，确认将当前账号切换为 V2 唯一写入路径</label>
                  <Button type="button" className="mt-3" disabled={!confirmed || pending} onClick={activate}>{pending ? '切换中…' : '确认启用 V2 写入'}</Button>
                </>
              )}
            </div>
          ) : null}
        </div>
      )}
      {message ? <p aria-live="polite" className="mt-3 text-sm font-medium text-slate-800">{message}</p> : null}
    </section>
  );
}

export default function SettingsPage({ section }: { section: ProductSettingSection }) {
  const [snapshot, setSnapshot] = useState<ProductSettingSnapshot | null>(null);
  const [form, setForm] = useState<SettingsForm>(emptyForm);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<SettingsImpactPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setMessage('');
    setPreview(null);
    setConfirmed(false);
    v2Api.productSetting(section, controller.signal)
      .then(value => {
        setSnapshot(value);
        setForm(snapshotToForm(value));
      })
      .catch(error => {
        if (!controller.signal.aborted && !isAbortError(error)) setMessage(error instanceof Error ? error.message : '设置读取失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [section]);

  const draftValues = useMemo(() => formToValues(section, form), [form, section]);
  const changedKeys = useMemo(() => {
    if (!snapshot) return [];
    return Object.keys(draftValues).filter(key => JSON.stringify(draftValues[key]) !== JSON.stringify(snapshot.values[key]));
  }, [draftValues, snapshot]);
  const draftFingerprint = useMemo(() => JSON.stringify(draftValues), [draftValues]);
  const previewMatchesDraft = Boolean(
    preview
    && snapshot
    && preview.baseVersion === snapshot.version
    && preview.fingerprint === draftFingerprint,
  );

  useEffect(() => {
    if (!preview || previewMatchesDraft) return;
    setPreview(null);
    setConfirmed(false);
    setMessage('设置在预览后已更改，请重新预览影响并确认。');
  }, [preview, previewMatchesDraft]);

  const showPreview = () => {
    if (!snapshot) return;
    const reviewedValues = JSON.parse(draftFingerprint) as Record<string, unknown>;
    setPreview({
      baseVersion: snapshot.version,
      changedKeys,
      values: reviewedValues,
      fingerprint: draftFingerprint,
    });
    setConfirmed(false);
    setMessage(changedKeys.length ? '已生成影响预览，请确认后保存。' : '当前没有待保存的变化。');
  };

  const save = async () => {
    if (!snapshot || !preview?.changedKeys.length || !confirmed || !previewMatchesDraft) {
      if (preview && !previewMatchesDraft) {
        setPreview(null);
        setConfirmed(false);
        setMessage('设置在预览后已更改，请重新预览影响并确认。');
      }
      return;
    }
    const reviewedPreview = preview;
    setSaving(true);
    setMessage('');
    try {
      const updated = await v2Api.updateProductSetting(section, {
        version: reviewedPreview.baseVersion,
        values: reviewedPreview.values,
      });
      setSnapshot(updated);
      setForm(snapshotToForm(updated));
      setPreview(null);
      setConfirmed(false);
      setMessage(`已保存版本 ${updated.version}，审计记录已写入。`);
    } catch (error) {
      const detail = error instanceof V2MutationError && error.code ? `${error.code}: ${error.message}` : error instanceof Error ? error.message : '保存失败';
      setMessage(detail);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingState label="正在读取 V2 设置…" />;

  return (
    <ProductPageShell
      eyebrow="设置 · Product V2"
      title={sectionLinks.find(item => item.section === section)?.label ?? '产品设置'}
      description="所有改动先预览影响、再人工确认并写入不可变审计；此处拒绝保存任何明文凭据。"
    >
      <nav aria-label="产品设置分区" className="flex gap-2 overflow-x-auto pb-1">
        {sectionLinks.map(item => (
          <Link key={item.section} href={item.href} aria-current={item.section === section ? 'page' : undefined} className={cn('min-h-11 shrink-0 rounded-lg border px-4 py-3 text-sm font-semibold', item.section === section ? 'border-slate-950 bg-slate-950 text-white' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50')}>
            {item.label}
          </Link>
        ))}
      </nav>

      {section === 'channels_integrations' ? <EmailAccountBindings /> : null}

      {snapshot ? (
        <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.38fr)]">
          <section aria-labelledby="settings-form-heading" className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 id="settings-form-heading" className="text-lg font-semibold text-slate-950">策略文档</h2>
                <p className="mt-1 text-xs text-slate-600">当前版本 {snapshot.version} · {snapshot.updated_at ? new Date(snapshot.updated_at).toLocaleString('zh-CN') : '尚未保存'}</p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">不接收 credentials</span>
            </div>

            <div className="mt-6 space-y-5">
              {section === 'icp_playbook' ? (
                <>
                  <div className="space-y-2"><Label htmlFor="setting-summary">ICP 简述</Label><Textarea id="setting-summary" value={form.summary} onChange={event => setForm(value => ({ ...value, summary: event.target.value }))} /></div>
                  <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="setting-industries">目标行业（逗号分隔）</Label><Input id="setting-industries" value={form.targetIndustries} onChange={event => setForm(value => ({ ...value, targetIndustries: event.target.value }))} /></div><div className="space-y-2"><Label htmlFor="setting-roles">目标岗位（逗号分隔）</Label><Input id="setting-roles" value={form.targetRoles} onChange={event => setForm(value => ({ ...value, targetRoles: event.target.value }))} /></div></div>
                  <div className="space-y-2"><Label htmlFor="setting-evidence">发布所需证据（逗号分隔）</Label><Input id="setting-evidence" value={form.evidenceRequirements} onChange={event => setForm(value => ({ ...value, evidenceRequirements: event.target.value }))} /></div>
                  <div className="space-y-2"><Label htmlFor="setting-playbook">Playbook 说明</Label><Textarea id="setting-playbook" value={form.playbookNotes} onChange={event => setForm(value => ({ ...value, playbookNotes: event.target.value }))} /></div>
                  <div className="space-y-2"><Label htmlFor="setting-proposal-status">提案状态</Label><select id="setting-proposal-status" value={form.proposalStatus} onChange={event => setForm(value => ({ ...value, proposalStatus: event.target.value as SettingsForm['proposalStatus'] }))} className="min-h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"><option value="draft">DRAFT 提案</option><option value="published">人工发布</option></select></div>
                </>
              ) : null}

              {section === 'channels_integrations' ? (
                <fieldset className="space-y-3"><legend className="mb-3 text-sm font-semibold text-slate-950">启用策略（不等于账号已就绪）</legend>{policyCheckbox({ id: 'setting-email', label: 'Email', checked: form.emailEnabled, onChange: checked => setForm(value => ({ ...value, emailEnabled: checked })) })}{policyCheckbox({ id: 'setting-linkedin', label: 'LinkedIn（尚未开放）', checked: false, disabled: true })}{policyCheckbox({ id: 'setting-whatsapp', label: 'WhatsApp（尚未开放）', checked: false, disabled: true })}<p className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-700">当前生产版本只支持 Email。未通过独立连接器、回调、安全与合规验收的渠道不能保存为启用状态。</p>{policyCheckbox({ id: 'setting-review-send', label: '发送前必须审核', checked: form.reviewBeforeSend, onChange: checked => setForm(value => ({ ...value, reviewBeforeSend: checked })) })}<div className="space-y-2 pt-2"><Label htmlFor="setting-unsubscribe">公共退订 URL</Label><Input id="setting-unsubscribe" type="url" value={form.publicUnsubscribeUrl} placeholder="https://example.com/unsubscribe" onChange={event => setForm(value => ({ ...value, publicUnsubscribeUrl: event.target.value }))} /></div><div className="space-y-2"><Label htmlFor="setting-integration-notes">集成说明（不填写密钥）</Label><Textarea id="setting-integration-notes" value={form.integrationNotes} onChange={event => setForm(value => ({ ...value, integrationNotes: event.target.value }))} /></div></fieldset>
              ) : null}

              {section === 'providers' ? (
                <><div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="setting-budget">全局预算上限</Label><Input id="setting-budget" type="number" min="0" step="0.01" value={form.globalBudgetLimit} onChange={event => setForm(value => ({ ...value, globalBudgetLimit: event.target.value }))} /></div><div className="space-y-2"><Label htmlFor="setting-currency">币种</Label><Input id="setting-currency" maxLength={3} value={form.currency} onChange={event => setForm(value => ({ ...value, currency: event.target.value }))} /></div></div><div className="space-y-2"><Label htmlFor="setting-price-version">价格版本</Label><Input id="setting-price-version" value={form.priceVersion} onChange={event => setForm(value => ({ ...value, priceVersion: event.target.value }))} /></div>{policyCheckbox({ id: 'setting-paid-miss', label: '付费 miss 需要人工复核', checked: form.paidMissRequiresReview, onChange: checked => setForm(value => ({ ...value, paidMissRequiresReview: checked })) })}<div className="space-y-2"><Label htmlFor="setting-provider-notes">供应商策略说明</Label><Textarea id="setting-provider-notes" value={form.providerPolicyNotes} onChange={event => setForm(value => ({ ...value, providerPolicyNotes: event.target.value }))} /></div></>
              ) : null}

              {section === 'permissions' ? (
                <div className="space-y-5"><OwnerV2WritePathControl /><fieldset className="space-y-3"><legend className="mb-3 text-sm font-semibold text-slate-950">不可降级的人工确认规则</legend>{policyCheckbox({ id: 'setting-paid-confirm', label: '付费操作必须确认', checked: true, disabled: true })}{policyCheckbox({ id: 'setting-bulk-confirm', label: '批量修改必须确认', checked: true, disabled: true })}{policyCheckbox({ id: 'setting-opportunity-confirm', label: 'AI 只提议商机，由销售人工确认', checked: true, disabled: true })}{policyCheckbox({ id: 'setting-review-confirm', label: 'Review 模式发送必须确认', checked: true, disabled: true })}<div className="space-y-2 pt-2"><Label htmlFor="setting-role-notes">角色与权限说明</Label><Textarea id="setting-role-notes" value={form.rolePolicyNotes} onChange={event => setForm(value => ({ ...value, rolePolicyNotes: event.target.value }))} /></div></fieldset></div>
              ) : null}
            </div>

            <div className="mt-6 flex flex-wrap gap-3 border-t border-slate-200 pt-5">
              <Button type="button" variant="outline" onClick={showPreview} disabled={!changedKeys.length}><Eye className="mr-2 h-4 w-4" />预览影响</Button>
              <Button type="button" onClick={save} disabled={!preview?.changedKeys.length || !confirmed || !previewMatchesDraft || saving}><Save className="mr-2 h-4 w-4" />{saving ? '保存中…' : '确认并保存'}</Button>
            </div>
          </section>

          <aside className="min-w-0 space-y-4" aria-label="设置影响与安全锁">
            <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="text-sm font-semibold text-slate-950">有效安全锁</h2>
              <dl className="mt-4 space-y-3 text-sm">
                {Object.entries(snapshot.effective_locks).map(([key, value]) => <div key={key} className="flex items-start justify-between gap-3"><dt className="text-slate-600">{key.replaceAll('_', ' ')}</dt><dd className="break-all text-right font-semibold text-slate-900">{String(value)}</dd></div>)}
              </dl>
              {snapshot.effective_locks.real_external_calls_allowed === false ? <p className="mt-4 flex gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-900"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />真实外部调用保持关闭。</p> : <p className="mt-4 flex gap-2 rounded-lg border border-rose-300 bg-rose-50 p-3 text-xs leading-5 text-rose-900"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />真实外部调用已允许，请复核生产审批。</p>}
            </section>

            <section aria-live="polite" className="rounded-lg border border-slate-200 bg-slate-50 p-5">
              <h2 className="text-sm font-semibold text-slate-950">影响预览</h2>
              {preview === null ? <p className="mt-3 text-sm text-slate-600">修改字段后点击“预览影响”。</p> : preview.changedKeys.length ? <><ul className="mt-3 space-y-2 text-sm text-slate-700">{preview.changedKeys.map(key => <li key={key} className="rounded-md bg-white px-3 py-2">将更新：{fieldLabels[key] ?? key}</li>)}</ul><label htmlFor="setting-impact-confirm" className="mt-4 flex min-h-11 items-center gap-3 text-sm font-medium text-slate-900"><input id="setting-impact-confirm" type="checkbox" checked={confirmed} disabled={!previewMatchesDraft} onChange={event => setConfirmed(event.target.checked)} className="h-5 w-5 accent-indigo-700" />我已核对影响并确认保存</label></> : <p className="mt-3 text-sm text-slate-600">没有变化。</p>}
              {message ? <p className="mt-4 text-sm font-medium text-slate-800">{message}</p> : null}
            </section>
          </aside>
        </div>
      ) : <div role="alert" className="rounded-lg border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900">{message || '设置不可用；写操作保持锁定。'}</div>}
    </ProductPageShell>
  );
}
