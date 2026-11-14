from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Person(models.Model):
    class Rank(models.TextChoices):
        PRIVATE = 'Private', 'Private'
        CORPORAL = 'Corporal', 'Corporal'
        SERGEANT = 'Sergeant', 'Sergeant'
        LIEUTENANT = 'Lieutenant', 'Lieutenant'
        CAPTAIN = 'Captain', 'Captain'
        MAJOR = 'Major', 'Major'
        COLONEL = 'Colonel', 'Colonel'
        COMMANDER = 'Commander', 'Commander'
        GENERAL = 'General', 'General'

    external_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=150)
    age = models.PositiveIntegerField()
    key = models.CharField(max_length=100, blank=True, default='')
    rank = models.CharField(
        max_length=50,
        blank=True,
        default='',
    )
    health = models.IntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        ordering = ['external_id']

    def __str__(self) -> str:
        return f'{self.name} (#{self.external_id})'


class DraftIntel(models.Model):
    key = models.CharField(max_length=100, unique=True)
    message = models.TextField(blank=True, default='')
    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    def __str__(self) -> str:
        return f'{self.key}: {self.message[:60]}'


class Unit(models.Model):
    class UnitType(models.TextChoices):
        INFANTRY = 'Infantry', 'Infantry'
        CANNON = 'Cannon', 'Cannon'
        CAVALRY = 'Cavalry', 'Cavalry'
        KNIGHT = 'Knight', 'Knight'

    class Team(models.TextChoices):
        BLUE = 'Blue', 'Blue'
        RED = 'Red', 'Red'

    MAX_HEALTH = 100

    name = models.CharField(max_length=150)
    unit_type = models.CharField(max_length=20, choices=UnitType.choices)
    team = models.CharField(max_length=10, choices=Team.choices)
    health = models.IntegerField(
        default=MAX_HEALTH,
        validators=[MinValueValidator(0), MaxValueValidator(MAX_HEALTH)],
    )
    attack_points = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(50)],
    )
    profile_image_link = models.URLField(max_length=500, blank=True, default='')
    person = models.OneToOneField(
        Person,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='unit',
    )

    class Meta:
        ordering = ['id']

    def __str__(self) -> str:
        return f'{self.name} ({self.team} {self.unit_type})'


class Fight(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'

    external_fight_id = models.IntegerField(null=True, blank=True, unique=True)
    fighter1 = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='fights_as_fighter1',
    )
    fighter2 = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='fights_as_fighter2',
    )
    winner = models.ForeignKey(
        Unit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='fights_won',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self) -> str:
        return f'Fight #{self.id}: {self.fighter1.name} vs {self.fighter2.name}'


class FightEvent(models.Model):
    fight = models.ForeignKey(
        Fight,
        on_delete=models.CASCADE,
        related_name='events',
    )
    event_number = models.PositiveIntegerField()
    attacker = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='events_attacked',
    )
    defender = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='events_defended',
    )
    damage = models.PositiveIntegerField()

    class Meta:
        ordering = ['event_number', 'id']

    def __str__(self) -> str:
        return (
            f'Event {self.event_number}: {self.attacker.name} -> '
            f'{self.defender.name} ({self.damage} dmg)'
        )


class FightStep(models.Model):
    fight = models.ForeignKey(
        Fight,
        on_delete=models.CASCADE,
        related_name='steps',
    )
    step_number = models.PositiveIntegerField()
    attacker = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='attacks_made',
    )
    defender = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='attacks_received',
    )
    attack_points = models.PositiveIntegerField()
    attacker_health = models.PositiveIntegerField()
    defender_health = models.PositiveIntegerField()

    class Meta:
        ordering = ['step_number', 'id']

    def __str__(self) -> str:
        return (
            f'Step {self.step_number}: {self.attacker.name} -> '
            f'{self.defender.name} ({self.attack_points} dmg)'
        )
