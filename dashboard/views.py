import logging
import json
from django.shortcuts import render
from django.http import JsonResponse, Http404, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from memory_engine.models import (
    DashboardToken, CalendarEvent, Memory, Reminder, SavedFile, BotUser
)
from memory_engine.utils import (
    create_otp, verify_otp, normalize_phone
)

logger = logging.getLogger(__name__)


def dashboard_landing(request):
    """Landing page — phone input + OTP flow"""
    context = {}
    return render(request, 'dashboard/landing.html', context)


@require_http_methods(["GET"])
def api_whatsapp_info(request):
    """Get Twilio WhatsApp bot info for joining (phone + join code)"""
    # Extract phone number from settings (remove 'whatsapp:' prefix if present)
    whatsapp_number = getattr(settings, 'TWILIO_WHATSAPP_NUMBER', '')
    if whatsapp_number.startswith('whatsapp:'):
        whatsapp_number = whatsapp_number.replace('whatsapp:', '')
    
    # Remove the leading '+' for the URL
    whatsapp_phone_clean = whatsapp_number.lstrip('+')
    
    join_code = getattr(settings, 'TWILIO_JOIN_CODE', '')
    
    return JsonResponse({
        'whatsapp_number': whatsapp_number,
        'whatsapp_phone_clean': whatsapp_phone_clean,
        'join_code': join_code,
        'whatsapp_link': f'https://wa.me/{whatsapp_phone_clean}?text={join_code.replace(" ", "%20")}'
    })


# ==========================================
# OTP AUTHENTICATION API ENDPOINTS
# ==========================================

