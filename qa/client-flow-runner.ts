import { chromium } from "playwright";
import dotenv from "dotenv";
import fs from "fs";
import { createClient } from "@supabase/supabase-js";

dotenv.config();

const BASE_URL = process.env.WORDPRESS_URL!;
const QUESTIONNAIRE_URL = process.env.QUESTIONNAIRE_URL!;
const PAYMENT_SUCCESS_URL = process.env.PAYMENT_SUCCESS_URL!;

const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!
);

async function run() {
  const browser = await chromium.launch({
    headless: process.env.HEADLESS !== "false",
  });

  const page = await browser.newPage();

  const report: any[] = [];

  const log = (step: string, status: string, details?: any) => {
    console.log(`${status === "passed" ? "✅" : "❌"} ${step}`);
    report.push({ step, status, details });
  };

  try {
    // 1. Site open
    await page.goto(BASE_URL);
    await page.waitForLoadState("networkidle");
    log("Open site", "passed");

    // 2. Start quiz (⚠️ заменишь селектор)
    try {
      await page.click("text=Start");
      log("Start clicked", "passed");
    } catch {
      log("Start button not found", "failed");
    }

    // ⛔ ВАЖНО: сюда вставишь свои шаги quiz через inspector

    await page.waitForTimeout(3000);

    // 3. Stripe redirect check
    if (page.url().includes("stripe")) {
      log("Redirect to Stripe", "passed");
    } else {
      log("Stripe redirect missing", "failed");
    }

    // 4. Fill Stripe test card
    try {
      await page.fill('input[name="cardnumber"]', process.env.STRIPE_TEST_CARD!);
      await page.fill('input[name="exp-date"]', process.env.STRIPE_TEST_EXP!);
      await page.fill('input[name="cvc"]', process.env.STRIPE_TEST_CVC!);
      await page.fill('input[name="postal"]', process.env.STRIPE_TEST_ZIP!);

      await page.click("button:has-text('Pay')");
      log("Stripe payment submitted", "passed");
    } catch (e) {
      log("Stripe payment failed", "failed", e);
    }

    // 5. Wait success page
    await page.waitForURL(PAYMENT_SUCCESS_URL, { timeout: 20000 });
    log("Payment success page", "passed");

    // 6. Questionnaire
    await page.goto(QUESTIONNAIRE_URL);
    await page.waitForLoadState("networkidle");
    log("Questionnaire open", "passed");

    try {
      await page.fill("input", process.env.QA_COMPANY_NAME!);
      log("Questionnaire filled", "passed");
    } catch {
      log("Questionnaire fill failed", "failed");
    }

    try {
      await page.click("button[type=submit]");
      log("Questionnaire submitted", "passed");
    } catch {
      log("Submit failed", "failed");
    }

    // 7. Wait backend processing
    await page.waitForTimeout(5000);

    // 8. Supabase check
    try {
      const { data, error } = await supabase
        .from("orders")
        .select("*")
        .order("created_at", { ascending: false })
        .limit(1);

      if (error) throw error;

      if (data && data.length > 0) {
        log("Supabase order found", "passed", data[0].status);
      } else {
        log("No order found", "failed");
      }
    } catch (e) {
      log("Supabase check failed", "failed", e);
    }

    log("Flow complete", "passed");
  } catch (e) {
    log("Critical error", "failed", e);
  }

  fs.mkdirSync("qa/reports", { recursive: true });
  fs.writeFileSync(
    "qa/reports/client-flow-report.json",
    JSON.stringify(report, null, 2)
  );

  await browser.close();
}

run();