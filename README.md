# NotifyMe

An agentic monitoring and notification system that watches for changes and alerts you via email. Uses Claude AI to evaluate complex conditions like "is this product available for purchase?" or "did my team win?"

## Features

- **Agentic Monitors** - Use Claude to evaluate natural language conditions against web pages
- **News Monitors** - Track Google News/RSS feeds with optional AI filtering
- **Hybrid Monitors** - Combine news feeds with agentic filtering to reduce false positives
- **Smart Notifications** - Only notifies on state changes, not repeated checks
- **Background Scheduling** - Runs automatically via macOS launchd

## Installation

```bash
# Clone the repo
git clone https://github.com/johnmooney55/NotifyMe.git
cd NotifyMe

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Optional: Install Playwright for JS-rendered pages
pip install playwright && playwright install chromium

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Environment Variables

```bash
# Required for agentic monitors
ANTHROPIC_API_KEY=sk-ant-...

# Required for email notifications
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-gmail-app-password  # Create at https://myaccount.google.com/apppasswords
NOTIFY_EMAIL=your-email@gmail.com
```

## Quick Start

```bash
# Activate the environment
source .venv/bin/activate

# Add a news monitor (free, no API cost)
notifyme add \
  --name "Scale AI News" \
  --type news \
  --url "https://news.google.com/rss/search?q=%22Scale+AI%22"

# Add an agentic monitor (uses Claude API)
notifyme add \
  --name "Oregon Ducks WBB Win" \
  --type agentic \
  --url "https://www.espn.com/womens-college-basketball/team/schedule/_/id/2483" \
  --condition "Oregon won their most recent completed game" \
  --notify-on-each

# Check all monitors
notifyme check

# List monitors
notifyme list
```

## Monitor Types

### News Monitor (Free)
Tracks RSS/Google News feeds. Alerts on new articles.

```bash
notifyme add \
  --name "Company News" \
  --type news \
  --url "https://news.google.com/rss/search?q=%22Company+Name%22"
```

### News + Agentic Filter (Hybrid)
Finds articles via RSS, then uses Claude to filter for relevance. Best for reducing false positives.

```bash
notifyme add \
  --name "MacBook Air Announcement" \
  --type news \
  --url "https://news.google.com/rss/search?q=MacBook+Air+announced" \
  --filter "Apple has OFFICIALLY announced a NEW MacBook Air. Not rumors or speculation." \
  --first-match  # Stop after first match (saves API costs)
```

### Agentic Monitor (Direct)
Fetches a webpage and uses Claude to evaluate a condition. Best for checking specific pages.

```bash
notifyme add \
  --name "Product Available" \
  --type agentic \
  --url "https://example.com/product" \
  --condition "Product is available for purchase with a buy button"
```

### Options

| Flag | Description |
|------|-------------|
| `--interval N` | Check every N minutes (default: 1440 = daily) |
| `--notify-on-each` | Notify on each new match, not just first time |
| `--filter "..."` | Agentic filter for news monitors |
| `--first-match` | Stop checking after first matching article |
| `--playwright` | Use headless browser for JS-rendered pages |

## CLI Commands

```bash
notifyme add [options]      # Add a new monitor
notifyme list               # List all monitors
notifyme check              # Check all due monitors
notifyme check "Name"       # Check specific monitor
notifyme check --all        # Check all monitors (ignore schedule)
notifyme history            # View notification history
notifyme pause <id|name>    # Pause a monitor
notifyme resume <id|name>   # Resume a monitor
notifyme remove <id|name>   # Delete a monitor
notifyme test-email         # Test email configuration
notifyme install-scheduler  # Set up background checking
```

## Background Scheduling

Set up automatic checking with macOS launchd:

```bash
# Generate and install the scheduler (checks every 5 minutes)
notifyme install-scheduler --interval 5

# Enable it
launchctl load ~/Library/LaunchAgents/com.notifyme.checker.plist

# Disable it
launchctl unload ~/Library/LaunchAgents/com.notifyme.checker.plist

# View logs
tail -f ~/.notifyme/scheduler.log
```

## Cost Estimates

| Monitor Type | Cost per Check |
|--------------|----------------|
| News (no filter) | Free |
| News + filter (Haiku) | ~$0.0002 per article |
| Agentic (Sonnet) | ~$0.003 per check |

Example: 5 monitors checking daily ≈ $1-2/month

## Examples

### Sports Win Alerts
```bash
notifyme add \
  --name "Oregon Ducks WBB Win" \
  --type agentic \
  --url "https://www.espn.com/womens-college-basketball/team/schedule/_/id/2483" \
  --condition "Oregon won their most recent completed game" \
  --notify-on-each \
  --interval 30  # Check every 30 minutes on game days
```

### Product Availability
```bash
notifyme add \
  --name "PS5 Pro In Stock" \
  --type news \
  --url "https://news.google.com/rss/search?q=PS5+Pro+in+stock+OR+available" \
  --filter "PS5 Pro is actually IN STOCK and available to purchase NOW" \
  --first-match
```

### Company News (Filtered)
```bash
notifyme add \
  --name "Anthropic News" \
  --type news \
  --url "https://news.google.com/rss/search?q=Anthropic" \
  --filter "Article is specifically about Anthropic the AI company, not generic AI news"
```

## Data Storage

- **Database**: `~/.notifyme/notifyme.db` (SQLite)
- **Logs**: `~/.notifyme/scheduler.log`
- **Config**: `.env` in project directory

## License

MIT
