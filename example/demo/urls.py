from django.contrib import admin
from django.urls import path

from store import views

urlpatterns = [
    path("", views.home, name="home"),
    path("ask/", views.ask, name="ask"),
    path("handoff/", views.handoff, name="handoff"),
    path("admin/", admin.site.urls),
]
