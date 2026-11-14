from contextlib import contextmanager
import random
import threading

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from simulator.models import Fight, FightStep, Person, Unit

_UNIT_LOCKS_GUARD = threading.Lock()
_UNIT_LOCKS: dict[int, threading.Lock] = {}


def _get_unit_lock(unit_id: int) -> threading.Lock:
    with _UNIT_LOCKS_GUARD:
        lock = _UNIT_LOCKS.get(unit_id)
        if lock is None:
            lock = threading.Lock()
            _UNIT_LOCKS[unit_id] = lock
        return lock


@contextmanager
def acquire_unit_locks(*unit_ids: int):
    ordered_ids = sorted(set(unit_ids))
    locks = [_get_unit_lock(uid) for uid in ordered_ids]
    for lk in locks:
        lk.acquire()
    try:
        yield
    finally:
        for lk in reversed(locks):
            lk.release()


def replay_fight_events(fight_id: int) -> Fight:
    fight_meta = Fight.objects.only('id', 'fighter1_id', 'fighter2_id').get(id=fight_id)

    with acquire_unit_locks(fight_meta.fighter1_id, fight_meta.fighter2_id):
        with transaction.atomic():
            fight = (
                Fight.objects.select_related('fighter1', 'fighter2')
                .prefetch_related('events')
                .get(id=fight_id)
            )
            fighter1 = Unit.objects.get(id=fight.fighter1_id)
            fighter2 = Unit.objects.get(id=fight.fighter2_id)
            unit_by_id = {fighter1.id: fighter1, fighter2.id: fighter2}

            fight.steps.all().delete()

            events = list(fight.events.all())
            for idx, ev in enumerate(events, start=1):
                attacker = unit_by_id.get(ev.attacker_id) or Unit.objects.get(id=ev.attacker_id)
                defender = unit_by_id.get(ev.defender_id) or Unit.objects.get(id=ev.defender_id)
                defender.health = max(0, defender.health - ev.damage)

                FightStep.objects.create(
                    fight=fight,
                    step_number=idx,
                    attacker=attacker,
                    defender=defender,
                    attack_points=ev.damage,
                    attacker_health=attacker.health,
                    defender_health=defender.health,
                )

            fighter1.save(update_fields=['health'])
            fighter2.save(update_fields=['health'])

            if fighter1.health >= fighter2.health:
                fight.winner = fighter1
            else:
                fight.winner = fighter2
            fight.status = Fight.Status.COMPLETED
            fight.save(update_fields=['winner', 'status'])
            return fight


RANK_CONFIG = {
    Person.Rank.PRIVATE: (Unit.UnitType.INFANTRY, 15),
    Person.Rank.CORPORAL: (Unit.UnitType.INFANTRY, 22),
    Person.Rank.SERGEANT: (Unit.UnitType.CAVALRY, 28),
    Person.Rank.LIEUTENANT: (Unit.UnitType.CAVALRY, 34),
    Person.Rank.CAPTAIN: (Unit.UnitType.CANNON, 40),
    Person.Rank.MAJOR: (Unit.UnitType.CANNON, 44),
    Person.Rank.COLONEL: (Unit.UnitType.KNIGHT, 47),
    Person.Rank.COMMANDER: (Unit.UnitType.KNIGHT, 50),
    Person.Rank.GENERAL: (Unit.UnitType.KNIGHT, 50),
}


def assign_rank_for_person(person: Person) -> str:
    if person.key:
        return Person.Rank.COMMANDER
    if person.age >= 55:
        return Person.Rank.CAPTAIN
    if person.age >= 45:
        return Person.Rank.LIEUTENANT
    if person.age >= 35:
        return Person.Rank.SERGEANT
    if person.age >= 25:
        return Person.Rank.CORPORAL
    return Person.Rank.PRIVATE


def promote_person_to_unit(person: Person) -> Unit:
    rank = person.rank or assign_rank_for_person(person)
    unit_type, attack_points = RANK_CONFIG.get(rank, (Unit.UnitType.INFANTRY, 25))
    team = Unit.Team.BLUE if person.external_id % 2 == 0 else Unit.Team.RED
    avatar_url = f'https://robohash.org/person-{person.external_id}.png?size=50x50'
    health = max(0, min(Unit.MAX_HEALTH, person.health))

    unit, _ = Unit.objects.update_or_create(
        person=person,
        defaults={
            'name': person.name,
            'unit_type': unit_type,
            'team': team,
            'health': health,
            'attack_points': attack_points,
            'profile_image_link': avatar_url,
        },
    )
    return unit


@transaction.atomic
def simulate_fight(fighter1: Unit, fighter2: Unit, rng=None) -> Fight:
    if fighter1.pk == fighter2.pk:
        raise ValidationError('Please select two different fighters.')
    if fighter1.team == fighter2.team:
        raise ValidationError('Selected fighters must belong to different teams.')
    if fighter1.health <= 0 or fighter2.health <= 0:
        raise ValidationError('Both selected fighters must have health above 0. Heal defeated fighters first.')
    if fighter1.attack_points <= 0 and fighter2.attack_points <= 0:
        raise ValidationError('At least one fighter must have AttackPoints above 0 to start a fight.')

    generator = rng or random
    fight = Fight.objects.create(fighter1=fighter1, fighter2=fighter2)

    attacker, defender = fighter1, fighter2
    step_number = 1

    while fighter1.health > 0 and fighter2.health > 0:
        damage = generator.randint(0, attacker.attack_points)
        defender.health = max(0, defender.health - damage)

        FightStep.objects.create(
            fight=fight,
            step_number=step_number,
            attacker=attacker,
            defender=defender,
            attack_points=damage,
            attacker_health=attacker.health,
            defender_health=defender.health,
        )

        if defender.health <= 0:
            break

        attacker, defender = defender, attacker
        step_number += 1

    fighter1.save(update_fields=['health'])
    fighter2.save(update_fields=['health'])

    fight.winner = fighter1 if fighter1.health > 0 else fighter2
    fight.save(update_fields=['winner'])
    return fight


def compute_team_stats() -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for team in (Unit.Team.BLUE, Unit.Team.RED):
        fights_count = Fight.objects.filter(
            Q(fighter1__team=team) | Q(fighter2__team=team)
        ).distinct().count()

        caused = (
            FightStep.objects.filter(attacker__team=team).aggregate(
                total=Sum('attack_points')
            )['total']
            or 0
        )
        taken = (
            FightStep.objects.filter(defender__team=team).aggregate(
                total=Sum('attack_points')
            )['total']
            or 0
        )

        stats[team] = {
            'fights': fights_count,
            'damage_caused': caused,
            'damage_taken': taken,
        }
    return stats
