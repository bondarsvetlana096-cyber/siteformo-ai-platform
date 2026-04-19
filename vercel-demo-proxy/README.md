# Vercel demo proxy

This mini project is intended for the `demo-siteformo.com` domain.

## What it does
- proxies requests on `/demo/*`
- proxies requests on `/demo-assets/*`
- forwards them to `https://api.siteformo.com`

## How to use it
1. Create a separate Vercel project from this folder.
2. Attach the `demo-siteformo.com` domain.
3. In Railway, set `DEMO_BASE_URL=https://demo-siteformo.com`.

## Important
If you later move to `demo.siteformo.com`, only update `DEMO_BASE_URL` in Railway.
