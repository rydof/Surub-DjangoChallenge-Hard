from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from simulator.models import DraftIntel, Fight, FightEvent, Person, Unit
from simulator.tasks import (
    download_fight_batch_task,
    download_people_task,
    draft_batch_task,
    draft_people_batch_task,
    enqueue_drafting_batches,
    enqueue_fight_replays,
    follow_intel_chain_task,
    simulate_remote_fight_task,
)


class BackgroundTasksTest(TestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch('simulator.tasks.requests.get')
    def test_download_people_task_registers_people_and_key(self, mock_get):
        people_resp = Mock()
        people_resp.raise_for_status = Mock()
        people_resp.json.return_value = {
            'data': [
                {'id': 0, 'name': 'Brody Sanchez', 'age': 21},
                {'id': 1, 'name': 'Wyatt Harris', 'age': 63},
                {'id': 99, 'name': 'Hellstorm Fireburn', 'age': 24, 'key': 'draft-info'},
            ],
            'status': 200,
        }
        info_resp = Mock()
        info_resp.raise_for_status = Mock()
        info_resp.json.return_value = {
            'data': {'message': 'Drafting will happen as a series of background processes.'},
            'status': 200,
        }
        mock_get.side_effect = [people_resp, info_resp]

        result = download_people_task.delay().get()

        self.assertEqual(Person.objects.count(), 3)
        hellstorm = Person.objects.get(external_id=99)
        self.assertEqual(hellstorm.name, 'Hellstorm Fireburn')
        self.assertEqual(hellstorm.key, 'draft-info')
        self.assertEqual(hellstorm.rank, '')
        self.assertEqual(result['created'], 3)
        self.assertEqual(result['draft_key'], 'draft-info')
        self.assertIn('background processes', result['draft_instructions'])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_draft_people_batch_task_sets_rank_and_creates_units(self):
        p1 = Person.objects.create(external_id=0, name='Young Recruit', age=21)
        p2 = Person.objects.create(external_id=1, name='Veteran', age=58)
        p3 = Person.objects.create(
            external_id=99, name='Hellstorm Fireburn', age=24, key='draft-info'
        )

        result = draft_people_batch_task.delay([p1.id, p2.id, p3.id]).get()

        p1.refresh_from_db()
        p2.refresh_from_db()
        p3.refresh_from_db()

        self.assertEqual(result['drafted'], 3)
        self.assertEqual(p1.rank, Person.Rank.PRIVATE)
        self.assertEqual(p2.rank, Person.Rank.CAPTAIN)
        self.assertEqual(p3.rank, Person.Rank.COMMANDER)

        self.assertEqual(Unit.objects.count(), 3)
        self.assertEqual(p1.unit.team, Unit.Team.BLUE)
        self.assertEqual(p2.unit.team, Unit.Team.RED)
        self.assertEqual(p3.unit.health, 100)
        self.assertGreater(p3.unit.attack_points, p1.unit.attack_points)

    @patch('simulator.tasks.draft_people_batch_task.delay')
    def test_enqueue_drafting_batches_dispatches_separate_task_per_batch(self, mock_delay):
        for i in range(25):
            Person.objects.create(external_id=i, name=f'Person {i}', age=20 + i)

        # Already drafted person should be skipped
        Person.objects.create(
            external_id=100, name='Already Drafted', age=30, rank=Person.Rank.CORPORAL
        )

        mock_delay.side_effect = [Mock(id=f'task-{idx}') for idx in range(3)]

        task_ids = enqueue_drafting_batches(batch_size=10)

        self.assertEqual(len(task_ids), 3)
        self.assertEqual(mock_delay.call_count, 3)
        batch_sizes = [len(call.args[0]) for call in mock_delay.call_args_list]
        self.assertEqual(batch_sizes, [10, 10, 5])

    def test_tasks_configure_celery_retry_policies(self):
        self.assertTrue(download_people_task.retry_backoff)
        self.assertGreaterEqual(download_people_task.max_retries, 3)
        self.assertIn(Exception, download_people_task.autoretry_for)

        self.assertTrue(draft_people_batch_task.retry_backoff)
        self.assertGreaterEqual(draft_people_batch_task.max_retries, 3)
        self.assertIn(Exception, draft_people_batch_task.autoretry_for)

        self.assertTrue(draft_batch_task.retry_backoff)
        self.assertGreaterEqual(draft_batch_task.max_retries, 3)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch('simulator.tasks.requests.get')
    def test_draft_batch_task_does_not_set_short_read_timeout(self, mock_get):
        resp = Mock()
        resp.status_code = 200
        resp.raise_for_status = Mock()
        resp.json.return_value = {'data': []}
        mock_get.return_value = resp

        draft_batch_task.delay(0).get()

        _, kwargs = mock_get.call_args
        timeout = kwargs['timeout']
        read_timeout = timeout[1] if isinstance(timeout, tuple) else timeout
        self.assertTrue(read_timeout is None or read_timeout >= 600)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch('simulator.tasks.requests.get')
    def test_download_fight_batch_task_saves_fights_and_events(self, mock_get):
        p28 = Person.objects.create(external_id=28, name='Avery Nelson', age=63, rank='General')
        p23 = Person.objects.create(external_id=23, name='Cooper Phillips', age=64, rank='Private')
        u28 = Unit.objects.create(
            name='Avery Nelson', unit_type='Knight', team='Blue', health=100, attack_points=50, person=p28
        )
        u23 = Unit.objects.create(
            name='Cooper Phillips', unit_type='Infantry', team='Red', health=100, attack_points=15, person=p23
        )

        fight_resp = Mock()
        fight_resp.status_code = 200
        fight_resp.raise_for_status = Mock()
        fight_resp.json.return_value = {
            'data': [
                {
                    'fight_id': 0,
                    'blue_soldier_id': 28,
                    'red_soldier_id': 23,
                    'steps': [
                        {'attacker': 28, 'defender': 23, 'damage': 8},
                        {'attacker': 23, 'defender': 28, 'damage': 5},
                    ],
                }
            ]
        }
        mock_get.return_value = fight_resp

        res = download_fight_batch_task.delay(0).get()
        self.assertEqual(res['fights_saved'], 1)
        self.assertEqual(res['events_saved'], 2)

        fight = Fight.objects.get(external_fight_id=0)
        self.assertEqual(fight.fighter1, u28)
        self.assertEqual(fight.fighter2, u23)
        self.assertEqual(fight.status, Fight.Status.PENDING)
        self.assertEqual(fight.events.count(), 2)

        sim_res = simulate_remote_fight_task.delay(fight.id).get()
        self.assertEqual(sim_res['status'], 'COMPLETED')
        u28.refresh_from_db()
        u23.refresh_from_db()
        self.assertEqual(u28.health, 95)
        self.assertEqual(u23.health, 92)

    @patch('simulator.tasks.simulate_remote_fight_task.delay')
    def test_enqueue_fight_replays_launches_one_task_per_pending_fight(self, mock_delay):
        u1 = Unit.objects.create(name='B1', unit_type='Knight', team='Blue', health=100, attack_points=30)
        u2 = Unit.objects.create(name='R1', unit_type='Cannon', team='Red', health=100, attack_points=30)
        f1 = Fight.objects.create(external_fight_id=0, fighter1=u1, fighter2=u2, status=Fight.Status.PENDING)
        f2 = Fight.objects.create(external_fight_id=1, fighter1=u1, fighter2=u2, status=Fight.Status.PENDING)
        Fight.objects.create(external_fight_id=2, fighter1=u1, fighter2=u2, status=Fight.Status.COMPLETED)

        mock_delay.side_effect = [Mock(id='sim-1'), Mock(id='sim-2')]
        task_ids = enqueue_fight_replays()

        self.assertEqual(task_ids, ['sim-1', 'sim-2'])
        self.assertEqual(mock_delay.call_count, 2)
        self.assertEqual([c.args[0] for c in mock_delay.call_args_list], [f1.id, f2.id])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch('simulator.tasks.requests.get')
    def test_draft_batch_task_fetches_remote_batch_and_follows_new_key(self, mock_get):
        Person.objects.create(external_id=0, name='Brody Sanchez', age=21)
        Person.objects.create(external_id=1, name='Colton Mitchell', age=34)
        Person.objects.create(
            external_id=99, name='Hellstorm Fireburn', age=24, key='draft-info'
        )

        batch_resp = Mock()
        batch_resp.status_code = 200
        batch_resp.raise_for_status = Mock()
        batch_resp.json.return_value = {
            'data': [
                {'id': 0, 'name': 'Brody Sanchez', 'age': 21, 'health': 100, 'rank': 'Lieutenant'},
                {'id': 1, 'name': 'Colton Mitchell', 'age': 34, 'health': 90, 'rank': 'General'},
                {
                    'id': 99,
                    'name': 'Hellstorm Fireburn',
                    'age': 24,
                    'health': 100,
                    'rank': 'Commander',
                    'key': 'war-ready',
                },
            ],
            'status': 200,
        }
        next_key_resp = Mock()
        next_key_resp.status_code = 200
        next_key_resp.raise_for_status = Mock()
        next_key_resp.json.return_value = {
            'data': {'message': 'All forces drafted! Prepare for war.'},
            'status': 200,
        }
        mock_get.side_effect = [batch_resp, next_key_resp]

        result = draft_batch_task.delay(0).get()

        self.assertEqual(result['batch_index'], 0)
        self.assertEqual(result['drafted'], 3)
        self.assertEqual(result['next_key'], 'war-ready')
        self.assertIn('Prepare for war', result['next_instructions'])

        brody = Person.objects.get(external_id=0)
        colton = Person.objects.get(external_id=1)
        hellstorm = Person.objects.get(external_id=99)
        self.assertEqual(brody.rank, 'Lieutenant')
        self.assertEqual(colton.rank, 'General')
        self.assertEqual(colton.unit.health, 90)
        self.assertEqual(hellstorm.key, 'war-ready')
        self.assertEqual(hellstorm.unit.attack_points, 50)
        intel = DraftIntel.objects.get(key='war-ready')
        self.assertIn('Prepare for war', intel.message)
        self.assertEqual(intel.raw_payload['data']['message'], 'All forces drafted! Prepare for war.')

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch('simulator.tasks.requests.get')
    def test_follow_intel_chain_handles_chained_keys_and_unit_payloads(self, mock_get):
        p = Person.objects.create(external_id=5, name='Michael Adams', age=34, rank='General')
        Unit.objects.create(
            name='Michael Adams',
            unit_type='Knight',
            team='Red',
            health=100,
            attack_points=50,
            person=p,
        )

        step1 = Mock()
        step1.status_code = 200
        step1.raise_for_status = Mock()
        step1.json.return_value = {
            'data': [
                {
                    'id': 5,
                    'name': 'Michael Adams',
                    'type': 'Cannon',
                    'team': 'Blue',
                    'health': 85,
                    'attack_points': 44,
                    'profile_image_link': 'https://example.com/michael.png',
                },
                {
                    'id': 99,
                    'name': 'Hellstorm Fireburn',
                    'key': 'final-order',
                },
            ]
        }

        step2 = Mock()
        step2.status_code = 200
        step2.raise_for_status = Mock()
        step2.json.return_value = {
            'data': {'message': 'Defend the fortress!'}
        }
        mock_get.side_effect = [step1, step2]

        result = follow_intel_chain_task.delay('deploy-units').get()

        self.assertEqual(result['visited_keys'], ['deploy-units', 'final-order'])
        p.unit.refresh_from_db()
        self.assertEqual(p.unit.unit_type, 'Cannon')
        self.assertEqual(p.unit.team, 'Blue')
        self.assertEqual(p.unit.health, 85)
        self.assertEqual(p.unit.attack_points, 44)
        self.assertEqual(p.unit.profile_image_link, 'https://example.com/michael.png')
        self.assertTrue(DraftIntel.objects.filter(key='final-order').exists())
