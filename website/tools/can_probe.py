"""First-frame test: FREE-WILi + Neptune -> car. No server needed.

Prints the firmware versions, asks the car for RPM once a second, and prints
every CAN frame seen. Success = a line like  RX 0x7E8  04 41 0C 1A F8 ...
Run from project root, car key ON:  python -m tools.can_probe [seconds]
"""
import sys
import time

from freewili import FreeWili
from freewili.types import FreeWiliProcessorType

from server import obd
from server.sources.freewili import CAN_CHANNEL

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10


def on_event(event_type, frame, data):
    if event_type.name.startswith("CAN"):
        tag = "RX" if "RX" in event_type.name else "TX"
        decoded = obd.parse_mode01(data.data) if data.arb_id in obd.ECU_RESPONSE_IDS else None
        print(f"{tag} {event_type.name[-1]} 0x{data.arb_id:03X}  {data.data.hex(' ').upper()}  {decoded or ''}")


with FreeWili.find_first().expect("No FREE-WILi found. Check the USB cable") as fw:
    print(f"Connected: {fw}")
    for cpu in (FreeWiliProcessorType.Main, FreeWiliProcessorType.Display):
        print(" ", fw.get_app_info(cpu))     # CAN needs Main v92 (SpartaHack firmware)
    fw.set_event_callback(on_event)
    for ch in (0, 1):
        print(f"  CAN{ch} streaming:", fw.can_enable_streaming(ch, True))
    request = obd.build_request(0x01, 0x0C)  # read-only: engine RPM
    obd.check_tx(obd.OBD_REQUEST_ID, request)
    end, next_tx = time.time() + seconds, 0.0
    while time.time() < end:
        if time.time() >= next_tx:
            next_tx = time.time() + 1
            print(f"TX {CAN_CHANNEL} 0x7DF  {request.hex(' ').upper()}  ->",
                  fw.can_transmit(CAN_CHANNEL, obd.OBD_REQUEST_ID, request, False, False))
        fw.process_events()
    for ch in (0, 1):
        fw.can_enable_streaming(ch, False)
