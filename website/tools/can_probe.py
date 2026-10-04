"""Car test without the server: FREE-WILi + Neptune -> car.

Prints the firmware versions, then asks the car (read-only) for:
  - supported readings, the VIN, stored (03), pending (07) and permanent (0A) codes
  - each answering ECU's own fault memory, asked directly: UDS 19 02, then the
    older KWP 18 02 for ECUs that don't speak UDS (raw reply printed either way)
  - engine RPM once a second
Tries 11-bit CAN IDs first, then 29-bit if nothing answers.
and prints every CAN frame seen, decoded where possible.
Success = a line like  RX 0 0x7E8  04 41 0C 1A F8 ...  ('rpm', 1726.0)
Run from the website folder, car key ON:  python -m tools.can_probe [seconds]
"""
import sys
import time

from freewili import FreeWili
from freewili.types import FreeWiliProcessorType

from server import obd, vin as vin_decoder
from server.sources.freewili import CAN_CHANNEL

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10
isotp = obd.IsoTpAssembler()
pending_fc = []
summary = {}
ecus = set()
extended = False


def describe(payload, data):
    """Plain-language meaning of one complete reply."""
    if payload[0] == 0x7F and len(payload) >= 3:
        return f"negative reply to service 0x{payload[1]:02X} (code 0x{payload[2]:02X}: not supported / not now)"
    for name, fn in (("supported PIDs", obd.parse_supported), ("check-engine lamp, code count", obd.parse_monitor)):
        r = fn(payload)
        if r is not None:
            return f"{name}: {sorted(r) if isinstance(r, set) else r}"
    if payload[0] in obd.DTC_LIST_REPLIES:
        kind = obd.DTC_LIST_REPLIES[payload[0]]
        codes = obd.parse_dtc_list(payload)
        summary[kind] = codes
        return f"{kind} codes: {codes or 'none'}"
    if payload[0] == 0x58:
        summary["kwp"] = payload.hex(" ").upper()
        return f"KWP fault memory (raw, decoded next round): {payload.hex(' ').upper()}"
    if payload[0] == 0x59:
        found = obd.parse_uds_dtcs(payload)
        summary["manufacturer"] = found
        return f"manufacturer fault memory: {found or 'none'}"
    if payload[0] == 0x49:
        vin = obd.parse_vin(payload)
        summary["vin"] = vin
        return f"VIN {vin} -> {vin_decoder.vehicle_name(vin_decoder.decode_offline(vin)) if vin else '?'}"
    return obd.parse_mode01(data) or ""


def on_event(event_type, frame, data):
    if not event_type.name.startswith("CAN"):
        return
    tag = "RX" if "RX" in event_type.name else "TX"
    meaning = ""
    ext = bool(getattr(data, "is_extended", False))
    if tag == "RX" and obd.is_response_id(data.arb_id, ext):
        payload, send_fc = isotp.feed(data.arb_id, data.data)
        if send_fc:
            pending_fc.append(obd.flow_control_id(data.arb_id, ext))
            meaning = "(long reply, asking for the rest)"
        elif payload:
            if payload[0] != 0x7F:
                ecus.add(data.arb_id)
            meaning = describe(payload, data.data)
    print(f"{tag} {event_type.name[-1]} 0x{data.arb_id:03X}  {data.data.hex(' ').upper()}  {meaning}")


def send(fw, can_id, request, label):
    obd.check_tx(can_id, request, extended)    # read-only gate, same as the server
    print(f"TX {CAN_CHANNEL} 0x{can_id:03X}  {request.hex(' ').upper()}  {label} ->",
          fw.can_transmit(CAN_CHANNEL, can_id, request, extended, False))


def broadcast():
    return obd.OBD_REQUEST_ID_29 if extended else obd.OBD_REQUEST_ID


def wait(fw, s):
    end = time.time() + s
    while time.time() < end:
        fw.process_events()
        while pending_fc:
            send(fw, pending_fc.pop(0), obd.FLOW_CONTROL, "flow control")


KWP_FAULT_MEMORY = bytes.fromhex("041802FF00000000")   # KWP2000 read DTCs by status: all


with FreeWili.find_first().expect("No FREE-WILi found. Check the USB cable") as fw:
    print(f"Connected: {fw}")
    for cpu in (FreeWiliProcessorType.Main, FreeWiliProcessorType.Display):
        print(" ", fw.get_app_info(cpu))     # CAN needs Main v92 (SpartaHack firmware)
    fw.set_event_callback(on_event)
    for ch in (0, 1):
        print(f"  CAN{ch} streaming:", fw.can_enable_streaming(ch, True))

    for extended in (False, True):         # who answers: 11-bit IDs first, then 29-bit
        send(fw, broadcast(), obd.build_request(0x01, 0x00), "supported PIDs" + (" (29-bit)" if extended else ""))
        wait(fw, 0.5)
        if ecus:
            break
    else:
        extended = False
        print("!! No ECU answered. Check key ON, wiring, 500 kbit/s and termination OFF.")

    once = [((0x01, 0x01), "check-engine lamp"), ((0x09, 0x02), "VIN"),
            ((0x03,), "stored codes"), ((0x07,), "pending codes"), ((0x0A,), "permanent codes")]
    for params, label in once:
        send(fw, broadcast(), obd.build_request(*params), label)
        wait(fw, 0.4)
    for ecu in sorted(ecus):               # fault memory, asked directly
        target = obd.physical_id(ecu, extended)
        send(fw, target, obd.build_request(*obd.UDS_READ_DTCS), f"fault memory (UDS) of ECU 0x{ecu:X}")
        wait(fw, 0.8)
        send(fw, target, KWP_FAULT_MEMORY, f"fault memory (KWP) of ECU 0x{ecu:X}")
        wait(fw, 0.8)

    rpm = obd.build_request(0x01, 0x0C)
    end = time.time() + seconds
    while time.time() < end:
        send(fw, broadcast(), rpm, "RPM")
        wait(fw, 1)
    for ch in (0, 1):
        fw.can_enable_streaming(ch, False)

print("\nSummary:")
print(f"  {'ECUs':13} {', '.join(f'0x{e:X}' for e in sorted(ecus)) or 'none'}  ({'29' if extended else '11'}-bit)")
for k in ("vin", "stored", "pending", "permanent", "manufacturer", "kwp"):
    print(f"  {k:13} {(summary[k] or 'none') if k in summary else 'no answer'}")
