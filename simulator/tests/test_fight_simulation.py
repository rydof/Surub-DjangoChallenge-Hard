from concurrent.futures import ThreadPoolExecutor
import random

from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TestCase, TransactionTestCase

from simulator.models import Fight, FightEvent, FightStep, Unit
from simulator.services import compute_team_stats, replay_fight_events, simulate_fight


class FightSimulationServiceTest(TestCase):
    def setUp(self):
        self.blue = Unit.objects.create(
            name='Blue Knight',
            unit_type=Unit.UnitType.KNIGHT,
            team=Unit.Team.BLUE,
            health=100,
            attack_points=40,
        )
        self.red = Unit.objects.create(
            name='Red Cannon',
            unit_type=Unit.UnitType.CANNON,
            team=Unit.Team.RED,
            health=100,
            attack_points=35,
        )

    def test_rejects_same_team_fighters(self):
        blue2 = Unit.objects.create(
            name='Blue Infantry',
            unit_type=Unit.UnitType.INFANTRY,
            team=Unit.Team.BLUE,
            health=100,
            attack_points=20,
        )
        with self.assertRaises(ValidationError):
            simulate_fight(self.blue, blue2)

    def test_rejects_defeated_fighter(self):
        self.red.health = 0
        self.red.save()
        with self.assertRaises(ValidationError):
            simulate_fight(self.blue, self.red)

    def test_rejects_zero_attack_stalemate(self):
        self.blue.attack_points = 0
        self.red.attack_points = 0
        self.blue.save()
        self.red.save()
        with self.assertRaises(ValidationError):
            simulate_fight(self.blue, self.red)

    def test_simulate_fight_persists_steps_health_and_winner(self):
        rng = random.Random(42)
        fight = simulate_fight(self.blue, self.red, rng=rng)

        steps = list(fight.steps.all())
        self.assertGreater(len(steps), 0)
        self.assertIn(fight.winner, [self.blue, self.red])

        self.blue.refresh_from_db()
        self.red.refresh_from_db()

        loser = self.red if fight.winner == self.blue else self.blue
        winner = fight.winner
        self.assertEqual(loser.health, 0)
        self.assertGreater(winner.health, 0)

        # Verify steps alternate and final step reflects final health statuses
        for idx, step in enumerate(steps, start=1):
            self.assertEqual(step.step_number, idx)
            self.assertLessEqual(step.attack_points, step.attacker.attack_points)
            if idx % 2 == 1:
                self.assertEqual(step.attacker, self.blue)
                self.assertEqual(step.defender, self.red)
            else:
                self.assertEqual(step.attacker, self.red)
                self.assertEqual(step.defender, self.blue)

        last_step = steps[-1]
        self.assertEqual(last_step.defender_health, 0)
        self.assertEqual(last_step.attacker, winner)

    def test_compute_team_stats_empty_and_after_fights(self):
        empty_stats = compute_team_stats()
        self.assertEqual(
            empty_stats,
            {
                'Blue': {'fights': 0, 'damage_caused': 0, 'damage_taken': 0},
                'Red': {'fights': 0, 'damage_caused': 0, 'damage_taken': 0},
            },
        )

        fight = simulate_fight(self.blue, self.red, rng=random.Random(7))
        blue_dmg = sum(
            s.attack_points
            for s in FightStep.objects.filter(fight=fight, attacker=self.blue)
        )
        red_dmg = sum(
            s.attack_points
            for s in FightStep.objects.filter(fight=fight, attacker=self.red)
        )

        stats = compute_team_stats()
        self.assertEqual(stats['Blue']['fights'], 1)
        self.assertEqual(stats['Red']['fights'], 1)
        self.assertEqual(stats['Blue']['damage_caused'], blue_dmg)
        self.assertEqual(stats['Blue']['damage_taken'], red_dmg)
        self.assertEqual(stats['Red']['damage_caused'], red_dmg)
        self.assertEqual(stats['Red']['damage_taken'], blue_dmg)


class ParallelFightReplayConcurrencyTest(TransactionTestCase):
    def test_replay_fight_events_decreases_health_and_sets_winner(self):
        blue = Unit.objects.create(
            name='Soldier 28', unit_type='Knight', team='Blue', health=100, attack_points=30
        )
        red = Unit.objects.create(
            name='Soldier 23', unit_type='Cannon', team='Red', health=100, attack_points=30
        )
        fight = Fight.objects.create(
            external_fight_id=0,
            fighter1=blue,
            fighter2=red,
            status=Fight.Status.PENDING,
        )
        FightEvent.objects.create(fight=fight, event_number=1, attacker=blue, defender=red, damage=12)
        FightEvent.objects.create(fight=fight, event_number=2, attacker=red, defender=blue, damage=5)
        FightEvent.objects.create(fight=fight, event_number=3, attacker=blue, defender=red, damage=18)

        replayed = replay_fight_events(fight.id)
        blue.refresh_from_db()
        red.refresh_from_db()

        self.assertEqual(replayed.status, Fight.Status.COMPLETED)
        self.assertEqual(blue.health, 95)
        self.assertEqual(red.health, 70)
        self.assertEqual(replayed.winner, blue)
        self.assertEqual(replayed.steps.count(), 3)

    def test_parallel_fight_replays_sharing_soldier_do_not_lose_health_updates(self):
        shared_red = Unit.objects.create(
            name='Shared Red 31', unit_type='Knight', team='Red', health=100, attack_points=30
        )
        fight_ids = []
        for idx in range(5):
            blue = Unit.objects.create(
                name=f'Blue {idx}', unit_type='Cavalry', team='Blue', health=100, attack_points=30
            )
            fight = Fight.objects.create(
                external_fight_id=100 + idx,
                fighter1=blue,
                fighter2=shared_red,
                status=Fight.Status.PENDING,
            )
            FightEvent.objects.create(
                fight=fight, event_number=1, attacker=blue, defender=shared_red, damage=10
            )
            FightEvent.objects.create(
                fight=fight, event_number=2, attacker=shared_red, defender=blue, damage=4
            )
            fight_ids.append(fight.id)

        def run_worker(fid):
            try:
                replay_fight_events(fid)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(run_worker, fight_ids))

        shared_red.refresh_from_db()
        # 5 fights * 10 damage each = 50 total damage -> exact 50 HP remaining
        self.assertEqual(shared_red.health, 50)
        self.assertEqual(Fight.objects.filter(status=Fight.Status.COMPLETED).count(), 5)
