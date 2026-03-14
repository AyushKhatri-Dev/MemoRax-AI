from django.contrib import admin
from django.urls import path, include

from dashboard.views import dashboard_landing
from memory_engine.views import serve_file

urlpatterns = [
    path('admin/', admin.site.urls),
    path('bot/', include('memoroe_bot.urls')),
    path('api/', include('memory_engine.urls')),
    path('dash/', include('dashboard.urls')),
    path('dashboard/', dashboard_landing, name='dashboard_landing'),
    # Protected file serving — HMAC signed URLs only (no public /media/)
    path('files/serve/', serve_file, name='serve_file'),
    path('', dashboard_landing, name='home'),
]
