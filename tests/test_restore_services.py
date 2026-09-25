import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import operations as op

class RestoreServiceTests(unittest.TestCase):
    def exercise(self,active='active',write=False,fail=False,stop_fail=False):
        calls=[];events=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[1]=='show':return active
            if args[1]=='stop' and stop_fail:raise RuntimeError('stop failed')
            return ''
        def update(*args,**kwargs):events.append((args,kwargs))
        with patch.object(op,'run',side_effect=run):
            def operation():
                with op.android_restore_session(update) as progress:
                    calls.append(['test','detach'])
                    if write:progress('restoring','write',device_writes_started=True)
                    if fail:raise RuntimeError('operation failed')
            if fail or stop_fail:
                with self.assertRaises(RuntimeError):operation()
            else:operation()
        return calls,events
    def test_stop_before_detach_restart_if_no_write(self):
        calls,_=self.exercise()
        self.assertEqual([x[1] for x in calls],['show','stop','detach','start'])
    def test_restart_on_prewrite_failure(self):
        calls,_=self.exercise(fail=True)
        self.assertEqual(calls[-1],['systemctl','start','opentee_linuxdriver.service'])
    def test_restart_when_stop_partly_fails(self):
        calls,_=self.exercise(stop_fail=True)
        self.assertEqual([x[1] for x in calls],['show','stop','start'])
    def test_leave_stopped_after_successful_write_or_partial_write(self):
        for fail in (False,True):
            with self.subTest(fail=fail):
                calls,events=self.exercise(write=True,fail=fail)
                self.assertNotIn('start',[x[1] for x in calls])
                self.assertTrue(any(e[1].get('device_writes_started') for e in events))
    def test_do_not_start_previously_inactive_service(self):
        calls,_=self.exercise(active='inactive',fail=True)
        self.assertEqual([x[1] for x in calls],['show','detach'])

    def test_cancel_at_write_boundary_restores_service(self):
        calls=[]
        def run(args,**kwargs):calls.append(args);return 'active' if args[1]=='show' else ''
        def update(*args,**kwargs):
            if kwargs.get('device_writes_started'):raise RuntimeError('cancel before writing')
        with patch.object(op,'run',side_effect=run):
            with self.assertRaisesRegex(RuntimeError,'cancel before writing'):
                with op.android_restore_session(update) as progress:
                    progress('restoring','write',device_writes_started=True)
        self.assertEqual(calls[-1],['systemctl','start','opentee_linuxdriver.service'])
