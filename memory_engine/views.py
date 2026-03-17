import re
import os
import time
import hmac
import hashlib
import mimetypes
import uuid
import logging
from datetime import timedelta
from urllib.parse import quote, unquote

from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.utils import timezone
from django.conf import settings

from .models import BotUser, DashboardToken
from .utils import normalize_phone

logger = logging.getLogger(__name__)


# =============================================
# SECURE FILE URL HELPERS
# =============================================

def _file_sig(file_path: str, expiry: int) -> str:
    """HMAC-SHA256 signature for a file path + expiry."""
    msg = f"{file_path}:{expiry}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:40]


def make_file_url(file_path: str, expires_in: int = 86400) -> str:
    """
    Return a signed relative URL for serving a media file securely.
    expires_in: seconds until expiry (default 24h, use 1800 for Twilio)
    """
    expiry = int(time.time()) + expires_in
    sig = _file_sig(file_path, expiry)
    return f"/files/serve/?p={quote(file_path)}&e={expiry}&s={sig}"


def serve_file(request):
    """
    Protected media file serving.
    Validates HMAC signature + expiry before serving the file.
    URL: /files/serve/?p=<path>&e=<expiry>&s=<sig>
    """
    file_path = unquote(request.GET.get('p', ''))
    expiry_str = request.GET.get('e', '')
    sig = request.GET.get('s', '')

    # --- validate params ---
    if not file_path or not expiry_str or not sig:
        return HttpResponse("Missing parameters.", status=400)

    try:
        expiry = int(expiry_str)
    except ValueError:
        return HttpResponse("Invalid token.", status=403)

    # --- check expiry ---
    if time.time() > expiry:
        return HttpResponse("Link expired. Request the file again on WhatsApp.", status=403)

    # --- verify signature ---
    expected = _file_sig(file_path, expiry)
    if not hmac.compare_digest(expected, sig):
        return HttpResponse("Invalid or tampered link.", status=403)

    # --- prevent path traversal ---
    media_root = str(settings.MEDIA_ROOT)
    full_path = os.path.normpath(os.path.join(media_root, file_path))
    if not full_path.startswith(media_root):
        return HttpResponse("Forbidden.", status=403)

    if not os.path.exists(full_path):
        raise Http404("File not found.")

    mime, _ = mimetypes.guess_type(full_path)
    return FileResponse(
        open(full_path, 'rb'),
        content_type=mime or 'application/octet-stream',
        as_attachment=False,
    )


def check_user(request):
    """
    Check if a WhatsApp number has registered with Memoroe.
    If yes, return their dashboard URL.

    GET /api/check-user/?phone=9876543210
    """
    phone = request.GET.get('phone', '').strip()
    if not phone:
        return JsonResponse({'error': 'Phone number required'}, status=400)

    # Normalize phone number to standard format
    phone = normalize_phone(phone)
    
    if not phone:
        return JsonResponse({'error': 'Invalid phone number'}, status=400)

    # Exact match on normalized phone number
    try:
        user = BotUser.objects.get(phone=phone)
    except BotUser.DoesNotExist:
        return JsonResponse({'registered': False})

    # Get existing valid token or create a new one
    token = DashboardToken.objects.filter(
        user=user,
        expires_at__gt=timezone.now(),
        is_used=False
    ).first()

    if not token:
        token_str = uuid.uuid4().hex + uuid.uuid4().hex[:16]
        expires = timezone.now() + timedelta(hours=24)
        token = DashboardToken.objects.create(
            user=user,
            token=token_str,
            expires_at=expires
        )

    return JsonResponse({
        'registered': True,
        'dashboard_url': f'/dash/{token.token}/',
    })
