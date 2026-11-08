"""One owner for scheduled and manual macOS catalog measurements."""
import asyncio
from contextlib import suppress
import hashlib
import json
import time

from . import catalog, node_updates


INTERVALS = (0, 5, 15, 30, 60)
DEFAULT_INTERVAL = 30


class ProbeSchedule:
    def __init__(self, root, key):
        self.root = root
        self.key = key
        self.settings = root / '.local/probe-settings.json'
        self.results = root / '.local/probe-results.json'
        settings = self._read(self.settings)
        minutes = settings.get('minutes', DEFAULT_INTERVAL)
        self.minutes = minutes if type(minutes) is int and minutes in INTERVALS else DEFAULT_INTERVAL
        self.last = self._read(self.results)
        self.wakeup = asyncio.Event()
        self.task = None
        self.retry_after = 0
        self.failed = False

    @staticmethod
    def _read(path):
        try:
            data = json.loads(path.read_bytes())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _version(self):
        local = self.root / 'routes.local.enc.json'
        path = local if local.is_file() else self.root / catalog.FILENAME
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def status(self):
        version = self._version()
        last = self.last if version and self.last.get('version') == version else {}
        measured = last.get('measuredAt')
        if type(measured) not in (float, int) or not isinstance(last.get('delays'), dict):
            last, measured = {}, None
        return {'ok': True, 'minutes': self.minutes, 'running': self.task is not None,
                'measuredAt': measured, 'delays': last.get('delays', {}),
                'origin': last.get('origin'), 'probeFailed': self.failed}

    def configure(self, minutes):
        if type(minutes) is not int or minutes not in INTERVALS:
            raise ValueError('Unsupported probe interval.')
        node_updates.save(self.settings, json.dumps({'minutes': minutes}))
        self.minutes = minutes
        self.wakeup.set()
        return self.status()

    async def _sample(self, origin):
        try:
            version = self._version()
            if not version:
                raise ValueError('No catalog installed.')
            entries = await asyncio.to_thread(catalog.read, self.root, self.key)
            delays = await catalog.probe_routes(entries)
            if self._version() != version:
                raise ValueError('Catalog changed during measurement.')
            result = {'version': version, 'measuredAt': time.time(), 'origin': origin, 'delays': delays}
            node_updates.save(self.results, json.dumps(result))
            self.last = result
            self.retry_after = 0
            self.failed = False
            return True
        except Exception:
            self.retry_after = time.time() + 60
            self.failed = True
            return False
        finally:
            self.task = None
            self.wakeup.set()

    async def measure(self, origin):
        if self.task is None:
            self.task = asyncio.create_task(self._sample(origin))
        ok = await asyncio.shield(self.task)
        return {**self.status(), 'ok': ok}

    async def run(self):
        try:
            while True:
                self.wakeup.clear()
                state = self.status()
                now = time.time()
                last = state['measuredAt'] or 0
                if last > now:
                    last = 0
                due = max(last + self.minutes * 60, self.retry_after)
                if self.minutes and now >= due:
                    await self.measure('scheduled')
                    continue
                # Recheck wall time after sleep/wake and detect a changed catalog.
                delay = min(30, max(1, due - now)) if self.minutes else 30
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.wakeup.wait(), delay)
        finally:
            if self.task is not None:
                task = self.task
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
