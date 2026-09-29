import contextlib
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'resources/lib'))
import bluetooth_startup as startup
import device


class FakeMonitor:
    """Advance time without sleeping or starting a real system service."""

    def __init__(self):
        self.now = 0.0
        self.aborted = False
        self.waits = []
        self.on_wait = None

    def abortRequested(self):
        return self.aborted

    def waitForAbort(self, seconds):
        if seconds <= 0:
            raise AssertionError('observation must wait between checks')
        self.waits.append(seconds)
        if len(self.waits) > 1000:
            raise AssertionError('observation did not reach its deadline')
        self.now += seconds
        if self.on_wait:
            self.on_wait(self)
        return self.aborted


class BluetoothStartupTests(unittest.TestCase):
    @contextlib.contextmanager
    def environment(self, chip='ap6275p', active='failed', unit_file='enabled',
                    load='loaded', branch='ng', board='4pro'):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp)
            bluetooth = root / 'bluetooth'
            bluetooth.mkdir()
            paths = {
                'BLUETOOTH': bluetooth,
                'STATE': root / 'retry.json',
                'LOCK': root / 'retry.lock',
                'REPAIR_LOCK': root / 'repair.lock',
                'BLUEZ_CONFIG': root / 'bluez.conf',
            }
            paths['BLUEZ_CONFIG'].write_text('')
            for name, path in paths.items():
                stack.enter_context(patch.object(startup, name, path))
            monitor = FakeMonitor()
            stack.enter_context(patch.object(startup.time, 'monotonic',
                                            side_effect=lambda: monitor.now))
            key = branch + '/' + board + ('/' + chip if board == '4pro' else '')
            profile = dict(branch=branch, board=board, chip=chip, payload=key,
                           dt_id=device.PROFILE_IDS[key],
                           expected_dt_id=device.PROFILE_IDS[key])
            detect = stack.enter_context(patch.object(startup.device, 'detect',
                                                      return_value=profile))
            pci = stack.enter_context(patch.object(startup.device, 'pci_chip',
                                                   return_value=''))
            state = dict(LoadState=load, ActiveState=active,
                         UnitFileState=unit_file, Result='exit-code' if active == 'failed' else 'success')
            bluez = dict(LoadState='loaded', ActiveState='active',
                         UnitFileState='enabled', Result='success')
            other = dict(LoadState='not-found', ActiveState='inactive',
                         UnitFileState='', Result='success')
            target = {'ap6275p': 'brcmfmac_sdio-firmware-aml.service',
                      'rtl8852': 'rtkbt-firmware-aml.service'}[chip]

            def read_state(unit):
                return dict(bluez if unit == 'bluetooth.service' else
                            state if unit == target else other)

            unit_state = stack.enter_context(patch.object(startup, 'unit_state',
                                                          side_effect=read_state))
            calls, logs = [], []

            def run(*args):
                calls.append((monitor.now, args))
                if 'stop' in args:
                    state['ActiveState'] = 'inactive'
                if 'start' in args:
                    state['ActiveState'] = 'active'
                    state['Result'] = 'success'
                return subprocess.CompletedProcess(args, 0, '', '')

            command = stack.enter_context(patch.object(startup, 'command', side_effect=run))
            yield dict(paths=paths, monitor=monitor, profile=profile,
                       detect=detect, pci=pci, state=state, unit_state=unit_state,
                       calls=calls, command=command, run=run, logs=logs,
                       bluez=bluez, other=other)

    def recover(self, env):
        startup.recover_once(env['monitor'], env['logs'].append)

    def assert_skipped(self, env):
        self.assertFalse(env['paths']['STATE'].exists())
        env['command'].assert_not_called()

    def status(self, env):
        return json.loads(env['paths']['STATE'].read_text())

    def add_hci(self, env):
        (env['paths']['BLUETOOTH'] / 'hci0').mkdir(exist_ok=True)

    def test_unknown_runtime_profile_is_ignored(self):
        with self.environment() as env:
            env['detect'].side_effect = RuntimeError('unknown DTB')
            self.recover(env)
            self.assert_skipped(env)

    def test_missing_or_mismatched_dtb_is_ignored(self):
        for dt_id in ('', 'sc2_s905x4_4g_1gbit', device.PROFILE_IDS['no/4pro/ap6275p']):
            with self.subTest(dt_id=dt_id), self.environment() as env:
                env['profile']['dt_id'] = dt_id
                self.recover(env)
                self.assert_skipped(env)

    def test_conflicting_pci_chip_is_ignored(self):
        with self.environment() as env:
            env['pci'].return_value = 'rtl8852'
            self.recover(env)
            self.assert_skipped(env)

    def test_runtime_detection_does_not_read_android(self):
        real_detect = device.detect
        with self.environment(active='inactive') as env:
            release = env['paths']['STATE'].parent / 'os-release'
            release.write_text('ID=coreelec\nCOREELEC_DEVICE=Amlogic-ng\nVERSION_ID=21.3\n')
            env['detect'].side_effect = real_detect
            with patch.object(device, 'RELEASE', release), \
                 patch.object(device, 'text_property', side_effect=lambda name: {
                     'compatible': 'amlogic,sc2', 'model': 'installed board',
                     'coreelec-dt-id': env['profile']['dt_id']}[name]), \
                 patch.object(device, 'android_board', side_effect=AssertionError('must not read Android')) as android:
                self.recover(env)
                env['detect'].assert_called_once_with(check_kernel=False, runtime_profile=True)
                android.assert_not_called()
                env['unit_state'].assert_called()
                self.assert_skipped(env)

    def test_real_runtime_profiles_retry_correct_unit_on_ng_and_no(self):
        real_detect = device.detect
        profiles = (
            ('6s', 'rtl8852', 'rtkbt-firmware-aml.service'),
            ('4pro', 'rtl8852', 'rtkbt-firmware-aml.service'),
            ('4pro', 'ap6275p', 'brcmfmac_sdio-firmware-aml.service'),
        )
        for branch, version in (('ng', '21.3'), ('no', '22.0')):
            for board, chip, unit in profiles:
                with self.subTest(branch=branch, board=board, chip=chip), \
                     self.environment(branch=branch, board=board, chip=chip,
                                      unit_file='static') as env:
                    release = env['paths']['STATE'].parent / 'os-release'
                    release.write_text('ID=coreelec\nCOREELEC_DEVICE=Amlogic-%s\nVERSION_ID=%s\n' %
                                       (branch, version))
                    env['detect'].side_effect = real_detect
                    env['bluez']['UnitFileState'] = 'disabled'

                    def run(*args):
                        result = env['run'](*args)
                        if 'start' in args:
                            self.add_hci(env)
                        return result

                    env['command'].side_effect = run
                    with patch.object(device, 'RELEASE', release), \
                         patch.object(device, 'text_property', side_effect=lambda name: {
                             'compatible': 'amlogic,sc2', 'model': 'installed board',
                             'coreelec-dt-id': env['profile']['dt_id']}[name]), \
                         patch.object(device, 'android_board',
                                      side_effect=AssertionError('must not read Android')) as android:
                        self.recover(env)
                        env['detect'].assert_called_once_with(check_kernel=False, runtime_profile=True)
                        android.assert_not_called()
                    self.assertEqual(len(env['calls']), 2)
                    self.assertIn('stop', env['calls'][0][1])
                    self.assertIn('start', env['calls'][1][1])
                    self.assertTrue(all(unit in args for _, args in env['calls']))
                    self.assertEqual(self.status(env)['unit'], unit)
                    self.assertEqual(self.status(env)['dt_id'], env['profile']['dt_id'])
                    self.assertEqual(self.status(env)['status'], 'recovered')

    def test_real_runtime_detection_rejects_generic_and_other_branch_dtb(self):
        real_detect = device.detect
        for branch, version, other in (('ng', '21.3', 'no'), ('no', '22.0', 'ng')):
            for dt_id in ('sc2_s905x4_4g_1gbit', device.PROFILE_IDS[other + '/6s']):
                with self.subTest(branch=branch, dt_id=dt_id), \
                     self.environment(branch=branch) as env:
                    release = env['paths']['STATE'].parent / 'os-release'
                    release.write_text('ID=coreelec\nCOREELEC_DEVICE=Amlogic-%s\nVERSION_ID=%s\n' %
                                       (branch, version))
                    env['detect'].side_effect = real_detect
                    with patch.object(device, 'RELEASE', release), \
                         patch.object(device, 'text_property', side_effect=lambda name: {
                             'compatible': 'amlogic,sc2', 'model': 'generic board',
                             'coreelec-dt-id': dt_id}[name]), \
                         patch.object(device, 'android_board',
                                      side_effect=AssertionError('must not read Android')) as android:
                        self.recover(env)
                        env['detect'].assert_called_once_with(check_kernel=False, runtime_profile=True)
                        android.assert_not_called()
                    self.assert_skipped(env)
                    env['unit_state'].assert_not_called()

    def test_each_chip_retries_only_its_own_unit_and_waits_after_stop(self):
        for chip, unit in (('ap6275p', 'brcmfmac_sdio-firmware-aml.service'),
                           ('rtl8852', 'rtkbt-firmware-aml.service')):
            with self.subTest(chip=chip), self.environment(chip=chip) as env:
                def run(*args):
                    result = env['run'](*args)
                    if 'start' in args:
                        self.add_hci(env)
                    return result
                env['command'].side_effect = run
                self.recover(env)
                actions = [(time, args) for time, args in env['calls']
                           if 'stop' in args or 'start' in args]
                self.assertEqual(len(actions), 2)
                self.assertIn('stop', actions[0][1])
                self.assertIn('start', actions[1][1])
                self.assertIn(unit, actions[0][1])
                self.assertIn(unit, actions[1][1])
                self.assertGreaterEqual(actions[1][0] - actions[0][0], 2)
                self.assertEqual(self.status(env)['unit'], unit)
                self.assertEqual(self.status(env)['status'], 'recovered')

    def test_inactive_disabled_masked_or_missing_units_are_not_started(self):
        for active, unit_file, load in (
                ('inactive', 'enabled', 'loaded'),
                ('failed', 'disabled', 'loaded'),
                ('failed', 'masked', 'loaded'),
                ('failed', 'enabled', 'masked'),
                ('failed', 'enabled', 'not-found')):
            with self.subTest(active=active, unit_file=unit_file, load=load), \
                 self.environment(active=active, unit_file=unit_file, load=load) as env:
                self.recover(env)
                self.assert_skipped(env)

    def test_existing_hci_prevents_retry(self):
        with self.environment() as env:
            (env['paths']['BLUETOOTH'] / 'hci7').mkdir()
            self.recover(env)
            self.assert_skipped(env)

    def test_inactive_or_masked_bluetooth_service_prevents_retry(self):
        for changes in ({'ActiveState': 'inactive'}, {'UnitFileState': 'masked'},
                        {'LoadState': 'masked'}):
            with self.subTest(changes=changes), self.environment() as env:
                env['bluez'].update(changes)
                self.recover(env)
                self.assert_skipped(env)

    def test_coreelec_bluetooth_setting_must_be_enabled(self):
        with self.environment() as env:
            env['paths']['BLUEZ_CONFIG'].unlink()
            self.recover(env)
            self.assert_skipped(env)

    def test_active_bluetooth_service_with_disabled_unit_file_can_retry(self):
        with self.environment() as env:
            env['bluez']['UnitFileState'] = 'disabled'
            def run(*args):
                result = env['run'](*args)
                if 'start' in args:
                    self.add_hci(env)
                return result
            env['command'].side_effect = run
            self.recover(env)
            self.assertEqual(self.status(env)['status'], 'recovered')

    def test_running_other_chip_service_prevents_retry(self):
        for active in ('active', 'activating', 'deactivating'):
            with self.subTest(active=active), self.environment() as env:
                env['other'].update(LoadState='loaded', UnitFileState='enabled',
                                    ActiveState=active)
                self.recover(env)
                self.assert_skipped(env)

    def test_initializing_unit_is_observed_without_restart(self):
        for active in ('active', 'activating'):
            with self.subTest(active=active), self.environment(active=active) as env:
                self.recover(env)
                self.assert_skipped(env)
                self.assertGreaterEqual(env['monitor'].now, 60)
                self.assertLessEqual(env['monitor'].now, 65)

    def test_hci_appearing_during_initialization_ends_observation(self):
        with self.environment(active='activating') as env:
            env['monitor'].on_wait = lambda monitor: self.add_hci(env)
            self.recover(env)
            self.assert_skipped(env)
            self.assertLess(env['monitor'].now, 60)

    def test_attempt_is_persisted_before_stop_and_never_repeated(self):
        with self.environment() as env:
            def run(*args):
                self.assertEqual(self.status(env)['status'], 'retrying')
                result = env['run'](*args)
                if 'start' in args:
                    self.add_hci(env)
                return result
            env['command'].side_effect = run
            self.recover(env)
            self.assertEqual(self.status(env)['status'], 'recovered')
            (env['paths']['BLUETOOTH'] / 'hci0').rmdir()
            env['state']['ActiveState'] = 'failed'
            first_calls = list(env['calls'])
            self.recover(env)
            self.assertEqual(env['calls'], first_calls)

    def test_existing_state_even_if_unreadable_prevents_retry(self):
        with self.environment() as env:
            env['paths']['STATE'].write_text('interrupted JSON write')
            self.recover(env)
            env['command'].assert_not_called()
            self.assertEqual(env['paths']['STATE'].read_text(), 'interrupted JSON write')

    def test_abort_before_attempt_does_not_consume_retry(self):
        with self.environment() as env:
            env['monitor'].aborted = True
            self.recover(env)
            self.assert_skipped(env)

    def test_abort_between_stop_and_start_does_not_start_service(self):
        with self.environment() as env:
            env['monitor'].on_wait = lambda monitor: setattr(monitor, 'aborted', True)
            self.recover(env)
            self.assertEqual(len(env['calls']), 1)
            self.assertIn('stop', env['calls'][0][1])
            self.assertEqual(self.status(env)['status'], 'aborted')
            self.recover(env)
            self.assertEqual(len(env['calls']), 1)

    def test_abort_while_observing_initialization_does_not_consume_retry(self):
        with self.environment(active='activating') as env:
            env['monitor'].on_wait = lambda monitor: setattr(monitor, 'aborted', True)
            self.recover(env)
            self.assert_skipped(env)

    def test_repair_or_other_startup_instance_lock_prevents_retry(self):
        for name in ('REPAIR_LOCK', 'LOCK'):
            with self.subTest(lock=name), self.environment() as env:
                with env['paths'][name].open('w') as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.recover(env)
                self.assert_skipped(env)

    def test_successful_start_without_hci_does_not_report_recovery(self):
        with self.environment() as env:
            self.recover(env)
            self.assertEqual(self.status(env)['status'], 'timeout')
            self.assertGreaterEqual(env['monitor'].now, 62)
            self.assertEqual(sum('start' in args for _, args in env['calls']), 1)

    def test_hci_appearing_after_start_is_required_for_recovery(self):
        with self.environment() as env:
            def wait(monitor):
                if any('start' in args for _, args in env['calls']):
                    self.assertEqual(self.status(env)['status'], 'retrying')
                    self.add_hci(env)
            env['monitor'].on_wait = wait
            self.recover(env)
            self.assertEqual(self.status(env)['status'], 'recovered')
            self.assertLess(env['monitor'].now, 62)

    def test_abort_after_start_records_aborted(self):
        with self.environment() as env:
            def wait(monitor):
                if any('start' in args for _, args in env['calls']):
                    monitor.aborted = True
            env['monitor'].on_wait = wait
            self.recover(env)
            self.assertEqual(self.status(env)['status'], 'aborted')
            self.assertEqual(sum('start' in args for _, args in env['calls']), 1)

    def test_setting_disabled_during_stop_wait_cancels_start(self):
        with self.environment() as env:
            env['monitor'].on_wait = lambda monitor: env['paths']['BLUEZ_CONFIG'].unlink()
            self.recover(env)
            self.assertEqual(len(env['calls']), 1)
            self.assertIn('stop', env['calls'][0][1])
            self.assertEqual(self.status(env)['status'], 'aborted')

    def test_target_state_changing_during_stop_wait_cancels_start(self):
        for active in ('active', 'activating', 'deactivating'):
            with self.subTest(active=active), self.environment() as env:
                def wait(monitor):
                    env['state']['ActiveState'] = active
                env['monitor'].on_wait = wait
                self.recover(env)
                self.assertEqual(len(env['calls']), 1)
                self.assertIn('stop', env['calls'][0][1])
                self.assertEqual(self.status(env)['status'], 'aborted')

    def test_retry_service_failure_does_not_trigger_another_restart(self):
        with self.environment() as env:
            def run(*args):
                result = env['run'](*args)
                if 'start' in args:
                    env['state'].update(ActiveState='failed', Result='exit-code')
                return result
            env['command'].side_effect = run
            self.recover(env)
            self.assertEqual(self.status(env)['status'], 'failed')
            first_calls = list(env['calls'])
            self.recover(env)
            self.assertEqual(env['calls'], first_calls)

    def test_initialization_must_fail_before_retry_is_attempted(self):
        with self.environment(active='activating') as env:
            def wait(monitor):
                if not env['calls']:
                    env['state'].update(ActiveState='failed', Result='exit-code')
            env['monitor'].on_wait = wait
            def run(*args):
                result = env['run'](*args)
                if 'start' in args:
                    self.add_hci(env)
                return result
            env['command'].side_effect = run
            self.recover(env)
            self.assertEqual(self.status(env)['status'], 'recovered')
            self.assertGreater(env['calls'][0][0], 0)

    def test_command_failure_records_failed_and_is_not_retried(self):
        for failing_action in ('stop', 'start'):
            with self.subTest(action=failing_action), self.environment() as env:
                def run(*args):
                    result = env['run'](*args)
                    if failing_action in args:
                        raise subprocess.CalledProcessError(1, args, stderr='test failure')
                    return result
                env['command'].side_effect = run
                self.recover(env)
                self.assertEqual(self.status(env)['status'], 'failed')
                count = env['command'].call_count
                self.recover(env)
                self.assertEqual(env['command'].call_count, count)


if __name__ == '__main__':
    unittest.main()
