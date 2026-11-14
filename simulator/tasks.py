import math

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction

from simulator.models import DraftIntel, Fight, FightEvent, Person, Unit
from simulator.services import assign_rank_for_person, promote_person_to_unit, replay_fight_events


def _extract_unit_fields(entry: dict) -> dict:
    fields: dict = {}
    raw_type = entry.get('type') or entry.get('unit_type') or entry.get('Type')
    if raw_type in Unit.UnitType.values:
        fields['unit_type'] = raw_type

    raw_team = entry.get('team') or entry.get('Team')
    if raw_team in Unit.Team.values:
        fields['team'] = raw_team

    raw_hp = entry.get('health') if 'health' in entry else entry.get('Health')
    if raw_hp is not None:
        fields['health'] = max(0, min(Unit.MAX_HEALTH, int(raw_hp)))

    raw_atk = (
        entry.get('attack_points')
        if 'attack_points' in entry
        else entry.get('attackPoints', entry.get('AttackPoints'))
    )
    if raw_atk is not None:
        fields['attack_points'] = max(0, min(50, int(raw_atk)))

    raw_img = (
        entry.get('profile_image_link')
        or entry.get('profileImageLink')
        or entry.get('ProfileImageLink')
    )
    if raw_img:
        fields['profile_image_link'] = str(raw_img)

    return fields


def _apply_intel_payload(payload: dict) -> tuple[str, list[str]]:
    data = payload.get('data', payload)
    discovered_keys: list[str] = []

    if isinstance(payload.get('key'), str) and payload['key']:
        discovered_keys.append(payload['key'])

    if isinstance(data, dict):
        if isinstance(data.get('key'), str) and data['key']:
            discovered_keys.append(data['key'])
        message = data.get('message', '') or payload.get('message', '') or str(data)
        return str(message), discovered_keys

    if isinstance(data, list):
        applied_units = 0
        with transaction.atomic():
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get('key'), str) and entry['key']:
                    discovered_keys.append(entry['key'])

                unit_fields = _extract_unit_fields(entry)
                ext_id = entry.get('id')
                name = entry.get('name') or entry.get('Name')
                rank = entry.get('rank', '')

                person = None
                if ext_id is not None:
                    person = Person.objects.filter(external_id=ext_id).first()
                    if person:
                        changed = False
                        if rank and person.rank != rank:
                            person.rank = rank
                            changed = True
                        if 'health' in unit_fields and person.health != unit_fields['health']:
                            person.health = unit_fields['health']
                            changed = True
                        if entry.get('key') and person.key != entry['key']:
                            person.key = entry['key']
                            changed = True
                        if changed:
                            person.save()

                if unit_fields:
                    unit = getattr(person, 'unit', None) if person else None
                    if unit is None and name:
                        unit = Unit.objects.filter(name=name).first()
                    if unit is not None:
                        for k, v in unit_fields.items():
                            setattr(unit, k, v)
                        unit.save()
                        applied_units += 1
                    elif name and 'unit_type' in unit_fields and 'team' in unit_fields:
                        Unit.objects.create(
                            name=name,
                            unit_type=unit_fields['unit_type'],
                            team=unit_fields['team'],
                            health=unit_fields.get('health', Unit.MAX_HEALTH),
                            attack_points=unit_fields.get('attack_points', 25),
                            profile_image_link=unit_fields.get('profile_image_link', ''),
                            person=person,
                        )
                        applied_units += 1

        return f'Received {len(data)} record(s); updated/created {applied_units} unit(s).', discovered_keys

    return str(data), discovered_keys


def _get_api_timeout():
    return getattr(settings, 'PEOPLE_API_TIMEOUT', (15, None))


