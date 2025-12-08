# Email Code to Discord Forwarder

A Python script that monitors an email inbox and forwards Netflix emails to a Discord channel via webhook.

## How It Works

1. Gmail auto-forwards emails to a receiving email address
2. This script polls the receiving email inbox via IMAP
3. New emails are sent to your Discord channel via webhook

## Setup

### Step 1: Create a Receiving Email Account

You can use any email provider that supports IMAP. Here are common options:

**Gmail (Recommended):**
- Create a new Gmail account or use an existing one
- IMAP Server: `imap.gmail.com`
- Port: `993`

**Outlook/Hotmail:**
- IMAP Server: `outlook.office365.com`
- Port: `993`

### Step 2: Enable IMAP Access

**For Gmail:**
1. Go to Gmail Settings > See all settings
2. Click "Forwarding and POP/IMAP" tab
3. Enable IMAP access
4. Save changes

### Step 3: Create an App Password (Gmail)

Gmail requires an "App Password" instead of your regular password:

1. Go to your Google Account > Security
2. Enable 2-Step Verification if not already enabled
3. Go to Security > 2-Step Verification > App passwords
4. Select "Mail" and "Windows Computer"
5. Click Generate
6. Copy the 16-character password (use this in config.json)

### Step 4: Set Up Gmail Forwarding

On your main Gmail account:
1. Go to Settings > See all settings
2. Click "Forwarding and POP/IMAP" tab
3. Click "Add a forwarding address"
4. Enter your receiving email address
5. Confirm the forwarding via the verification email
6. Select "Forward a copy of incoming mail to..."
7. Save changes

### Step 5: Create a Discord Webhook

1. Open Discord and go to your server
2. Right-click the channel where you want notifications
3. Click "Edit Channel" > "Integrations" > "Webhooks"
4. Click "New Webhook"
5. Name it (e.g., "Email Forwarder")
6. Click "Copy Webhook URL"

### Step 6: Configure the Script

1. Copy the example config:
   ```bash
   copy config.example.json config.json
   ```

2. Edit `config.json` with your settings:
   ```json
   {
       "imap_server": "imap.gmail.com",
       "imap_port": 993,
       "email_address": "your-receiving-email@gmail.com",
       "email_password": "your-16-char-app-password",
       "discord_webhook": "https://discord.com/api/webhooks/...",
       "folder": "INBOX",
       "search_criteria": "UNSEEN",
       "mark_as_read": true,
       "poll_interval": 60,
       "subject_filter": "Your Netflix temporary access code"
   }
   ```

### Step 7: Install Python and Dependencies

1. Install Python 3.8+ from [python.org](https://www.python.org/downloads/)
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Step 8: Run the Script

```bash
python email_to_discord.py
```

## Running as a Windows Service

To keep the script running in the background on your Windows server:

### Option A: Task Scheduler (Simple)

1. Open Task Scheduler
2. Click "Create Basic Task"
3. Name: "Email to Discord Forwarder"
4. Trigger: "When the computer starts"
5. Action: "Start a program"
6. Program: `pythonw.exe` (for no console window)
7. Arguments: `C:\path\to\email_to_discord.py`
8. Start in: `C:\path\to\` (directory containing the script)

### Option B: NSSM (Recommended for Services)

1. Download NSSM from [nssm.cc](https://nssm.cc/download)
2. Open Command Prompt as Administrator
3. Run:
   ```bash
   nssm install EmailToDiscord
   ```
4. Configure:
   - Path: `C:\Python311\python.exe`
   - Startup directory: `C:\path\to\forward`
   - Arguments: `email_to_discord.py`
5. Start the service:
   ```bash
   nssm start EmailToDiscord
   ```

## Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `imap_server` | IMAP server hostname | Required |
| `imap_port` | IMAP server port | 993 |
| `email_address` | Email address to monitor | Required |
| `email_password` | Email password or app password | Required |
| `discord_webhook` | Discord webhook URL | Required |
| `folder` | Email folder to monitor | INBOX |
| `search_criteria` | IMAP search criteria | UNSEEN |
| `mark_as_read` | Mark emails as read after processing | true |
| `poll_interval` | Seconds between inbox checks | 60 |
| `subject_filter` | Only forward emails containing this text in subject (case-insensitive) | None (all emails) |

## Troubleshooting

### "Authentication failed"
- For Gmail: Make sure you're using an App Password, not your regular password
- Ensure 2-Step Verification is enabled on your Google account

### "Connection refused"
- Check that IMAP is enabled in your email settings
- Verify the IMAP server and port are correct

### Emails not being forwarded
- Check Gmail's forwarding settings
- Verify the forwarding address is confirmed
- Check the spam folder of the receiving account

### Discord messages not appearing
- Verify the webhook URL is correct
- Check that the webhook hasn't been deleted
- Look for errors in `email_forwarder.log`

## Files

- `email_to_discord.py` - Main script
- `config.json` - Your configuration (create from example)
- `config.example.json` - Example configuration template
- `processed_emails.json` - Tracks processed emails (auto-created)
- `email_forwarder.log` - Log file (auto-created)
- `requirements.txt` - Python dependencies

## License

MIT License - Feel free to modify and use as needed.