@csrf_exempt
@require_http_methods(["POST"])
def api_check_phone(request):
    """Check if phone exists and return email info. For new users, pre-create with email."""
    try:
        data = json.loads(request.body)
        phone = normalize_phone(data.get('phone', '').strip())
        email = data.get('email', '').strip()

        if not phone:
            return JsonResponse({
                "success": False,
                "message": "Phone number is required"
            }, status=400)

        # Check if user exists
        try:
            user = BotUser.objects.get(phone=phone)
            # Update email if provided and different
            if email and user.email != email:
                user.email = email
                user.save()
            return JsonResponse({
                "success": True,
                "exists": True,
                "email": user.email or email,
                "name": user.name,
                "message": f"Welcome back! We'll send OTP to {user.email or email}"
            })
        except BotUser.DoesNotExist:
            # PRE-CREATE USER with email for signup flow
            if email:
                user = BotUser.objects.create(phone=phone, email=email)
                logger.info(f"Pre-created user {phone} with email {email}")
                return JsonResponse({
                    "success": True,
                    "exists": False,
                    "email": email,
                    "message": "New user! Email saved. You can now scan the QR code."
                })
            else:
                return JsonResponse({
                    "success": True,
                    "exists": False,
                    "message": "New user! Please provide your email to continue."
                })

    except Exception as e:
        logger.error(f"Error checking phone: {str(e)}")
        return JsonResponse({
            "success": False,
            "message": "❌ Something went wrong"
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_send_otp(request):
    """Send OTP to email"""
    try:
        data = json.loads(request.body)
        phone = normalize_phone(data.get('phone', '').strip())
        email = data.get('email', '').strip()

        if not phone or not email:
            return JsonResponse({
                "success": False,
                "message": "Phone and email are required"
            }, status=400)

        # Validate email format
        if '@' not in email or '.' not in email:
            return JsonResponse({
                "success": False,
                "message": "❌ Invalid email format"
            }, status=400)

        # Create and send OTP
        result = create_otp(phone, email)
        return JsonResponse(result)

    except Exception as e:
        logger.error(f"Error sending OTP: {str(e)}")
        return JsonResponse({
            "success": False,
            "message": "❌ Failed to send OTP"
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_verify_otp(request):
    """Verify OTP and create dashboard token"""
    try:
        data = json.loads(request.body)
        phone = normalize_phone(data.get('phone', '').strip())
        email = data.get('email', '').strip()
        otp_code = data.get('otp', '').strip()

        if not phone or not email or not otp_code:
            return JsonResponse({
                "success": False,
                "message": "Phone, email, and OTP are required"
            }, status=400)

        # Verify OTP
        otp_result = verify_otp(phone, email, otp_code)

        if not otp_result["success"]:
            return JsonResponse({
                "success": False,
                "message": otp_result["message"]
            }, status=400)

        user = otp_result["user"]

        # Create dashboard token (expires in 7 days)
        import secrets
        from datetime import timedelta
        
        token_str = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(days=7)
        
        dashboard_token = DashboardToken.objects.create(
            user=user,
            token=token_str,
            expires_at=expires_at
        )

        return JsonResponse({
            "success": True,
            "message": "✅ Login successful!",
            "token": dashboard_token.token,
            "redirect_url": f"/dash/{dashboard_token.token}/"
        })

    except Exception as e:
        logger.error(f"Error verifying OTP: {str(e)}")
        return JsonResponse({
            "success": False,
            "message": "❌ OTP verification failed"
        }, status=500)


def get_client_ip(request):
    """Get client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


# ==========================================
# SESSION-BASED DASHBOARD VIEWS
# ==========================================


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
        'url_prefix': f'/dash/{token}/',  # For token-based URLs
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
        'url_prefix': f'/dash/{token}/',  # For token-based URLs
    }
    return render(request, 'dashboard/calendar.html', context)


def api_events(request, token):
    """JSON API for FullCalendar - returns events + reminders"""
    # Check if user object is attached (session-based call)
    if hasattr(request, 'user_obj'):
        user = request.user_obj
    else:
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
        'url_prefix': f'/dash/{token}/',  # For token-based URLs
    }
    return render(request, 'dashboard/files.html', context)


def api_files(request, token):
    """JSON API for saved files — returns signed 24h URLs (no direct /media/ exposure)"""
    if hasattr(request, 'user_obj'):
        user = request.user_obj
    else:
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

    # Check if user object is attached (session-based call)
    if hasattr(request, 'user_obj'):
        user = request.user_obj
    else:
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
    # Check if user object is attached (session-based call)
    if hasattr(request, 'user_obj'):
        user = request.user_obj
    else:
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


# ==========================================
# CREATE ENDPOINTS (SESSION-BASED)
# ==========================================

# ==========================================
# CREATE ENDPOINTS (TOKEN-BASED)
# ==========================================

@csrf_exempt
@require_http_methods(["POST"])
def api_create_reminder(request, token):
    """Create reminder from dashboard (token-based)"""
    try:
        user = get_user_from_token(token)
        data = json.loads(request.body)
        
        content = data.get('content', '').strip()
        remind_at_str = data.get('remind_at', '')
        
        if not content:
            return JsonResponse({'success': False, 'error': 'Content required'}, status=400)
        if not remind_at_str:
            return JsonResponse({'success': False, 'error': 'Remind time required'}, status=400)
        
        # Parse datetime
        from datetime import datetime
        try:
            remind_at = datetime.fromisoformat(remind_at_str.replace('Z', '+00:00'))
        except:
            return JsonResponse({'success': False, 'error': 'Invalid datetime format'}, status=400)
        
        reminder = Reminder.objects.create(
            user=user,
            content=content,
            remind_at=remind_at
        )
        
        return JsonResponse({
            'success': True,
            'reminder_id': reminder.id,
            'message': 'Reminder created'
        })
    except Exception as e:
        logger.error(f"Create reminder error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_create_event(request, token):
    """Create calendar event from dashboard (token-based)"""
    try:
        user = get_user_from_token(token)
        data = json.loads(request.body)
        
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        location = data.get('location', '').strip()
        participants = data.get('participants', '').strip()
        color = data.get('color', 'blue')
        start_time_str = data.get('start_time', '')
        end_time_str = data.get('end_time', '')
        
        if not title:
            return JsonResponse({'success': False, 'error': 'Title required'}, status=400)
        if not start_time_str:
            return JsonResponse({'success': False, 'error': 'Start time required'}, status=400)
        
        from datetime import datetime
        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00')) if end_time_str else start_time
        except:
            return JsonResponse({'success': False, 'error': 'Invalid datetime format'}, status=400)
        
        event = CalendarEvent.objects.create(
            user=user,
            title=title,
            description=description,
            location=location,
            participants=participants,
            color=color,
            start_time=start_time,
            end_time=end_time
        )
        
        return JsonResponse({
            'success': True,
            'event_id': event.id,
            'message': 'Event created'
        })
    except Exception as e:
        logger.error(f"Create event error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_upload_file(request, token):
    """Upload file to vault (token-based)"""
    try:
        user = get_user_from_token(token)
        
        if 'file' not in request.FILES:
            return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)
        
        file_obj = request.FILES['file']
        caption = request.POST.get('caption', '')
        
        # Validate file size (10MB max)
        if file_obj.size > 10 * 1024 * 1024:
            return JsonResponse({'success': False, 'error': 'File too large (max 10MB)'}, status=400)
        
        # Save to database
        import os
        filename = f"{user.id}_{file_obj.name}"
        file_path = f"vault/{user.id}/{filename}"
        
        saved_file = SavedFile.objects.create(
            user=user,
            file=file_obj,
            file_path=file_path,
            caption=caption
        )
        
        return JsonResponse({
            'success': True,
            'file_id': saved_file.id,
            'filename': file_obj.name,
            'message': 'File uploaded'
        })
    except Exception as e:
        logger.error(f"Upload file error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ==========================================
# SESSION LOGOUT
# ==========================================

@csrf_exempt
@require_http_methods(["POST"])
def api_logout(request):
    """Log out user by clearing session"""
    request.session.flush()
    return JsonResponse({
        'success': True,
        'message': 'Logged out successfully'
    })


# ==========================================
# USER PROFILE
# ==========================================

def profile_view(request, token):
    """User profile page"""
    user = get_user_from_token(token)
    
    context = {
        'user': user,
        'token': token,
        'url_prefix': f'/dash/{token}/',
        'stats': {
            'total_memories': Memory.objects.filter(user=user, is_deleted=False).count(),
            'total_events': CalendarEvent.objects.filter(user=user).count(),
            'total_reminders': Reminder.objects.filter(user=user).count(),
            'total_files': SavedFile.objects.filter(user=user).count(),
        }
    }
    return render(request, 'dashboard/profile.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def api_update_profile(request, token):
    """Update user profile (name, email)"""
    try:
        user = get_user_from_token(token)
        data = json.loads(request.body)
        
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        reminder_repeat = data.get('reminder_repeat_minutes', 0)
        
        if name:
            user.name = name
        if email and email != user.email:
            # Check if email already exists
            if BotUser.objects.filter(email=email).exclude(id=user.id).exists():
                return JsonResponse({
                    'success': False,
                    'message': '❌ Email already used by another account'
                }, status=400)
            user.email = email
        
        if reminder_repeat is not None:
            user.reminder_repeat_minutes = int(reminder_repeat)
        
        user.save()
        
        return JsonResponse({
            'success': True,
            'message': '✅ Profile updated!',
            'user': {
                'name': user.name,
                'email': user.email,
                'tier': user.tier,
                'reminder_repeat_minutes': user.reminder_repeat_minutes
            }
        })
    
    except Exception as e:
        logger.error(f"Update profile error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_get_profile(request, token):
    """Get user profile data"""
    try:
        user = get_user_from_token(token)
        
        return JsonResponse({
            'success': True,
            'profile': {
                'name': user.name,
                'email': user.email,
                'phone': user.phone,
                'tier': user.tier,
                'memory_count': user.memory_count,
                'reminder_repeat_minutes': user.reminder_repeat_minutes,
                'created_at': user.created_at.isoformat(),
                'is_active': user.is_active,
            },
            'stats': {
                'total_memories': Memory.objects.filter(user=user, is_deleted=False).count(),
                'total_events': CalendarEvent.objects.filter(user=user).count(),
                'total_reminders': Reminder.objects.filter(user=user).count(),
                'total_files': SavedFile.objects.filter(user=user).count(),
            }
        })
    
    except Exception as e:
        logger.error(f"Get profile error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

