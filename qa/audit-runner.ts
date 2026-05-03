#!/usr/bin/env tsx
/**
 * SiteFormo AI Platform — Final Read-only Audit Runner
 * ----------------------------------------------------
 * Flow being audited:
 * Quiz → Stripe → Questionnaire → 5 previews → choice → edits → generation → final payment
 *
 * This runner checks only staging/read-only readiness:
 * - WordPress frontend
 * - FastAPI backend
 * - Supabase tables/read access
 * - Stripe TEST configuration
 * - Email provider configuration
 * - Critical safety flags
 *
 * It NEVER:
 * - creates orders
 * - writes to Supabase
 * - sends emails
 * - charges cards
 * - changes WordPress/backend code
 * - starts generation
 */

import 'dotenv/config';
import fs from 'node:fs';
import path from 'node:path';
import Stripe from 'stripe';
import { createClient } from '@supabase/supabase-js';

type Status = 'PASS' | 'WARN' | 'FAIL' | 'SKIP';

type Result = {
  group: string;
  name: string;
  status: Status;
  details: string;
  meta?: Record<string, unknown>;
};

const startedAt = new Date();
const results: Result[] = [];

const env = {
  NODE_ENV: process.env.NODE_ENV,
  AUDIT_MODE: process.env.AUDIT_MODE,
  ALLOW_FIXES: process.env.ALLOW_FIXES,

  WORDPRESS_URL: process.env.WORDPRESS_URL,
  QUESTIONNAIRE_URL: process.env.QUESTIONNAIRE_URL,
  PAYMENT_SUCCESS_URL: process.env.PAYMENT_SUCCESS_URL,

  BACKEND_URL: process.env.BACKEND_URL,

  SUPABASE_URL: process.env.SUPABASE_URL,
  SUPABASE_ANON_KEY: process.env.SUPABASE_ANON_KEY,
  SUPABASE_SERVICE_ROLE_KEY: process.env.SUPABASE_SERVICE_ROLE_KEY,

  STRIPE_SECRET_KEY: process.env.STRIPE_SECRET_KEY,
  STRIPE_WEBHOOK_SECRET: process.env.STRIPE_WEBHOOK_SECRET,

  RESEND_API_KEY: process.env.RESEND_API_KEY,
  EMAIL_FROM: process.env.EMAIL_FROM,

  REPORT_DIR: process.env.REPORT_DIR || 'audit-reports',
};

function add(result: Result) {
  results.push(result);
}

function cleanUrl(url: string) {
  return url.replace(/\/$/, '');
}

function required(name: keyof typeof env): string | undefined {
  const value = env[name];
  if (!value) {
    add({
      group: 'ENV',
      name,
      status: 'FAIL',
      details: `Missing required variable: ${name}`,
    });
    return undefined;
  }
  return value;
}

function optional(name: keyof typeof env): string | undefined {
  return env[name];
}

function mask(value?: string) {
  if (!value) return undefined;
  if (value.length <= 10) return '***';
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function assertReadOnlySafetyGate() {
  const safetyErrors: string[] = [];

  if (env.NODE_ENV !== 'staging') {
    safetyErrors.push('NODE_ENV must be exactly staging');
  }

  if (env.AUDIT_MODE !== 'read_only') {
    safetyErrors.push('AUDIT_MODE must be exactly read_only');
  }

  if (env.ALLOW_FIXES !== 'false') {
    safetyErrors.push('ALLOW_FIXES must be exactly false');
  }

  const looksProduction = [
    env.WORDPRESS_URL,
    env.QUESTIONNAIRE_URL,
    env.PAYMENT_SUCCESS_URL,
    env.BACKEND_URL,
  ]
    .filter(Boolean)
    .some((url) => /production|prod\.|live\.|www\./i.test(String(url)));

  if (looksProduction) {
    add({
      group: 'Safety',
      name: 'Production-looking URL warning',
      status: 'WARN',
      details: 'One or more URLs look like production. Continue only if these are truly staging URLs.',
    });
  }

  if (safetyErrors.length > 0) {
    add({
      group: 'Safety',
      name: 'Read-only safety gate',
      status: 'FAIL',
      details: safetyErrors.join('; '),
      meta: {
        NODE_ENV: env.NODE_ENV,
        AUDIT_MODE: env.AUDIT_MODE,
        ALLOW_FIXES: env.ALLOW_FIXES,
      },
    });

    finish(1);
  }

  add({
    group: 'Safety',
    name: 'Read-only safety gate',
    status: 'PASS',
    details: 'Staging/read-only mode confirmed. Fixes and mutations are disabled.',
  });
}

async function get(url: string, timeoutMs = 12000): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      method: 'GET',
      signal: controller.signal,
      headers: {
        'User-Agent': 'SiteFormo-AuditRunner/2.0 ReadOnly',
        Accept: 'text/html,application/json,*/*',
      },
    });
  } finally {
    clearTimeout(timeout);
  }
}

