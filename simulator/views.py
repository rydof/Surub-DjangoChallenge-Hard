from celery.result import AsyncResult
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from simulator.forms import UnitForm
from simulator.models import DraftIntel, Fight, FightEvent, FightStep, Person, Unit
from simulator.services import compute_team_stats, simulate_fight
from simulator.tasks import (
    download_people_task,
    enqueue_drafting_batches,
    enqueue_fight_replays,
    enqueue_remote_draft_batches,
    enqueue_remote_fight_batches,
    follow_intel_chain_task,
    run_full_remote_pipeline_task,
)


@require_GET
def home(request):
    return redirect('unit_list')


@require_http_methods(['GET', 'POST'])
def unit_create(request):
    if request.method == 'POST':
        form = UnitForm(request.POST)
        if form.is_valid():
            unit = form.save()
            messages.success(request, f'Fighter "{unit.name}" was added to the army.')
            return redirect('unit_list')
    else:
        form = UnitForm()

    return render(request, 'simulator/unit_form.html', {'form': form})


@require_http_methods(['GET', 'POST'])
def unit_list(request):
    error_message = None

    if request.method == 'POST':
        selected_ids = request.POST.getlist('selected_fighters')
        if len(selected_ids) != 2:
            error_message = 'Please select exactly 2 fighters of different teams to simulate a fight.'
        else:
            fighters = list(Unit.objects.filter(id__in=selected_ids))
            if len(fighters) != 2:
                error_message = 'One or both selected fighters could not be found.'
            else:
                fighter_map = {str(f.id): f for f in fighters}
                fighter1 = fighter_map[selected_ids[0]]
                fighter2 = fighter_map[selected_ids[1]]
                try:
                    fight = simulate_fight(fighter1, fighter2)
                    messages.success(
                        request,
                        f'Fight #{fight.id} complete! Winner: {fight.winner.name} ({fight.winner.team}).',
                    )
                    return redirect('unit_list')
                except ValidationError as exc:
                    error_message = exc.messages[0] if exc.messages else str(exc)

    units = Unit.objects.select_related('person').all()
    team_stats = compute_team_stats()
    blue_count = sum(1 for u in units if u.team == Unit.Team.BLUE)
    red_count = sum(1 for u in units if u.team == Unit.Team.RED)
    injured_count = sum(1 for u in units if u.health < Unit.MAX_HEALTH)
    total_damage_all = team_stats['Blue']['damage_caused'] + team_stats['Red']['damage_caused']
    blue_dmg_share = (
        round((team_stats['Blue']['damage_caused'] / total_damage_all) * 100)
        if total_damage_all
        else 50
    )
    red_dmg_share = 100 - blue_dmg_share if total_damage_all else 50
    recent_fights = Fight.objects.select_related('fighter1', 'fighter2', 'winner')[:5]

    return render(
        request,
        'simulator/unit_list.html',
        {
            'units': units,
            'blue_count': blue_count,
            'red_count': red_count,
            'injured_count': injured_count,
            'team_stats': team_stats,
            'blue_dmg_share': blue_dmg_share,
            'red_dmg_share': red_dmg_share,
            'recent_fights': recent_fights,
            'error_message': error_message,
        },
    )


@require_GET
def unit_detail(request, unit_id: int):
    unit = get_object_or_404(Unit.objects.select_related('person'), id=unit_id)
    fights = (
        Fight.objects.filter(Q(fighter1=unit) | Q(fighter2=unit))
        .select_related('fighter1', 'fighter2', 'winner')
        .prefetch_related('steps__attacker', 'steps__defender')
        .order_by('-created_at', '-id')
    )
    total_fights = fights.count()
    wins = fights.filter(winner=unit).count()
    losses = total_fights - wins
    damage_dealt = (
        FightStep.objects.filter(attacker=unit).aggregate(total=Sum('attack_points'))['total']
        or 0
    )
    damage_taken = (
        FightStep.objects.filter(defender=unit).aggregate(total=Sum('attack_points'))['total']
        or 0
    )

    return render(
        request,
        'simulator/unit_detail.html',
        {
            'unit': unit,
            'fights': fights,
            'total_fights': total_fights,
            'wins': wins,
            'losses': losses,
            'damage_dealt': damage_dealt,
            'damage_taken': damage_taken,
        },
    )


@require_POST
def unit_heal(request, unit_id: int):
    unit = get_object_or_404(Unit, id=unit_id)
    unit.health = Unit.MAX_HEALTH
    unit.save(update_fields=['health'])
    messages.success(request, f'{unit.name} healed back to {Unit.MAX_HEALTH} HP.')
    next_url = request.POST.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('unit_list')


@require_GET
def people_list(request):
    people = Person.objects.select_related('unit').all()
    total_people = people.count()
    drafted_count = people.exclude(rank='').count()
    undrafted_count = total_people - drafted_count
    draft_pct = round((drafted_count / total_people) * 100) if total_people else 0
    key_person = people.exclude(key='').first()
    intel_logs = DraftIntel.objects.all()
    tracked_task_ids = request.session.get('tracked_task_ids', [])

    return render(
        request,
        'simulator/people_list.html',
        {
            'people': people,
            'total_people': total_people,
            'drafted_count': drafted_count,
            'undrafted_count': undrafted_count,
            'draft_pct': draft_pct,
            'key_person': key_person,
            'intel_logs': intel_logs,
            'tracked_task_ids': tracked_task_ids,
        },
    )


