import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';

const liveBaseUrl = process.env.LIVE_V2_BASE_URL;
const liveUsername = process.env.LIVE_V2_USERNAME;
const livePassword = process.env.LIVE_V2_PASSWORD;
const liveAcceptanceEnabled = process.env.LIVE_V2_ACCEPTANCE === '1';
const liveCredentialsAvailable = Boolean(
  liveAcceptanceEnabled && liveBaseUrl && liveUsername && livePassword,
);

test.skip(
  !liveCredentialsAvailable,
  'Set LIVE_V2_ACCEPTANCE=1 plus LIVE_V2_BASE_URL, LIVE_V2_USERNAME, and LIVE_V2_PASSWORD to run isolated live acceptance.',
);

async function signIn(page: Page) {
  // GIVEN: A local preview user and the isolated fake-connector stack.
  await page.goto('/login');
  await page.locator('input[type="text"]').fill(liveUsername!);
  await page.locator('input[type="password"]').fill(livePassword!);

  // WHEN: The user signs in through the real login form.
  await page.locator('button[type="submit"]').click();

  // THEN: The real backend session opens the Product V2 workbench.
  await expect(page).toHaveURL(/\/dashboard(?:\/)?$/);
  await expect(page.getByRole('heading', { name: '今日工作' })).toBeVisible();
}

async function seriousAccessibilityViolations(page: Page) {
  await page.addScriptTag({ content: axe.source });
  return page.evaluate(async () => {
    const axeRuntime = (window as typeof window & {
      axe: {
        run: (root: Document) => Promise<{
          violations: Array<{
            id: string;
            impact: string | null;
            help: string;
            nodes: Array<{ target: string[]; failureSummary?: string }>;
          }>;
        }>;
      };
    }).axe;
    const result = await axeRuntime.run(document);
    return result.violations.filter(violation =>
      violation.impact === 'serious' || violation.impact === 'critical',
    );
  });
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test('exposes an accessible labeled login form', async ({ page }) => {
  // GIVEN: The public login surface rendered in the local preview.
  await page.goto('/login');

  // WHEN/THEN: Both credentials have programmatic names and the complete
  // surface has no serious or critical automated WCAG finding.
  await expect(page.getByLabel(/Username|用户名/)).toBeVisible();
  await expect(page.getByLabel(/Password|密码/)).toBeVisible();
  expect(await seriousAccessibilityViolations(page)).toEqual([]);
});

async function saveChannelNotes(page: Page, value: string) {
  const versionText = await page.getByText(/当前版本 \d+/).textContent();
  const previousVersion = Number(versionText?.match(/\d+/)?.[0]);
  expect(Number.isInteger(previousVersion)).toBe(true);

  await page.getByLabel('集成说明（不填写密钥）').fill(value);
  await page.getByRole('button', { name: '预览影响' }).click();
  await expect(page.getByText('将更新：集成说明')).toBeVisible();
  await page.getByLabel('我已核对影响并确认保存').check();

  const responsePromise = page.waitForResponse(response =>
    response.request().method() === 'PUT'
    && response.url().endsWith('/api/v2/settings/channels_integrations'),
  );
  await page.getByRole('button', { name: '确认并保存' }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json() as { version: number; values: { integration_notes: string } };
  expect(payload.version).toBe(previousVersion + 1);
  expect(payload.values.integration_notes).toBe(value);
  await expect(page.getByText(`已保存版本 ${payload.version}，审计记录已写入。`)).toBeVisible();
}

test('persists a preview-confirmed settings update through the real V2 API', async ({ page }) => {
  // GIVEN: The live channels policy loaded from the isolated backend.
  await page.goto('/dashboard/settings/channels');
  await expect(page.getByRole('heading', { name: '渠道与集成' })).toBeVisible();
  await expect(page.getByText(/当前版本 \d+/)).toBeVisible();
  await expect(page.getByText('真实外部调用保持关闭。')).toBeVisible();

  const notes = page.getByLabel('集成说明（不填写密钥）');
  const original = await notes.inputValue();
  const acceptedValue = `local browser acceptance ${Date.now()}`;

  try {
    // WHEN: A human previews and confirms a changed policy document.
    await saveChannelNotes(page, acceptedValue);

    // THEN: A reload reads the exact value written by the real V2 API.
    await page.reload();
    await expect(page.getByLabel('集成说明（不填写密钥）')).toHaveValue(acceptedValue);
  } finally {
    // AND: Restore the prior local policy even if a later assertion fails.
    await page.goto('/dashboard/settings/channels');
    await expect(page.getByText(/当前版本 \d+/)).toBeVisible();
    const current = await page.getByLabel('集成说明（不填写密钥）').inputValue();
    if (current !== original) await saveChannelNotes(page, original);
  }
});

test('keeps mobile Product V2 pages contained and free of serious WCAG violations', async ({ page }) => {
  // GIVEN: A narrow mobile viewport.
  await page.setViewportSize({ width: 390, height: 844 });

  const surfaces = [
    { route: '/dashboard/customers', ready: 'V2 实时数据' },
    { route: '/dashboard/analytics', ready: 'V2 实时数据' },
    { route: '/dashboard/settings/channels', ready: '真实外部调用保持关闭。' },
  ];
  for (const { route, ready } of surfaces) {
    // WHEN: A primary Product V2 surface is rendered from the real backend.
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible();
    await expect(page.getByText(ready, { exact: true }).first()).toBeVisible();

    // THEN: The document has no horizontal overflow or serious/critical axe finding.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${route} horizontal overflow`).toBeLessThanOrEqual(1);
    expect(await seriousAccessibilityViolations(page), `${route} WCAG violations`).toEqual([]);
  }
});

test('does not emit legacy read-only exceptions while rendering a legacy diagnostics page', async ({ page }) => {
  // GIVEN: A signed-in browser with console error capture enabled.
  const errors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', error => errors.push(error.message));

  // WHEN: A legacy surface is opened in read-only mode.
  await page.goto('/dashboard/api-usage');
  await expect(page.getByRole('heading', { name: /接口用量和余额|API Usage & Balances/ })).toBeVisible();
  await expect(page.getByText(/此区域仅用于迁移对账/)).toBeAttached();
  await expect(page.getByText(/409|LEGACY_API_READ_ONLY/).first()).toBeVisible();

  // THEN: Blocking unsafe legacy calls does not leak an exception into the console.
  expect(errors.filter(message => message.includes('LegacyReadOnlyRequestError'))).toEqual([]);
});
