"""Ten-minute native read/reconnect soak, only on generated disposable media."""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import json
import os
from pathlib import Path
import resource
import time
import urllib.request

from emulator.runtime.keys import Buttons, Device
from guest_checks import native_resources, stock_processes
from handover import AUTHORITY, connect_native, health, query, released
from soak_metrics import (MAX_FDS, MAX_THREADS, MAX_RSS_KIB, MAX_RSS_GROWTH_KIB,
                          SOFT_NOFILE, check_sample, summarize)

ROOT = Path('/work/rootfs')
REPORT = Path('/work/disc-soak.json')
CYCLES = 20
CYCLE_SECONDS = 30


def snapshot(started):
    value = native_resources()
    # comm may contain spaces/parentheses: fields after its final ')' begin at 3.
    fields = Path(f'/proc/{value["pid"]}/stat').read_text().rsplit(')',1)[1].split()
    return dict(pid=value['pid'], startTicks=int(fields[19]),
                rssKiB=int(value['qemuVmRSS'].split()[0]), fds=value['fds'],
                threads=int(value['threads']),
                cpuSeconds=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK'),
                elapsedSeconds=round(time.monotonic()-started,3),
                nofileSoft=resource.prlimit(value['pid'],resource.RLIMIT_NOFILE)[0])


def catalog(start):
    req = urllib.request.Request('http://127.0.0.1:7870/api/catalog/stream',
        headers={'Host':AUTHORITY,'start-pos':str(start),'num-max':'1'})
    with urllib.request.urlopen(req,timeout=4) as response:
        assert response.status == 200 and response.headers['total-num'] == '3'
        data = json.load(response)
        assert isinstance(data,list) and len(data) == (1 if start<3 else 0), data
        if data:assert data[0]['pos'] == start,data


def playback(ws):
    state = json.loads(query(ws,'0202','a202'))
    song = json.loads(state['song']) if isinstance(state['song'],str) else state['song']
    assert song['song_name'] == 'Second — Ё.flac', song
    return state['state']


def wait_state(ws, expected):
    deadline = time.monotonic()+6
    while playback(ws) != expected:
        assert time.monotonic()<deadline, 'Local playback transition not observed'
        time.sleep(.1)


def pulse(ws, buttons):
    # A short local Play/Pause keeps the ordinary idle policy alive. No setting
    # changes or native mutations; five brief intervals fit this fixture.
    assert playback(ws) == 1
    buttons.gesture('play_pause','single');wait_state(ws,0)
    time.sleep(1)
    buttons.gesture('play_pause','single');wait_state(ws,1)


def settle(baseline, started):
    deadline = time.monotonic()+3
    while True:
        sample = snapshot(started)
        if sample['fds']==baseline['fds'] and sample['threads']==baseline['threads']:
            return sample
        assert time.monotonic()<deadline, ('Idle resources did not recover',baseline,sample)
        time.sleep(.05)


def run():
    stock = stock_processes()  # Also enforces CI_DISPOSABLE=1.
    assert not health()['controlActive'], 'Disconnect browser before soak'
    started = time.monotonic()
    initial = snapshot(started)
    limits = resource.prlimit(initial['pid'],resource.RLIMIT_NOFILE)
    assert limits[1] >= SOFT_NOFILE
    buttons = Buttons(ROOT,Device(ROOT))
    report = dict(status='running', stockPids=stock, initial=initial, samples=[], cycles=[],
                  budgets=dict(rssKiB=MAX_RSS_KIB,rssGrowthKiB=MAX_RSS_GROWTH_KIB,
                               fds=MAX_FDS,threads=MAX_THREADS,nofileSoft=SOFT_NOFILE),
                  scope='QEMU-host regression limits, not physical player memory/CPU or day-long soak acceptance.')
    def save():REPORT.write_text(json.dumps(report,indent=2)+'\n')
    save()
    resource.prlimit(initial['pid'],resource.RLIMIT_NOFILE,(SOFT_NOFILE,limits[1]))
    try:
        # Warm both HTTP and WS paths before measuring allocator/translation growth.
        for _ in range(2):
            ws = connect_native()
            try:
                assert query(ws,'0599','a599','0000') == IDENTITY.encode()
                assert json.loads(query(ws,'0501','a501'))['soc_version'] == MAIN_OS
                assert playback(ws)==1
                for page in range(4):catalog(page)
            finally:ws.close()
            released();settle(initial,started)
        started = time.monotonic()
        baseline = snapshot(started);report['baseline'] = baseline
        for cycle in range(CYCLES):
            cycle_start = time.monotonic()
            ws = connect_native()
            reads = pages = 0
            try:
                assert query(ws,'0599','a599','0000') == IDENTITY.encode()
                settings = json.loads(query(ws,'0501','a501'))
                assert settings['soc_version']==MAIN_OS
                if cycle%4==0:pulse(ws,buttons)
                while time.monotonic()-cycle_start < CYCLE_SECONDS:
                    tick = time.monotonic()
                    assert playback(ws)==1
                    reads += 1;catalog(pages%4);pages += 1
                    sample = snapshot(started);check_sample(baseline,sample)
                    report['samples'].append(sample)
                    assert stock_processes()==stock, 'Stock process changed'
                    time.sleep(max(0,min(2-(time.monotonic()-tick),CYCLE_SECONDS-(time.monotonic()-cycle_start))))
            finally:ws.close()
            released()
            idle = settle(baseline,started);check_sample(baseline,idle,idle=True)
            report['samples'].append(idle)
            report['cycles'].append(dict(cycle=cycle+1,stateReads=reads,catalogReads=pages,idle=idle))
            save()
            print(json.dumps(dict(cycle=cycle+1,of=CYCLES,elapsed=idle['elapsedSeconds'],
                                  rssKiB=idle['rssKiB'],fds=idle['fds'],threads=idle['threads'])),flush=True)
        report['summary'] = summarize(baseline,report['samples'],report['cycles'],time.monotonic()-started)
        assert stock_processes()==stock
        assert not health()['controlActive']
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed',error=f'{type(error).__name__}: {error}')
        raise
    finally:
        # Do not restore limits onto a replacement/reused PID after a failure.
        try:
            current = snapshot(started)
            assert (current['pid'],current['startTicks']) == (initial['pid'],initial['startTicks']), 'Native identity changed before cleanup'
            resource.prlimit(initial['pid'],resource.RLIMIT_NOFILE,limits)
            report['nofileRestored'] = resource.prlimit(initial['pid'],resource.RLIMIT_NOFILE)==limits
            assert report['nofileRestored'], 'FD limit restoration failed'
        except Exception as error:
            report['cleanupError'] = f'{type(error).__name__}: {error}'
            if report['status']=='passed':
                report['status']='failed'
                raise
        finally:save()
    print(json.dumps(report['summary'],indent=2),flush=True)


if __name__ == '__main__':run()
