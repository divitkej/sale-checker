# Sale checker

Checks product prices every 30 minutes on GitHub Actions and sends a push notification to your phone when a price drops.

## How it works

1. `check.py` downloads each page listed in `products.json` and reads the price from the page's structured product data, price meta tags, or common store layouts (Amazon, Flipkart, WooCommerce shops).
2. The last seen price is saved in `state.json`, which the workflow commits back to the repo.
3. When the price is lower than the last check, or at or below your `target_price`, it sends a notification through [ntfy](https://ntfy.sh). Tapping it opens the product page. A quieter "Price went up" alert tells you when a price rises again, for example when a sale ends. When the store publishes an end time for the discount applied at checkout, drop alerts say how long the sale price has left, and "Sale ending soon" reminders arrive 24 hours, 6 hours and 1 hour before it ends. On Shopify stores the alert also has a **Buy now** button that opens checkout with your size already in the cart; you still confirm the payment yourself.
4. On Shopify stores (like cottonon.ae) it can follow one size. It reads the store's product feed, so it tracks the exact price and stock of that size, and also alerts you when your size comes back in stock.
5. If a price cannot be read 3 times in a row, you get one warning so the checker never breaks silently.

## Setup

1. Install the **ntfy** app on your phone (App Store or Google Play).
2. In the app, subscribe to a topic with a long, hard to guess name, for example `sale-checker-k7q2x9m4`. Anyone who knows the name can read your alerts, so treat it like a password.
3. In this GitHub repo go to **Settings > Secrets and variables > Actions > New repository secret**, name it `NTFY_TOPIC` and paste the topic name.
4. Edit `products.json` with your product.
5. Go to **Actions > Check prices > Run workflow**, tick "Send a test notification", and run it. Your phone should buzz.
6. Run it once more without the tick to record the starting price.

## products.json

```json
[
  {
    "name": "Running shoes",
    "url": "https://www.example.com/product/123",
    "currency": "$",
    "target_price": 80,
    "size": "32"
  }
]
```

| Field | Required | Meaning |
| --- | --- | --- |
| `url` | yes | Product page link |
| `name` | no | Label used in notifications |
| `currency` | no | Symbol shown in notifications |
| `target_price` | no | Also alert when the price is at or below this, even on the first check. These alerts use ntfy's urgent priority |
| `size` | no | Shopify stores only: the size to follow, for example `"32"` (any length), `"W32L32"`, or a list like `["W32L32", "W32L34"]`. Any size in the link is ignored |
| `size_option` | no | Shopify stores only: option name to match the size against (for example `"Waist"`) when a product has two options that share values |
| `selector` | no | CSS selector for the price element, for sites the automatic detection misses |

Add more objects to the list to track several products.

## Run it on your computer

```bash
pip install -r requirements.txt
python check.py                      # check prices once
NTFY_TOPIC=your-topic python check.py --test-notification
python -m pytest tests               # run tests (needs pytest)
```

## Limits

- Some large stores (Amazon especially) sometimes block requests from GitHub's servers. If you get the "needs attention" warning, the site is blocking the checker rather than the price being missing.
- GitHub can delay scheduled runs by a few minutes at busy times.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. Price change commits count as activity; if the price never changes, re-enable the workflow from the Actions tab when GitHub emails you.

## Rove Hotels 36-hour rates

`hotels.py` checks every Rove hotel for each night from today until the end date in `hotels.json`, with the promo code applied. The booking site only loads rates in a browser, so the check opens each search in a headless Chromium and reads the rates the page itself receives.

- One notification per run lists every change: a cheaper night, a price rise, a hotel newly offering the rate, or a hotel no longer offering it.
- If the promo code stops being accepted, you get one warning.
- Prices are the site's room rates before service charge and fees.
- Alerts go to a separate ntfy topic, set as the `NTFY_TOPIC_HOTELS` secret.
- The `Check hotels` workflow runs every hour. To make that reliable, add a second cron-job.org job like the one above, pointed at `check-hotels.yml` with the body `{"ref":"master"}`.
