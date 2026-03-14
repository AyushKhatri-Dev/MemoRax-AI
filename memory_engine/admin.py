from django.contrib import admin
from .models import BotUser, Memory, ConversationHistory, Reminder, CalendarEvent, DashboardToken


@admin.register(BotUser)
class BotUserAdmin(admin.ModelAdmin):
    list_display = ['phone', 'name', 'tier', 'memory_count', 'messages_today', 'created_at']
    list_filter = ['tier', 'is_active']
    search_fields = ['phone', 'name']


@admin.register(Memory)
class MemoryAdmin(admin.ModelAdmin):
    list_display = ['user', 'content_preview', 'source', 'created_at', 'is_deleted']
    list_filter = ['source', 'is_deleted', 'created_at']
    search_fields = ['content_preview']


@admin.register(ConversationHistory)
class ConversationHistoryAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'content', 'created_at']
    list_filter = ['role']


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ['user', 'content', 'remind_at', 'is_sent', 'created_at']
    list_filter = ['is_sent', 'remind_at']
    search_fields = ['content']


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ['user', 'title', 'start_time', 'end_time', 'color', 'source', 'created_at']
    list_filter = ['color', 'source', 'start_time']
    search_fields = ['title', 'description', 'location']


@admin.register(DashboardToken)
class DashboardTokenAdmin(admin.ModelAdmin):
    list_display = ['user', 'token', 'created_at', 'expires_at', 'is_used']
    list_filter = ['is_used']
