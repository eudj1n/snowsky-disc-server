"""Reject incomplete soak evidence and resource regressions without firmware."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('soak_metrics', Path(__file__).resolve().parents[1]/'integration/soak_metrics.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


class SoakMetricsTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(pid=123, startTicks=999, rssKiB=12000, fds=4, threads=7,
                         nofileSoft=64, cpuSeconds=2, elapsedSeconds=0)
        self.samples = [dict(self.base, elapsedSeconds=i*30, cpuSeconds=2+i*.1, rssKiB=12100) for i in range(1, 21)]
        self.cycles = [dict(idle=s, stateReads=15, catalogReads=15) for s in self.samples]

    def test_complete_stable_run(self):
        result = metrics.summarize(self.base, self.samples, self.cycles, 600)
        self.assertEqual(result['stateReads'], 300)
        self.assertEqual(result['rssGrowthKiB'], 100)
        self.assertEqual(result['qemuCpuSeconds'], 2)

    def test_each_resource_or_identity_regression_fails(self):
        for key, value in [('pid',124), ('startTicks',1000), ('rssKiB',17000),
                           ('fds',13), ('threads',metrics.MAX_THREADS + 1), ('nofileSoft',1024)]:
            with self.subTest(key=key):
                with self.assertRaises(AssertionError):metrics.check_sample(self.base, dict(self.base, **{key:value}))
        with self.assertRaisesRegex(AssertionError, 'ceiling'):
            metrics.check_sample(dict(self.base,rssKiB=32000),dict(self.base,rssKiB=33000))

    def test_active_allowance_does_not_hide_idle_leaks(self):
        for key in ('fds','threads'):
            sample = dict(self.base, **{key:self.base[key]+1})
            metrics.check_sample(self.base, sample)
            with self.assertRaises(AssertionError):metrics.check_sample(self.base, sample, idle=True)

    def test_short_or_incomplete_workload_is_not_acceptance(self):
        for duration, cycles in [(599,self.cycles), (600,self.cycles[:-1])]:
            with self.assertRaises(AssertionError):metrics.summarize(self.base,self.samples,cycles,duration)
        cycles = copy.deepcopy(self.cycles);cycles[0]['catalogReads'] = 0
        with self.assertRaises(AssertionError):metrics.summarize(self.base,self.samples,cycles,600)

    def test_missing_final_idle_or_regressing_clock_fails(self):
        with self.assertRaises(AssertionError):metrics.summarize(self.base,self.samples[:-1],self.cycles,600)
        samples = copy.deepcopy(self.samples);samples[2]['elapsedSeconds'] = 0
        with self.assertRaises(AssertionError):metrics.summarize(self.base,samples,self.cycles,600)


if __name__ == '__main__':unittest.main()
