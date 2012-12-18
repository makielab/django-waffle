from django.dispatch import Signal

flag_evaluated = Signal(providing_args=["request", "name", "active"])
switch_evaluated = Signal(providing_args=["name", "active"])
sample_evaluated = Signal(providing_args=["name", "active"])
