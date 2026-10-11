# Sophia Martin Jewelry — Marketing, PR & Advertising

*Heirlooms Redefined — vintage heirlooms, beautifully reimagined.*

A complete go-to-market kit for Sophia Martin Jewelry: three strategic plans
(marketing, PR, advertising) plus a ready-to-run 7-day Instagram launch
campaign and a script that publishes one post per day.

## Files

### Strategic plans (read these first)

| File | What it is |
|---|---|
| `marketing-plan.md` | **Organic social media strategy** — 90-day objectives & KPIs, audience personas, messaging pillars, channel strategy (Instagram, Pinterest, TikTok, email), 6 content pillars, weekly posting framework, growth tactics, 30-60-90 roadmap. |
| `pr-plan.md` | **PR & earned media** — 6 story angles, media target list, a ready-to-send press release, pitch templates, a tiered influencer/gifting strategy (with FTC guidance), a seasonal gift-guide calendar, partnerships, and a 90-day timeline. |
| `advertising-plan.md` | **Paid social advertising** — full-funnel strategy (Meta primary, Pinterest secondary, Google, optional TikTok), Meta campaign/ad-set architecture, 10 ad-creative concepts, three illustrative budget tiers, measurement & kill/scale rules, 90-day flight plan. |

### The 7-day launch campaign (ready to post)

| File | What it is |
|---|---|
| `campaign-strategy.md` | Launch-week goals, audience, brand voice, hashtag strategy, timing. |
| `content-calendar.md` | The 7 daily posts, fully written (caption, visual concept, CTA, hashtags). |
| `posts.csv` | The same 7 posts as a spreadsheet — import into Later/Buffer/Meta Planner, or feed the script. |
| `instagram_autopost.py` | Publishes the post scheduled for today via the Instagram Graph API. |
| `requirements.txt` | Python dependency (`requests`). |

> Anything in `[SQUARE BRACKETS]` is a placeholder — your **public image URLs**,
> specific review quotes, and any optional incentive code. Real product names
> and prices are already filled in. Fill the brackets before you post.

## Brand snapshot (source of truth for all the files above)

- **Tagline:** *Heirlooms Redefined* · **Hero:** *Vintage Heirlooms, Beautifully Reimagined*
- **What it is:** high-end, largely **one-of-a-kind** vintage & Art Deco fine
  jewelry — necklaces, pendants, rings, and brooches — reimagined as wearable
  heirlooms. Ethically sourced materials.
- **Founder story:** Sophia Lapat, inspired by her father **Martin** (a lawyer
  who reimagined Americana antiques into art); the brand is named for him.
- **Prices:** ~$300 (Art Deco Crystal & Green Glass Pendant) to $3,500 (Diamond
  Starburst Lariat).
- **Scarcity is real:** pieces are one-of-a-kind — "when it's gone, it's gone"
  is honest, so the campaign leans on that instead of discount codes.

## Sales channels

Primary: the Shopify site (**sophiamartinjewelry.com**). The brand **also sells
on Etsy** (shop *SophiaMartinJewelry*) — worth layering into every plan:

- **Marketing:** cross-link Etsy reviews as social proof; funnel some organic
  traffic to whichever channel converts best.
- **PR / SEO:** Etsy listings rank and build backlinks; keep titles/tags
  keyword-rich (*Art Deco*, *vintage*, *one of a kind*, *heirloom*).
- **Advertising:** consider **Etsy Ads** (on-platform) and **Etsy Offsite Ads**
  in addition to Meta/Pinterest; keep a Google Shopping feed covering rings and
  brooches, not just pendants.

## The simplest way to post (no code)

If you don't want to run a script, you don't have to:

