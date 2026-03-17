"""
Memoroe AI - Background Reminder Scheduler
Checks for due reminders every 60 seconds and sends WhatsApp notifications.
"""
import logging
from django.conf import settings
from django.utils import timezone
from apscheduler.schedulers.background import BackgroundScheduler
from twilio.rest import Client

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def check_and_send_reminders():
    """Send due reminders; repeat per user's reminder_repeat_minutes setting."""
    from datetime import timedelta
    from django.db.models import Q
    from memory_engine.models import Reminder

    now = timezone.now()

    # First pass: get all due, unacknowledged, never-sent reminders
    # Second pass: also get reminders that need repeating (user has repeat enabled)
    due = Reminder.objects.filter(
        remind_at__lte=now,
        is_acknowledged=False,
        last_sent_at__isnull=True,   # never sent yet
    ).select_related('user')

    # Also fetch reminders that were sent but user wants repeats
    repeat_candidates = Reminder.objects.filter(
        remind_at__lte=now,
        is_acknowledged=False,
        last_sent_at__isnull=False,  # already sent at least once
        user__reminder_repeat_minutes__gt=0,  # user has repeat enabled
    ).select_related('user')

    # Filter repeat_candidates by each user's custom interval
    repeat_due = []
    for reminder in repeat_candidates:
        interval_secs = reminder.user.reminder_repeat_minutes * 60
        cutoff = now - timedelta(seconds=interval_secs)
        if reminder.last_sent_at <= cutoff:
            repeat_due.append(reminder)

    # Combine: unsent + repeat-due
    all_due = list(due) + repeat_due

    if not all_due:
        return

    try:
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    except Exception as e:
        logger.error(f"Twilio client init failed: {e}")
        return

    for reminder in all_due:
        try:
            remind_ist = timezone.localtime(reminder.remind_at)
            repeat_note = f"\n_(Reminder #{reminder.send_count + 1})_" if reminder.send_count > 0 else ""

            message_body = (
                f"⏰ *Reminder!*\n\n"
                f"📝 {reminder.content}\n\n"
                f"🕐 {remind_ist.strftime('%d %b, %I:%M %p')}"
                f"{repeat_note}\n\n"
                f"✅ Reply *GOT IT* to dismiss"
            )

            # Ensure phone has whatsapp: prefix for Twilio API
            to_phone = reminder.user.phone
            if not to_phone.startswith('whatsapp:'):
                to_phone = f'whatsapp:{to_phone}'

            # status_callback receives "read" event when user opens the message
            base_url = getattr(settings, 'BASE_URL', '')
            callback_url = f"{base_url}/bot/message-status/" if base_url else None

            kwargs = dict(
                body=message_body,
                from_=settings.TWILIO_WHATSAPP_NUMBER,
                to=to_phone,
            )
            if callback_url:
                kwargs['status_callback'] = callback_url

            msg = client.messages.create(**kwargs)

            reminder.is_sent = True
            reminder.last_sent_at = now
            reminder.send_count += 1
            reminder.last_twilio_sid = msg.sid
            reminder.save(update_fields=['is_sent', 'last_sent_at', 'send_count', 'last_twilio_sid'])

            logger.info(f"Reminder #{reminder.send_count} sent to {reminder.user.phone}: {reminder.content[:40]}")
        except Exception as e:
            logger.error(f"Failed to send reminder {reminder.id}: {e}")


def start_scheduler():
    """Start the background scheduler"""
    if not scheduler.running:
        scheduler.add_job(
            check_and_send_reminders,
            'interval',
            seconds=30,
            id='check_reminders',
            replace_existing=True
        )
        scheduler.start()
        logger.info("Reminder scheduler started")
