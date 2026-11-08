from django.contrib import admin

from .models import HandoffRequest

admin.site.register(HandoffRequest, list_display=("created_at", "email", "question"))
