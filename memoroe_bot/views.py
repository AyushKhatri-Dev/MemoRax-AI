"""
🤖 MemoRax AI - WhatsApp Bot Webhook
Handles all incoming WhatsApp messages via Twilio
"""
import uuid
import logging
from datetime import timedelta
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from django.utils import timezone
from twilio.rest import Client
from twilio.request_validator import RequestValidator

from memory_engine.models import BotUser, DashboardToken
from memory_engine.brain import MemoRaxBrain

logger = logging.getLogger(__name__)

# Initialize Twilio client
twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

# Initialize Memory Brain
brain = MemoRaxBrain()


# =============================================
# HELPERS
# =============================================
def _generate_files_link(user: BotUser) -> str:
    """Create a magic link token and return files/vault URL"""
    token_str = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    expires = timezone.now() + timedelta(hours=24)
    DashboardToken.objects.create(user=user, token=token_str, expires_at=expires)
    base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
    url = f"{base_url}/dash/{token_str}/files/"
    return (
        f"📁 *Your File Vault:*\n\n"
        f"{url}\n\n"
        f"_All your images, PDFs & notes in one place._\n"
        f"_Link valid for 24 hours._"
    )


def _generate_dashboard_link(user: BotUser, go_to_calendar: bool = False) -> str:
    """Create a magic link token and return dashboard URL"""
    token_str = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    expires = timezone.now() + timedelta(hours=24)
    DashboardToken.objects.create(user=user, token=token_str, expires_at=expires)

    base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
    if go_to_calendar:
        url = f"{base_url}/dash/{token_str}/calendar/"
        return (
            f"📅 *Your Calendar:*\n\n"
            f"{url}\n\n"
            f"_Link valid for 24 hours. Send /calendar anytime for a new link._"
        )
    url = f"{base_url}/dash/{token_str}/"
    return (
        f"🧠 *Your MemoRax Dashboard:*\n\n"
        f"{url}\n\n"
        f"📅 Tap 'Calendar' inside to see all your events!\n"
        f"_Link valid for 24 hours. Send /dashboard anytime for a new link._"
    )


# =============================================
# COMMAND DEFINITIONS
# =============================================
HELP_TEXT = """🧠 *MemoRax AI - Your Memory Assistant*

📌 *Commands:*

/save <text> — Save a memory
/ask <question> — Ask your memories
/list — Show recent memories
/delete <number> — Delete a memory
/search <tag> — Search by tag
/stats — Your memory stats
/dashboard — Open your personal dashboard
/calendar — Open calendar view
/files — View your file vault (images, PDFs)
/help — Show this menu

💡 *Or just send any message!*
I'll figure out if you want to save, search, or add to calendar.

🎤 Voice notes — I'll transcribe & process them
📸 Images — I'll analyze & save them
📅 Meetings — I'll add to your calendar automatically!

_Powered by MemoRax AI ✨_"""

WELCOME_TEXT = """👋 *Welcome to MemoRax AI!*

I'm your personal memory assistant on WhatsApp. 🧠

Here's what I can do:
✅ Save anything you want to remember
✅ Find your memories instantly with AI
✅ Auto-tag and organize your thoughts
✅ Understand voice notes & documents

*Quick start:*
1️⃣ Type: /save Buy groceries tomorrow at 5pm
2️⃣ Later ask: /ask What do I need to do tomorrow?

Type /help for all commands 🚀"""


