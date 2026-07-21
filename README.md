# Email to Discord Forwarder

Forward emails from Gmail to Discord with customizable templates for different services (Netflix, HBO, Disney+, etc.).

## How It Works

1. Gmail auto-forwards emails to a receiving email address
2. This script monitors that inbox via IMAP
3. Emails matching your filters are sent to Discord with nice formatting
4. Templates extract links and info automatically (like Netflix "Get Code" links)

## Quick Start (Windows)

### 1. Set Up Gmail Forwarding

On your main Gmail account:
1. Settings > See all settings > "Forwarding and POP/IMAP"
2. Click "Add a forwarding address"
3. Enter your receiving email (the one this script will monitor)
4. Confirm via the verification email
5. Select "Forward a copy of incoming mail to..." or create a custom filter

### 2. Create Discord Webhook(s)

1. Right-click your Discord channel > Edit Channel > Integrations > Webhooks
2. Click "New Webhook" and copy the URL
3. Create multiple webhooks if you want different services in different channels

### 3. Set Up the Receiving Email

For Gmail as the receiving account:
1. Enable IMAP: Settings > See all settings > "Forwarding and POP/IMAP" > Enable IMAP
2. Create an App Password:
   - Google Account > Security > 2-Step Verification (enable if needed)
   - Security > App passwords > Select "Mail" > Generate
   - Save the 16-character password

### 4. Configure the Script

Copy and edit the config:
```cmd
copy config.example.json config.json
```

Edit `config.json`:
```json
{
    "imap_server": "imap.gmail.com",
    "imap_port": 993,
    "email_address": "your-email@gmail.com",
    "email_password": "your-16-char-app-password",
    "folder": "INBOX",
    "search_criteria": "UNSEEN",
    "mark_as_read": true,
    "poll_interval": 60,

    "discord_webhook": "https://discord.com/api/webhooks/DEFAULT_WEBHOOK",

    "subject_filters": ["netflix", "hbo", "disney", "access code"],

    "templates": {
        "netflix": {
            "subject_contains": "netflix",
            "emoji": "🎬",
            "title": "Netflix Access Code Requested",
            "color": 14423100,
            "webhook": "https://discord.com/api/webhooks/NETFLIX_CHANNEL",
            "link_patterns": [
                "<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>\\s*Get\\s*Code"
            ],
            "info_pattern": "Requested by\\s+(?P<name>.+?)\\s+from\\s+(?:an?\\s+)?(?P<device>.+?)\\s+at\\s+(?P<time>[^\\r\\n]+)"
        },
        "hbo": {
            "subject_contains": "hbo",
            "emoji": "📺",
            "title": "HBO Max Code Requested",
            "color": 9932887
        }
    }
}
```

### 5. Install Python & Dependencies

