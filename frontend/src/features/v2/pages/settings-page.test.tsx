import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { v2Api } from '../api';
import type { ProductSettingSnapshot } from '../types';
import SettingsPage from './settings-page';

afterEach(() => vi.restoreAllMocks());

const channelSnapshot: ProductSettingSnapshot = {
  section: 'channels_integrations',
  version: 2,
  values: {
    email_enabled: false,
    linkedin_enabled: false,
    whatsapp_enabled: false,
    public_unsubscribe_url: '',
    review_before_send: true,
    integration_notes: '',
  },
  updated_at: '2026-07-16T01:00:00Z',
  updated_by_user_id: 1,
  effective_locks: {
    environment: 'local',
    connector_mode: 'fake',
    outbound_hard_pause: true,
    real_external_calls_allowed: false,
    credentials_accepted_here: false,
  },
};

const permissionSnapshot: ProductSettingSnapshot = {
  section: 'permissions',
  version: 0,
  values: {
    paid_actions_require_confirmation: true,
    bulk_mutations_require_confirmation: true,
    opportunity_requires_human_confirmation: true,
    review_mode_send_requires_confirmation: true,
    role_policy_notes: '',
  },
  updated_at: null,
  updated_by_user_id: null,
  effective_locks: { real_external_calls_allowed: false },
};

const channelAccount = {
  id: 7,
  ownerId: 13,
  channel: 'email',
  provider: 'smtp',
  address: 'info@example.com',
  displayName: 'Sales Mailbox',
  enabled: true,
  healthStatus: 'unknown' as const,
  dailyLimit: 20,
  timezone: 'UTC',
  smtpHost: 'smtp.example.com',
  smtpPort: 465,
  imapHost: 'imap.example.com',
  imapPort: 993,
  transport: 'smtps',
  credentialsConfigured: true,
  legacyEmailAccountId: 9,
};