@require_POST
def people_download(request):
    auto_draft = request.POST.get('auto_draft') == '1'
    if auto_draft:
        task = run_full_remote_pipeline_task.delay()
        request.session['tracked_task_ids'] = [str(task.id)]
        messages.info(request, f'Queued parallel remote pipeline task: {task.id}')
    else:
        task = download_people_task.delay()
        request.session['tracked_task_ids'] = [str(task.id)]
        messages.info(request, f'Queued background download task: {task.id}')
    return redirect('people_list')


@require_POST
def people_follow_key(request):
    key = (request.POST.get('key') or '').strip().lstrip('/')
    if not key:
        messages.error(request, 'Please provide a key endpoint to follow.')
        return redirect('people_list')
    task = follow_intel_chain_task.delay(key)
    request.session['tracked_task_ids'] = [str(task.id)]
    messages.info(request, f'Queued intel chain task for /{key}: {task.id}')
    return redirect('people_list')


@require_POST
def people_reset(request):
    with transaction.atomic():
        drafted_units_deleted, _ = Unit.objects.filter(person__isnull=False).delete()
        people_deleted, _ = Person.objects.all().delete()
        DraftIntel.objects.all().delete()
    request.session.pop('tracked_task_ids', None)
    messages.warning(
        request,
        f'Cleared {people_deleted} registered people and {drafted_units_deleted} drafted unit(s). Ready to start from 0.',
    )
    return redirect('people_list')


@require_POST
def people_draft(request):
    mode = request.POST.get('mode', 'local')
    if mode == 'remote':
        try:
            num_batches = int(request.POST.get('num_batches', 10))
            if num_batches <= 0:
                raise ValueError
        except ValueError:
            messages.error(request, 'Number of batches must be a positive integer.')
            return redirect('people_list')

        task_ids = enqueue_remote_draft_batches(num_batches=num_batches)
        request.session['tracked_task_ids'] = task_ids
        messages.info(
            request,
            f'Queued {len(task_ids)} separate remote /draft/<batch> Celery task(s) with retry policies.',
        )
        return redirect('people_list')

    try:
        batch_size = int(request.POST.get('batch_size', 10))
        if batch_size <= 0:
            raise ValueError
    except ValueError:
        messages.error(request, 'Batch size must be a positive integer.')
        return redirect('people_list')

    task_ids = enqueue_drafting_batches(batch_size=batch_size)
    if not task_ids:
        messages.warning(request, 'No undrafted people found to draft.')
    else:
        request.session['tracked_task_ids'] = task_ids
        messages.info(
            request,
            f'Queued {len(task_ids)} separate background drafting batch task(s).',
        )
    return redirect('people_list')


@require_GET
def tasks_status(request):
    raw_ids = request.GET.get('ids', '')
    task_ids = [tid.strip() for tid in raw_ids.split(',') if tid.strip()]
    statuses = []
    for tid in task_ids:
        res = AsyncResult(tid)
        statuses.append({'id': tid, 'status': res.status})
    return JsonResponse({'tasks': statuses})


@require_http_methods(['GET', 'POST'])
def simulate_page(request):
    if request.method == 'POST':
        task_ids = enqueue_fight_replays()
        if not task_ids:
            messages.warning(request, 'No pending fights to replay. Fetch fight batches first.')
        else:
            request.session['simulate_task_ids'] = task_ids
            messages.info(
                request,
                f'Launched {len(task_ids)} parallel Celery fight replay task(s).',
            )
        return redirect('simulate_page')

    fights = (
        Fight.objects.select_related('fighter1', 'fighter2', 'winner')
        .prefetch_related('events__attacker', 'events__defender', 'steps__attacker', 'steps__defender')
        .order_by('external_fight_id', '-id')
    )
    total_fights = fights.count()
    completed_count = fights.filter(status=Fight.Status.COMPLETED).count()
    pending_count = total_fights - completed_count
    total_events = FightEvent.objects.count()
    simulate_task_ids = request.session.get('simulate_task_ids', [])
    team_stats = compute_team_stats()

    return render(
        request,
        'simulator/simulate.html',
        {
            'fights': fights,
            'total_fights': total_fights,
            'completed_count': completed_count,
            'pending_count': pending_count,
            'total_events': total_events,
            'simulate_task_ids': simulate_task_ids,
            'team_stats': team_stats,
        },
    )


@require_POST
def simulate_fetch(request):
    try:
        num_batches = int(request.POST.get('num_batches', 1))
        if num_batches <= 0:
            raise ValueError
    except ValueError:
        messages.error(request, 'Number of fight batches must be a positive integer.')
        return redirect('simulate_page')

    task_ids = enqueue_remote_fight_batches(num_batches=num_batches)
    request.session['simulate_task_ids'] = task_ids
    messages.info(
        request,
        f'Queued {len(task_ids)} parallel /fight/<batch> download task(s).',
    )
    return redirect('simulate_page')


@require_POST
def simulate_reset(request):
    with transaction.atomic():
        Fight.objects.filter(external_fight_id__isnull=False).update(
            status=Fight.Status.PENDING,
            winner=None,
        )
        FightStep.objects.filter(fight__external_fight_id__isnull=False).delete()
        Unit.objects.all().update(health=Unit.MAX_HEALTH)
    request.session.pop('simulate_task_ids', None)
    messages.warning(
        request,
        'Reset remote fights to PENDING and restored all units to 100 HP.',
    )
    return redirect('simulate_page')


@require_POST
def simulate_clear(request):
    with transaction.atomic():
        fight_count = Fight.objects.count()
        FightStep.objects.all().delete()
        FightEvent.objects.all().delete()
        Fight.objects.all().delete()
        Unit.objects.all().update(health=Unit.MAX_HEALTH)
    request.session.pop('simulate_task_ids', None)
    messages.warning(
        request,
        f'Cleared {fight_count} fight(s) and restored all units to 100 HP.',
    )
    return redirect('simulate_page')