# =============================================
# MAIN WEBHOOK
# =============================================
@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    """
    Main webhook endpoint - receives all WhatsApp messages from Twilio
    URL: /bot/webhook/
    """
    try:
        # Extract message data from Twilio
        user_phone = request.POST.get("From", "")
        user_msg = request.POST.get("Body", "").strip()
        user_name = request.POST.get("ProfileName", "")
        num_media = int(request.POST.get("NumMedia", 0))

        if not user_phone:
            return HttpResponse("No phone", status=400)

        logger.info(f"Message from {user_phone}: {user_msg[:50]}")

        # Get or create user
        user, is_new = BotUser.objects.get_or_create(
            phone=user_phone,
            defaults={"name": user_name}
        )

        # Update name if changed
        if user_name and user.name != user_name:
            user.name = user_name
            user.save()

        # Rate limiting check
        if not user.can_send_message():
            reply = "⚠️ Daily message limit reached. Upgrade to Pro for more!\nType /upgrade for details."
            send_whatsapp(user_phone, reply)
            return HttpResponse("OK", status=200)

        # Increment message count
        user.increment_message_count()

        # Welcome new users
        if is_new:
            send_whatsapp(user_phone, WELCOME_TEXT)
            return HttpResponse("OK", status=200)

        # Handle media (voice notes, images, documents)
        if num_media > 0:
            reply = handle_media(request, user)
            send_whatsapp(user_phone, reply)
            return HttpResponse("OK", status=200)

        # Process text commands
        reply = process_command(user, user_msg)

        # Check if reply is a dict (for image retrieval)
        if isinstance(reply, dict):
            if reply.get("success") and reply.get("media_path"):
                # Send image
                send_whatsapp_media(user_phone, reply["media_path"], reply.get("description", ""))
            else:
                # Send error message
                send_whatsapp(user_phone, reply.get("message", "Sorry, something went wrong."))
        else:
            # Normal text reply
            send_whatsapp(user_phone, reply)

        return HttpResponse("OK", status=200)

    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return HttpResponse("Error", status=500)


# =============================================
# COMMAND PROCESSOR
# =============================================
def process_command(user: BotUser, message: str) -> str:
    """Route message to appropriate handler"""

    msg_lower = message.lower().strip()

    # /help
    if msg_lower in ["/help", "help", "/start"]:
        return HELP_TEXT

    # /save <content>
    elif msg_lower.startswith("/save"):
        content = message[5:].strip()
        if not content:
            return "💡 Usage: /save <your memory>\n\nExample:\n/save Meeting with client tomorrow at 2pm"
        result = brain.save_memory(user, content)
        return result["message"]

    # /ask <query>
    elif msg_lower.startswith("/ask"):
        query = message[4:].strip()
        if not query:
            return "💡 Usage: /ask <your question>\n\nExample:\n/ask When is my meeting?"
        return brain.query_memory(user, query)

    # /list
    elif msg_lower.startswith("/list"):
        return brain.get_recent_memories(user)

    # /delete <number>
    elif msg_lower.startswith("/delete"):
        try:
            index = int(message[7:].strip())
            return brain.delete_memory(user, index)
        except (ValueError, IndexError):
            return "💡 Usage: /delete <number>\n\nFirst type /list to see memory numbers."

    # /search <tag>
    elif msg_lower.startswith("/search"):
        tag = message[7:].strip()
        if not tag:
            return "💡 Usage: /search <tag>\n\nExample: /search meeting"
        return brain.search_by_tag(user, tag)

    # /stats
    elif msg_lower in ["/stats", "/status"]:
        return brain.get_stats(user)

    # /upgrade
    elif msg_lower == "/upgrade":
        return """⭐ *MemoRax Pro - ₹199/month*

✅ Unlimited memories (vs 50 free)
✅ Voice note transcription
✅ Document processing (PDF, images)
✅ Priority AI responses
✅ Custom tags & categories
✅ Export your memories

💳 Pay here: [Payment link coming soon]

_Or reply UPGRADE to get started!_"""

    # /dashboard
    elif msg_lower in ["/dashboard", "/dash", "dashboard"]:
        return _generate_dashboard_link(user)

    # /calendar
    elif msg_lower in ["/calendar", "/cal"]:
        return _generate_dashboard_link(user, go_to_calendar=True)

    # /files
    elif msg_lower in ["/files", "/vault", "/docs"]:
        return _generate_files_link(user)

    # No command - smart chat mode
    else:
        return brain.smart_chat(user, message)


