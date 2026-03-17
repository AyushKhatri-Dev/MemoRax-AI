from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    # OTP-based auth flow
    path('', views.dashboard_landing, name='landing'),
    path('api/whatsapp-info/', views.api_whatsapp_info, name='api_whatsapp_info'),
    path('api/check-phone/', views.api_check_phone, name='api_check_phone'),
    path('api/send-otp/', views.api_send_otp, name='api_send_otp'),
    path('api/verify-otp/', views.api_verify_otp, name='api_verify_otp'),
    path('api/logout/', views.api_logout, name='api_logout'),
    
    # Token-based dashboard (all access via token from WhatsApp /dashboard command or OTP)
    path('<str:token>/', views.dashboard_home, name='home'),
    path('<str:token>/calendar/', views.calendar_view, name='calendar'),
    path('<str:token>/files/', views.files_view, name='files'),
    path('<str:token>/profile/', views.profile_view, name='profile'),
    path('<str:token>/api/events/', views.api_events, name='api_events'),
    path('<str:token>/api/memories/', views.api_memories, name='api_memories'),
    path('<str:token>/api/files/', views.api_files, name='api_files'),
    path('<str:token>/api/settings/', views.api_save_settings, name='api_settings'),
    path('<str:token>/api/profile/', views.api_get_profile, name='api_profile'),
    path('<str:token>/api/profile/update/', views.api_update_profile, name='api_update_profile'),
    path('<str:token>/api/create-reminder/', views.api_create_reminder, name='api_create_reminder'),
    path('<str:token>/api/create-event/', views.api_create_event, name='api_create_event'),
    path('<str:token>/api/upload-file/', views.api_upload_file, name='api_upload_file'),
]
