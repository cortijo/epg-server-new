#!/usr/bin/env python3
"""Verify DVB EIT and TDT/TOT timing in a UDP multicast transport stream."""

import argparse
import datetime as dt
import ipaddress
import socket
import struct
import time
from pathlib import Path


def bcd(value):
    return ((value >> 4) * 10) + (value & 0x0F)


def dvb_time(raw):
    mjd = (raw[0] << 8) | raw[1]
    day = dt.date(1970, 1, 1) + dt.timedelta(days=mjd - 40587)
    return dt.datetime(day.year, day.month, day.day,
                       bcd(raw[2]), bcd(raw[3]), bcd(raw[4]),
                       tzinfo=dt.timezone.utc)


parser = argparse.ArgumentParser()
parser.add_argument("group", nargs="?")
parser.add_argument("port", nargs="?", type=int)
parser.add_argument("--file", help="captura MPEG-TS bruta em vez de multicast ao vivo")
parser.add_argument("--interface", default="0.0.0.0")
parser.add_argument("--seconds", type=int, default=15)
parser.add_argument("--expected-tsid", type=int)
parser.add_argument("--expected-onid", type=int)
parser.add_argument("--expected-title-latin9")
parser.add_argument("--expected-offset-minutes", type=int)
parser.add_argument("--expected-correction-minutes", type=int, default=0)
args = parser.parse_args()

tables = {}
if args.file:
    datagrams = [Path(args.file).read_bytes()]
else:
    if not args.group or args.port is None:
        parser.error("informe GROUP PORT ou use --file CAPTURA.ts")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", args.port))
    if ipaddress.ip_address(args.group).is_multicast:
        membership = socket.inet_aton(args.group) + socket.inet_aton(args.interface)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    sock.settimeout(1)
    datagrams = []
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        try:
            datagram, _ = sock.recvfrom(65535)
            datagrams.append(datagram)
        except socket.timeout:
            continue

for datagram in datagrams:
    for offset in range(0, len(datagram) - 187, 188):
        packet = datagram[offset:offset + 188]
        if packet[0] != 0x47 or not (packet[1] & 0x40):
            continue
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        if pid not in (0x12, 0x14):
            continue
        adaptation = (packet[3] >> 4) & 0x03
        payload = 4
        if adaptation == 3:
            payload += 1 + packet[4]
        if payload >= 188:
            continue
        payload += 1 + packet[payload]
        if payload >= 188:
            continue
        table_id = packet[payload]
        tables.setdefault(table_id, packet[payload:])

print("Tabelas:", ", ".join(f"0x{item:02X}" for item in sorted(tables)))
decoded_clock = {}
for table_id in (0x70, 0x73):
    section = tables.get(table_id)
    if section and len(section) >= 8:
        decoded_clock[table_id] = dvb_time(section[3:8])
        print(f"{'TDT' if table_id == 0x70 else 'TOT'} codificado:",
              decoded_clock[table_id].isoformat())

tot = tables.get(0x73)
if tot and len(tot) >= 20 and tot[10] == 0x58:
    polarity = -1 if (tot[15] & 1) else 1
    minutes = polarity * (bcd(tot[16]) * 60 + bcd(tot[17]))
    print("TOT país:", bytes(tot[12:15]).decode("ascii", "replace"))
    print("TOT deslocamento local:", f"{minutes // 60:+03d}:{abs(minutes) % 60:02d}")
    if args.expected_offset_minutes is not None and minutes != args.expected_offset_minutes:
        raise SystemExit(
            f"Offset TOT incorreto: esperado={args.expected_offset_minutes} recebido={minutes}")

if args.expected_offset_minutes is not None and 0x70 in decoded_clock:
    expected_shift = args.expected_offset_minutes + args.expected_correction_minutes
    observed_shift = (decoded_clock[0x70] - dt.datetime.now(dt.timezone.utc)).total_seconds() / 60
    print("Deslocamento total observado:", f"{observed_shift:+.1f} min")
    if abs(observed_shift - expected_shift) > 2:
        raise SystemExit(
            f"Relógio incorreto: esperado={expected_shift:+d} min recebido={observed_shift:+.1f} min")

eit = tables.get(0x4E)
if eit and len(eit) >= 21:
    print("EIT evento atual UTC:", dvb_time(eit[16:21]).isoformat())
    service_id = (eit[3] << 8) | eit[4]
    version = (eit[5] >> 1) & 0x1F
    tsid = (eit[8] << 8) | eit[9]
    onid = (eit[10] << 8) | eit[11]
    print("EIT IDs:", f"service_id={service_id} tsid={tsid} onid={onid} version={version}")
    if args.expected_tsid is not None and tsid != args.expected_tsid:
        raise SystemExit(f"TSID incorreto: esperado={args.expected_tsid} recebido={tsid}")
    if args.expected_onid is not None and onid != args.expected_onid:
        raise SystemExit(f"ONID incorreto: esperado={args.expected_onid} recebido={onid}")
    if args.expected_title_latin9 is not None:
        if len(eit) < 32 or eit[26] != 0x4D:
            raise SystemExit("short_event_descriptor 0x4D ausente na EIT p/f")
        title_length = eit[31]
        title_bytes = bytes(eit[32:32 + title_length])
        expected_bytes = args.expected_title_latin9.encode("iso-8859-15")
        print("EIT título Latin-9:", title_bytes.decode("iso-8859-15", "replace"))
        if title_bytes != expected_bytes:
            raise SystemExit(
                f"Título Latin-9 incorreto: esperado={expected_bytes!r} recebido={title_bytes!r}")

required = {0x4E, 0x50, 0x70, 0x73}
missing = required.difference(tables)
if missing:
    print("Ausentes:", ", ".join(f"0x{item:02X}" for item in sorted(missing)))
    raise SystemExit(2)
