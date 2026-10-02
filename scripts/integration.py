#!/usr/bin/env python3
"""Bounded integration stage for the recorded disposable emulator."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from firmware_profile import state_profile, require_scenario
ROOT=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--lifecycle',action='store_true',help='Include two-minute session and deliberate stock loss/reboot')
parser.add_argument('--transitions',action='store_true',help='Include isolated cable loss, display sleep/wake and explicit guest Power')
parser.add_argument('--offline',action='store_true',help='Include guest boot without Ethernet/Wi-Fi in a private network namespace')
parser.add_argument('--idle',action='store_true',help='Wait for natural five-minute stock idle shutdown and explicit recovery')
parser.add_argument('--soak',action='store_true',help='Ten-minute native read/reconnect resource acceptance with short local Play/Pause pulses')
parser.add_argument('--gateway',action='store_true',help='Publish a catalog release on the disposable card and drive guarded mutations, upload and scan against stock')
parser.add_argument('--history',action='store_true',help='Record a stock play in the card database, restart the service and read it back')
parser.add_argument('--store',action='store_true',help='Dislike a track through the card store and watch the service skip it on stock')
parser.add_argument('--trash',action='store_true',help='Move a scanned probe folder to the card trash, rescan, restore, replace a cover, empty')
parser.add_argument('--audio',action='store_true',help='Read generated card audio through the audio route, whole and in byte ranges')
parser.add_argument('--recovery',action='store_true',help='Kill the supervised service (restarted), then stop it with the card switch')
parser.add_argument('--about',action='store_true',help='Read the diagnostics document and serve the page from the image while the card has none')
parser.add_argument('--cue',action='store_true',help='Play a generated three-track CUE image: one play per track; a disliked CUE track is skipped')
parser.add_argument('--m3u',action='store_true',help='Research stock M3U lists: forms, navigation, history, favorites (evidence for docs/m3u.md)')
parser.add_argument('--lists',action='store_true',help='Combined-009 routes on stock: an M3U list written, played through the gateway, replaced and deleted; a browser play in the history')
parser.add_argument('--queue-research',action='store_true',help='Research for combined-009: long lists, a visible list folder, queue swaps heard in the audio capture, the queue over a pause and an idle power-off')
args=parser.parse_args()
state=json.loads((ROOT/'work/emulator.json').read_text())
profile=state_profile(state)
for scenario in ('smoke', 'coexistence', 'handover'):
    require_scenario(profile, scenario)
for scenario in ('lifecycle', 'transitions', 'offline', 'idle', 'soak'):
    if getattr(args, scenario):require_scenario(profile, scenario)
if args.gateway or args.history or args.store or args.trash or args.audio or args.recovery or args.about or args.cue or args.m3u or args.queue_research or args.lists:require_scenario(profile, 'smoke')
container=state['id']+'-emu'
subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
subprocess.run([sys.executable,str(ROOT/'tests/integration/native_smoke.py')],check=True)
if args.gateway or args.store or args.trash or args.cue or args.m3u or args.queue_research or args.lists or args.about:
    # combined-009: the reviewed catalogs come with the (guest) image; the disposable card needs an app at /
    # with the reviewed origins for the page's policy. The probe goes there unless an app already is
    # (the page's acceptance installs Disc Player itself); the guest's SN is the credential.
    subprocess.run(['docker','exec','-i',container,'python3','-','/tmp/sdcard'],input=b"""
