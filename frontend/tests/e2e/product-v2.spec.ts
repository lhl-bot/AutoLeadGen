import { expect, test } from '@playwright/test';
import axe from 'axe-core';

async function seriousAccessibilityViolations(page: import('@playwright/test').Page) {
  await page.addScriptTag({ content: axe.source });
  return page.evaluate(async () => {
    const runtime = (window as typeof window & { axe: { run: (root: Document) => Promise<{ violations: Array<{ impact: string | null; id: string }> }> } }).axe;
    const result = await runtime.run(document);
    return result.violations.filter(item => item.impact === 'serious' || item.impact === 'critical');
  });
}

test.beforeEach(async ({ page }) => {
  // GIVEN: An authenticated local-only browser session and an unavailable V2 API.
  await page.addInitScript(() => {
    window.localStorage.setItem('auth_token', 'e2e-local-token');
    window.localStorage.setItem('auth_user', JSON.stringify({ username: 'e2e', is_admin: true, credit_balance: 0 }));
    window.localStorage.setItem('v2_demo_mode', '1');
  });
  await page.route('**/api/notifications**', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.route('**/api/auth/me', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ username: 'e2e', is_admin: true, credit_balance: 0 }) }));
  await page.route('**/api/v2/**', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'isolated e2e' }) }));
});

test('exposes the four task-oriented Product V2 destinations without static health claims', async ({ page }) => {
  // WHEN: The user opens the daily workbench.
  await page.goto('/dashboard');

  // THEN: All four primary destinations are present and explicit demo fallback is marked.
  const navigation = page.getByRole('complementary', { name: '主导航' });
  for (const label of ['工作台', '客户', '对话', '结果']) {
    await expect(navigation.getByRole('link', { name: label, exact: true })).toBeVisible();
  }
  await expect(page.getByRole('heading', { name: '今日工作' })).toBeVisible();
  await expect(page.getByText('示例数据').first()).toBeVisible();
  await expect(page.getByText(/System Online/i)).toHaveCount(0);

  // WHEN: The user navigates to the results page.
  await navigation.getByRole('link', { name: '结果' }).click();

  // THEN: The Product V2 opportunities page is visible.
  await expect(page.getByRole('heading', { name: '商机', exact: true })).toBeVisible();
});

test('supports keyboard entry and a mobile navigation drawer', async ({ page }) => {
  // GIVEN: A mobile viewport on the Product V2 workbench.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/dashboard');

  // WHEN: The user opens navigation with the visible mobile control.
  await page.getByRole('button', { name: '打开导航' }).click();

  // THEN: The navigation drawer and its customer entry are usable.
  await expect(page.getByRole('complementary', { name: '主导航' })).toBeVisible();
  await page.getByRole('complementary', { name: '主导航' }).getByRole('link', { name: '客户', exact: true }).click();
  await expect(page.getByRole('heading', { name: '客户', exact: true })).toBeVisible();

  // WHEN: Focus enters the document through the keyboard.
  await page.keyboard.press('Tab');

  // THEN: A focusable control is present and the page remains horizontally contained.
  await expect(page.locator(':focus')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(await seriousAccessibilityViolations(page)).toEqual([]);
});

test('shows a retryable formal-account error instead of silently injecting examples', async ({ page }) => {
  // GIVEN: The browser is not in explicit demo mode and the formal V2 API is unavailable.
  await page.addInitScript(() => window.localStorage.removeItem('v2_demo_mode'));

  // WHEN: The user opens a formal customer page.
  await page.goto('/dashboard/customers');

  // THEN: A retryable error is visible and no sample source label is rendered.
  await expect(page.getByRole('alert').getByText('暂时无法读取正式账号数据')).toBeVisible();
  await expect(page.getByRole('button', { name: '重试' })).toBeVisible();
  await expect(page.getByText('示例数据')).toHaveCount(0);
});
