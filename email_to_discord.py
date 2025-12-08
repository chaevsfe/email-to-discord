#!/usr/bin/env python3
"""
Email to Discord Forwarder

This script monitors an email inbox via IMAP and forwards new emails
to a Discord channel via webhook.

Setup:
1. Configure your email settings in config.json
2. Create a Discord webhook and add the URL to config.json
3. Run this script on your always-on Windows server
"""

import imaplib
import email
from email.header import decode_header
import json
import time
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('email_forwarder.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Path to track processed emails
PROCESSED_FILE = 'processed_emails.json'

def load_config(config_path: str = 'config.json') -> dict:
    """Load configuration from JSON file."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_path}")
        logger.info("Please create a config.json file. See config.example.json for template.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        sys.exit(1)

def load_processed_emails() -> set:
    """Load the set of already processed email IDs."""
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r') as f:
                data = json.load(f)
                return set(data.get('processed_ids', []))
        except (json.JSONDecodeError, IOError):
            return set()
    return set()

def save_processed_emails(processed_ids: set):
    """Save the set of processed email IDs."""
    # Keep only the last 1000 IDs to prevent file from growing too large
    ids_list = list(processed_ids)[-1000:]
    with open(PROCESSED_FILE, 'w') as f:
        json.dump({'processed_ids': ids_list, 'last_updated': datetime.now().isoformat()}, f)

def decode_mime_header(header: str) -> str:
    """Decode a MIME-encoded email header."""
    if header is None:
        return ""

    decoded_parts = []
    for part, encoding in decode_header(header):
        if isinstance(part, bytes):
            try:
                decoded_parts.append(part.decode(encoding or 'utf-8', errors='replace'))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(part.decode('utf-8', errors='replace'))
        else:
            decoded_parts.append(part)

    return ''.join(decoded_parts)

def get_email_body(msg) -> tuple:
    """Extract the body text and HTML from an email message."""
    body = ""
    html = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            # Skip attachments
            if "attachment" in content_disposition:
                continue

            # Get plain text
            if content_type == "text/plain" and not body:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(charset, errors='replace')
                except Exception as e:
                    logger.warning(f"Error decoding email part: {e}")

            # Get HTML
            elif content_type == "text/html" and not html:
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    payload = part.get_payload(decode=True)
                    if payload:
                        html = payload.decode(charset, errors='replace')
                        # If no plain text, create one from HTML
                        if not body:
                            import re
                            body = re.sub(r'<[^>]+>', '', html)
                            body = re.sub(r'\s+', ' ', body).strip()
                except Exception as e:
                    logger.warning(f"Error decoding HTML part: {e}")
    else:
        try:
            charset = msg.get_content_charset() or 'utf-8'
            payload = msg.get_payload(decode=True)
            if payload:
                content = payload.decode(charset, errors='replace')
                if '<html' in content.lower():
                    html = content
                    import re
                    body = re.sub(r'<[^>]+>', '', content)
                    body = re.sub(r'\s+', ' ', body).strip()
                else:
                    body = content
        except Exception as e:
            logger.warning(f"Error decoding email body: {e}")

    return body.strip(), html

def truncate_text(text: str, max_length: int = 1900) -> str:
    """Truncate text to fit Discord's message limit."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...\n\n[Message truncated]"

def find_matching_template(subject: str, templates: dict) -> tuple:
    """Find a template that matches the email subject."""
    subject_lower = subject.lower()
    for name, template in templates.items():
        if template.get('subject_contains', '').lower() in subject_lower:
            return name, template
    return None, None

def extract_template_info(body: str, html: str, template: dict) -> dict:
    """Extract info from email using template patterns."""
    import re

    info = {}

    # Extract info using info_pattern if defined
    info_pattern = template.get('info_pattern')
    if info_pattern:
        match = re.search(info_pattern, body, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 1:
                info['name'] = groups[0]
            if len(groups) >= 2:
                info['device'] = groups[1].strip() if groups[1] else None
            if len(groups) >= 3:
                info['time'] = groups[2].strip() if groups[2] else None

    # Extract link using link_patterns if defined
    link_patterns = template.get('link_patterns', [])
    if html and link_patterns:
        for pattern in link_patterns:
            try:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    info['link'] = match.group(1)
                    break
            except re.error as e:
                logger.warning(f"Invalid regex pattern: {pattern} - {e}")

    return info

def send_to_discord(default_webhook: str, subject: str, sender: str, body: str, html: str, timestamp: str, templates: dict):
    """Send an email notification to Discord via webhook."""

    # Try to find a matching template
    template_name, template = find_matching_template(subject, templates)

    # Get webhook(s) - support both single 'webhook' and multiple 'webhooks'
    if template:
        webhooks = template.get('webhooks', [])
        if not webhooks:
            # Fall back to single webhook
            single_webhook = template.get('webhook', default_webhook)
            webhooks = [single_webhook]
    else:
        webhooks = [default_webhook]

    if template:
        # Use template-based formatting
        template_info = extract_template_info(body, html, template)

        fields = []
        if template_info.get('name'):
            fields.append({
                "name": "Requested By",
                "value": template_info['name'],
                "inline": True
            })
        if template_info.get('device'):
            fields.append({
                "name": "Device",
                "value": template_info['device'],
                "inline": True
            })
        if template_info.get('time'):
            fields.append({
                "name": "Time",
                "value": template_info['time'],
                "inline": False
            })

        # Build description with link if found
        description = "Someone requested a temporary access code."
        if template_info.get('link'):
            description += f"\n\n**[Click here to Get Code]({template_info['link']})**\n\n⚠️ Link expires in 15 minutes"
        else:
            description += " Check your email to approve."

        emoji = template.get('emoji', '📧')
        title = template.get('title', f'{template_name.title()} Code Requested')
        color = template.get('color', 3447003)

        embed = {
            "title": f"{emoji} {title}",
            "description": description,
            "color": color,
            "fields": fields if fields else [{"name": "Info", "value": "Check email for details", "inline": False}],
            "footer": {
                "text": f"{template_name.title()} Access Code"
            },
            "timestamp": datetime.now().isoformat()
        }
    else:
        # Fallback: Standard email formatting (raw email)
        embed = {
            "title": f"📧 {truncate_text(subject, 250)}",
            "color": 3447003,  # Blue color
            "fields": [
                {
                    "name": "From",
                    "value": truncate_text(sender, 250),
                    "inline": True
                },
                {
                    "name": "Received",
                    "value": timestamp,
                    "inline": True
                },
                {
                    "name": "Content",
                    "value": truncate_text(body, 1000) if body else "(No text content)",
                    "inline": False
                }
            ],
            "footer": {
                "text": "Email Forwarder"
            },
            "timestamp": datetime.now().isoformat()
        }

    payload = {
        "embeds": [embed]
    }

    # Send to all webhooks with retry logic
    success = False
    for webhook_url in webhooks:
        webhook_success = False
        retries = 3
        delay = 2  # Start with 2 second delay

        for attempt in range(retries):
            try:
                response = requests.post(webhook_url, json=payload, timeout=10)
                response.raise_for_status()
                logger.info(f"Successfully sent to webhook: {webhook_url[:50]}...")
                webhook_success = True
                success = True
                break
            except requests.exceptions.RequestException as e:
                if attempt < retries - 1:
                    logger.warning(f"Webhook failed (attempt {attempt + 1}/{retries}), retrying in {delay}s: {e}")
                    time.sleep(delay)
                    delay *= 2  # Exponential backoff
                else:
                    logger.error(f"Failed to send to webhook after {retries} attempts: {webhook_url[:50]}... - {e}")

    if success:
        logger.info(f"Email forwarded to Discord: {subject}")

    return success

def connect_to_imap(config: dict) -> imaplib.IMAP4_SSL:
    """Connect to the IMAP server."""
    try:
        mail = imaplib.IMAP4_SSL(config['imap_server'], config.get('imap_port', 993))
        mail.login(config['email_address'], config['email_password'])
        logger.info(f"Connected to {config['imap_server']}")
        return mail
    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP connection failed: {e}")
        raise

def check_for_new_emails(mail: imaplib.IMAP4_SSL, config: dict, processed_ids: set) -> set:
    """Check for new emails and forward them to Discord."""
    try:
        # Select the inbox (or configured folder)
        folder = config.get('folder', 'INBOX')
        mail.select(folder)

        # Search for unread emails
        search_criteria = config.get('search_criteria', 'UNSEEN')
        status, messages = mail.search(None, search_criteria)

        if status != 'OK':
            logger.warning("Failed to search emails")
            return processed_ids

        email_ids = messages[0].split()

        if not email_ids:
            logger.debug("No new emails found")
            return processed_ids

        logger.info(f"Found {len(email_ids)} email(s) matching criteria")

        for email_id in email_ids:
            email_id_str = email_id.decode()

            # Skip if already processed
            if email_id_str in processed_ids:
                continue

            try:
                # Fetch the email
                status, msg_data = mail.fetch(email_id, '(RFC822)')

                if status != 'OK':
                    continue

                # Parse the email
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Extract email details
                subject = decode_mime_header(msg.get('Subject', '(No Subject)'))
                sender = decode_mime_header(msg.get('From', 'Unknown'))
                date = msg.get('Date', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                body, html = get_email_body(msg)

                logger.info(f"Processing email: {subject} from {sender}")

                # Check subject filters if configured
                subject_filters = config.get('subject_filters', [])
                subject_filter = config.get('subject_filter')  # Legacy single filter support

                # Convert legacy single filter to list
                if subject_filter and not subject_filters:
                    subject_filters = [subject_filter]

                # Check if subject matches any filter
                if subject_filters:
                    subject_lower = subject.lower()
                    matches = any(f.lower() in subject_lower for f in subject_filters)
                    if not matches:
                        logger.debug(f"Skipping email - subject doesn't match filters: {subject}")
                        processed_ids.add(email_id_str)
                        if config.get('mark_as_read', True):
                            mail.store(email_id, '+FLAGS', '\\Seen')
                        continue

                # Get templates from config
                templates = config.get('templates', {})

                # Send to Discord
                if send_to_discord(config['discord_webhook'], subject, sender, body, html, date, templates):
                    processed_ids.add(email_id_str)

                    # Mark as read if configured
                    if config.get('mark_as_read', True):
                        mail.store(email_id, '+FLAGS', '\\Seen')

                # Small delay between emails to avoid rate limiting
                time.sleep(1)

            except Exception as e:
                logger.error(f"Error processing email {email_id_str}: {e}")

        return processed_ids

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error: {e}")
        raise

def main():
    """Main function to run the email forwarder."""
    logger.info("Starting Email to Discord Forwarder")

    # Load configuration
    config = load_config()

    # Validate required config
    required_fields = ['imap_server', 'email_address', 'email_password', 'discord_webhook']
    for field in required_fields:
        if field not in config:
            logger.error(f"Missing required config field: {field}")
            sys.exit(1)

    # Load processed emails
    processed_ids = load_processed_emails()
    logger.info(f"Loaded {len(processed_ids)} previously processed email IDs")

    # Polling interval (in seconds)
    poll_interval = config.get('poll_interval', 60)

    # Main loop
    mail = None
    reconnect_delay = 30

    while True:
        try:
            # Connect if not connected
            if mail is None:
                mail = connect_to_imap(config)

            # Check for new emails
            processed_ids = check_for_new_emails(mail, config, processed_ids)

            # Save processed IDs
            save_processed_emails(processed_ids)

            # Wait before next check
            logger.debug(f"Waiting {poll_interval} seconds before next check...")
            time.sleep(poll_interval)

            # Keep connection alive with NOOP
            try:
                mail.noop()
            except:
                mail = None

        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP error, will reconnect: {e}")
            mail = None
            time.sleep(reconnect_delay)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            if mail:
                try:
                    mail.logout()
                except:
                    pass
            save_processed_emails(processed_ids)
            break

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            mail = None
            time.sleep(reconnect_delay)

if __name__ == "__main__":
    main()
