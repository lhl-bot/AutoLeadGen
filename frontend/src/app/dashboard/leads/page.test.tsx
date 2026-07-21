import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiFetchMock, translateMock } = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  translateMock: vi.fn((key: string) => key),
}));

vi.mock('@/lib/utils', async importOriginal => {
  const actual = await importOriginal<typeof import('@/lib/utils')>();
  return { ...actual, apiFetch: apiFetchMock };
});

vi.mock('@/lib/i18n', () => ({
  useTranslation: () => ({ t: translateMock }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import LeadsPage from './page';

const lead = {
  id: 1,
  workflow_id: 18,
  domain: 'ready.example',
  company_name: 'Ready Buyer',
  email: 'buyer@ready.example',
  first_name: 'Ada',
  last_name: 'Buyer',
  job_title: 'Purchasing Manager',
  status: 'found',
  email_verified: true,
  email_validation_status: 'valid',
  fit_score: 86,
  fit_grade: 'A',
};

const brief = {
  id: 7,
  lead_id: 1,
  company_overview: 'Verified public company overview.',
  recent_news: 'Opened a new bedding collection.',
  pain_points: 'Qualification hypotheses: supplier reliability.',
  value_proposition_alignment: 'Potential bedding supply alignment.',
  specific_products: 'Sheets; Duvet Covers',
  recent_activity: 'Expanded its online catalog.',
  personalization_hook: 'Reference the bedding catalog.',
  research_status: 'valid',
  quality_flags: ['public_web:evidence_first'],
  evidence_sources: [{ type: 'official_website', value: 'https://ready.example' }],
  researched_at: '2026-07-19T11:00:00Z',
  created_at: '2026-07-19T11:00:00Z',
  updated_at: '2026-07-19T11:00:00Z',
};

describe('Legacy customer research workspace', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/workflows/') return new Response(JSON.stringify([]), { status: 200 });
      if (path === '/api/leads/1/brief') return new Response(JSON.stringify(brief), { status: 200 });
      if (path.startsWith('/api/leads?')) return new Response(JSON.stringify([lead]), { status: 200 });
      return new Response('{}', { status: 404 });
    });
  });

  it('combines evidence and email filters and renders the complete dossier', async () => {
    const user = userEvent.setup();
    render(<LeadsPage />);

    await screen.findByRole('button', { name: /Ready Buyer/ });
    await user.selectOptions(screen.getByLabelText('Research status'), 'valid');
    await user.selectOptions(screen.getByLabelText('Email status'), 'valid');
    await user.selectOptions(screen.getByLabelText('Contact history'), 'never_contacted');

    await waitFor(() => {
      expect(apiFetchMock.mock.calls.some(([path]) => (
        String(path).includes('research_status=valid')
        && String(path).includes('email_status=valid')
        && String(path).includes('contact_history=never_contacted')
      ))).toBe(true);
    });

    await user.click(await screen.findByRole('button', { name: /Ready Buyer/ }));

    expect(await screen.findByText('Verified public company overview.')).toBeInTheDocument();
    expect(screen.getByText('Sheets; Duvet Covers')).toBeInTheDocument();
    expect(screen.getByText('Potential bedding supply alignment.')).toBeInTheDocument();
    expect(screen.getByText('public_web:evidence_first')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /https:\/\/ready\.example/ })).toHaveAttribute(
      'href',
      'https://ready.example/',
    );
  });
});