def _fetch_intel_step(api_base: str, key: str) -> tuple[str, list[str]]:
    if not key:
        return '', []
    resp = requests.get(f'{api_base}/{key}', timeout=_get_api_timeout())
    if resp.status_code == 404:
        return 'Endpoint returned 404.', []
    resp.raise_for_status()
    payload = resp.json()
    message, discovered_keys = _apply_intel_payload(payload)
    DraftIntel.objects.update_or_create(
        key=key,
        defaults={'message': message, 'raw_payload': payload},
    )
    return message, discovered_keys


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=8,
)
def follow_intel_chain_task(
    start_key: str,
    base_url: str | None = None,
    max_depth: int = 10,
) -> dict:
    api_base = (base_url or settings.PEOPLE_API_BASE_URL).rstrip('/')
    visited: list[str] = []
    queue: list[str] = [start_key] if start_key else []
    last_message = ''

    while queue and len(visited) < max_depth:
        current_key = queue.pop(0)
        if not current_key or current_key in visited or current_key == 'draft-info':
            continue
        visited.append(current_key)
        last_message, discovered = _fetch_intel_step(api_base, current_key)
        for next_key in discovered:
            if next_key not in visited and next_key not in queue and next_key != 'draft-info':
                queue.append(next_key)

    return {
        'visited_keys': visited,
        'last_message': last_message,
    }


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=8,
)
def download_people_task(
    base_url: str | None = None,
    auto_draft: bool = False,
    batch_size: int = 10,
) -> dict:
    api_base = (base_url or settings.PEOPLE_API_BASE_URL).rstrip('/')
    response = requests.get(f'{api_base}/people', timeout=_get_api_timeout())
    response.raise_for_status()
    payload = response.json()
    people_data = payload.get('data', [])

    created = 0
    updated = 0
    draft_key = ''
    draft_task_ids: list[str] = []

    for batch_index, start in enumerate(range(0, len(people_data), batch_size)):
        chunk = people_data[start : start + batch_size]
        chunk_ids: list[int] = []
        with transaction.atomic():
            for entry in chunk:
                key = entry.get('key', '') or ''
                if key and not draft_key:
                    draft_key = key

                person, was_created = Person.objects.update_or_create(
                    external_id=entry['id'],
                    defaults={
                        'name': entry['name'],
                        'age': entry['age'],
                        'key': key,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
                chunk_ids.append(person.id)

        if auto_draft and chunk_ids:
            async_res = draft_batch_task.delay(batch_index, base_url=api_base)
            draft_task_ids.append(str(async_res.id))

    draft_instructions = ''
    if draft_key:
        draft_instructions, _ = _fetch_intel_step(api_base, draft_key)

    return {
        'total': len(people_data),
        'created': created,
        'updated': updated,
        'draft_key': draft_key,
        'draft_instructions': draft_instructions,
        'draft_task_ids': draft_task_ids,
    }


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=8,
)
def draft_batch_task(batch_index: int, base_url: str | None = None) -> dict:
    api_base = (base_url or settings.PEOPLE_API_BASE_URL).rstrip('/')
    response = requests.get(f'{api_base}/draft/{batch_index}', timeout=_get_api_timeout())
    if response.status_code == 404:
        return {
            'batch_index': batch_index,
            'drafted': 0,
            'next_key': '',
            'next_instructions': '',
        }
    response.raise_for_status()
    payload = response.json()
    batch_people = payload.get('data', payload) if isinstance(payload, dict) else payload

    next_key = ''
    drafted_count = 0

    with transaction.atomic():
        for entry in batch_people:
            key = entry.get('key', '') or ''
            rank = entry.get('rank', '') or ''
            health = entry.get('health', 100)
            defaults = {
                'name': entry['name'],
                'age': entry['age'],
                'rank': rank,
                'health': max(0, min(100, int(health))),
            }
            if key:
                defaults['key'] = key
                if key != 'draft-info':
                    next_key = key

            person, _ = Person.objects.update_or_create(
                external_id=entry['id'],
                defaults=defaults,
            )
            promote_person_to_unit(person)
            drafted_count += 1

    next_instructions = ''
    visited_keys: list[str] = []
    if next_key:
        chain_result = follow_intel_chain_task(next_key, base_url=api_base)
        visited_keys = chain_result.get('visited_keys', [])
        next_instructions = chain_result.get('last_message', '')

    return {
        'batch_index': batch_index,
        'drafted': drafted_count,
        'next_key': next_key,
        'next_instructions': next_instructions,
        'visited_keys': visited_keys,
    }


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=8,
)
def draft_people_batch_task(person_ids: list[int]) -> dict:
    drafted_ids = []
    with transaction.atomic():
        people = Person.objects.filter(id__in=person_ids).order_by('external_id')
        for person in people:
            person.rank = assign_rank_for_person(person)
            person.save(update_fields=['rank'])
            promote_person_to_unit(person)
            drafted_ids.append(person.id)

    return {
        'drafted': len(drafted_ids),
        'person_ids': drafted_ids,
    }


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=8,
)
def run_full_remote_pipeline_task(batch_size: int = 10, base_url: str | None = None) -> dict:
    api_base = (base_url or settings.PEOPLE_API_BASE_URL).rstrip('/')
    dl = download_people_task(base_url=api_base, auto_draft=True, batch_size=batch_size)
    num_batches = max(1, math.ceil((dl.get('total') or 0) / batch_size))
    return {
        'downloaded': dl.get('total', 0),
        'batches_dispatched': len(dl.get('draft_task_ids', [])) or num_batches,
        'draft_task_ids': dl.get('draft_task_ids', []),
    }