import pathlib, shutil, subprocess, sys
card = pathlib.Path(sys.argv[1])
if not (card/'Apps'/'Disc Player'/'index.html').is_file():
    out = pathlib.Path('/work/probe-app.zip')
    out.unlink(missing_ok=True)
    subprocess.run(['python3', '/platform/scripts/app_bundle.py', 'zip', '--source', '/platform/apps/probe', '--output', str(out),
                    '--origins'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['python3', '/platform/scripts/app_bundle.py', 'install', '--app', str(out), '--card', str(card),
                    '--confirm-card-write'], check=True, stdout=subprocess.DEVNULL)
""",check=True)
if args.gateway:
    try:
        subprocess.run(['docker','exec',container,'timeout','300','python3','-B','/platform/tests/integration/gateway_mutation.py'],check=True,timeout=310)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-gateway.json',str(ROOT/'work/gateway-acceptance.json')],check=False)
        # Remove the uploaded copies from the disposable card and rescan so later fixtures see the generated media only.
        subprocess.run(['docker','exec',container,'rm','-rf','/tmp/sdcard/Gateway Upload'],check=True)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.history:
    try:
        subprocess.run(['docker','exec',container,'timeout','120','python3','-B','/platform/tests/integration/history_database.py','record'],check=True,timeout=130)
        # A restarted service knows nothing of the file: its first open runs the quick check.
        subprocess.run([sys.executable,str(ROOT/'scripts/emulator.py'),'start-service'],check=True)
        subprocess.run(['docker','exec',container,'timeout','60','python3','-B','/platform/tests/integration/history_database.py','reopen'],check=True,timeout=70)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-history.json',str(ROOT/'work/history-acceptance.json')],check=False)
        # Play all left stock on another queue; later scenarios expect the prepared fixture.
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.store:
    try:
        subprocess.run(['docker','exec',container,'timeout','150','python3','-B','/platform/tests/integration/store_skip.py'],check=True,timeout=160)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-store.json',str(ROOT/'work/store-acceptance.json')],check=False)
        # Play all left stock on another queue; later scenarios expect the prepared fixture.
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.trash:
    try:
        subprocess.run(['docker','exec',container,'timeout','400','python3','-B','/platform/tests/integration/trash_card.py'],check=True,timeout=410)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-trash.json',str(ROOT/'work/trash-acceptance.json')],check=False)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.audio:
    try:
        subprocess.run(['docker','exec',container,'timeout','120','python3','-B','/platform/tests/integration/audio_route.py'],check=True,timeout=130)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-audio.json',str(ROOT/'work/audio-acceptance.json')],check=False)
if args.recovery:
    try:
        subprocess.run(['docker','exec',container,'timeout','90','python3','-B','/platform/tests/integration/self_recovery.py','crash'],check=True,timeout=100)
        subprocess.run(['docker','exec',container,'timeout','60','python3','-B','/platform/tests/integration/self_recovery.py','switch'],check=True,timeout=70)
    finally:
        subprocess.run(['docker','exec',container,'rm','-rf','/tmp/sdcard/.disc/disabled'],check=True)
        subprocess.run(['docker','cp',container+':/work/disc-recovery.json',str(ROOT/'work/recovery-acceptance.json')],check=False)
        subprocess.run([sys.executable,str(ROOT/'scripts/emulator.py'),'start-service'],check=True)
if args.about:
    try:
        subprocess.run(['docker','exec',container,'timeout','60','python3','-B','/platform/tests/integration/about_page.py'],check=True,timeout=70)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-about.json',str(ROOT/'work/about-acceptance.json')],check=False)
if args.cue:
    try:
        subprocess.run(['docker','exec',container,'timeout','400','python3','-B','/platform/tests/integration/cue_plays.py'],check=True,timeout=410)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-cue.json',str(ROOT/'work/cue-acceptance.json')],check=False)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.m3u:
    try:
        subprocess.run(['docker','exec',container,'timeout','600','python3','-B','/platform/tests/integration/m3u_playback.py'],check=True,timeout=610)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-m3u.json',str(ROOT/'work/m3u-research.json')],check=False)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.lists:
    try:
        subprocess.run(['docker','exec',container,'timeout','300','python3','-B','/platform/tests/integration/lists_history.py'],check=True,timeout=310)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-lists.json',str(ROOT/'work/lists-acceptance.json')],check=False)
        subprocess.run(['docker','exec',container,'rm','-rf','/tmp/sdcard/Lists Acceptance'],check=True)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.queue_research:
    research=['docker','exec',container,'python3','-B','/platform/tests/integration/queue_research.py']
    try:
        subprocess.run(research+['run'],check=True,timeout=900)
        subprocess.run(research+['before-idle'],check=True,timeout=120)
        subprocess.run(['docker','exec',container,'timeout','430','python3','-B','/platform/tests/integration/queue_research.py','wait-off'],check=True,timeout=440)
        subprocess.run([sys.executable,str(ROOT/'scripts/emulator.py'),'boot'],check=True)
        subprocess.run(research+['after-boot'],check=True,timeout=180)
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-queue-research.json',str(ROOT/'work/queue-research.json')],check=False)
        subprocess.run(research+['cleanup'],check=False,timeout=300)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
if args.soak:
    try:
        subprocess.run(['docker','exec',container,'timeout','720','python3','-B','/platform/tests/integration/soak.py'],check=True,timeout=730)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
        # A failed read may leave playback uncertain. Recover the disposable
        # guest explicitly instead of blindly toggling Play/Pause after timeout.
        subprocess.run([sys.executable,str(ROOT/'scripts/emulator.py'),'boot'],check=True)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
        raise
    finally:
        subprocess.run(['docker','cp',container+':/work/disc-soak.json',str(ROOT/'work/soak.json')],check=False)
if args.offline:
    subprocess.run([sys.executable,str(ROOT/'tests/integration/offline.py')],check=True)
if args.transitions:
    subprocess.run([sys.executable,str(ROOT/'tests/integration/transitions.py')],check=True)
if args.idle:
    try:
        subprocess.run(['docker','exec',container,'timeout','410','python3','-B','/platform/tests/integration/idle_supervision.py'],check=True,timeout=420)
        subprocess.run(['docker','cp',container+':/work/disc-idle-supervision.json',str(ROOT/'work/idle-supervision.json')],check=True)
    finally:
        subprocess.run([sys.executable,str(ROOT/'scripts/emulator.py'),'boot'],check=True)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
        subprocess.run([sys.executable,str(ROOT/'tests/integration/native_smoke.py')],check=True)
if args.lifecycle:
    try:
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/lifecycle.py'],check=True)
        subprocess.run(['docker','cp',container+':/work/disc-lifecycle.json',str(ROOT/'work/lifecycle.json')],check=True)
    finally:
        subprocess.run([sys.executable,str(ROOT/'scripts/emulator.py'),'boot'],check=True)
        subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/prepare_guest.py'],check=True)
        subprocess.run([sys.executable,str(ROOT/'tests/integration/native_smoke.py')],check=True)
subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/coexistence.py'],check=True)
subprocess.run(['docker','cp',container+':/work/disc-coexistence.json',str(ROOT/'work/coexistence.json')],check=True)
subprocess.run(['docker','exec',container,'python3','-B','/platform/tests/integration/handover.py'],check=True)
subprocess.run(['docker','cp',container+':/work/disc-handover.json',str(ROOT/'work/handover.json')],check=True)
subprocess.run([sys.executable,str(ROOT/'scripts/emulator.py'),'start-service'],check=True)
subprocess.run([sys.executable,str(ROOT/'tests/integration/native_smoke.py')],check=True)
