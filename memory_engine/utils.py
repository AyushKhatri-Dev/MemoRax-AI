"""
Utilities for OTP generation, email sending, and device session management
"""
import random
import string
import hashlib
import logging
from datetime import timedelta
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from .models import OTPVerification, DeviceSession, BotUser

logger = logging.getLogger(__name__)


# =============================================
# PHONE NUMBER NORMALIZATION
# =============================================

def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to consistent international format.
    
    Removes 'whatsapp:' prefix and ensures consistent format.
    This prevents duplicate user accounts from phone number format inconsistencies.
    
    Examples:
    - "whatsapp:+919266770651" → "+919266770651"
    - "919266770651" → "+919266770651"  
    - "+919266770651" → "+919266770651"
    - "whatsapp:919266770651" → "+919266770651"
    
    Args:
        phone: Raw phone number string (may have prefixes or inconsistent format)
    
    Returns:
        Normalized phone number with + prefix and country code
    """
    if not phone:
        return phone
    
    # Remove 'whatsapp:' prefix if present
    phone = phone.replace('whatsapp:', '').strip()
    
    # Ensure it has + prefix for international format
    if not phone.startswith('+'):
        # If it's 10 digits (Indian), assume +91 country code
        if len(phone) == 10 and phone.isdigit():
            phone = '+91' + phone
        # If it's already longer digits without +, add + prefix
        elif phone.isdigit() and len(phone) >= 10:
            phone = '+' + phone
    
    return phone


def generate_otp(length=6):
    """Generate a random 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=length))


def generate_session_token(length=64):
    """Generate a random session token"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def get_device_fingerprint(user_agent="", ip_address=""):
    """Create a hash-based device fingerprint from user agent + IP"""
    fingerprint_str = f"{user_agent}{ip_address}"
    return hashlib.sha256(fingerprint_str.encode()).hexdigest()


def send_otp_email(phone: str, email: str, otp_code: str) -> bool:
    """
    Send OTP to user's email
    Returns True if successful, False otherwise
    """
    try:
        subject = "🔐 Your MemoRax Dashboard Login Code"
        message = f"""
Hello,

Your MemoRax dashboard login OTP is:

    {otp_code}

This code is valid for 10 minutes. Do not share it with anyone.

If you didn't request this code, you can safely ignore this email.

---
MemoRax AI Team
"""
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
        logger.info(f"✅ OTP sent to {email} for phone {phone}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send OTP to {email}: {str(e)}")
        return False


def create_otp(phone: str, email: str) -> dict:
    """
    Create a new OTP record and send email
    Returns dict with success status and message
    """
    # Normalize phone number to prevent duplicates
    phone = normalize_phone(phone)
    
    # Clean up expired/verified OTPs AND any existing unverified OTPs
    OTPVerification.objects.filter(
        phone=phone, email=email
    ).exclude(is_verified=True).delete()

    # Check rate limiting - max 3 OTPs per 15 minutes
    recent_otps = OTPVerification.objects.filter(
        phone=phone, email=email, created_at__gte=timezone.now() - timedelta(minutes=15)
    ).count()

    if recent_otps >= 3:
        return {
            "success": False,
            "message": "⏰ Too many OTP requests. Please try again after 15 minutes."
        }

    # Generate OTP
    otp_code = generate_otp()
    expires_at = timezone.now() + timedelta(minutes=10)

    # Save OTP
    otp_obj = OTPVerification.objects.create(
        phone=phone,
        email=email,
        otp_code=otp_code,
        expires_at=expires_at
    )

    # Send email
    if send_otp_email(phone, email, otp_code):
        return {
            "success": True,
            "message": f"✅ OTP sent to {email}",
            "otp_id": otp_obj.id
        }
    else:
        otp_obj.delete()
        return {
            "success": False,
            "message": "❌ Failed to send OTP. Please try again."
        }


def verify_otp(phone: str, email: str, otp_code: str) -> dict:
    """
    Verify OTP and return user + success status
    Returns dict with success, message, and user if successful
    """
    # Normalize phone number to prevent duplicates
    phone = normalize_phone(phone)
    
    # Get latest unverified OTP (use filter + first to handle potential duplicates)
    otp_obj = OTPVerification.objects.filter(
        phone=phone, email=email, is_verified=False
    ).order_by('-created_at').first()
    
    if not otp_obj:
        return {
            "success": False,
            "message": "❌ No active OTP found. Request a new one."
        }

    # Check expiration
    if otp_obj.is_expired():
        return {
            "success": False,
            "message": "⏰ OTP expired. Please request a new one."
        }

    # Check attempts
    if otp_obj.attempts >= otp_obj.max_attempts:
        otp_obj.delete()
        return {
            "success": False,
            "message": "❌ Too many failed attempts. Please request a new OTP."
        }

    # Verify code
    if otp_obj.otp_code != otp_code:
        otp_obj.attempts += 1
        otp_obj.save()
        remaining = otp_obj.max_attempts - otp_obj.attempts
        return {
            "success": False,
            "message": f"❌ Invalid OTP. {remaining} attempts remaining."
        }

    # Mark as verified
    otp_obj.is_verified = True
    otp_obj.save()

    # Get or create user with normalized phone number
    user, created = BotUser.objects.get_or_create(
        phone=phone,
        defaults={"email": email}
    )

    # Update email if it was empty
    if user.email != email:
        user.email = email
        user.save()

    return {
        "success": True,
        "message": "✅ OTP verified successfully!",
        "user": user
    }


def create_device_session(
    user: BotUser,
    user_agent: str = "",
    ip_address: str = "",
    expires_days: int = 30
) -> DeviceSession:
    """
    Create a new device session for the user
    Sessions last 30 days by default
    """
    session_token = generate_session_token()
    device_fingerprint = get_device_fingerprint(user_agent, ip_address)
    expires_at = timezone.now() + timedelta(days=expires_days)

    session = DeviceSession.objects.create(
        user=user,
        session_token=session_token,
        device_fingerprint=device_fingerprint,
        user_agent=user_agent,
        ip_address=ip_address,
        expires_at=expires_at
    )

    logger.info(f"✅ Created device session for {user.phone}")
    return session


def get_session_user(session_token: str) -> dict:
    """
    Validate session token and return user if valid
    Returns dict with success, user, and message
    """
    try:
        session = DeviceSession.objects.get(
            session_token=session_token, is_active=True
        )
    except DeviceSession.DoesNotExist:
        return {
            "success": False,
            "message": "Invalid or expired session."
        }

    # Check if session is valid (not expired)
    if not session.is_valid():
        session.is_active = False
        session.save()
        return {
            "success": False,
            "message": "Session expired. Please log in again."
        }

    # Update last used time
    session.last_used_at = timezone.now()
    session.save()

    return {
        "success": True,
        "user": session.user,
        "session": session
    }


def logout_session(session_token: str) -> bool:
    """Invalidate a device session"""
    try:
        session = DeviceSession.objects.get(session_token=session_token)
        session.is_active = False
        session.save()
        logger.info(f"✅ Logged out device session for {session.user.phone}")
        return True
    except DeviceSession.DoesNotExist:
        return False