async function readBodySafely(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return '';
  }
}

async function checkUrl(group: string, name: string, url?: string, expectations?: string[]) {
  if (!url) {
    add({
      group,
      name,
      status: 'SKIP',
      details: 'URL is not configured.',
    });
    return;
  }

  try {
    const response = await get(url);
    const body = await readBodySafely(response);

    if (!response.ok) {
      add({
        group,
        name,
        status: 'FAIL',
        details: `HTTP ${response.status} from ${url}`,
      });
      return;
    }

    const missing = (expectations || []).filter((text) => !body.includes(text));

    add({
      group,
      name,
      status: missing.length ? 'WARN' : 'PASS',
      details: missing.length
        ? `Reachable, but expected markers were not found: ${missing.join(', ')}`
        : `Reachable: HTTP ${response.status}`,
      meta: {
        url,
        contentType: response.headers.get('content-type'),
      },
    });
  } catch (error) {
    add({
      group,
      name,
      status: 'FAIL',
      details: `${url} failed: ${(error as Error).message}`,
    });
  }
}

async function auditWordPress() {
  const wordpressUrl = required('WORDPRESS_URL');

  await checkUrl('WordPress', 'Main site', wordpressUrl, ['SiteFormo']);

  await checkUrl(
    'WordPress',
    'Extended questionnaire page',
    optional('QUESTIONNAIRE_URL'),
    ['siteformo-questionnaire-root']
  );

  await checkUrl(
    'WordPress',
    'Payment success page',
    optional('PAYMENT_SUCCESS_URL'),
    ['Deposit received']
  );
}

async function auditFastAPI() {
  const backendUrl = required('BACKEND_URL');
  if (!backendUrl) return;

  const base = cleanUrl(backendUrl);

  const healthCandidates = ['/health', '/api/health', '/docs', '/openapi.json'];
  let healthPassed = false;

  for (const endpoint of healthCandidates) {
    try {
      const response = await get(`${base}${endpoint}`);
      if (response.ok) {
        add({
          group: 'FastAPI',
          name: 'Backend health',
          status: 'PASS',
          details: `Backend responded at ${endpoint} with HTTP ${response.status}.`,
        });
        healthPassed = true;
        break;
      }
    } catch {
      // continue
    }
  }

  if (!healthPassed) {
    add({
      group: 'FastAPI',
      name: 'Backend health',
      status: 'FAIL',
      details: 'No health/docs endpoint responded successfully. Checked /health, /api/health, /docs, /openapi.json.',
    });
  }

  const readOnlyEndpoints = [
    '/openapi.json',
    '/api/pricing/packages',
    '/api/pricing',
  ];

  for (const endpoint of readOnlyEndpoints) {
    try {
      const response = await get(`${base}${endpoint}`);
      add({
        group: 'FastAPI',
        name: `Read-only endpoint ${endpoint}`,
        status: response.ok ? 'PASS' : 'WARN',
        details: `HTTP ${response.status}`,
      });
    } catch (error) {
      add({
        group: 'FastAPI',
        name: `Read-only endpoint ${endpoint}`,
        status: 'WARN',
        details: `Not available or failed: ${(error as Error).message}`,
      });
    }
  }
}

