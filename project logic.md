# SiteFormo AI Sales Platform — Project Logic

## Goal

Build an AI-powered sales platform for SiteFormo:

**Quiz → lead → WhatsApp/Telegram/Email → AI-guided dialogue → offer → 50% payment → owner approval → final page generation → Divi 5 review**

The website at `https://siteformo.com/` will be rebuilt around two primary actions.

## Primary buttons

### 1. Generate demo page

This button creates a temporary demo page for the visitor.

Rules:

- The public demo page is available for **10 minutes**.
- The generated demo asset is retained internally for **96 hours**.
- The retained demo can be reused later when the same client starts a full order.
- The demo is stored in the database/storage with enough metadata to identify the client and original request.

### 2. Create full page

This button starts a guided order quiz for a paid full-page project.

The quiz must be short enough not to scare the client away, but complete enough for AI to estimate the work.

## Full-page quiz flow

### Question 1 — Client contact

The client chooses or enters one of these contact methods:

- Email
- WhatsApp
- Telegram
- Facebook Messenger in the future

Rules:

- If the client enters email, they immediately continue to question 2.
- If the client chooses WhatsApp, Telegram, or future Messenger, the system generates a unique message.
- The client must send that generated message to the real SiteFormo owner contact for that channel.
- This protects against platform blocks and confirms that the user initiated contact.
- After the client confirms that the message was sent, the quiz continues to question 2.

## Question 2 — Project context

This question has two fields:

1. **Business topic or existing website**
   - If the client has no website, they describe the business/theme.
   - If the client has an existing website, they paste the URL.

2. **Reference websites** — optional
   - Text shown to the client: “You can paste links to websites you like here.”
   - Maximum 3 websites.
   - These references help AI understand the desired visual direction.

## Question 3 — Short AI questionnaire

The client answers a short, clear questionnaire designed for AI understanding.

The short questionnaire should collect:

- Business name
- Target audience
- Main goal of the page
- Offer or service description
- Required sections
- Preferred style/tone
- Required integrations or special functionality
- Deadline/urgency
- Additional notes

The first questionnaire must stay lightweight. The goal is to estimate and sell, not to overload the client.

## AI price estimate

After the short questionnaire:

1. The bot evaluates the work required.
2. It calculates a market-based price.
3. It explains the price in a client-friendly way.
4. If the price may feel high, the bot offers a cheaper/simplified option instead of losing the client.

## Demo reuse logic

After the short questionnaire, the bot checks the demo database.

### If a recent demo exists

If the client generated a demo within the last **96 hours**, the bot reuses that context and creates exactly **two homepage concepts** based on that demo.

### If no recent demo exists

If the client did not generate a demo or the retained asset expired, the bot creates new context and then creates exactly **two homepage concepts**.

### If the client entered an existing website URL

If the second question contains an existing website URL:

- The bot checks whether a recent demo/context exists.
- If none exists, the bot generates a new page concept based on the existing website.
- Content and business data should match the original website as closely as possible.

## Payment flow

After the price and two concepts are shown:

1. The client is asked to make a **50% prepayment**.
2. The client is told that after payment they will receive a more detailed questionnaire.
3. When the client reports payment, the bot sends an approval email to `klon97048@gmail.com`.
4. The owner visually verifies the payment.
5. The owner clicks an approval button in the email.
6. Only after approval does the system continue to the extended questionnaire and final generation.

## Extended questionnaire

After owner approval, the bot asks a more detailed questionnaire so AI can generate the final page correctly.

The extended questionnaire can collect:

- Full business details
- Exact offer details
- Brand voice
- Sections and content blocks
- Social proof/reviews
- Images/assets notes
- Legal/contact information
- SEO notes
- Integration requirements
- Final special instructions

## Final generation and owner delivery

After the final page generation:

An email is sent to `klon97048@gmail.com` containing:

- Client contact data
- Site topic
- Whether this is a new site or redesign of an existing site
- Two different homepage/page samples
- Divi 5-ready HTML/code blocks for editing
- Notes for visual review

The owner reviews and edits visually before final delivery.

## Language and localization rules

- Russian must not appear anywhere in the public client-facing interface.
- Main language: English.
- The project must support multilingual clients from Europe, North America, Japan, Korea, and Gulf countries.
- Browser automatic translation must be allowed.
- Do not block Google Translate or browser translation tools.
- UI and chat must be mobile-first and responsive.

## Current production architecture

```text
User → SiteFormo website / WhatsApp / Telegram
↓
Guided quiz / channel webhook
↓
FastAPI backend on Railway
↓
Supabase / database
↓
OpenAI generation and pricing logic
↓
Owner approval by email
↓
Final Divi 5-ready output
```

## Required implementation priorities

1. Keep public UI English-only.
2. Set demo public lifetime to 10 minutes and internal retention to 96 hours.
3. Use the correct Twilio webhook: `/twilio/webhook`.
4. Keep the existing `/channels/whatsapp/webhook` alias for backward compatibility.
5. Add a full-page order intake route.
6. Add 50% payment approval flow with owner email approval links.
7. Generate exactly two homepage/page concepts before payment.
8. Generate final owner delivery after extended questionnaire.
9. Keep the flow mobile-friendly and simple for clients.

## Owner email exception

`klon97048@gmail.com` is the project owner email. If this email is used in the full-page order quiz, payment approval is skipped and the order is generated immediately as a ready Divi package.
