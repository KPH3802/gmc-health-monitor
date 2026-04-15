"""
GMC Health Monitor - Configuration EXAMPLE
Copy this file to config.py and fill in real credentials.
Never commit config.py to git.
"""

# ---------------------------------------------------------------------------
# Email settings (Namecheap Private Email SMTP)
# ---------------------------------------------------------------------------
EMAIL_SENDER   = "you@yourdomain.com"
EMAIL_PASSWORD = "your-smtp-password"
EMAIL_RECIPIENT = "you@yourdomain.com"
SMTP_SERVER    = "mail.privateemail.com"
SMTP_PORT      = 587

# ---------------------------------------------------------------------------
# Gmail IMAP (scanner email checks)
# ---------------------------------------------------------------------------
IMAP_HOST     = "imap.gmail.com"
IMAP_USER     = "you@gmail.com"
IMAP_PASSWORD = "xxxx xxxx xxxx xxxx"

# ---------------------------------------------------------------------------
# FMP API
# ---------------------------------------------------------------------------
FMP_API_KEY = "your-fmp-api-key"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
POSITIONS_DB = "/path/to/positions.db"
NEWS_DIGEST_LOG = "/path/to/news_digest/news_digest.log"
