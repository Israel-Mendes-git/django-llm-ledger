from django.dispatch import Signal

#: Sent when a call is attempted while the month's spend is at or over budget.
#: kwargs: spent (Decimal), budget (Decimal), feature (str for a feature budget, None for the
#: global one)
budget_exceeded = Signal()