async function auditSupabase() {
  const supabaseUrl = required('SUPABASE_URL');
  const key = env.SUPABASE_ANON_KEY || env.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseUrl || !key) {
    add({
      group: 'Supabase',
      name: 'Credentials',
      status: 'FAIL',
      details: 'SUPABASE_URL and either SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY are required.',
    });
    return;
  }

  add({
    group: 'Supabase',
    name: 'Key selected',
    status: env.SUPABASE_ANON_KEY ? 'PASS' : 'WARN',
    details: env.SUPABASE_ANON_KEY
      ? 'Using anon key for read-only audit.'
      : 'Using service role key. Safer recommendation: create a read-only audit key/policy later.',
    meta: { key: mask(key) },
  });

  const supabase = createClient(supabaseUrl, key, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
  });

  const expectedTables = [
    'orders',
    'client_profiles',
    'design_briefs',
    'design_previews',
  ];

  for (const table of expectedTables) {
    try {
      const { error, count } = await supabase
        .from(table)
        .select('*', { count: 'exact', head: true });

      if (error) {
        add({
          group: 'Supabase',
          name: `Table ${table}`,
          status: 'FAIL',
          details: error.message,
        });
      } else {
        add({
          group: 'Supabase',
          name: `Table ${table}`,
          status: 'PASS',
          details: `Readable. Row count: ${count ?? 'unknown'}.`,
        });
      }
    } catch (error) {
      add({
        group: 'Supabase',
        name: `Table ${table}`,
        status: 'FAIL',
        details: (error as Error).message,
      });
    }
  }
}

async function auditStripe() {
  const stripeKey = required('STRIPE_SECRET_KEY');
  if (!stripeKey) return;

  if (!stripeKey.startsWith('sk_test_')) {
    add({
      group: 'Stripe',
      name: 'Test mode key',
      status: 'FAIL',
      details: 'Stripe key is not a test key. Expected sk_test_*. Refusing Stripe audit.',
      meta: { key: mask(stripeKey) },
    });
    return;
  }

  add({
    group: 'Stripe',
    name: 'Test mode key',
    status: 'PASS',
    details: 'Stripe secret key is test-mode.',
    meta: { key: mask(stripeKey) },
  });

  try {
    const stripe = new Stripe(stripeKey, {
      apiVersion: '2024-06-20',
    });

    const account = await stripe.accounts.retrieve();

    add({
      group: 'Stripe',
      name: 'Account access',
      status: 'PASS',
      details: 'Stripe account is reachable with test key.',
      meta: {
        accountId: account.id,
        chargesEnabled: account.charges_enabled,
        payoutsEnabled: account.payouts_enabled,
      },
    });
  } catch (error) {
    add({
      group: 'Stripe',
      name: 'Account access',
      status: 'FAIL',
      details: (error as Error).message,
    });
  }

  if (!env.STRIPE_WEBHOOK_SECRET) {
    add({
      group: 'Stripe',
      name: 'Webhook secret',
      status: 'WARN',
      details: 'STRIPE_WEBHOOK_SECRET is missing. Webhook verification may fail.',
    });
  } else if (!env.STRIPE_WEBHOOK_SECRET.startsWith('whsec_')) {
    add({
      group: 'Stripe',
      name: 'Webhook secret',
      status: 'WARN',
      details: 'Webhook secret exists but does not look like whsec_*.',
      meta: { key: mask(env.STRIPE_WEBHOOK_SECRET) },
    });
  } else {
    add({
      group: 'Stripe',
      name: 'Webhook secret',
      status: 'PASS',
      details: 'Webhook secret is configured.',
      meta: { key: mask(env.STRIPE_WEBHOOK_SECRET) },
    });
  }
}

