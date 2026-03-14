from django.urls import path
from . import views

urlpatterns = [
    path('webhook/', views.whatsapp_webhook, name='whatsapp_webhook'),
    path('message-status/', views.message_status_callback, name='message_status'),
]
