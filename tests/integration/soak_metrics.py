"""Firmware-independent acceptance rules for the disposable QEMU soak."""
MAX_RSS_KIB = 32768
MAX_RSS_GROWTH_KIB = 4096
MAX_FDS = 12
# combined-008: the play observer runs from the main loop, which frees the slot it
# took in combined-007 (peak 9): main, civetweb master, 4 workers, one session reader.
MAX_THREADS = 12
SOFT_NOFILE = 64
MIN_SECONDS = 600
MIN_CYCLES = 20


def check_sample(baseline, sample, idle=False):
    assert (sample['pid'], sample['startTicks']) == (baseline['pid'], baseline['startTicks']), 'Native process identity changed'
    assert sample['rssKiB'] <= MAX_RSS_KIB, 'QEMU RSS ceiling exceeded'
    assert sample['rssKiB'] - baseline['rssKiB'] <= MAX_RSS_GROWTH_KIB, 'QEMU RSS growth budget exceeded'
    assert sample['fds'] <= MAX_FDS, 'FD ceiling exceeded'
    assert sample['threads'] <= MAX_THREADS, 'Thread ceiling exceeded'
    assert sample['nofileSoft'] == SOFT_NOFILE, 'Test FD limit changed'
    if idle:
        assert sample['fds'] == baseline['fds'], 'Idle FD count did not recover'
        assert sample['threads'] == baseline['threads'], 'Idle thread count did not recover'


def summarize(baseline, samples, cycles, duration):
    assert duration >= MIN_SECONDS, 'Observation too short'
    assert len(cycles) >= MIN_CYCLES, 'Too few connection cycles'
    assert samples and cycles[-1]['idle'] == samples[-1], 'Final idle sample missing'
    for sample in samples:
        check_sample(baseline, sample)
    for cycle in cycles:
        check_sample(baseline, cycle['idle'], idle=True)
        assert cycle['stateReads'] >= 10 and cycle['catalogReads'] >= 10, 'Insufficient cycle workload'
    assert all(b['elapsedSeconds'] >= a['elapsedSeconds'] for a, b in zip(samples, samples[1:])), 'Sample clock regressed'
    cpu_seconds = (samples[-1]['cpuSeconds'] - baseline['cpuSeconds'])
    return dict(cycles=len(cycles), durationSeconds=round(duration, 2),
                stateReads=sum(c['stateReads'] for c in cycles),
                catalogReads=sum(c['catalogReads'] for c in cycles),
                peakRssKiB=max(s['rssKiB'] for s in samples),
                rssGrowthKiB=max(s['rssKiB'] for s in samples)-baseline['rssKiB'],
                finalRssDeltaKiB=samples[-1]['rssKiB']-baseline['rssKiB'],
                peakFds=max(s['fds'] for s in samples), peakThreads=max(s['threads'] for s in samples),
                qemuCpuSeconds=round(cpu_seconds, 3),
                qemuCpuPercentOfOneCore=round(100*cpu_seconds/duration, 3))
