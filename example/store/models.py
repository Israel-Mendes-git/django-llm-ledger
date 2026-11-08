from django.db import models


class HandoffRequest(models.Model):
    """A customer asked to talk to a human. In a real shop this would email the support team."""

    created_at = models.DateTimeField(auto_now_add=True)
    email = models.EmailField()
    question = models.TextField()

    def __str__(self):
        return f"{self.email}: {self.question[:40]}"
