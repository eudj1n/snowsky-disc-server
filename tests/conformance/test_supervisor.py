"""Power-request supervision must never become an automatic guest restarter."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile

spec = importlib.util.spec_from_file_location('supervisor', Path(__file__).resolve().parents[2]/'scripts/runtime/guest_supervisor.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class Clock:
    now = 0
    stopped = False
    def __call__(self): return self.now
    def is_set(self): return self.stopped
    def wait(self, seconds): self.now += seconds


class Device:
    transition = None
    error = None
    calls = 0
    def service_requests(self): self.calls += 1
    def toggle_power(self): raise AssertionError('Supervisor must never boot a guest')


class SupervisorTests(unittest.TestCase):
    def test_process_discovery_excludes_other_commands_chroots_and_zombies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = [b'python3', b'-B', supervisor.SCRIPT.encode(), b'run', b'']
            for pid, args, state, process_root in (
                (1, expected, 'S', '/'),
                (2, expected[:-2]+[b'status', b''], 'S', '/'),
                (3, [b'python3', b'-B', b'/some/other/script.py', b'run', b''], 'S', '/'),
                (4, expected, 'Z', '/'),
                (5, expected, 'S', str(root)),
            ):
                entry = root/str(pid)
                entry.mkdir()
                (entry/'cmdline').write_bytes(b'\0'.join(args))
                (entry/'stat').write_text(f'{pid} (python) {state} 0')
                (entry/'root').symlink_to(process_root)
            self.assertEqual(supervisor.processes(root), [1])

    def test_stop_rechecks_identity_before_signal(self):
        with patch.object(supervisor, 'processes', side_effect=[[123], [], []]), patch.object(supervisor.os, 'kill') as kill:
            supervisor.stop()
        kill.assert_not_called()

    def test_stale_ready_file_is_not_a_running_observer(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory)/'ready.json'
            ready.write_text('{"pid": 123}')
            with patch.object(supervisor, 'READY', ready), patch.object(supervisor, 'processes', return_value=[]):
                self.assertIsNone(supervisor.ready())

    def test_bounded_lifetime_even_without_requests(self):
        clock, device = Clock(), Device()
        supervisor.supervise(device, clock, lifetime=1, clock=clock)
        self.assertEqual(clock.now, 1)
        self.assertEqual(device.calls, 4)

    def test_stop_does_not_consume_new_requests(self):
        clock, device = Clock(), Device()
        clock.stopped = True
        supervisor.supervise(device, clock, clock=clock)
        self.assertEqual(device.calls, 0)

    def test_stop_waits_for_existing_shutdown_to_finish(self):
        clock, device = Clock(), Device()
        device.transition = 'stopping'
        clock.stopped = True
        def sleep(seconds):
            clock.now += seconds
            if clock.now >= .5: device.transition = None
        supervisor.supervise(device, clock, clock=clock, sleep=sleep)
        self.assertEqual(clock.now, .5)
        self.assertEqual(device.calls, 0)

    def test_shutdown_failure_is_reported_not_retried(self):
        clock, device = Clock(), Device()
        def fail(): device.error = 'guest process survived'
        device.service_requests = fail
        with self.assertRaisesRegex(RuntimeError, 'guest process survived'):
            supervisor.supervise(device, clock, clock=clock)

    def test_stuck_transition_prevents_successful_stop(self):
        clock, device = Clock(), Device()
        device.transition = 'stopping'
        clock.stopped = True
        with self.assertRaisesRegex(TimeoutError, 'shutdown'):
            supervisor.supervise(device, clock, clock=clock, sleep=clock.wait)
        self.assertLessEqual(clock.now, 5)

    def test_observes_requests_after_a_completed_shutdown(self):
        clock, device = Clock(), Device()
        def handle():
            device.calls += 1
            # Emulate a request and its completion, then another explicit boot's
            # later request. The supervisor itself never calls a boot operation.
            device.transition = 'stopping' if device.calls in (1, 3) else None
        device.service_requests = handle
        supervisor.supervise(device, clock, lifetime=1, clock=clock)
        self.assertEqual(device.calls, 4)


if __name__ == '__main__': unittest.main()