1. Open `content-calendar.md`, fill in the brackets, and post each day by hand, **or**
2. Import `posts.csv` into a scheduler (**Later**, **Buffer**, **Metricool**,
   or Meta's own **Business Suite Planner**), attach each day's image/Reel, and
   let it auto-publish on the dates in the `publish_date` column.

The script below is for people who'd rather automate it themselves.

---

## Auto-posting with `instagram_autopost.py`

### What it does
Reads `posts.csv`, finds the row whose `publish_date` is **today**, and
publishes it as a single-image feed post, then posts the hashtags as the first
comment. It records what it published in `.autopost_state.json` so it won't
double-post if the scheduler fires twice.

> **Reels & carousels:** the Graph API uses heavier, separate flows for those.
> This script publishes a single image (using `image_url`). For a true Reel or
> multi-image carousel on a given day, post that one manually, or extend the
> script using Meta's `media_type=REELS` / `CAROUSEL` docs.

### One-time setup

Instagram only allows API publishing from a **Business or Creator** account.

1. **Convert your account** to Business/Creator (Instagram app → Settings →
   *Account type*), and **link it to a Facebook Page**.
2. **Create a Meta app** at https://developers.facebook.com → add the
   *Instagram Graph API* product.
3. **Get a long-lived access token** with these permissions:
   `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`
   (and `pages_show_list`). Use the Graph API Explorer or your app's token
   tools, then exchange it for a long-lived token (~60 days — refresh before it
   expires).
4. **Find your Instagram user id** (the numeric id, not the @handle) — e.g. via
   `GET /{page-id}?fields=instagram_business_account`.
5. **Host your images** so each `image_url` is a **public https URL** (your
   store's CDN, an S3/Cloudinary bucket, etc.). Instagram fetches the image
   server-side — it can't read a local file or a private URL.

### Install

```bash
pip install -r marketing/requirements.txt
```

### Configure (never hard-code secrets)

```bash
export IG_USER_ID="1784xxxxxxxxxxx"
export IG_ACCESS_TOKEN="EAAG...your-long-lived-token..."
export GRAPH_API_VERSION="v21.0"   # optional
```

### Run

```bash
# Preview today's post without calling the API:
python marketing/instagram_autopost.py --dry-run

# Publish today's post:
python marketing/instagram_autopost.py

# Publish a specific row by day number or date:
python marketing/instagram_autopost.py --day 1
python marketing/instagram_autopost.py --date 2026-10-12
```

### Schedule it to run every day

**Linux/macOS (cron)** — run daily at 11:00 local time:

```cron
# crontab -e
0 11 * * * cd /path/to/Jay-and-brownie-repository && \
  IG_USER_ID=... IG_ACCESS_TOKEN=... \
  /usr/bin/python3 marketing/instagram_autopost.py >> marketing/autopost.log 2>&1
```

(Better: keep the secrets in a file the cron job sources, not inline.)

**macOS (launchd)** or **GitHub Actions** (a scheduled `workflow` with the
token stored as an encrypted repo secret) work too. Whatever runs it daily is
fine — the script itself is idempotent per day.

> ⚠️ **Don't commit secrets.** `.autopost_state.json` and any `.env`/`autopost.log`
> are safe to gitignore. Store `IG_ACCESS_TOKEN` as an environment variable or
> an encrypted secret — never in the repo.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `image_url ... is still a placeholder` | Replace `[PUBLIC_IMAGE_URL_DAYn]` with a real public https URL. |
| `error ... (#10) permission` | Token missing `instagram_content_publish`, or account isn't Business/Creator. |
| `error ... media ... not ready` | Image URL unreachable or too large; verify it loads in a browser. |
| Posts twice | Scheduler fired twice; the state file prevents this unless `--force` is passed. |

---

## Quick start checklist

- [ ] Fill in brackets in `posts.csv` (prices, product names, promo code).
- [ ] Host 7 images and paste their public https URLs into `image_url`.
- [ ] Make sure the launch code `[LAUNCH10]` is live in your store checkout.
- [ ] Set the bio link to your campaign landing/product page.
- [ ] `--dry-run` to preview, then schedule the script (or your scheduler) daily.
- [ ] Each day: reshare to Stories with a Link sticker; reply to comments/DMs.