def enqueue_drafting_batches(batch_size: int = 10) -> list[str]:
    if batch_size <= 0:
        raise ValueError('batch_size must be a positive integer')

    undrafted_ids = list(
        Person.objects.filter(rank='').order_by('external_id').values_list('id', flat=True)
    )
    task_ids: list[str] = []
    for start in range(0, len(undrafted_ids), batch_size):
        batch = undrafted_ids[start : start + batch_size]
        async_result = draft_people_batch_task.delay(batch)
        task_ids.append(str(async_result.id))
    return task_ids


def enqueue_remote_draft_batches(num_batches: int = 10) -> list[str]:
    if num_batches <= 0:
        raise ValueError('num_batches must be a positive integer')
    return [str(draft_batch_task.delay(i).id) for i in range(num_batches)]


def _resolve_soldier_unit(external_soldier_id: int, default_team: str) -> Unit:
    person = Person.objects.select_related('unit').filter(external_id=external_soldier_id).first()
    if person and getattr(person, 'unit', None):
        return person.unit
    if person:
        unit = promote_person_to_unit(person)
        if unit.team != default_team:
            unit.team = default_team
            unit.save(update_fields=['team'])
        return unit
    unit, _ = Unit.objects.get_or_create(
        name=f'Soldier #{external_soldier_id}',
        defaults={
            'unit_type': Unit.UnitType.INFANTRY,
            'team': default_team,
            'health': Unit.MAX_HEALTH,
            'attack_points': 20,
        },
    )
    return unit


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=8,
)
def download_fight_batch_task(batch_index: int, base_url: str | None = None) -> dict:
    api_base = (base_url or settings.PEOPLE_API_BASE_URL).rstrip('/')
    response = requests.get(f'{api_base}/fight/{batch_index}', timeout=_get_api_timeout())
    if response.status_code == 404:
        return {'batch_index': batch_index, 'fights_saved': 0, 'events_saved': 0}
    response.raise_for_status()
    payload = response.json()
    fights_data = payload.get('data', payload) if isinstance(payload, dict) else payload

    fights_saved = 0
    events_saved = 0

    for entry in fights_data:
        ext_fight_id = entry['fight_id']
        blue_id = entry['blue_soldier_id']
        red_id = entry['red_soldier_id']
        steps = entry.get('steps', [])

        blue_unit = _resolve_soldier_unit(blue_id, Unit.Team.BLUE)
        red_unit = _resolve_soldier_unit(red_id, Unit.Team.RED)
        soldier_map = {blue_id: blue_unit, red_id: red_unit}

        with transaction.atomic():
            fight, _ = Fight.objects.update_or_create(
                external_fight_id=ext_fight_id,
                defaults={
                    'fighter1': blue_unit,
                    'fighter2': red_unit,
                    'winner': None,
                    'status': Fight.Status.PENDING,
                },
            )
            fight.events.all().delete()
            fight.steps.all().delete()
            for idx, step in enumerate(steps, start=1):
                attacker_unit = soldier_map.get(step['attacker']) or _resolve_soldier_unit(
                    step['attacker'], Unit.Team.BLUE
                )
                defender_unit = soldier_map.get(step['defender']) or _resolve_soldier_unit(
                    step['defender'], Unit.Team.RED
                )
                FightEvent.objects.create(
                    fight=fight,
                    event_number=idx,
                    attacker=attacker_unit,
                    defender=defender_unit,
                    damage=max(0, int(step['damage'])),
                )
                events_saved += 1
            fights_saved += 1

    return {
        'batch_index': batch_index,
        'fights_saved': fights_saved,
        'events_saved': events_saved,
    }


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=8,
)
def simulate_remote_fight_task(fight_id: int) -> dict:
    fight = replay_fight_events(fight_id)
    return {
        'fight_id': fight.id,
        'external_fight_id': fight.external_fight_id,
        'status': fight.status,
        'winner': fight.winner.name if fight.winner else None,
    }


def enqueue_remote_fight_batches(num_batches: int = 1) -> list[str]:
    if num_batches <= 0:
        raise ValueError('num_batches must be a positive integer')
    return [str(download_fight_batch_task.delay(i).id) for i in range(num_batches)]


def enqueue_fight_replays() -> list[str]:
    pending_ids = list(
        Fight.objects.filter(status=Fight.Status.PENDING)
        .order_by('external_fight_id', 'id')
        .values_list('id', flat=True)
    )
    return [str(simulate_remote_fight_task.delay(fid).id) for fid in pending_ids]
