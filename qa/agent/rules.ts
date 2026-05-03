import { Page, expect } from '@playwright/test';

const CLIENT_EMAIL = 'porto3011969@gmail.com';
const CLIENT_PHONE = '+3538711234567';

export async function runRules(page: Page) {
  console.log('[QA] Starting full SiteFormo flow...');

  await page.goto('https://siteformo.com/', {
    waitUntil: 'domcontentloaded',
    timeout: 90_000,
  });

  // Open quiz
  await page.getByRole('button', { name: 'Get Your Website Estimate' }).click();

  // Contact step
  await page.getByRole('button', { name: 'Email' }).click();
  await page.getByRole('textbox', { name: 'you@example.com' }).fill(CLIENT_EMAIL);
  await page.getByRole('button', { name: 'Continue' }).click();

  // Quiz
  await page.getByRole('button', { name: 'Landing page One focused page' }).click();
  await page.getByRole('button', { name: 'Continue with this example' }).click();
  await page.getByRole('button', { name: 'Simple enquiry/contact form' }).click();
  await page.getByRole('button', { name: 'Not sure ⭐ Let SiteFormo' }).click();
  await page.getByRole('button', { name: /Under €/ }).click();
  await page.getByRole('button', { name: 'Show my recommended package' }).click();

  // Deposit
  await page.getByRole('button', { name: 'Secure my project with deposit' }).click();
  await page.getByRole('checkbox', { name: 'I confirm that I have read' }).check();

  await Promise.all([
    page.waitForURL(/checkout\.stripe\.com/i, { timeout: 90_000 }),
    page.getByRole('button', { name: 'I agree and continue to' }).click(),
  ]);

  console.log('[QA] Stripe opened');

  // Stripe test card
  await page.getByRole('textbox', { name: /Номер карты|Card number/i }).fill('4242 4242 4242 4242');
  await page
    .getByRole('textbox', { name: /Срок окончания действия|Expiration|Expiry/i })
    .fill('12 / 34');
  await page.getByRole('textbox', { name: /Код CVV\/CVC|CVC|CVV/i }).fill('123');
  await page.getByRole('textbox', { name: /Имя владельца карты|Cardholder name/i }).fill('porto');

  await Promise.all([
    page.waitForURL(/extended-questionnaire/i, { timeout: 120_000 }),
    page.getByTestId('hosted-payment-submit-button').click(),
  ]);

  console.log('[QA] Extended questionnaire opened after payment');

  // Extended questionnaire
  await page.locator('#sf-real-continue-questionnaire-top').click();

  await page.getByRole('textbox', { name: 'email@example.com' }).fill(CLIENT_EMAIL);
  await page.getByRole('textbox', { name: '+353871234567 or @username' }).fill(CLIENT_PHONE);
  await page
    .getByRole('textbox', { name: 'Example: Dublin Cleaning' })
    .fill("Electrician's service record in Dublin");
  await page
    .getByRole('textbox', { name: '12 Main Street, Dublin,' })
    .fill('Dublin, Ireland');

  await page.getByRole('combobox').first().selectOption('Local service business');
  await page.getByRole('combobox').nth(1).selectOption('Sell a service');
  await page.getByRole('combobox').nth(3).selectOption('Explain services');

  await page.getByRole('button', { name: 'Luxury / high-end +€180' }).click();
  await page.getByRole('button', { name: '✓ Selected WOW design (' }).click();
  await page.getByRole('button', { name: 'Create custom photos +€120 We' }).click();
  await page.getByRole('button', { name: 'No video Recommended for most' }).click();
  await page.getByRole('button', { name: 'No social networks Do not add' }).click();
  await page.getByRole('button', { name: 'I need a logo +€100 You will' }).click();
  await page.getByRole('button', { name: 'No examples We will choose' }).click();
  await page.getByRole('button', { name: 'I do not have hosting yet' }).click();
  await page.getByRole('button', { name: 'I will arrange hosting myself' }).click();

  await page.getByRole('button', { name: 'Confirm and send project' }).click();
  await page.getByRole('checkbox', { name: 'I confirm that I will pay the' }).check();
  await page.getByRole('button', { name: 'Confirm and send project' }).click();

  await expect(page.getByText(/sent to development|project has been sent|thank you/i)).toBeVisible({
    timeout: 60_000,
  });

  console.log('[QA] DONE: full flow completed');
}