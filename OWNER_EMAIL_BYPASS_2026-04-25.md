# Owner Email Bypass Update

Owner email: `klon97048@gmail.com`

Implemented behavior:

- This email is treated as the SiteFormo owner/admin email.
- When an intake/order is created with this email, the project skips the 50% payment approval requirement.
- The order is automatically approved and moved directly to `final_ready`.
- A Divi-ready final package is generated immediately from the current brief and Concept A.
- The `/api/orders/{order_id}/payment-reported` endpoint also skips payment verification for this email.
- The `/api/orders/{order_id}/extended-brief` endpoint accepts this owner email even if normal payment approval was not performed.
- `deposit_due_eur` returns `0` for the owner bypass flow.

Railway variables:

```env
OWNER_EMAIL=klon97048@gmail.com
PAYMENT_APPROVAL_BYPASS_EMAILS=klon97048@gmail.com
```

GitHub commands:

```bash
git status
git add .
git commit -m "Add owner email payment approval bypass"
git push origin main
```
