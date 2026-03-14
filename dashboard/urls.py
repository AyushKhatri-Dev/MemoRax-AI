from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('<str:token>/', views.dashboard_home, name='home'),
    path('<str:token>/calendar/', views.calendar_view, name='calendar'),
    path('<str:token>/files/', views.files_view, name='files'),
    path('<str:token>/api/events/', views.api_events, name='api_events'),
    path('<str:token>/api/memories/', views.api_memories, name='api_memories'),
    path('<str:token>/api/files/', views.api_files, name='api_files'),
    path('<str:token>/api/settings/', views.api_save_settings, name='api_settings'),
]
