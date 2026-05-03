import { chromium } from '@playwright/test';
import { runRules } from './rules';

async function main() {
  console.log('Opening site...');

  const browser = await chromium.launch({
    headless: false,
    slowMo: 250,
    args: ['--start-maximized'],
  });

  const context = await browser.newContext({
    viewport: null,
  });

  const page = await context.newPage();

  try {
    await page.goto('https://siteformo.com/?sfContactStep=1', {
      waitUntil: 'domcontentloaded',
      timeout: 90_000,
    });

    console.log('Opening quiz...');

    await page.waitForTimeout(1500);

    console.log('Quiz opened');

    await runRules(page);
  } catch (error) {
    console.error('❌ QA FAILED:', error instanceof Error ? error.message : error);
  }
}

main();