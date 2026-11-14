from django.contrib import admin

from simulator.models import DraftIntel, Fight, FightEvent, FightStep, Person, Unit

admin.site.register(Person)
admin.site.register(DraftIntel)
admin.site.register(Unit)
admin.site.register(Fight)
admin.site.register(FightEvent)
admin.site.register(FightStep)
