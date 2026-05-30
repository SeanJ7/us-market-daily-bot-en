# US Market Daily Bot (English)

This repository generates an English-language US market close report, sends a Telegram-friendly short version, and publishes a detailed HTML copy to GitHub Pages.

## What it does

- pulls daily market data from Yahoo Finance and FRED
- generates a short Telegram version in 3 messages
- saves a detailed Markdown and HTML report
- uploads report artifacts in GitHub Actions
- publishes `reports/latest.html` to GitHub Pages
- sends a Telegram failure alert if report generation fails

## Required GitHub secrets

Add these repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Schedule

The workflow is configured to run at `08:35 Australia/Melbourne` on local `Tuesday` through `Saturday` mornings, which maps to US market closes from Monday through Friday.

## GitHub setup

1. Create a new repository, for example `us-market-daily-bot-en`
2. Upload the contents of this folder
3. Add the two repository secrets
4. In `Settings -> Pages`, set `Source` to `GitHub Actions`
5. Run the workflow once manually from the `Actions` tab

## Manual local run

```bash
cp .env.example .env
# fill in Telegram values
python3 send_latest_report.py --env-file .env --force
```

## Output

Each successful run writes:

- `reports/YYYY-MM-DD.md`
- `reports/YYYY-MM-DD.html`
- `reports/latest.md`
- `reports/latest.html`

GitHub Actions also:

- uploads these files as artifacts
- deploys `reports/latest.html` to GitHub Pages

## Expected GitHub Pages URL

If your repository is named `us-market-daily-bot-en`, the Pages URL will usually look like:

- `https://seanj7.github.io/us-market-daily-bot-en/`
