from django.dispatch import Signal

#: Sent when a call is attempted while the month's spend is at or over budget.
#: kwargs: spent (Decimal), budget (Decimal)
budget_exceeded = Signal()
