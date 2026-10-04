# FREE-WILi firmware for Otto (Neptune CAN)

CAN through the Neptune Orca needs the **SpartaHack firmware**: Main **V92** +
Display **V67** (committed to freewili/freewili-firmware on 2026-01-31, the day
before SpartaHack). The current stock OG firmware (v024) has no Neptune CAN support.

| File | Download | SHA-256 |
|---|---|---|
| FreeWiliMainV92.uf2 | [link](https://raw.githubusercontent.com/freewili/freewili-firmware/08850fa9485294272c4417d08709b31180e13006/FreeWiliMainV92.uf2) | `3bd4e477ffcd4a0b76c35490926fdb45c42d07366af6afc90db2ab13e56f38d5` |
| FreeWiliDisplayV67.uf2 | [link](https://raw.githubusercontent.com/freewili/freewili-firmware/08850fa9485294272c4417d08709b31180e13006/FreeWiliDisplayV67.uf2) | `0a18d29dac646bfd30dd759277706060b67c1574ffe34129ef485a092fb1c69b` |
| ogfw_main-024 (rollback) | [release](https://github.com/freewili/freewili-firmware/releases/tag/v024) | `48c3174b73fb392397325aa81f25d40ba5e662d308a86c1dd2664593f7378eb9` |

The .uf2 files are git-ignored; download them here.

## 1. Check first: you may not need to flash

```
cd website
python -m tools.can_probe 5
```
If it prints `Main v92` (or newer in the same line) and `CAN0 streaming: Ok(...)`, skip to step 3.

## 2. Flash MAIN only, then test

CAN runs on the MAIN CPU, so V92 on MAIN is the part that matters. Both images
start at 0x10000000, so a plain UF2 drag-and-drop installs them.

```
python -c "from freewili import FreeWili; from freewili.types import FreeWiliProcessorType as P; fw=FreeWili.find_first().unwrap(); fw.open(); print(fw.reset_to_uf2_bootloader(P.Main))"
```
An `RPI-RP2` drive appears. Copy `FreeWiliMainV92.uf2` onto it, wait for it to
reboot, then rerun step 1.

Only if the display misbehaves afterwards: same command with `P.Display`, then
copy `FreeWiliDisplayV67.uf2`. Untested: whether the v024 display still reboots
into BOOTSEL once MAIN runs V92. If it doesn't, ask the FREE-WILi table.

Rollback to stock: install the OG bootloader and v024 with
[FreeWili OG App Explorer](https://github.com/freewili/fwOGAppExplorer) (see the v024 release notes).

## 3. Wiring (Neptune channel A = API channel 0)

| Car OBD-II pin | Neptune DB15 pin |
|---|---|
| 6 CAN High | 6 CAN_H_A |
| 14 CAN Low | 14 CAN_L_A |
| 5 signal ground | 8 GND |
| 16 +12 V | 15 VBAT: leave disconnected until confirmed |

In the device's Neptune Settings menu: CAN1 rate 500 kbit/s (most cars after
2008), **termination off** (the car's bus is already terminated). If channel A
turns out to be API channel 1, set `FREEWILI_CAN_CHANNEL=1`.

## 4. Bench test without a car (fake car on channel B)

1. Unplug the OBD cable. Wire Neptune channel A to channel B on the DB15:
   pin 6 <-> pin 4 (CAN High), pin 14 <-> pin 12 (CAN Low). Two wires.
2. Neptune Settings: termination **ON** for CAN1 and CAN2, both at 500 kbit/s.
3. Start the server with a scenario for the fake car:
   ```powershell
   cd website
   $env:FREEWILI_BENCH_ECU = "lean"   # healthy, lean, misfire, overheat, low_voltage, thermostat
   .\.venv\Scripts\python -m uvicorn server.app:app --port 8000
   ```
4. Dashboard: Live car -> Start capture. The status reads "BENCH: fake car on
   Neptune channel B". Readings fill in; codes appear as the scenario develops.

Before plugging into a real car: close that terminal (or `Remove-Item Env:FREEWILI_BENCH_ECU`),
remove the A-B wires, and turn termination back OFF.
