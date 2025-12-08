# Email to Discord Forwarder

Forward emails from Gmail to Discord with customizable templates for different services (Netflix, HBO, Disney+, etc.).

## How It Works

1. Gmail auto-forwards emails to a receiving email address (e.g., DuckDuckGo email)
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
5. Select "Forward a copy of incoming mail to..."

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
            "info_pattern": "Requested by\\s+(\\w+)\\s+from\\s+(?:a\\s+)?(.+?)\\s+at\\s+(.+?)(?:\\n|Get Code)"
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
| `subject_contains` | Text to match in email subject (case-insensitive) |
| `emoji` | Emoji for the Discord title |
| `title` | Discord embed title |
| `color` | Embed color (decimal) |
| `webhook` | Override webhook for this template (optional) |
| `link_patterns` | Regex patterns to extract links from HTML |
| `info_pattern` | Regex to extract requester info (name, device, time) |

### How Matching Works

| Scenario | Result |
|----------|--------|
| Email matches template | Uses template formatting + webhook |
| Email matches filter but no template | Shows raw email content |
| Email doesn't match any filter | Skipped |

### Multiple Webhooks

Send different services to different Discord channels:

```json
{
    "discord_webhook": "https://discord.com/api/webhooks/DEFAULT",

    "templates": {
        "netflix": {
            "webhook": "https://discord.com/api/webhooks/NETFLIX_CHANNEL",
            ...
        },
        "hbo": {
            "webhook": "https://discord.com/api/webhooks/HBO_CHANNEL",
            ...
        },
        "disney": {
            ...  // Uses default webhook (no override)
        }
    }
}
```

## Configuration Reference

| Option | Description | Default |
|--------|-------------|---------|
| `imap_server` | IMAP server hostname | Required |
| `imap_port` | IMAP server port | 993 |
| `email_address` | Email to monitor | Required |
| `email_password` | App password | Required |
| `discord_webhook` | Default webhook URL | Required |
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
| `config.json` | Your configuration |
| `config.example.json` | Example template |
| `requirements.txt` | Python dependencies |
| `processed_emails.json` | Tracks processed emails (auto-created) |
| `email_forwarder.log` | Log file (auto-created) |
