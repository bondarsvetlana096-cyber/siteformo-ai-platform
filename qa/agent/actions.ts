export async function smartClick(page, texts: string[]) {
  for (const text of texts) {
    const el = page.locator(`text=${text}`).first();
    if (await el.isVisible().catch(() => false)) {
      await el.click();
      return true;
    }
  }
  return false;
}

export async function fillAllInputs(page, value: string) {
  const inputs = await page.locator("input").all();
  for (const input of inputs) {
    try {
      await input.fill(value);
    } catch {}
  }
}