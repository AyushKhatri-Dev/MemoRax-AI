from django.urls import path
from . import views

urlpatterns = [
    path('check-user/', views.check_user, name='check_user'),
]