1. Install Python 3.8+ from [python.org](https://www.python.org/downloads/)
2. Install requests:
   ```cmd
   pip install -r requirements.txt
   ```

### 6. Test the Script

```cmd
cd C:\forward
python email_to_discord.py
```

If it connects successfully, you'll see:
```
INFO - Starting Email to Discord Forwarder
INFO - Connected to imap.gmail.com
```

Press `Ctrl+C` to stop.

## Running as a Windows Service (NSSM)

To run automatically on startup:

### Install NSSM

1. Download from [nssm.cc](https://nssm.cc/download)
2. Extract to a folder (e.g., `C:\Users\YourName\Downloads\nssm-2.24\win64\`)

### Create the Service

Open Command Prompt **as Administrator**:

```cmd
"C:\Users\YourName\Downloads\nssm-2.24\nssm-2.24\win64\nssm.exe" install EmailToDiscord
```

In the GUI that opens:
- **Path**: Your Python path (run `where python` to find it)
  - Example: `C:\Users\YourName\AppData\Local\Programs\Python\Python311\python.exe`
- **Startup directory**: `C:\forward`
- **Arguments**: `email_to_discord.py`

Click "Install service".

### Manage the Service

```cmd
# Start
"C:\path\to\nssm.exe" start EmailToDiscord

# Check status
"C:\path\to\nssm.exe" status EmailToDiscord

# Stop
"C:\path\to\nssm.exe" stop EmailToDiscord

# Restart (after config changes)
"C:\path\to\nssm.exe" restart EmailToDiscord

# View/edit settings
"C:\path\to\nssm.exe" edit EmailToDiscord

# Remove service
"C:\path\to\nssm.exe" remove EmailToDiscord confirm
```

### Verify Auto-Start

1. Press `Win + R`, type `services.msc`
2. Find "EmailToDiscord"
3. Ensure "Startup type" is **Automatic**

## Template System

Templates let you customize how different email types are displayed.

### Template Options

| Option | Description |
|--------|-------------|
| `subject_contains` | Text to match in email subject (case-insensitive). **Required** — a template without it matches every email |
| `emoji` | Emoji for the Discord title |
| `title` | Discord embed title |
| `color` | Embed color (decimal) |
| `webhook` | Single webhook URL for this template (optional) |
| `webhooks` | Array of webhook URLs to send to multiple servers (optional) |
| `link_patterns` | Regex patterns to extract links from HTML |
| `info_pattern` | Regex to extract requester info. Named groups `name`, `device`, `location`, `time` are matched against the plain-text body **and** the HTML rendered to text |
| `code_pattern` | Regex whose first group is a login code. When it matches, the code is shown in its own `Your Code` block |
| `info_field_name` | Label for the requester line (default `📱 Requested By`) |
| `display_name` | Name used in the footer (default: the template key with underscores replaced) |
| `footer_text` | Overrides the whole footer line |
| `edit_template` | Template name, or list of names, whose last message this one should **edit** instead of posting |
| `edit_field_name` | Label for the appended field (default `✅ Signed In`) |
| `edit_color` | Colour the edited embed becomes (default green) |
| `edit_window_minutes` | How recent the target message must be (default 15) |

### How Matching Works

| Scenario | Result |
|----------|--------|
| Email matches a template with `edit_template` | Appends a field to that template's last message, if it is newer than `edit_window_minutes` |
| ...and no recent message exists | Posts its own standalone embed instead |
| Email matches template | Uses template formatting + webhook |
| Email matches filter but no template | Shows raw email content |
| Email doesn't match any filter | Marked read and skipped |

### Correlating a Sign-In With a Code Request

`netflix_new_device` sets `edit_template`, so a "new device" email finds the sign-in or
access code message that preceded it and appends `✅ Signed In` to it — one Discord
message tells the whole story of one sign-in.

The match is made **on time alone**, within `edit_window_minutes`. Device names are not
comparable between the two emails: an access request from a `Samsung Galaxy S25 Edge`
produces a sign-in alert naming `Android Phone Chrome - Mobile Browser`.

15 minutes is the default because Netflix states that expiry in both code emails. If no
code request falls inside the window, the sign-in posts as its own message rather than
attaching itself to an unrelated one.

### Multiple Webhooks

**Send to different channels per service:**
```json
{
    "templates": {
        "netflix": {
            "webhook": "https://discord.com/api/webhooks/NETFLIX_CHANNEL",
            ...
        },
        "hbo": {
            "webhook": "https://discord.com/api/webhooks/HBO_CHANNEL",
            ...
        }
    }
}
```

**Send to multiple servers at once:**
```json
{
    "templates": {
        "netflix": {
            "webhooks": [
                "https://discord.com/api/webhooks/SERVER1_CHANNEL",
                "https://discord.com/api/webhooks/SERVER2_CHANNEL"
            ],
            ...
        }
    }
}
```

If no `webhook` or `webhooks` is specified, the default `discord_webhook` is used.

### Retry Logic

When a webhook fails (network issues, Discord down, etc.):
- Makes **3 attempts** per webhook, sleeping 2s then 4s between them
- If all attempts fail, tries the next webhook in the list
- If **all webhooks fail**, the email stays unread and will be retried on the next poll cycle

One caveat worth knowing: if there are several webhooks and *some* succeed, the email
counts as delivered and is marked read. It is not re-sent to the channels that failed —
those are logged as errors instead. Emails are only retried when **every** webhook failed.

## Configuration Reference

| Option | Description | Default |
|--------|-------------|---------|
| `imap_server` | IMAP server hostname | Required |
| `imap_port` | IMAP server port | 993 |
| `email_address` | Email to monitor | Required |
| `email_password` | App password | Required |
| `discord_webhook` | Default webhook URL, or an array of URLs | Required |
| `imap_timeout` | IMAP socket timeout in seconds | 60 |
| `folder` | Email folder | INBOX |
| `search_criteria` | IMAP search | UNSEEN |
| `mark_as_read` | Mark processed emails as read | true |
| `poll_interval` | Seconds between checks | 60 |
| `subject_filters` | Array of keywords to match | [] |
| `subject_filter` | Single keyword (legacy) | null |
| `templates` | Service-specific formatting | {} |

## Troubleshooting

### "ModuleNotFoundError: No module named 'requests'"

You have multiple Python versions. Install for the correct one:
```cmd
"C:\path\to\python.exe" -m pip install requests
```

Use the same Python path that NSSM is configured to use.

### "Authentication failed"

- Use an App Password, not your regular Gmail password
- Make sure 2-Step Verification is enabled

### Service stuck in PAUSED state

```cmd
nssm stop EmailToDiscord
nssm remove EmailToDiscord confirm
```
Then reinstall the service.

### Check service logs

Set up logging in NSSM:
1. Run `nssm edit EmailToDiscord`
2. Go to "I/O" tab
3. Set stdout and stderr to log files:
   - `C:\forward\service_output.log`
   - `C:\forward\service_error.log`

### Emails not forwarding

- Check `email_forwarder.log` in the script directory
- Verify Gmail forwarding is set up and confirmed
- Check spam folder of receiving account

## Files

| File | Description |
|------|-------------|
| `email_to_discord.py` | Main script |
| `config.json` | Your configuration — git-ignored, holds your live credentials |
| `config.example.json` | Example template, safe to commit |
| `requirements.txt` | Python dependencies |
| `processed_emails.json` | Tracks processed emails (auto-created) |
| `recent_messages.json` | Tracks recent message IDs so sign-ins can edit them (auto-created) |
| `email_forwarder.log` | Log file (auto-created) |
