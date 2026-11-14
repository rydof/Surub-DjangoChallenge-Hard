from django.core.exceptions import ValidationError
from django.test import TestCase

from simulator.models import Fight, FightEvent, FightStep, Person, Unit


class UnitModelTest(TestCase):
    def test_unit_defaults_and_valid_creation(self):
        unit = Unit(
            name='Aldric',
            unit_type=Unit.UnitType.KNIGHT,
            team=Unit.Team.BLUE,
            attack_points=35,
            profile_image_link='https://example.com/aldric.png',
        )
        unit.full_clean()
        unit.save()
        self.assertEqual(unit.health, 100)
        self.assertEqual(unit.unit_type, 'Knight')
        self.assertEqual(unit.team, 'Blue')

    def test_unit_rejects_health_out_of_range(self):
        unit = Unit(
            name='TooHealthy',
            unit_type=Unit.UnitType.INFANTRY,
            team=Unit.Team.RED,
            health=101,
            attack_points=20,
        )
        with self.assertRaises(ValidationError):
            unit.full_clean()

        unit.health = -1
        with self.assertRaises(ValidationError):
            unit.full_clean()

    def test_unit_rejects_attack_points_out_of_range(self):
        unit = Unit(
            name='Overpowered',
            unit_type=Unit.UnitType.CANNON,
            team=Unit.Team.RED,
            health=100,
            attack_points=51,
        )
        with self.assertRaises(ValidationError):
            unit.full_clean()

        unit.attack_points = -1
        with self.assertRaises(ValidationError):
            unit.full_clean()

    def test_unit_rejects_invalid_type_and_team(self):
        unit = Unit(
            name='BadType',
            unit_type='Wizard',
            team='Green',
            health=100,
            attack_points=10,
        )
        with self.assertRaises(ValidationError):
            unit.full_clean()


class PersonAndFightModelTest(TestCase):
    def test_person_supports_rank_field_and_unit_link(self):
        person = Person.objects.create(
            external_id=99,
            name='Hellstorm Fireburn',
            age=24,
            key='draft-info',
            rank='General',
        )
        person.full_clean()
        unit = Unit.objects.create(
            name=person.name,
            unit_type=Unit.UnitType.KNIGHT,
            team=Unit.Team.BLUE,
            health=100,
            attack_points=45,
            person=person,
        )
        self.assertEqual(person.rank, 'General')
        self.assertEqual(person.unit, unit)

    def test_fight_and_steps_ordering(self):
        blue = Unit.objects.create(
            name='BlueOne', unit_type='Infantry', team='Blue', health=100, attack_points=20
        )
        red = Unit.objects.create(
            name='RedOne', unit_type='Cavalry', team='Red', health=100, attack_points=15
        )
        fight = Fight.objects.create(fighter1=blue, fighter2=red, winner=blue)
        step2 = FightStep.objects.create(
            fight=fight,
            step_number=2,
            attacker=red,
            defender=blue,
            attack_points=10,
            attacker_health=80,
            defender_health=90,
        )
        step1 = FightStep.objects.create(
            fight=fight,
            step_number=1,
            attacker=blue,
            defender=red,
            attack_points=20,
            attacker_health=100,
            defender_health=80,
        )
        self.assertEqual(list(fight.steps.all()), [step1, step2])

    def test_fight_supports_external_id_status_and_events(self):
        blue = Unit.objects.create(
            name='Blue28', unit_type='Knight', team='Blue', health=100, attack_points=30
        )
        red = Unit.objects.create(
            name='Red23', unit_type='Cannon', team='Red', health=100, attack_points=30
        )
        fight = Fight.objects.create(
            external_fight_id=0,
            fighter1=blue,
            fighter2=red,
            status=Fight.Status.PENDING,
        )
        ev1 = FightEvent.objects.create(
            fight=fight, event_number=1, attacker=blue, defender=red, damage=8
        )
        ev2 = FightEvent.objects.create(
            fight=fight, event_number=2, attacker=red, defender=blue, damage=5
        )
        self.assertEqual(fight.status, 'PENDING')
        self.assertEqual(list(fight.events.all()), [ev1, ev2])