async function auditEmail() {
  if (!env.RESEND_API_KEY) {
    add({
      group: 'Email',
      name: 'Resend API key',
      status: 'FAIL',
      details: 'RESEND_API_KEY is missing.',
    });
  } else if (!env.RESEND_API_KEY.startsWith('re_')) {
    add({
      group: 'Email',
      name: 'Resend API key',
      status: 'WARN',
      details: 'RESEND_API_KEY exists but does not look like a Resend key.',
      meta: { key: mask(env.RESEND_API_KEY) },
    });
  } else {
    add({
      group: 'Email',
      name: 'Resend API key',
      status: 'PASS',
      details: 'Resend API key is configured. No email was sent.',
      meta: { key: mask(env.RESEND_API_KEY) },
    });
  }

  if (!env.EMAIL_FROM) {
    add({
      group: 'Email',
      name: 'Sender address',
      status: 'FAIL',
      details: 'EMAIL_FROM is missing.',
    });
  } else if (!env.EMAIL_FROM.includes('@')) {
    add({
      group: 'Email',
      name: 'Sender address',
      status: 'WARN',
      details: 'EMAIL_FROM does not look like an email address.',
      meta: { EMAIL_FROM: env.EMAIL_FROM },
    });
  } else {
    add({
      group: 'Email',
      name: 'Sender address',
      status: 'PASS',
      details: `EMAIL_FROM is configured: ${env.EMAIL_FROM}`,
    });
  }
}

function summarize() {
  return results.reduce<Record<Status, number>>(
    (acc, result) => {
      acc[result.status] += 1;
      return acc;
    },
    { PASS: 0, WARN: 0, FAIL: 0, SKIP: 0 }
  );
}

function statusIcon(status: Status) {
  if (status === 'PASS') return '✅';
  if (status === 'WARN') return '⚠️';
  if (status === 'SKIP') return '⏭️';
  return '❌';
}

function makeJsonReport() {
  return {
    project: 'SiteFormo AI Platform',
    mode: 'read_only_staging_audit',
    startedAt: startedAt.toISOString(),
    finishedAt: new Date().toISOString(),
    summary: summarize(),
    results,
  };
}

function writeReportFile() {
  const reportDir = path.resolve(process.cwd(), env.REPORT_DIR);
  fs.mkdirSync(reportDir, { recursive: true });

  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const filePath = path.join(reportDir, `siteformo-audit-${stamp}.json`);

  fs.writeFileSync(filePath, JSON.stringify(makeJsonReport(), null, 2), 'utf8');
  return filePath;
}

function printReport(filePath?: string) {
  const summary = summarize();

  console.log('
================================================');
  console.log('SiteFormo AI Platform — Final Read-only Audit');
  console.log('================================================');
  console.log(`Started: ${startedAt.toISOString()}`);
  console.log(`Mode: ${env.NODE_ENV}/${env.AUDIT_MODE}, ALLOW_FIXES=${env.ALLOW_FIXES}`);
  console.log('------------------------------------------------
');

  const groups = Array.from(new Set(results.map((result) => result.group)));

  for (const group of groups) {
    console.log(`
[${group}]`);
    for (const result of results.filter((item) => item.group === group)) {
      console.log(`${statusIcon(result.status)} ${result.status} — ${result.name}`);
      console.log(`   ${result.details}`);
      if (result.meta) {
        console.log(`   meta: ${JSON.stringify(result.meta)}`);
      }
    }
  }

  console.log('
------------------------------------------------');
  console.log(`PASS: ${summary.PASS} | WARN: ${summary.WARN} | FAIL: ${summary.FAIL} | SKIP: ${summary.SKIP}`);
  if (filePath) console.log(`JSON report: ${filePath}`);
  console.log('------------------------------------------------
');
}

function finish(code?: number): never {
  let filePath: string | undefined;

  try {
    filePath = writeReportFile();
  } catch (error) {
    add({
      group: 'Report',
      name: 'Write JSON report',
      status: 'WARN',
      details: `Could not write JSON report: ${(error as Error).message}`,
    });
  }

  printReport(filePath);

  const hasFail = results.some((result) => result.status === 'FAIL');
  process.exit(code ?? (hasFail ? 1 : 0));
}

async function main() {
  assertReadOnlySafetyGate();

  await auditWordPress();
  await auditFastAPI();
  await auditSupabase();
  await auditStripe();
  await auditEmail();

  finish();
}

main().catch((error) => {
  add({
    group: 'Fatal',
    name: 'Unexpected audit runner error',
    status: 'FAIL',
    details: error instanceof Error ? error.message : String(error),
  });

  finish(1);
});
