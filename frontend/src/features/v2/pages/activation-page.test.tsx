import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { activationMock, acquisitionRunMock, channelAccountsMock, importMock } = vi.hoisted(() => ({
  activationMock: vi.fn(),
  acquisitionRunMock: vi.fn(),
  channelAccountsMock: vi.fn(),
  importMock: vi.fn(),
}));

vi.mock('../api', () => ({
  V2MutationError: class V2MutationError extends Error {},
  v2Api: {
    activation: activationMock,
    acquisitionRun: acquisitionRunMock,
    channelAccounts: channelAccountsMock,
    importAcquisitionCsv: importMock,
    searchAcquisition: vi.fn(),
    verifyAcquisition: vi.fn(),
    commitAcquisition: vi.fn(),
    previewActivationLaunch: vi.fn(),
    launchActivation: vi.fn(),
  },
}));

import ActivationPage from './activation-page';

const activation = {
  activated: false,
  current_step: 3,
  started_at: null,
  first_sent_at: null,
  steps: [
    { key: 'icp', label: '定义 ICP', completed: true, detail: '已发布', href: '/dashboard/settings/icp-playbook' },
    { key: 'mailbox', label: '发件邮箱', completed: true, detail: '已绑定', href: '/dashboard/settings/channels' },
    { key: 'customers', label: '首批客户', completed: false, detail: '待准备', href: '/dashboard/get-started?step=3' },
    { key: 'plan', label: '审核计划', completed: false, detail: '待创建', href: '/dashboard/get-started?step=4' },
    { key: 'send', label: '首封邮件', completed: false, detail: '待发送', href: '/dashboard/get-started?step=5' },
  ],
  blockers: [],
  latest_run_id: null,
  campaign_id: null,
  review_tasks_open: 0,
};

const importedRun = {
  id: 41,
  source: 'csv',
  status: 'ready',
  name: '首批客户',
  criteria: {},
  column_mapping: {},
  provider: 'csv-import',
  estimated_units: '0',
  price_version: 'free-v1',
  last_error: null,
  committed_at: null,
  created_at: '2026-07-20T04:00:00Z',
  updated_at: '2026-07-20T04:00:00Z',
  candidates: [{
    id: 101,
    run_id: 41,
    row_number: 2,
    status: 'ready',
    selected: false,
    company_name: 'Nordic Home',
    normalized_domain: 'nordic-home.example',
    first_name: 'Ada',
    last_name: null,
    full_name: 'Ada',
    job_title: 'Buyer',
    email: 'ada@nordic-home.example',
    source_url: null,
    evidence: { source: 'csv' },
    confidence: '1.0000',
    verification_status: 'unverified',
    verification_source: null,
    verification_checked_at: null,
    rejection_reason: null,
    committed_company_id: null,
    committed_contact_id: null,
    committed_contact_point_id: null,
  }],
  job_id: null,
};

describe('first-touch activation page', () => {
  beforeEach(() => {
    activationMock.mockReset().mockResolvedValue(activation);
    acquisitionRunMock.mockReset();
    channelAccountsMock.mockReset().mockResolvedValue([]);
    importMock.mockReset().mockResolvedValue(importedRun);
  });

  it('keeps CSV data in a review workspace before verification and commit', async () => {
    const user = userEvent.setup();
    render(<ActivationPage acquisitionOnly />);

    const fileInput = await screen.findByLabelText('CSV 文件（最大 2 MB）');
    const previewButton = screen.getByRole('button', { name: '安全预览' });
    expect(previewButton).toBeDisabled();

    const file = new File(['Company,Domain,Email\nNordic Home,nordic-home.example,ada@nordic-home.example'], 'pilot.csv', { type: 'text/csv' });
    await user.upload(fileInput, file);
    await user.click(previewButton);

    await waitFor(() => expect(importMock).toHaveBeenCalledWith(file, 'pilot'));
    expect(screen.getByRole('status')).toHaveTextContent('尚未写入正式客户库');
    expect(screen.getByText('Nordic Home')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '验证 1 人' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '确认入库 0 人' })).toBeDisabled();
  });

  it('shows the complete five-step route and never renders more than 100 candidate rows', async () => {
    activationMock.mockResolvedValue({ ...activation, latest_run_id: 41 });
    acquisitionRunMock.mockResolvedValue({
      ...importedRun,
      candidates: Array.from({ length: 130 }, (_, index) => ({
        ...importedRun.candidates[0],
        id: index + 1,
        row_number: index + 2,
        company_name: `Company ${index + 1}`,
      })),
    });

    render(<ActivationPage />);

    expect(await screen.findByRole('heading', { name: '1. 发布 ICP / Playbook' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '4. 创建逐封审核计划' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '5. 在审核中批准第一封邮件' })).toBeInTheDocument();
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(101);
    expect(screen.getByText('当前仅展示前 100 条；首批试跑最多选择 20 人。')).toBeInTheDocument();
  });
});
