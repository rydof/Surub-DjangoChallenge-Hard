import os
import shutil
import signal
import subprocess
import sys
import time

from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)


def _set_parent_death_signal():
    """Ask Linux to SIGTERM the worker if its runserver parent dies."""
    if not sys.platform.startswith('linux'):
        return
    try:
        import ctypes

        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


class Command(StaticfilesRunserverCommand):
    """Development server that also owns a Celery worker process.

    The worker is started only in the process that actually serves requests
    (not the autoreload parent) and is stopped again when runserver exits or
    restarts. An already running Celery worker is detected and reused so the
    app does not accidentally run two worker pools against the same broker.
    """

    help = 'Starts the Django development server and an auto-managed Celery worker.'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--no-celery',
            action='store_true',
            help='Do not start a Celery worker alongside the development server.',
        )

    def handle(self, *args, **options):
        self._worker = None
        self._worker_pid_file = settings.BASE_DIR / 'celery_worker.pid'
        if self._should_start_worker(options):
            self._start_worker()
            self._install_signal_handlers()

        try:
            super().handle(*args, **options)
        finally:
            self._stop_worker()

    def _should_start_worker(self, options):
        if options.get('no_celery'):
            return False
        if not getattr(settings, 'CELERY_AUTOSTART', True):
            return False
        if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            return False

        # With autoreload, runserver starts a parent that immediately spawns
        # the real child. Only the child should own the worker.
        use_reloader = options.get('use_reloader', True)
        if use_reloader and os.environ.get('RUN_MAIN') != 'true':
            return False

        if os.environ.get('CELERY_AUTOSTART_FORCE') == '1':
            return True
        return not self._worker_already_running()

    def _worker_already_running(self):
        pids = set()

        try:
            if self._worker_pid_file.exists():
                pid = int(self._worker_pid_file.read_text(encoding='utf-8').strip())
                if self._is_celery_worker(pid):
                    pids.add(pid)
        except (OSError, ValueError):
            pass

        pgrep = shutil.which('pgrep')
        if pgrep:
            try:
                result = subprocess.run(
                    [pgrep, '-f', r'celery.*-A[ =]coderun2026_starter.*worker'],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                for token in result.stdout.split():
                    try:
                        pid = int(token)
                    except ValueError:
                        continue
                    if pid != os.getpid() and self._is_celery_worker(pid):
                        pids.add(pid)
            except (OSError, subprocess.SubprocessError):
                pass

        if not pids:
            return False

        self.stdout.write(
            self.style.WARNING(
                'Celery worker already running (pid(s): {}); '
                'runserver will not start another. Use --no-celery to silence '
                'this, or CELERY_AUTOSTART_FORCE=1 to start a managed worker.'
            ).format(', '.join(str(pid) for pid in sorted(pids)))
        )
        return True

    @staticmethod
    def _is_celery_worker(pid):
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as handle:
                cmdline = handle.read().replace(b'\0', b' ').decode(errors='replace')
        except OSError:
            try:
                result = subprocess.run(
                    ['ps', '-p', str(pid), '-o', 'args='],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                cmdline = result.stdout
            except (OSError, subprocess.SubprocessError):
                return False
        return (
            'celery' in cmdline
            and 'worker' in cmdline
            and 'coderun2026_starter' in cmdline
        )

    def _start_worker(self):
        cmd = [
            sys.executable,
            '-m',
            'celery',
            '-A',
            'coderun2026_starter',
            'worker',
            '--loglevel=info',
            f'--pool={getattr(settings, "CELERY_WORKER_POOL", "threads")}',
            f'--concurrency={getattr(settings, "CELERY_WORKER_CONCURRENCY", 16)}',
        ]
        env = os.environ.copy()
        env.setdefault('DJANGO_SETTINGS_MODULE', 'coderun2026_starter.settings')

        popen_kwargs = {
            'cwd': str(settings.BASE_DIR),
            'env': env,
        }
        if os.name == 'posix':
            popen_kwargs['start_new_session'] = True
            popen_kwargs['preexec_fn'] = _set_parent_death_signal

        self.stdout.write(
            self.style.SUCCESS('Starting Celery worker: {}'.format(' '.join(cmd)))
        )
        try:
            self._worker = subprocess.Popen(cmd, **popen_kwargs)
        except OSError as exc:
            self._worker = None
            self.stderr.write(self.style.ERROR(f'Could not start Celery worker: {exc}'))
            return

        try:
            self._worker_pid_file.write_text(str(self._worker.pid), encoding='utf-8')
        except OSError:
            pass

        time.sleep(1)
        if self._worker.poll() is not None:
            self.stderr.write(
                self.style.ERROR(
                    'Celery worker exited immediately with code '
                    f'{self._worker.returncode}. Check the log above.'
                )
            )
            self._worker = None
            self._remove_pid_file()

    def _install_signal_handlers(self):
        signums = [signal.SIGTERM, signal.SIGINT]
        if hasattr(signal, 'SIGHUP'):
            signums.append(signal.SIGHUP)
        for signum in signums:
            try:
                signal.signal(signum, self._handle_stop_signal)
            except (OSError, ValueError):
                pass

    def _handle_stop_signal(self, signum, frame):
        self._stop_worker()
        try:
            signal.signal(signum, signal.SIG_DFL)
        except (OSError, ValueError):
            pass
        os.kill(os.getpid(), signum)

    def _stop_worker(self):
        worker = getattr(self, '_worker', None)
        self._worker = None
        if worker is not None and worker.poll() is None:
            self.stdout.write(f'Stopping Celery worker (pid {worker.pid})...')
            try:
                if os.name == 'posix':
                    os.killpg(os.getpgid(worker.pid), signal.SIGTERM)
                else:
                    worker.terminate()
            except (OSError, ProcessLookupError):
                try:
                    worker.terminate()
                except OSError:
                    pass
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    worker.kill()
                except OSError:
                    pass
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self._remove_pid_file()

    def _remove_pid_file(self):
        try:
            self._worker_pid_file.unlink()
        except OSError:
            pass