describe('Product V2 settings', () => {
  it('requires impact preview and explicit confirmation before saving a versioned policy', async () => {
    // GIVEN: A live V2 channel policy loaded from the immutable settings stream.
    const user = userEvent.setup();
    vi.spyOn(v2Api, 'productSetting').mockResolvedValue(channelSnapshot);
    vi.spyOn(v2Api, 'channelAccounts').mockResolvedValue([channelAccount]);
    const update = vi.spyOn(v2Api, 'updateProductSetting').mockResolvedValue({
      ...channelSnapshot,
      version: 3,
      values: { ...channelSnapshot.values, email_enabled: true },
    });
    render(<SettingsPage section="channels_integrations" />);
    await screen.findByRole('heading', { name: '渠道与集成' });
    expect(screen.getByRole('checkbox', { name: 'LinkedIn（尚未开放）' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'WhatsApp（尚未开放）' })).toBeDisabled();
    expect(screen.getByText(/当前生产版本只支持 Email/)).toBeInTheDocument();

    // WHEN: The operator enables Email but has not reviewed the impact.
    await user.click(screen.getByRole('checkbox', { name: 'Email' }));

    // THEN: Saving stays locked until preview and confirmation are explicit.
    const save = screen.getByRole('button', { name: '确认并保存' });
    expect(save).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '预览影响' }));
    expect(screen.getByText('将更新：Email 策略')).toBeInTheDocument();
    expect(save).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: '我已核对影响并确认保存' }));
    await user.click(save);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith('channels_integrations', {
      version: 2,
      values: { ...channelSnapshot.values, email_enabled: true },
    });
    expect(await screen.findByText(/已保存版本 3/)).toBeInTheDocument();
  });

  it('shows the migrated mailbox and requires preview plus confirmation before V2 takeover', async () => {
    const user = userEvent.setup();
    vi.spyOn(v2Api, 'productSetting').mockResolvedValue(channelSnapshot);
    vi.spyOn(v2Api, 'channelAccounts').mockResolvedValue([channelAccount]);
    const bindingPreview = {
      legacyEmailAccountId: 9,
      currentChannelAccountId: 7,
      address: 'info@example.com',
      dailyLimit: 10,
      timezone: 'Asia/Shanghai',
      previewChecksum: 'b'.repeat(64),
      effects: {
        credential_copy_count: 0,
        message_send_count: 0,
        external_provider_call_count: 0,
      },
      warnings: [],
    };
    const preview = vi.spyOn(v2Api, 'previewEmailAccountBinding').mockResolvedValue(bindingPreview);
    const apply = vi.spyOn(v2Api, 'applyEmailAccountBinding').mockResolvedValue({
      ...channelAccount,
      dailyLimit: 10,
      timezone: 'Asia/Shanghai',
    });

    render(<SettingsPage section="channels_integrations" />);
    expect(await screen.findByRole('heading', { name: 'V2 邮箱账户' })).toBeInTheDocument();
    expect(await screen.findByText('info@example.com')).toBeInTheDocument();
    expect(screen.getByText('已配置（不可见）')).toBeInTheDocument();
    expect(screen.getByText('待无发送探测')).toBeInTheDocument();

    const dailyLimit = screen.getByRole('spinbutton', { name: '每日硬上限' });
    await user.clear(dailyLimit);
    await user.type(dailyLimit, '10');
    const timezone = screen.getByRole('textbox', { name: 'Timezone' });
    await user.clear(timezone);
    await user.type(timezone, 'Asia/Shanghai');
    await user.click(screen.getByRole('button', { name: '预览账户接管' }));

    await waitFor(() => expect(preview).toHaveBeenCalledWith({
      legacyEmailAccountId: 9,
      dailyLimit: 10,
      timezone: 'Asia/Shanghai',
    }));
    expect(screen.getByText('复制密码：0；外部调用：0；发送邮件：0')).toBeInTheDocument();
    expect(apply).not.toHaveBeenCalled();
    const confirm = screen.getByRole('checkbox', { name: '我已核对邮箱身份、每日上限和影响，确认由 V2 接管' });
    expect(screen.getByRole('button', { name: '确认接管邮箱账户' })).toBeDisabled();
    await user.click(confirm);
    await user.click(screen.getByRole('button', { name: '确认接管邮箱账户' }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith(bindingPreview));
    expect(await screen.findByText(/真实外发暂停状态未改变/)).toBeInTheDocument();
  });

  it('renders human confirmation rules as non-downgradeable controls', async () => {
    // GIVEN: The locked Product V2 permission policy.
    vi.spyOn(v2Api, 'productSetting').mockResolvedValue(permissionSnapshot);
    vi.spyOn(v2Api, 'ownerMigrationState').mockResolvedValue({
      ownerId: 13,
      currentPath: 'legacy',
      version: 0,
      explicit: false,
    });

    // WHEN: The permission page is rendered.
    render(<SettingsPage section="permissions" />);
    await screen.findByRole('heading', { name: '权限策略' });

    // THEN: AI opportunity confirmation and other hard policies cannot be switched off.
    expect(screen.getByRole('checkbox', { name: 'AI 只提议商机，由销售人工确认' })).toBeDisabled();
    expect(screen.getAllByRole('checkbox')).toHaveLength(4);
    for (const checkbox of screen.getAllByRole('checkbox')) expect(checkbox).toBeDisabled();
  });

  it('requires a server preview and explicit confirmation before activating the V2 write path', async () => {
    const user = userEvent.setup();
    vi.spyOn(v2Api, 'productSetting').mockResolvedValue(permissionSnapshot);
    vi.spyOn(v2Api, 'ownerMigrationState').mockResolvedValue({
      ownerId: 13,
      currentPath: 'legacy',
      version: 0,
      explicit: false,
    });
    const migrationPreview = {
      ownerId: 13,
      currentPath: 'legacy' as const,
      targetPath: 'v2' as const,
      expectedVersion: 0,
      previewChecksum: 'a'.repeat(64),
      effects: { legacy_writes_allowed: false, v2_writes_allowed: true },
      blockers: [],
    };
    const preview = vi.spyOn(v2Api, 'previewOwnerV2Migration').mockResolvedValue(migrationPreview);
    const activate = vi.spyOn(v2Api, 'activateOwnerV2Migration').mockResolvedValue({
      ownerId: 13,
      currentPath: 'v2',
      version: 1,
      explicit: true,
      switchedAt: '2026-07-19T12:00:00Z',
    });

    render(<SettingsPage section="permissions" />);
    const previewButton = await screen.findByRole('button', { name: '预览启用 V2 写入' });
    await waitFor(() => expect(previewButton).toBeEnabled());
    await user.click(previewButton);

    expect(preview).toHaveBeenCalledTimes(1);
    expect(activate).not.toHaveBeenCalled();
    const confirmation = await screen.findByRole('checkbox', { name: '我已核对影响，确认将当前账号切换为 V2 唯一写入路径' });
    expect(screen.getByRole('button', { name: '确认启用 V2 写入' })).toBeDisabled();

    await user.click(confirmation);
    await user.click(screen.getByRole('button', { name: '确认启用 V2 写入' }));

    await waitFor(() => expect(activate).toHaveBeenCalledWith(migrationPreview));
    expect(await screen.findByText(/V2 写入路径已显式启用，版本 1/)).toBeInTheDocument();
  });

  it('invalidates preview and confirmation when the reviewed draft changes', async () => {
    // GIVEN: The operator has previewed and confirmed an Email policy change.
    const user = userEvent.setup();
    vi.spyOn(v2Api, 'productSetting').mockResolvedValue(channelSnapshot);
    vi.spyOn(v2Api, 'channelAccounts').mockResolvedValue([channelAccount]);
    const update = vi.spyOn(v2Api, 'updateProductSetting').mockImplementation(
      async (_section, reviewed) => ({
        ...channelSnapshot,
        version: 3,
        values: reviewed.values,
      }),
    );
    render(<SettingsPage section="channels_integrations" />);
    await screen.findByRole('heading', { name: '渠道与集成' });
    await user.click(screen.getByRole('checkbox', { name: 'Email' }));
    await user.click(screen.getByRole('button', { name: '预览影响' }));
    await user.click(screen.getByRole('checkbox', { name: '我已核对影响并确认保存' }));
    expect(screen.getByRole('button', { name: '确认并保存' })).toBeEnabled();

    // WHEN: A text field is edited after that confirmation.
    await user.type(
      screen.getByRole('textbox', { name: '集成说明（不填写密钥）' }),
      'reviewed again',
    );

    // THEN: The stale preview and confirmation disappear, and no unreviewed
    // snapshot can be submitted.
    await waitFor(() => {
      expect(screen.queryByRole('checkbox', { name: '我已核对影响并确认保存' })).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: '确认并保存' })).toBeDisabled();
    expect(screen.getByText('设置在预览后已更改，请重新预览影响并确认。')).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();

    // WHEN: The operator previews and confirms the new snapshot.
    await user.click(screen.getByRole('button', { name: '预览影响' }));
    expect(screen.getByText('将更新：集成说明')).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: '我已核对影响并确认保存' }));
    await user.click(screen.getByRole('button', { name: '确认并保存' }));

    // THEN: Save receives exactly the newly reviewed version and values.
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith('channels_integrations', {
      version: 2,
      values: {
        ...channelSnapshot.values,
        email_enabled: true,
        integration_notes: 'reviewed again',
      },
    });
  });
});
