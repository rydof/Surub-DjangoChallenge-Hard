import random
from unittest.mock import Mock, patch

from django.test import TestCase

from simulator.models import Fight, FightEvent, FightStep, Person, Unit
from simulator.services import simulate_fight


class UnitViewsTest(TestCase):
    def test_unit_create_page_get_shows_appropriate_inputs(self):
        response = self.client.get('/unit')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="name"', html)
        self.assertIn('<select name="unit_type"', html)
        self.assertIn('<select name="team"', html)
        self.assertIn('type="number" name="health"', html)
        self.assertIn('type="number" name="attack_points"', html)
        self.assertIn('>Add<', html)

    def test_unit_create_post_valid_saves_to_db(self):
        response = self.client.post(
            '/unit',
            data={
                'name': 'Sir Cedric',
                'unit_type': 'Knight',
                'team': 'Blue',
                'health': 100,
                'attack_points': 45,
                'profile_image_link': 'https://example.com/cedric.png',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/unit/all')
        unit = Unit.objects.get(name='Sir Cedric')
        self.assertEqual(unit.unit_type, 'Knight')
        self.assertEqual(unit.team, 'Blue')
        self.assertEqual(unit.health, 100)
        self.assertEqual(unit.attack_points, 45)

    def test_unit_create_post_invalid_signals_errors(self):
        response = self.client.post(
            '/unit',
            data={
                'name': 'Invalid Unit',
                'unit_type': 'Dragon',
                'team': 'Blue',
                'health': 150,
                'attack_points': 99,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Unit.objects.count(), 0)
        html = response.content.decode()
        self.assertIn('100', html)
        self.assertIn('50', html)

    def test_unit_all_displays_cards_50x50_avatar_and_conditional_heal_button(self):
        healthy = Unit.objects.create(
            name='Healthy Guard',
            unit_type='Infantry',
            team='Blue',
            health=100,
            attack_points=20,
            profile_image_link='https://example.com/healthy.png',
        )
        injured = Unit.objects.create(
            name='Injured Rider',
            unit_type='Cavalry',
            team='Red',
            health=60,
            attack_points=30,
            profile_image_link='https://example.com/injured.png',
        )

        response = self.client.get('/unit/all')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        self.assertIn('Healthy Guard', html)
        self.assertIn('Injured Rider', html)
        self.assertIn('width="50"', html)
        self.assertIn('height="50"', html)
        self.assertIn(f'/unit/{healthy.id}', html)
        self.assertIn(f'/unit/{injured.id}', html)
        self.assertIn(f'/unit/{injured.id}/heal', html)
        self.assertNotIn(f'/unit/{healthy.id}/heal', html)

    def test_heal_endpoint_restores_health_to_100(self):
        unit = Unit.objects.create(
            name='Wounded Cannon',
            unit_type='Cannon',
            team='Red',
            health=25,
            attack_points=40,
        )
        response = self.client.post(f'/unit/{unit.id}/heal')
        self.assertEqual(response.status_code, 302)
        unit.refresh_from_db()
        self.assertEqual(unit.health, 100)

    def test_simulate_fight_via_unit_all_post_valid_and_invalid(self):
        blue1 = Unit.objects.create(
            name='Blue1', unit_type='Knight', team='Blue', health=100, attack_points=35
        )
        blue2 = Unit.objects.create(
            name='Blue2', unit_type='Infantry', team='Blue', health=100, attack_points=25
        )
        red1 = Unit.objects.create(
            name='Red1', unit_type='Cavalry', team='Red', health=100, attack_points=30
        )

        # Invalid: same team
        bad_resp = self.client.post(
            '/unit/all',
            data={'selected_fighters': [str(blue1.id), str(blue2.id)]},
        )
        self.assertEqual(bad_resp.status_code, 200)
        self.assertEqual(Fight.objects.count(), 0)
        self.assertIn('different teams', bad_resp.content.decode())

        # Valid: different teams
        good_resp = self.client.post(
            '/unit/all',
            data={'selected_fighters': [str(blue1.id), str(red1.id)]},
            follow=True,
        )
        self.assertEqual(good_resp.status_code, 200)
        self.assertEqual(Fight.objects.count(), 1)
        fight = Fight.objects.first()
        html = good_resp.content.decode()
        self.assertIn(fight.winner.name, html)
        self.assertIn('Team Statistics', html)

    def test_unit_detail_shows_all_details_and_fight_history_cards(self):
        blue = Unit.objects.create(
            name='Detail Blue',
            unit_type='Knight',
            team='Blue',
            health=100,
            attack_points=40,
            profile_image_link='https://example.com/blue.png',
        )
        red = Unit.objects.create(
            name='Detail Red',
            unit_type='Cannon',
            team='Red',
            health=100,
            attack_points=30,
            profile_image_link='https://example.com/red.png',
        )
        fight = simulate_fight(blue, red, rng=random.Random(12))

        response = self.client.get(f'/unit/{blue.id}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Detail Blue', html)
        self.assertIn('Knight', html)
        self.assertIn('Blue', html)
        self.assertIn(f'Fight #{fight.id}', html)
        self.assertIn(f'Winner: {fight.winner.name}', html)
        first_step = fight.steps.first()
        self.assertIn(str(first_step.attack_points), html)
        self.assertIn('Damage Dealt', html)
        self.assertIn('Wins', html)


class PeopleViewsTest(TestCase):
    def test_people_page_displays_registered_people_and_ranks(self):
        Person.objects.create(
            external_id=99,
            name='Hellstorm Fireburn',
            age=24,
            key='draft-info',
            rank='Commander',
        )
        response = self.client.get('/people')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Hellstorm Fireburn', html)
        self.assertIn('draft-info', html)
        self.assertIn('Commander', html)

    @patch('simulator.views.download_people_task.delay')
    def test_people_download_post_queues_celery_task(self, mock_delay):
        mock_delay.return_value = Mock(id='dl-task-1')
        response = self.client.post('/people/download')
        self.assertEqual(response.status_code, 302)
        mock_delay.assert_called_once()

    @patch('simulator.views.enqueue_drafting_batches')
    def test_people_draft_post_queues_batch_tasks(self, mock_enqueue):
        mock_enqueue.return_value = ['batch-1', 'batch-2']
        response = self.client.post('/people/draft', data={'batch_size': '10'})
        self.assertEqual(response.status_code, 302)
        mock_enqueue.assert_called_once_with(batch_size=10)

    @patch('simulator.views.enqueue_remote_draft_batches')
    def test_people_draft_remote_mode_queues_remote_batches(self, mock_remote):
        mock_remote.return_value = [f'remote-{i}' for i in range(10)]
        response = self.client.post(
            '/people/draft',
            data={'mode': 'remote', 'num_batches': '10'},
        )
        self.assertEqual(response.status_code, 302)
        mock_remote.assert_called_once_with(num_batches=10)

    @patch('simulator.views.follow_intel_chain_task.delay')
    def test_people_follow_key_post_queues_intel_chain_task(self, mock_follow):
        mock_follow.return_value = Mock(id='intel-1')
        response = self.client.post('/people/follow-key', data={'key': 'war-ready'})
        self.assertEqual(response.status_code, 302)
        mock_follow.assert_called_once_with('war-ready')

    def test_people_reset_clears_people_drafted_units_and_intel_only(self):
        p = Person.objects.create(external_id=1, name='Drafted Guy', age=30, rank='Private')
        Unit.objects.create(
            name='Drafted Guy',
            unit_type='Infantry',
            team='Blue',
            health=100,
            attack_points=15,
            person=p,
        )
        Unit.objects.create(
            name='Manual Guard',
            unit_type='Knight',
            team='Red',
            health=100,
            attack_points=40,
        )

        response = self.client.post('/people/reset')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Unit.objects.count(), 1)
        self.assertEqual(Unit.objects.first().name, 'Manual Guard')


class SimulateViewsTest(TestCase):
    def test_simulate_page_displays_fights_and_results_in_html(self):
        blue = Unit.objects.create(
            name='Blue 28', unit_type='Knight', team='Blue', health=95, attack_points=40
        )
        red = Unit.objects.create(
            name='Red 23', unit_type='Cannon', team='Red', health=92, attack_points=30
        )
        fight = Fight.objects.create(
            external_fight_id=0,
            fighter1=blue,
            fighter2=red,
            winner=blue,
            status=Fight.Status.COMPLETED,
        )
        FightEvent.objects.create(
            fight=fight, event_number=1, attacker=blue, defender=red, damage=8
        )

        response = self.client.get('/simulate')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Blue 28', html)
        self.assertIn('Red 23', html)
        self.assertIn('Winner: Blue 28', html)
        self.assertIn('COMPLETED', html)

    @patch('simulator.views.enqueue_fight_replays')
    def test_simulate_post_launches_parallel_fight_tasks(self, mock_enqueue):
        mock_enqueue.return_value = ['f-1', 'f-2']
        response = self.client.post('/simulate')
        self.assertEqual(response.status_code, 302)
        mock_enqueue.assert_called_once()

    @patch('simulator.views.enqueue_remote_fight_batches')
    def test_simulate_fetch_post_queues_fight_batch_downloads(self, mock_fetch):
        mock_fetch.return_value = ['fb-0', 'fb-1']
        response = self.client.post('/simulate/fetch', data={'num_batches': '2'})
        self.assertEqual(response.status_code, 302)
        mock_fetch.assert_called_once_with(num_batches=2)

    def test_simulate_clear_deletes_all_history_and_restores_units(self):
        blue = Unit.objects.create(
            name='Clear Blue', unit_type='Knight', team='Blue', health=35, attack_points=40
        )
        red = Unit.objects.create(
            name='Clear Red', unit_type='Cannon', team='Red', health=12, attack_points=30
        )
        fight = Fight.objects.create(
            external_fight_id=1,
            fighter1=blue,
            fighter2=red,
            winner=blue,
            status=Fight.Status.COMPLETED,
        )
        FightEvent.objects.create(
            fight=fight, event_number=1, attacker=blue, defender=red, damage=8
        )
        FightStep.objects.create(
            fight=fight,
            step_number=1,
            attacker=blue,
            defender=red,
            attack_points=8,
            attacker_health=35,
            defender_health=4,
        )
        session = self.client.session
        session['simulate_task_ids'] = ['task-1']
        session.save()

        response = self.client.post('/simulate/clear')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/simulate')
        self.assertEqual(Fight.objects.count(), 0)
        self.assertEqual(FightEvent.objects.count(), 0)
        self.assertEqual(FightStep.objects.count(), 0)
        self.assertEqual(Unit.objects.filter(health=100).count(), 2)
        self.assertNotIn('simulate_task_ids', self.client.session)

    def test_simulate_clear_get_not_allowed(self):
        response = self.client.get('/simulate/clear')
        self.assertEqual(response.status_code, 405)
