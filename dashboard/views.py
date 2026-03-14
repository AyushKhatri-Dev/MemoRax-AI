import logging
from django.shortcuts import render
from django.http import JsonResponse, Http404, HttpResponse
from django.utils import timezone

from memory_engine.models import DashboardToken, CalendarEvent, Memory, Reminder, SavedFile

logger = logging.getLogger(__name__)


def dashboard_landing(request):
    """Landing page — number input + join code + auto redirect"""
    from django.conf import settings

    # Twilio number without "whatsapp:" prefix
    raw_number = getattr(settings, 'TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
    twilio_number = raw_number.replace('whatsapp:', '')

    context = {
        'join_code': getattr(settings, 'TWILIO_JOIN_CODE', 'join happy-elephant'),
        'twilio_number': twilio_number,
    }
    return render(request, 'dashboard/landing.html', context)


def get_user_from_token(token_str):
    """Validate token and return user, or raise Http404"""
    try:
        token = DashboardToken.objects.get(token=token_str)
    except DashboardToken.DoesNotExist:
        raise Http404("Invalid or expired link.")

    if not token.is_valid():
        raise Http404("This link has expired. Send /dashboard on WhatsApp to get a new one.")

    return token.user


def dashboard_home(request, token):
    """Main dashboard page"""
    from datetime import timedelta
    from itertools import groupby
    from django.utils.timezone import localtime

    user = get_user_from_token(token)

    total_memories = Memory.objects.filter(user=user, is_deleted=False).count()
    total_events   = CalendarEvent.objects.filter(user=user).count()
    upcoming_events = CalendarEvent.objects.filter(
        user=user, start_time__gte=timezone.now()
    ).order_by('start_time')[:5]
    pending_reminders = Reminder.objects.filter(
        user=user, is_acknowledged=False, remind_at__gte=timezone.now()
    ).order_by('remind_at')[:5]
    recent_memories = Memory.objects.filter(
        user=user, is_deleted=False
    ).order_by('-created_at')[:10]

    # ── ANALYTICS ──────────────────────────────────────────────
    total_messages  = Memory.objects.filter(user=user, is_deleted=False, source='chat').count()
    total_files     = SavedFile.objects.filter(user=user).count()
    total_reminders_set = Reminder.objects.filter(user=user).count()

    analytics = {
        'total_messages':  total_messages,
        'total_memories':  total_memories,
        'total_meetings':  total_events,
        'total_reminders': total_reminders_set,
        'total_files':     total_files,
    }

    # ── ACTIVITY TIMELINE ──────────────────────────────────────
    SOURCE_ICON  = {'image': '📸', 'voice': '🎤', 'document': '📄', 'chat': '💬'}
    SOURCE_LABEL = {'image': 'Image saved', 'voice': 'Voice note saved',
                    'document': 'Document saved', 'chat': 'Message saved'}

    raw = []
    for m in Memory.objects.filter(user=user, is_deleted=False).order_by('-created_at')[:80]:
        raw.append({'dt': m.created_at,
                    'icon': SOURCE_ICON.get(m.source, '💬'),
                    'label': SOURCE_LABEL.get(m.source, 'Memory saved'),
                    'detail': (m.content_preview or '')[:70]})

    for e in CalendarEvent.objects.filter(user=user).order_by('-created_at')[:30]:
        raw.append({'dt': e.created_at, 'icon': '📅',
                    'label': 'Meeting added', 'detail': e.title})

    for r in Reminder.objects.filter(user=user).order_by('-created_at')[:30]:
        raw.append({'dt': r.created_at, 'icon': '⏰',
                    'label': 'Reminder set', 'detail': (r.content or '')[:70]})

    for f in SavedFile.objects.filter(user=user).order_by('-created_at')[:20]:
        raw.append({'dt': f.created_at, 'icon': '📁',
                    'label': 'File saved', 'detail': f.name})

    raw.sort(key=lambda x: x['dt'], reverse=True)

    today     = localtime(timezone.now()).date()
    yesterday = today - timedelta(days=1)

    timeline_grouped = []
    for date_key, items in groupby(raw[:120], key=lambda x: localtime(x['dt']).date()):
        items_list = list(items)
        # attach formatted time to each item
        for item in items_list:
            item['time'] = localtime(item['dt']).strftime('%I:%M %p').lstrip('0')
        if date_key == today:
            display = 'Today'
        elif date_key == yesterday:
            display = 'Yesterday'
        else:
            display = date_key.strftime('%d %b %Y')
        timeline_grouped.append({'date': display, 'items': items_list})

    context = {
        'user': user,
        'token': token,
        'total_memories': total_memories,
        'total_events': total_events,
        'upcoming_events': upcoming_events,
        'pending_reminders': pending_reminders,
        'recent_memories': recent_memories,
        'analytics': analytics,
        'timeline_grouped': timeline_grouped,
    }
    return render(request, 'dashboard/home.html', context)


def calendar_view(request, token):
    """Calendar page"""
    user = get_user_from_token(token)
    context = {
        'user': user,
        'token': token,
    }
    return render(request, 'dashboard/calendar.html', context)


def api_events(request, token):
    """JSON API for FullCalendar - returns events + reminders"""
    user = get_user_from_token(token)

    start = request.GET.get('start')
    end = request.GET.get('end')

    events_qs = CalendarEvent.objects.filter(user=user)
    if start:
        events_qs = events_qs.filter(end_time__gte=start)
    if end:
        events_qs = events_qs.filter(start_time__lte=end)

    events = []
    color_map = {
        'blue': '#3B82F6', 'green': '#10B981', 'red': '#EF4444',
        'purple': '#8B5CF6', 'orange': '#F97316',
    }

    for event in events_qs:
        participants_str = ', '.join(event.participants) if event.participants else ''
        desc = event.description
        if participants_str:
            desc += f"\n👥 {participants_str}"
        if event.location:
            desc += f"\n📍 {event.location}"

        events.append({
            'id': event.id,
            'title': event.title,
            'start': event.start_time.isoformat(),
            'end': event.end_time.isoformat(),
            'color': color_map.get(event.color, '#3B82F6'),
            'extendedProps': {
                'description': desc,
                'location': event.location,
                'participants': event.participants,
                'type': 'event',
            }
        })

    # Add reminders as calendar dots
    reminders_qs = Reminder.objects.filter(user=user, is_sent=False)
    if start:
        reminders_qs = reminders_qs.filter(remind_at__gte=start)
    if end:
        reminders_qs = reminders_qs.filter(remind_at__lte=end)

    for reminder in reminders_qs:
        events.append({
            'id': f'r_{reminder.id}',
            'title': f'⏰ {reminder.content}',
            'start': reminder.remind_at.isoformat(),
            'end': reminder.remind_at.isoformat(),
            'color': '#F59E0B',
            'extendedProps': {
                'description': 'Reminder',
                'location': '',
                'participants': [],
                'type': 'reminder',
            }
        })

    return JsonResponse(events, safe=False)


def files_view(request, token):
    """File vault page"""
    user = get_user_from_token(token)
    context = {
        'user': user,
        'token': token,
    }
    return render(request, 'dashboard/files.html', context)


def api_files(request, token):
    """JSON API for saved files — returns signed 24h URLs (no direct /media/ exposure)"""
    user = get_user_from_token(token)
    file_type = request.GET.get('type', '')

    qs = SavedFile.objects.filter(user=user)
    if file_type:
        qs = qs.filter(file_type=file_type)

    from django.conf import settings
    from memory_engine.views import make_file_url
    base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')

    files = []
    for f in qs[:100]:
        signed = make_file_url(f.file_path, expires_in=86400)  # 24h
        files.append({
            'id': f.id,
            'name': f.name,
            'file_type': f.file_type,
            'url': f"{base_url}{signed}",
            'caption': f.caption,
            'ai_description': f.ai_description,
            'created_at': f.created_at.strftime('%d %b %Y, %I:%M %p'),
        })

    return JsonResponse(files, safe=False)


def api_save_settings(request, token):
    """POST: save user settings (reminder_repeat_minutes)"""
    import json
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    user = get_user_from_token(token)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    repeat_minutes = data.get('reminder_repeat_minutes')
    if repeat_minutes is not None:
        try:
            repeat_minutes = max(0, int(repeat_minutes))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid value'}, status=400)
        user.reminder_repeat_minutes = repeat_minutes
        user.save(update_fields=['reminder_repeat_minutes'])

    return JsonResponse({'ok': True, 'reminder_repeat_minutes': user.reminder_repeat_minutes})


def api_memories(request, token):
    """JSON API for memories list"""
    user = get_user_from_token(token)
    source_filter = request.GET.get('source', '')

    memories_qs = Memory.objects.filter(user=user, is_deleted=False)
    if source_filter:
        memories_qs = memories_qs.filter(source=source_filter)

    memories = []
    for mem in memories_qs[:50]:
        memories.append({
            'id': mem.id,
            'content': mem.content_preview,
            'source': mem.source,
            'tags': mem.tags,
            'created_at': mem.created_at.strftime('%d %b %Y, %I:%M %p'),
        })

    return JsonResponse(memories, safe=False)
