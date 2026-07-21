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
import re
import sys
import html as html_lib
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from pathlib import Path
import socket
import tempfile
import requests

# Configure logging with rotation (5 MB max, keep 3 backups)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('email_forwarder.log', maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Path to track processed emails
PROCESSED_FILE = 'processed_emails.json'
# Path to track recent Discord message IDs for editing
RECENT_MESSAGES_FILE = 'recent_messages.json'
# How many processed UIDs to remember (oldest are dropped first)
MAX_PROCESSED_IDS = 1000
# How long a code message stays eligible to receive a "signed in" edit.
# Netflix states 15 minutes in both code emails, and in the sample corpus genuine
# pairs are <=10.7 min apart while unrelated sign-ins are >=2.6 days apart.
DEFAULT_EDIT_WINDOW_MINUTES = 15

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

def write_json_atomically(path: str, data) -> bool:
    """Write JSON via temp file + rename so a power loss can't leave a truncated file.

    tmp_path is bound BEFORE json.dump: if the dump itself raises, the cleanup below
    must still know which file to remove. Getting this wrong raises UnboundLocalError
    out of the handler, which callers don't expect and which crashes the poll loop
    *after* a Discord message has already been sent - producing a duplicate on every
    subsequent poll, forever.
    """
    dir_path = os.path.dirname(os.path.abspath(path))
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=dir_path, suffix='.tmp', delete=False) as tmp:
            tmp_path = tmp.name
            json.dump(data, tmp)
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        logger.error(f"Failed to save {path}: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False

def load_processed_emails() -> dict:
    """Load already-processed email UIDs.

    A dict (not a set) is used purely as an insertion-ordered set, so the newest IDs
    are identifiable when the file is trimmed. Set iteration order is hash-based, which
    made the old truncation drop an arbitrary subset instead of the oldest.
    """
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r') as f:
                data = json.load(f)
                return dict.fromkeys(data.get('processed_ids', []))
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_processed_emails(processed_ids: dict):
    """Trim to the newest MAX_PROCESSED_IDS and save. Atomic; see write_json_atomically."""
    # Trim in place so memory and file stay in agreement
    while len(processed_ids) > MAX_PROCESSED_IDS:
        processed_ids.pop(next(iter(processed_ids)))
    write_json_atomically(PROCESSED_FILE, {
        'processed_ids': list(processed_ids),
        'last_updated': datetime.now().isoformat(),
    })

def load_recent_messages() -> dict:
    """Load recent Discord message IDs keyed by template name.
    Each entry: {template_name: {"webhook": url, "message_id": id, "timestamp": iso}}
    """
    if os.path.exists(RECENT_MESSAGES_FILE):
        try:
            with open(RECENT_MESSAGES_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_recent_messages(recent_messages: dict):
    """Save recent Discord message IDs. Atomic; see write_json_atomically."""
    write_json_atomically(RECENT_MESSAGES_FILE, recent_messages)

def prune_recent_messages(recent_messages: dict, window_minutes: int) -> bool:
    """Drop code messages too old to be the one a sign-in used. Returns True if any went.

    Without this, target selection only ranks candidates against each other and never
    against the present, so a "new device" email will happily attach itself to a code
    message from days earlier. Entries with an unreadable timestamp are dropped too -
    an entry that can't be aged is exactly the kind that lingers forever.
    """
    cutoff = datetime.now() - timedelta(minutes=window_minutes)
    stale = []
    for name, entry in recent_messages.items():
        try:
            stored = datetime.fromisoformat(entry.get('timestamp', ''))
        except (ValueError, TypeError):
            logger.warning(f"Dropping '{name}' from recent messages: unreadable timestamp")
            stale.append(name)
            continue
        if stored < cutoff:
            logger.info(f"Expiring '{name}' from recent messages (older than {window_minutes} min)")
            stale.append(name)
    for name in stale:
        del recent_messages[name]
    return bool(stale)

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

def html_to_text(html: str) -> str:
    """Strip HTML to text while PRESERVING line breaks.

    get_email_body's inline stripper collapses every run of whitespace to one space,
    producing a single enormous line. Line-anchored regexes run against that output
    silently capture hundreds of characters instead of one field, so patterns that read
    structured detail out of an email must use this instead.
    """
    text = re.sub(r'<(script|style)[\s\S]*?</\1>', '', html, flags=re.IGNORECASE)
    text = re.sub(r'<head[\s\S]*?</head>', '', text, flags=re.IGNORECASE)
    # Block-level boundaries become newlines; everything else becomes a space
    text = re.sub(r'<(?:br|/p|/div|/tr|/td|/table|/h[1-6]|/a|/li)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    lines = (re.sub(r'[^\S\n]+', ' ', line).strip() for line in text.split('\n'))
    return '\n'.join(line for line in lines if line and line != '\xa0')

def truncate_text(text: str, max_length: int = 1900) -> str:
    """Truncate text to fit Discord's message limit."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...\n\n[Message truncated]"

def parse_email_date(date_str: str) -> str:
    """Parse email date string to ISO format for Discord."""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.isoformat()
    except (ValueError, TypeError):
        # Fall back to now, with an explicit offset: Discord reads an offset-less
        # timestamp as UTC, which would shift the displayed time by the host's offset.
        return datetime.now().astimezone().isoformat()

def find_matching_template(subject: str, templates: dict) -> tuple:
    """Find a template that matches the email subject."""
    subject_lower = subject.lower()
    for name, template in templates.items():
        if template.get('subject_contains', '').lower() in subject_lower:
            return name, template
    return None, None

def extract_template_info(body: str, html: str, template: dict) -> dict:
    """Extract info from email using template patterns."""
    info = {}

    # Extract code using code_pattern if defined
    code_pattern = template.get('code_pattern')
    if code_pattern:
        try:
            match = re.search(code_pattern, body, re.IGNORECASE)
            if match:
                info['code'] = match.group(1)
        except re.error as e:
            logger.warning(f"Invalid code_pattern: {code_pattern} - {e}")

    # Extract info using info_pattern if defined.
    # Try plaintext first, then the HTML rendered to text: Netflix puts the
    # "Requested by X from a Y at Z" line in the HTML part ONLY, so a plaintext-only
    # search finds nothing on exactly the emails that carry the most detail.
    info_pattern = template.get('info_pattern')
    if info_pattern:
        haystacks = [body]
        if html:
            haystacks.append(html_to_text(html))
        for haystack in haystacks:
            try:
                match = re.search(info_pattern, haystack, re.IGNORECASE)
            except re.error as e:
                logger.warning(f"Invalid info_pattern: {info_pattern} - {e}")
                break
            if not match:
                continue
            # Map named groups first, fall back to positional
            groupdict = match.groupdict()
            if groupdict:
                info.update({k: v.strip() for k, v in groupdict.items() if v})
            else:
                groups = match.groups()
                if len(groups) >= 1:
                    info['name'] = groups[0].strip() if groups[0] else None
                if len(groups) >= 2:
                    info['device'] = groups[1].strip() if groups[1] else None
                if len(groups) >= 3:
                    info['time'] = groups[2].strip() if groups[2] else None
            break

    # Extract link using link_patterns if defined
    link_patterns = template.get('link_patterns', [])
    if html and link_patterns:
        for pattern in link_patterns:
            try:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    # hrefs are HTML-escaped in source; without unescaping, '&amp;'
                    # survives into the URL and the parameter after it is lost.
                    info['link'] = html_lib.unescape(match.group(1))
                    break
            except re.error as e:
                logger.warning(f"Invalid regex pattern: {pattern} - {e}")

    return info

def edit_discord_message(webhook_url: str, message_id: str, embed: dict) -> bool:
    """Edit an existing Discord webhook message."""
    edit_url = f"{webhook_url}/messages/{message_id}"
    try:
        response = requests.patch(edit_url, json={"embeds": [embed]}, timeout=10)
        if response.status_code == 429:
            retry_after = response.json().get('retry_after', 2)
            logger.warning(f"Discord rate limited on edit, waiting {retry_after}s")
            time.sleep(retry_after)
            response = requests.patch(edit_url, json={"embeds": [embed]}, timeout=10)
        response.raise_for_status()
        logger.info(f"Successfully edited Discord message {message_id}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to edit Discord message {message_id}: {e}")
        return False

def post_embed(webhooks: list, embed: dict, retries: int = 3) -> tuple:
    """POST an embed to every webhook. Returns (any_succeeded, [{webhook, message_id}, ...]).

    Every webhook is attempted - callers must not stop at the first success, or the
    multi-channel support becomes single-channel support that happens to work when
    there is only one channel configured.
    """
    payload = {"embeds": [embed]}
    sent_messages = []
    success = False

    for webhook_url in webhooks:
        delay = 2
        for attempt in range(retries):
            try:
                # ?wait=true makes Discord return the created message, so we get its id
                response = requests.post(f"{webhook_url}?wait=true", json=payload, timeout=10)
                if response.status_code == 429:
                    try:
                        retry_after = float(response.json().get('retry_after', delay))
                    except (ValueError, TypeError, AttributeError):
                        retry_after = delay
                    if attempt < retries - 1:
                        logger.warning(f"Discord rate limited, waiting {retry_after}s")
                        time.sleep(retry_after)
                        delay *= 2
                        continue
                    # Falling out of the loop here used to be completely silent
                    logger.error(f"Gave up after {retries} rate-limited attempts: {webhook_url[:50]}...")
                    break
                response.raise_for_status()
                logger.info(f"Successfully sent to webhook: {webhook_url[:50]}...")

                try:
                    resp_data = response.json()
                except ValueError:
                    resp_data = {}
                if resp_data.get('id'):
                    sent_messages.append({
                        "webhook": webhook_url,
                        "message_id": resp_data['id'],
                    })

                success = True
                break
            except requests.exceptions.RequestException as e:
                if attempt < retries - 1:
                    logger.warning(f"Webhook failed (attempt {attempt + 1}/{retries}), retrying in {delay}s: {e}")
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Failed to send to webhook after {retries} attempts: {webhook_url[:50]}... - {e}")

    return success, sent_messages

def send_to_discord(default_webhook, subject: str, sender: str, body: str, html: str, timestamp: str, templates: dict, recent_messages: dict):
    """Send an email notification to Discord via webhook."""

    # Normalize default webhook(s) to a list
    if isinstance(default_webhook, str):
        default_webhooks = [default_webhook]
    elif isinstance(default_webhook, (list, tuple)):
        default_webhooks = list(default_webhook)
    else:
        logger.error(f"discord_webhook must be a URL or a list of URLs, got {type(default_webhook).__name__}")
        return False

    # Try to find a matching template
    template_name, template = find_matching_template(subject, templates)

    # Get webhook(s) - support both single 'webhook' and multiple 'webhooks'
    if template:
        webhooks = template.get('webhooks', [])
        if not webhooks:
            single_webhook = template.get('webhook')
            webhooks = [single_webhook] if single_webhook else default_webhooks
    else:
        webhooks = default_webhooks

    # Parse email timestamp for Discord
    email_timestamp = parse_email_date(timestamp)

    # Check if this template should edit a previous message instead of sending new
    edit_targets = template.get('edit_template') if template else None
    if edit_targets:
        if isinstance(edit_targets, str):
            edit_targets = [edit_targets]

        window = template.get('edit_window_minutes', DEFAULT_EDIT_WINDOW_MINUTES)
        # Expire first, then choose. Selecting by "newest candidate" alone compares
        # candidates only to each other and never to the clock, which is how a sign-in
        # ends up appended to a code request from days earlier.
        if prune_recent_messages(recent_messages, window):
            save_recent_messages(recent_messages)

        best_target = None
        best_time = None
        for target in edit_targets:
            if target in recent_messages:
                ts = recent_messages[target].get('timestamp', '')
                if best_time is None or ts > best_time:
                    best_time = ts
                    best_target = target

        template_info = extract_template_info(body, html, template)

        # Build the sign-in detail line (used for both edit and standalone)
        parts = []
        if template_info.get('name'):
            parts.append(template_info['name'])
        if template_info.get('device'):
            parts.append(template_info['device'])
        if template_info.get('location'):
            parts.append(template_info['location'])
        if template_info.get('time'):
            parts.append(template_info['time'])
        signin_summary = " • ".join(parts) if parts else "See email for details"
        field_name = template.get('edit_field_name', '✅ Signed In')
        edit_color = template.get('edit_color', 3066993)

        # Channels that still need to hear about this sign-in. A channel is served
        # either by editing its existing code message or by its own standalone post -
        # never neither, which is what the old any_edited flag allowed.
        pending_webhooks = list(webhooks)

        if best_target:
            prev = recent_messages[best_target]
            prev_messages = prev.get('messages', [])
            # Support old format (single webhook/message_id)
            if not prev_messages and prev.get('webhook'):
                prev_messages = [{"webhook": prev['webhook'], "message_id": prev['message_id']}]

            pending_webhooks = [w for w in webhooks
                                if w not in {m['webhook'] for m in prev_messages}]
            edited = 0
            for msg in prev_messages:
                try:
                    get_url = f"{msg['webhook']}/messages/{msg['message_id']}"
                    resp = requests.get(get_url, timeout=10)
                    resp.raise_for_status()
                    original_embed = resp.json()['embeds'][0]
                except Exception as e:
                    logger.error(f"Failed to fetch message {msg['message_id']}: {e}")
                    pending_webhooks.append(msg['webhook'])
                    continue

                # Append a new field to the original embed
                if 'fields' not in original_embed:
                    original_embed['fields'] = []
                original_embed['fields'].append({
                    "name": field_name,
                    "value": signin_summary,
                    "inline": False
                })
                original_embed['color'] = edit_color

                if edit_discord_message(msg['webhook'], msg['message_id'], original_embed):
                    edited += 1
                else:
                    pending_webhooks.append(msg['webhook'])

            if edited:
                logger.info(f"Edited {edited} previous '{best_target}' message(s) with sign-in details")

            # Consume the entry either way: the code message is stale now, and leaving
            # it behind lets an unrelated later sign-in append a second field to it.
            del recent_messages[best_target]
            save_recent_messages(recent_messages)

            if pending_webhooks:
                logger.warning(f"{len(pending_webhooks)} channel(s) could not be edited, posting standalone there")
            else:
                return True

        # No code message to edit (expired, absent, or the edit failed) - post a
        # standalone embed to every channel that has not been told yet.
        embed = {
            "title": f"{template.get('emoji', '📧')} {template.get('title', 'New Device Sign-In')}",
            "color": edit_color,
            "fields": [
                {
                    "name": field_name,
                    "value": signin_summary,
                    "inline": False
                }
            ],
            "timestamp": email_timestamp
        }
        success, _ = post_embed(pending_webhooks, embed)
        if success:
            logger.info("Sent standalone sign-in notification")
        return success

    if template:
        # Use template-based formatting
        template_info = extract_template_info(body, html, template)

        # .title() on the raw key would render the config's underscore
        # ('netflix_access' -> 'Netflix_Access') straight into the footer
        display_name = template.get('display_name', template_name.replace('_', ' ').title())
        emoji = template.get('emoji', '📧')
        title = template.get('title', f'{display_name} Code Requested')
        color = template.get('color', 3447003)
        footer_text = template.get('footer_text', f"{display_name} Code • Expires in 15 minutes")

        # Who asked, from what, and when - as one line at the bottom, so a later
        # "signed in" edit appends its own field directly underneath it.
        requester_parts = []
        for key in ('name', 'device', 'location', 'time'):
            if template_info.get(key):
                requester_parts.append(template_info[key])
        info_fields = []
        if requester_parts:
            info_fields.append({
                "name": template.get('info_field_name', '📱 Requested By'),
                "value": truncate_text(" • ".join(requester_parts), 1000),
                "inline": False
            })

        # Check if we have a code to display prominently
        if template_info.get('code'):
            # Code found - display it prominently
            embed = {
                "title": f"{emoji} {title}",
                "color": color,
                "fields": [
                    {
                        "name": "Your Code",
                        "value": f"```{template_info['code']}```",
                        "inline": False
                    }
                ] + info_fields,
                "footer": {
                    "text": footer_text
                },
                "timestamp": email_timestamp
            }
        else:
            # Build description with link if found
            description = "Someone requested a temporary access code."
            if template_info.get('link'):
                description += f"\n\n**[Click here to Get Code]({template_info['link']})**"
            else:
                description += " Check your email to approve."

            embed = {
                "title": f"{emoji} {title}",
                "description": description,
                "color": color,
                "fields": info_fields,
                "footer": {
                    "text": footer_text
                },
                "timestamp": email_timestamp
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
            "timestamp": email_timestamp
        }

    # Send to all webhooks with retry logic
    success, sent_messages = post_embed(webhooks, embed)

    if success:
        logger.info(f"Email forwarded to Discord: {subject}")
        # Store all message IDs for potential future edits
        if sent_messages and template_name:
            recent_messages[template_name] = {
                "messages": sent_messages,
                "timestamp": datetime.now().isoformat()
            }
            save_recent_messages(recent_messages)

    return success

def connect_to_imap(config: dict) -> imaplib.IMAP4_SSL:
    """Connect to the IMAP server."""
    try:
        timeout = config.get('imap_timeout', 60)
        mail = imaplib.IMAP4_SSL(config['imap_server'], config.get('imap_port', 993), timeout=timeout)
        mail.login(config['email_address'], config['email_password'])
        logger.info(f"Connected to {config['imap_server']}")
        return mail
    except (imaplib.IMAP4.error, socket.timeout, OSError) as e:
        logger.error(f"IMAP connection failed: {e}")
        raise

def check_for_new_emails(mail: imaplib.IMAP4_SSL, config: dict, processed_ids: dict, recent_messages: dict) -> dict:
    """Check for new emails and forward them to Discord."""
    try:
        # Select the inbox (or configured folder)
        folder = config.get('folder', 'INBOX')
        mail.select(folder)

        # Search for unread emails using UIDs (stable across session/mailbox changes)
        search_criteria = config.get('search_criteria', 'UNSEEN')
        status, messages = mail.uid('search', None, search_criteria)

        if status != 'OK':
            logger.warning("Failed to search emails")
            return processed_ids

        email_ids = messages[0].split()

        if not email_ids:
            logger.debug("No new emails found")
            return processed_ids

        logger.info(f"Found {len(email_ids)} email(s) matching criteria")

        for email_uid in email_ids:
            uid_str = email_uid.decode()

            # Skip if already processed
            if uid_str in processed_ids:
                continue

            try:
                # Fetch the email by UID
                status, msg_data = mail.uid('fetch', email_uid, '(RFC822)')

                # A UID deleted or moved between SEARCH and FETCH (another mail client,
                # the Gmail web UI, a phone) comes back as OK with [None], not an error
                if status != 'OK' or not msg_data or not isinstance(msg_data[0], (tuple, list)):
                    logger.warning(f"Could not fetch email UID {uid_str} - it may have been moved or deleted")
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
                        processed_ids[uid_str] = None
                        if config.get('mark_as_read', True):
                            mail.uid('store', email_uid, '+FLAGS', '\\Seen')
                        continue

                # Get templates from config
                templates = config.get('templates', {})

                # Send to Discord
                if send_to_discord(config['discord_webhook'], subject, sender, body, html, date, templates, recent_messages):
                    processed_ids[uid_str] = None

                    # Mark as read if configured
                    if config.get('mark_as_read', True):
                        mail.uid('store', email_uid, '+FLAGS', '\\Seen')

                # Small delay between emails to avoid rate limiting
                time.sleep(1)

            except Exception as e:
                logger.error(f"Error processing email UID {uid_str}: {e}")

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

    # Load recent Discord message IDs for editing
    recent_messages = load_recent_messages()

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
            processed_ids = check_for_new_emails(mail, config, processed_ids, recent_messages)

            # Save processed IDs
            save_processed_emails(processed_ids)

            # Wait before next check
            logger.debug(f"Waiting {poll_interval} seconds before next check...")
            time.sleep(poll_interval)

            # Keep connection alive with NOOP
            try:
                mail.noop()
            except Exception:
                # Deliberately not a bare except: that swallows the KeyboardInterrupt
                # from Ctrl+C / a service stop and skips the clean-shutdown save below
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
