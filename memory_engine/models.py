from django.db import models
from django.utils import timezone


class BotUser(models.Model):
    """WhatsApp user - identified by phone number"""
    phone = models.CharField(max_length=30, unique=True, db_index=True)
    name = models.CharField(max_length=100, blank=True, default="")
    tier = models.CharField(
        max_length=10,
        choices=[("free", "Free"), ("pro", "Pro")],
        default="free"
    )
    memory_count = models.IntegerField(default=0)
    messages_today = models.IntegerField(default=0)
    last_message_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    # 0 = send once, >0 = repeat every N minutes until acknowledged
    reminder_repeat_minutes = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.name or 'Unknown'} ({self.phone})"

    def can_send_message(self):
        """Rate limiting check"""
        today = timezone.now().date()
        if self.last_message_date != today:
            self.messages_today = 0
            self.last_message_date = today
            self.save()
        
        max_daily = 200 if self.tier == "pro" else 50
        return self.messages_today < max_daily

    def can_save_memory(self):
        """Memory limit check"""
        max_memories = 10000 if self.tier == "pro" else 50
        return self.memory_count < max_memories

    def increment_message_count(self):
        today = timezone.now().date()
        if self.last_message_date != today:
            self.messages_today = 1
            self.last_message_date = today
        else:
            self.messages_today += 1
        self.save()


class Memory(models.Model):
    """Metadata for each saved memory (actual vector is in ChromaDB)"""
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="memories")
    content_preview = models.CharField(max_length=200)  # First 200 chars
    source = models.CharField(
        max_length=20,
        choices=[
            ("whatsapp", "WhatsApp"),
            ("voice", "Voice Note"),
            ("image", "Image"),
            ("document", "Document"),
            ("web", "Web App"),
        ],
        default="whatsapp"
    )
    tags = models.JSONField(default=list, blank=True)
    chroma_id = models.CharField(max_length=100, unique=True)  # Reference to ChromaDB
    media_path = models.CharField(max_length=500, blank=True, null=True)  # Local path for images/files
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Memories"

    def __str__(self):
        return f"[{self.user.phone}] {self.content_preview[:50]}..."


class ConversationHistory(models.Model):
    """Track conversation context for multi-turn chats"""
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="conversations")
    role = models.CharField(max_length=10, choices=[("user", "User"), ("assistant", "Assistant")])
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.role}] {self.content[:50]}..."


class Reminder(models.Model):
    """Scheduled reminders for users"""
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="reminders")
    content = models.CharField(max_length=500)
    remind_at = models.DateTimeField(db_index=True)
    is_sent = models.BooleanField(default=False)           # first send happened
    is_acknowledged = models.BooleanField(default=False)   # seen (blue tick) or GOT IT
    last_sent_at = models.DateTimeField(null=True, blank=True)  # last repeat time
    send_count = models.IntegerField(default=0)            # how many times sent
    last_twilio_sid = models.CharField(max_length=64, blank=True, default='')  # for read receipt
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['remind_at']

    def __str__(self):
        return f"[{self.user.phone}] {self.content[:50]} @ {self.remind_at}"


class CalendarEvent(models.Model):
    """Calendar events created from WhatsApp messages"""
    COLOR_CHOICES = [
        ("blue", "Blue"),
        ("green", "Green"),
        ("red", "Red"),
        ("purple", "Purple"),
        ("orange", "Orange"),
    ]

    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="calendar_events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    location = models.CharField(max_length=300, blank=True, default="")
    participants = models.JSONField(default=list, blank=True)  # ["Rahul", "Priya"]
    color = models.CharField(max_length=10, choices=COLOR_CHOICES, default="blue")
    source = models.CharField(max_length=20, default="whatsapp")  # whatsapp / dashboard
    reminder_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']

    def __str__(self):
        return f"[{self.user.phone}] {self.title} @ {self.start_time.strftime('%d %b %Y %H:%M')}"


class DashboardToken(models.Model):
    """Magic link tokens for passwordless dashboard login"""
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="tokens")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at

    def __str__(self):
        return f"[{self.user.phone}] Token {'valid' if self.is_valid() else 'expired'}"


class SavedFile(models.Model):
    """Files sent by user on WhatsApp — images, PDFs, notes"""
    FILE_TYPE_CHOICES = [
        ('image', 'Image'),
        ('pdf', 'PDF'),
        ('note', 'Note'),
        ('other', 'Other'),
    ]
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name='saved_files')
    name = models.CharField(max_length=300)               # filename or auto-generated
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default='other')
    file_path = models.CharField(max_length=500)          # relative to MEDIA_ROOT
    caption = models.TextField(blank=True, default='')    # user caption
    ai_description = models.TextField(blank=True, default='')  # AI description
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.user.phone}] {self.name}"





    
