import { render, screen } from '@testing-library/react';
import axe from 'axe-core';
import { describe, expect, it } from 'vitest';
import { mapReadiness, mapRuntimeSnapshot, resolveCompositeSource } from '../api';
import { ProductPageShell, RuntimeGrid, RuntimeSummary, SourceBanner } from './product-ui';

describe('Product V2 shared UI', () => {
  it('reports expired heartbeat leases as offline instead of healthy', () => {
    // GIVEN: A worker heartbeat whose lease has already expired.
    const runtime = mapRuntimeSnapshot([{
      worker_name: 'outbound-test',
      worker_type: 'outbound',
      status: 'running',
      last_seen_at: '2025-01-01T00:00:00Z',
      lease_expires_at: '2025-01-01T00:01:00Z',
      details: {},
    }]);

    // WHEN: The runtime snapshot is rendered.
    render(<RuntimeGrid runtime={runtime} />);

    // THEN: The UI shows an offline state and never claims all workers are online.
    expect(screen.getByText('离线')).toBeInTheDocument();
    expect(screen.queryByText(/System Online/i)).not.toBeInTheDocument();
  });

  it('treats a heartbeat without a lease as offline', () => {
    // GIVEN: A worker reports running but omits the lease that proves ownership.
    const runtime = mapRuntimeSnapshot([{
      worker_name: 'inbox-without-lease',
      worker_type: 'inbox',
      status: 'running',
      last_seen_at: new Date().toISOString(),
      lease_expires_at: null,
      details: {},
    }]);

    // WHEN: Runtime truth is mapped for the UI.
    const inbox = runtime.services.find(service => service.id === 'inbox');

    // THEN: Missing lease cannot produce a healthy state.
    expect(inbox?.state).toBe('offline');
  });

  it('does not show a green summary for disabled or backing-off workers', () => {
    // GIVEN: Every known worker is either disabled or in provider backoff.
    const view = render(<RuntimeSummary runtime={{
      services: [
        { id: 'outbound', label: '外发执行', state: 'backoff', detail: '限速' },
        { id: 'inbox', label: '收件箱', state: 'disabled', detail: '未配置' },
      ],
      activeCampaigns: 0,
      recentMessages: 0,
      timestamp: new Date().toISOString(),
    }} />);

    // WHEN: The compact runtime summary is rendered.
    const summary = screen.getByLabelText(/系统状态/);

    // THEN: It reports degraded truth and does not render the healthy green indicator.
    expect(summary).toHaveTextContent('2 个执行器退避或禁用');
    expect(view.container.querySelector('.bg-emerald-600')).not.toBeInTheDocument();
  });

  it('labels a live and sample composition as mixed and routes every core blocker', () => {
    // GIVEN: The work page combines live tasks with a sampled runtime section.
    const source = resolveCompositeSource(['live', 'sample']);
    const checks = mapReadiness({
      campaign_id: 1,
      ready: false,
      blockers: [
        { code: 'valid_audience', severity: 'blocker', passed: false, message: '需要有效受众' },
        { code: 'published_revision', severity: 'blocker', passed: false, message: '需要发布版本' },
        { code: 'safety_lock', severity: 'blocker', passed: false, message: '存在硬暂停' },
        { code: 'channel_accounts', severity: 'blocker', passed: false, message: '需要渠道账户' },
      ],
      warnings: [],
      checked_at: new Date().toISOString(),
    });

    // WHEN: Source and blocker remediation are mapped.
    const routes = Object.fromEntries(checks.map(check => [check.key, check.remediationHref]));

    // THEN: The page cannot claim fully live data and every blocker has a useful destination.
    expect(source).toBe('mixed');
    expect(routes).toEqual({
      valid_audience: '/dashboard/customers',
      published_revision: '/dashboard/campaigns',
      safety_lock: '/dashboard/work',
      channel_accounts: '/dashboard/settings/channels',
    });
  });

  it('marks sample data and passes an automated accessibility smoke check', async () => {
    // GIVEN: A page using a clearly marked sample envelope.
    const view = render(
      <ProductPageShell eyebrow="Product V2" title="今日工作" description="测试描述">
        <SourceBanner envelope={{ data: {}, source: 'sample', observedAt: '2026-07-15T00:00:00Z', warning: '本地 API 不可用' }} />
      </ProductPageShell>,
    );

    // WHEN: axe inspects the rendered region.
    const results = await axe.run(view.container, {
      rules: { 'color-contrast': { enabled: false } },
    });

    // THEN: The source is explicit and no basic WCAG violation is detected.
    expect(screen.getByText('示例数据')).toBeInTheDocument();
    expect(results.violations).toEqual([]);
  });

  it('makes a mixed source explicit instead of presenting it as live', () => {
    // GIVEN: A page has both persisted V2 data and sample fallback content.
    render(<SourceBanner envelope={{ data: {}, source: 'mixed', observedAt: '2026-07-15T00:00:00Z' }} />);

    // WHEN / THEN: The banner uses the dedicated mixed label.
    expect(screen.getByText('混合数据')).toBeInTheDocument();
    expect(screen.queryByText('V2 实时数据')).not.toBeInTheDocument();
  });
});
