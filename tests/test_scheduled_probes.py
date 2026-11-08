import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from day8_gateway import catalog
from day8_gateway.scheduled_probes import ProbeSchedule


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / catalog.FILENAME).write_bytes(b'synthetic catalog')
        self.schedule = ProbeSchedule(self.root, bytes(32))
        self.enterContext(patch.object(catalog, 'read', return_value=[]))

    async def test_timer_runs_and_restores_snapshot_after_restart(self):
        probe = self.enterContext(patch.object(catalog, 'probe_routes', new=AsyncMock(return_value={'A': 22})))
        timer = asyncio.create_task(self.schedule.run())
        try:
            async with asyncio.timeout(2):
                while self.schedule.status()['measuredAt'] is None:
                    await asyncio.sleep(.01)
            await asyncio.sleep(.02)
            probe.assert_awaited_once()
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        self.schedule.configure(15)
        loaded = ProbeSchedule(self.root, bytes(32)).status()
        self.assertEqual((loaded['minutes'], loaded['origin'], loaded['delays']), (15, 'scheduled', {'A': 22}))
        (self.root / catalog.FILENAME).write_bytes(b'replaced catalog')
        self.assertEqual(self.schedule.status()['delays'], {})

    async def test_manual_joins_scheduled_work(self):
        gate = asyncio.Event()
        async def sample(entries):
            await gate.wait()
            return {'A': 11}
        probe = self.enterContext(patch.object(catalog, 'probe_routes', side_effect=sample))
        scheduled = asyncio.create_task(self.schedule.measure('scheduled'))
        manual = asyncio.create_task(self.schedule.measure('manual'))
        await asyncio.sleep(.01)
        self.assertTrue(self.schedule.status()['running'])
        gate.set()
        first, second = await asyncio.gather(scheduled, manual)
        self.assertEqual(first, second)
        probe.assert_awaited_once()

    async def test_disabled_and_overdue(self):
        probe = self.enterContext(patch.object(catalog, 'probe_routes', new=AsyncMock(return_value={'A': -1})))
        self.schedule.configure(0)
        timer = asyncio.create_task(self.schedule.run())
        try:
            await asyncio.sleep(.02)
            probe.assert_not_awaited()
            await self.schedule.measure('manual')
            self.schedule.last['measuredAt'] = time.time() - 3600
            self.schedule.configure(5)
            async with asyncio.timeout(2):
                while self.schedule.status()['origin'] != 'scheduled':
                    await asyncio.sleep(.01)
            self.assertEqual(probe.await_count, 2)
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
