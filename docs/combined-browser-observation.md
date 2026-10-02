# Physical LAN and browser observation — 2026-09-24

The owner returned the safely ejected PLAY card to the player and reported
Wi-Fi address `192.168.88.21`. The Mac reached the engineering listener at
port 7870. `GET /api/health` returned HTTP 200 with `readOnly:true` and
`controlActive:false`. `GET /` returned 2,664 bytes, SHA-256
`f644516171009b3f77126c27f8d91b7827f8bd467622d38dcdc94b49d9618e2d`,
identical to `index.html` in the published SD release
`fe0753a8ec469745`. The in-app Chromium browser rendered the native probe
page from the same physical address. This establishes SD mount, remote marker
admission and static web serving without a second NAND write.

The first browser **Connect** attempt ended `Disconnected`. Afterward, the
player still answered ICMP, but TCP port 7870 refused connections. The owner
rebooted normally with the USB cable attached and confirmed the stock screen.
The listener and USB console returned. Before a single deliberate reproduction,
`/run/disc-web.pid` named PID 1022, `ps` showed `disc-service` with the
reviewed `0.0.0.0`/SD/LAN arguments, and `/api/health` again returned 200.
After **Connect**, the browser again reported `Disconnected`, port 7870 refused
connections, and PID 1022 no longer appeared. The kernel recorded:

```text
do_page_fault(): sending SIGSEGV to civetweb-worker for invalid read access from 77c587e0
epc = 77c587e0 inra = 77cc1f34 in disc-service[77caa000+4c000]
```

The installed `disc-service` SHA-256 is
`858d6f892041e1d2ad474c58e6fa3c0f1319c82522567afc1095c66663a0b959`.
The complete bounded console observations are retained only in ignored
`work/combined-003-service-before.json`, `work/combined-003-service-after.json`
and `work/combined-003-service-kernel-after.json`. No process was restarted by
the agent and no device settings, boot hooks or NAND were changed in the
diagnostic session.

## Corrected analysis (later the same day)

The first write-up resolved the `ra` register to offset `0x21f34` (musl
allocator `enframe`). That subtraction was wrong: `0x77cc1f34 - 0x77caa000 =
0x17f34`. Rebuilding revision `f887984` in the pinned toolchain reproduces the
installed layout (identical 708,280-byte size; the `.text` section only differs
from the current build by a 16-byte shift), and at `0x17f34` the executable
holds the return address of `bal atoi` in CivetWeb `read_websocket`
(`civetweb.c:13232`, `timeout = atoi(...) / 1000.0`). The following instruction
stream is the first floating-point work of the connection:

```text
17f38  mtc1   v0,$f0
17f40  cvt.d.w $f20,$f0
17f54  div.d  $f20,$f20,$f0
17f58  c.le.d $f20,$f22        ; if (timeout <= 0.0)
17f60  bc1f   18000            ; taken: websocket_timeout_ms 2000 > 0
17f64  li     v0,1             ; delay slot, not a nop
```

The player kernel is Linux 4.4.94 (boot report (snowsky-disc-boot `docs/boot-report-observation.md`)).
Before Linux 4.8, the MIPS FPU emulator executes the delay slot of a taken
floating-point branch by writing that instruction plus a `break` into a frame on
the **user stack** and pointing `epc` at it (`mips_dsemul`). If that stack page
is not executable on a CPU with execute-inhibit, `do_page_fault` reports exactly
the recorded signature: a read fault whose address equals `epc`, in an
anonymous mapping (no file name printed), with `ra` still valid. The fault
address `0x77c587e0` is 8-byte aligned and lies about 22.5 KiB below a
plausible worker-stack top, matching the frames of `handle_request` (16.6 KiB),
`read_websocket` (4.2 KiB) and their callers. The thread still carried the
`civetweb-worker` name because `read_websocket` renames it to `wsock` only
after computing the timeout. The disposable guest cannot reproduce this: qemu
user emulation executes floating point itself and never enters the kernel
trampoline. The installed executable is hard-float (`readelf -A`: "Hard float
(double precision)"), and musl creates thread stacks with `mmap(PROT_NONE)` plus
`mprotect(PROT_READ|PROT_WRITE)`.

Still unverified on the device, all read-only through the USB console:
`/proc/cpuinfo` (FPU presence and mode), `/proc/<pid>/maps` of a healthy
service (thread stack permissions) and the FP ABI of stock user-space
executables. The earlier candidates (SHA-1 vectorization, unaligned access,
`USE_STACK_SIZE`, the cited CivetWeb incidents, heap corruption) do not explain
a valid `ra` together with an `epc` outside every file mapping and are closed.

Fix: build the native companion **soft-float** so no floating-point
instruction ever traps into the kernel emulator. `device/Dockerfile.toolchain`
now pins the musl.cc `mipsel-linux-muslsf-cross` toolchain by SHA-256,
`scripts/build.sh mips` refuses executables whose MIPS ABI flags are not
soft-float or that still contain FPU instructions, and the candidate builder
rejects non-soft-float executables before packaging. The rebuilt service must
pass the synthetic MIPS conformance and integration checks in the disposable
guest, then needs a new reviewed image and a separately authorized physical
installation before the interactive LAN acceptance item can be checked.
That installation (combined-004 (snowsky-disc-web `docs/combined-004-installation-observation.md`))
completed and the owner's browser session passed on the soft-float build.

Evidence for the soft-float rebuild: `disc-service` 785,260 bytes, SHA-256
`37c391fc8db870ae6e63865ee16c2e6b6e52efb5ad49b1b2d50cecd29c941c0f`, `FP ABI:
Soft float`, zero FPU instructions in the disassembly (the previous hard-float
build carried 393); the same guard rejected the hard-float toolchain's output.
Host conformance (299 tests), MIPS conformance in the disposable guest
(32 tests) and the integration smoke pass with the new executable. These are
qemu user-mode results and do not by themselves certify the physical kernel.

Separately, the first published diagnostic page still contained a literal
`257` firmware check. The page source now takes protocol identity and main OS
number from `compatibility.json`, generated from the selected reviewed firmware
profile; the release digest includes that file. The updated bundle has not yet
been published on PLAY, and it cannot by itself repair the native crash.
