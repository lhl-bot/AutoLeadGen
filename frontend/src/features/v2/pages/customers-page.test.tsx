import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CompanyWorkspace, CustomerSnapshot, DataEnvelope } from '../types';

const { companyWorkspaceMock, mutateV2JsonMock, queryState, refreshMock, updateCompanyMock, updateContactMock } = vi.hoisted(() => ({
  companyWorkspaceMock: vi.fn(),
  mutateV2JsonMock: vi.fn(),
  queryState: { value: {} as unknown },
  refreshMock: vi.fn(),
  updateCompanyMock: vi.fn(),
  updateContactMock: vi.fn(),
}));

vi.mock('../api', () => ({
  mutateV2Json: mutateV2JsonMock,
  v2Api: {
    companyWorkspace: companyWorkspaceMock,
    customers: vi.fn(),
    updateCompany: updateCompanyMock,
    updateContact: updateContactMock,
  },
}));

vi.mock('../use-v2-query', () => ({
  useV2Query: () => queryState.value,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import CustomersPage from './customers-page';

const snapshot: CustomerSnapshot = {
  companies: [{
    id: '17',
    name: 'Acme Manufacturing',
    domain: 'acme.example',
    industry: '制造',
    region: '上海',
    contacts: 1,
    verifiedContacts: 1,
  }],
  contacts: [{
    id: '23',
    companyId: '17',
    name: 'Ada Chen',
    company: 'Acme Manufacturing',
    domain: 'acme.example',
    email: 'ada@acme.example',
    title: 'VP Sales',
    status: 'valid / available',
    verified: true,
    channels: ['email'],
  }],
  lists: [],
};

const workspace: CompanyWorkspace = {
  company: snapshot.companies[0],
  contacts: snapshot.contacts,
  evidence: [{
    id: '31',
    source: 'official_company_site',
    sourceUrl: 'https://acme.example/about',
    confidence: 0.94,
    capturedAt: '2026-07-15T07:00:00Z',
    evidence: {
      fit_grade: 'A',
      fit_score: 88,
      company_overview: 'Verified manufacturer profile.',
      specific_products: 'Industrial textiles',
      personalization_hook: 'Public expansion program.',
      quality_flags: ['public_web:official_site'],
    },
  }],
  outreach: { enrollmentCount: 1, sentCount: 2, replyCount: 1 },
};

function envelope(source: DataEnvelope<CustomerSnapshot>['source']): DataEnvelope<CustomerSnapshot> {
  return { data: snapshot, source, observedAt: '2026-07-15T08:00:00Z' };
}

describe('Product V2 customer library writes', () => {
  beforeEach(() => {
    mutateV2JsonMock.mockReset();
    mutateV2JsonMock.mockResolvedValue({ id: 101 });
    companyWorkspaceMock.mockReset();
    companyWorkspaceMock.mockResolvedValue(workspace);
    refreshMock.mockReset();
    updateCompanyMock.mockReset();
    updateCompanyMock.mockResolvedValue(workspace.company);
    updateContactMock.mockReset();
    updateContactMock.mockResolvedValue(workspace.contacts[0]);
  });

  it('keeps every create entry point locked for sample data', () => {
    // GIVEN: The customer view fell back to visibly labeled sample data.
    queryState.value = { result: envelope('sample'), loading: false, refresh: refreshMock };

    // WHEN: The Product V2 customer library is rendered.
    render(<CustomersPage />);

    // THEN: Sample entities can be inspected but can never be posted back as real records.
    expect(screen.getByRole('status', { name: '客户库写入状态' })).toHaveTextContent('示例数据');
    expect(screen.getByRole('button', { name: '新建公司' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新建联系人' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新建分组' })).toBeDisabled();
    expect(mutateV2JsonMock).not.toHaveBeenCalled();
  });

  it('previews and posts a new email ContactPoint explicitly as unverified', async () => {
    // GIVEN: A real V2 Company is loaded and the user enters a new Contact.
    const user = userEvent.setup();
    queryState.value = { result: envelope('live'), loading: false, refresh: refreshMock };
    render(<CustomersPage />);

    await user.click(screen.getByRole('button', { name: '新建联系人' }));
    await user.type(screen.getByRole('textbox', { name: 'Contact 姓名' }), 'Ada Chen');
    await user.type(screen.getByRole('textbox', { name: 'Contact 岗位' }), 'VP Sales');
    await user.type(screen.getByRole('textbox', { name: 'Contact timezone' }), 'Asia/Shanghai');
    await user.type(screen.getByRole('textbox', { name: 'Contact email' }), 'ada@acme.example');

    // WHEN: The first action only prepares an impact preview.
    await user.click(screen.getByRole('button', { name: '预览创建 Contact' }));

    // THEN: Nothing is written before the explicit confirmation, and the unverified effect is visible.
    expect(mutateV2JsonMock).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: '创建 Contact · 影响确认' })).toBeInTheDocument();
    expect(screen.getByText(/unverified \/ available/)).toBeInTheDocument();

    // WHEN: The user confirms the impact.
    await user.click(screen.getByRole('button', { name: '确认创建 Contact' }));

    // THEN: Only the V2 Contact endpoint is called and the UI never upgrades verification status.
    await waitFor(() => expect(mutateV2JsonMock).toHaveBeenCalledTimes(1));
    expect(mutateV2JsonMock).toHaveBeenCalledWith('/api/v2/contacts', {
      method: 'POST',
      body: {
        company_id: 17,
        full_name: 'Ada Chen',
        job_title: 'VP Sales',
        timezone: 'Asia/Shanghai',
        contact_points: [{
          channel: 'email',
          value: 'ada@acme.example',
          verification_status: 'unverified',
          availability_status: 'available',
          is_primary: true,
        }],
      },
    });
    const payload = mutateV2JsonMock.mock.calls[0][1].body;
    expect(payload.contact_points[0].verification_status).not.toBe('valid');
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it('filters both customer tables and opens an evidence-backed editable dossier', async () => {
    const user = userEvent.setup();
    queryState.value = { result: envelope('live'), loading: false, refresh: refreshMock };
    render(<CustomersPage />);

    await user.type(screen.getByRole('textbox', { name: '搜索公司、域名、联系人或邮箱' }), 'Ada Chen');
    expect(screen.getByText('匹配 1 / 1 家')).toBeInTheDocument();
    expect(screen.getByText('匹配 1 / 1 人')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '查看完整档案' }));
    expect(await screen.findByRole('dialog', { name: 'Acme Manufacturing' })).toBeInTheDocument();
    expect(screen.getByText('Verified manufacturer profile.')).toBeInTheDocument();
    expect(screen.getAllByText('暂无可靠证据；不会自动编造。').length).toBeGreaterThan(0);
    expect(screen.getByRole('region', { name: '客户关键指标' })).toHaveTextContent('88');
    expect(screen.getByRole('region', { name: '客户关键指标' })).toHaveTextContent('1');

    const industry = screen.getByRole('textbox', { name: '编辑公司行业' });
    await user.clear(industry);
    await user.type(industry, '制造业');
    await user.click(screen.getByRole('button', { name: '保存公司资料' }));

    await waitFor(() => expect(updateCompanyMock).toHaveBeenCalledWith('17', {
      name: 'Acme Manufacturing',
      domain: 'acme.example',
      industry: '制造业',
      region: '上海',
    }));
    expect(companyWorkspaceMock).toHaveBeenCalledWith('17', expect.any(AbortSignal));
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it('renders large customer datasets in bounded pages', async () => {
    const user = userEvent.setup();
    const companies = Array.from({ length: 30 }, (_, index) => ({
      ...snapshot.companies[0],
      id: String(index + 1),
      name: `Company ${index + 1}`,
    }));
    const contacts = Array.from({ length: 30 }, (_, index) => ({
      ...snapshot.contacts[0],
      id: String(index + 1),
      companyId: String(index + 1),
      name: `Contact ${index + 1}`,
      company: `Company ${index + 1}`,
    }));
    queryState.value = {
      result: { ...envelope('live'), data: { companies, contacts, lists: [] } },
      loading: false,
      refresh: refreshMock,
    };

    render(<CustomersPage />);

    const companyTable = screen.getByRole('region', { name: 'Companies 表格，可横向滚动' });
    expect(within(companyTable).getAllByRole('row')).toHaveLength(26);
    expect(screen.getByRole('navigation', { name: 'Companies 分页' })).toHaveTextContent('当前 1–25，共 30 条');

    await user.click(screen.getByRole('button', { name: 'Companies 下一页' }));

    expect(within(companyTable).getAllByRole('row')).toHaveLength(6);
    expect(within(companyTable).getByText('Company 30')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Companies 分页' })).toHaveTextContent('第 2 / 2 页');
  });

  it('creates an Audience List exactly once after impact confirmation', async () => {
    const user = userEvent.setup();
    queryState.value = { result: envelope('live'), loading: false, refresh: refreshMock };
    render(<CustomersPage />);

    await user.click(screen.getByRole('button', { name: '新建分组' }));
    await user.type(screen.getByRole('textbox', { name: 'Audience List 名称' }), 'Priority accounts');
    await user.click(screen.getByRole('button', { name: '预览创建 Audience List' }));
    expect(mutateV2JsonMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认创建 Audience List' }));

    await waitFor(() => expect(mutateV2JsonMock).toHaveBeenCalledTimes(1));
    expect(mutateV2JsonMock).toHaveBeenCalledWith('/api/v2/lists', {
      method: 'POST',
      body: { name: 'Priority accounts' },
    });
  });
});