# =============================================
# MEDIA HANDLER
# =============================================
def handle_media(request, user: BotUser) -> str:
    """Handle voice notes, images, and documents"""
    media_type = request.POST.get("MediaContentType0", "")
    media_url = request.POST.get("MediaUrl0", "")
    caption = request.POST.get("Body", "").strip()

    if "audio" in media_type:
        return brain.transcribe_voice(user, media_url, caption)

    elif "image" in media_type:
        return brain.analyze_image(user, media_url, media_type, caption)

    elif "pdf" in media_type or "document" in media_type or "msword" in media_type or "officedocument" in media_type:
        return brain.save_document(user, media_url, media_type, caption)

    else:
        return "I can handle text and images right now. Try sending a text message or a photo!"


# =============================================
# SEND WHATSAPP MESSAGE
# =============================================
def send_whatsapp(to: str, body: str):
    """Send a WhatsApp message via Twilio"""
    try:
        # Twilio has 1600 char limit per message
        if len(body) > 1500:
            # Split long messages
            chunks = [body[i:i+1500] for i in range(0, len(body), 1500)]
            for chunk in chunks:
                twilio_client.messages.create(
                    body=chunk,
                    from_=settings.TWILIO_WHATSAPP_NUMBER,
                    to=to
                )
        else:
            twilio_client.messages.create(
                body=body,
                from_=settings.TWILIO_WHATSAPP_NUMBER,
                to=to
            )
        logger.info(f"Sent message to {to}: {body[:50]}...")
    except Exception as e:
        logger.error(f"Twilio send error: {e}")


def send_whatsapp_media(to: str, media_path: str, caption: str = ""):
    """Send media back to user.
    - Images + PDFs → Twilio media_url (WhatsApp renders them natively)
    - docx/other   → signed download link as text (WhatsApp doesn't support these)
    Files are served via HMAC-signed URLs — direct /media/ access is disabled.
    """
    import os
    from memory_engine.views import make_file_url

    full_path = os.path.join(settings.MEDIA_ROOT, media_path)
    if not os.path.exists(full_path):
        logger.error(f"Media file not found: {full_path}")
        send_whatsapp(to, "📭 Sorry, that file is missing from the vault.")
        return

    base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
    # Signed URL valid 30 min — enough for Twilio to fetch + deliver
    public_url = f"{base_url}{make_file_url(media_path, expires_in=1800)}"
    ext = os.path.splitext(media_path)[1].lower()

    # WhatsApp supports: images + PDF via Twilio media_url
    twilio_supported = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf'}

    if ext in twilio_supported:
        icon = '📸' if ext != '.pdf' else '📄'
        try:
            twilio_client.messages.create(
                body=caption or f"{icon} Here's your file!",
                media_url=[public_url],
                from_=settings.TWILIO_WHATSAPP_NUMBER,
                to=to
            )
            logger.info(f"Media sent to {to}: {public_url}")
            return
        except Exception as e:
            logger.error(f"Twilio media_url send error: {e}")
            # fall through to link fallback

    # docx, doc, txt, and unsupported formats → send download link
    filename = os.path.basename(media_path)
    icon = '📝' if ext in {'.docx', '.doc', '.txt'} else '📎'
    msg = f"{icon} *{filename}*\n"
    if caption:
        msg += f"{caption}\n"
    msg += f"\n🔗 Download link:\n{public_url}\n\n_Open in browser to download._"
    send_whatsapp(to, msg)


# =============================================
# TWILIO MESSAGE STATUS CALLBACK
# =============================================
@csrf_exempt
def message_status_callback(request):
    """
    Twilio calls this URL with delivery/read status updates.
    When MessageStatus=read → user has opened the reminder → auto-acknowledge it.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    message_sid    = request.POST.get('MessageSid', '')
    message_status = request.POST.get('MessageStatus', '')

    logger.info(f"Status callback: SID={message_sid} status={message_status}")

    if message_status == 'read' and message_sid:
        try:
            from memory_engine.models import Reminder
            reminder = Reminder.objects.filter(
                last_twilio_sid=message_sid,
                is_acknowledged=False,
            ).first()
            if reminder:
                reminder.is_acknowledged = True
                reminder.save(update_fields=['is_acknowledged'])
                logger.info(f"Reminder {reminder.id} auto-acknowledged via read receipt")
        except Exception as e:
            logger.error(f"Status callback error: {e}")

    return HttpResponse(status=204)
