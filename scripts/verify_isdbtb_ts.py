#!/usr/bin/env python3
"""Validate the PSI/SI subset exported by TVStream's EPG diagnostic endpoint."""

import argparse
import collections
import json
import struct
import sys


TS_SIZE = 188


def mpeg_crc32(data):
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def payload(packet):
    control = (packet[3] >> 4) & 0x03
    if control not in (1, 3):
        return b""
    offset = 4
    if control == 3:
        offset += 1 + packet[4]
    return packet[offset:] if offset < TS_SIZE else b""


class SectionAssembler:
    def __init__(self):
        self.buffer = bytearray()
        self.sections = []
        self.started = False

    def _consume(self):
        while len(self.buffer) >= 3:
            if self.buffer[0] == 0xFF:
                self.buffer.clear()
                return
            total = 3 + (((self.buffer[1] & 0x0F) << 8) | self.buffer[2])
            if total < 3 or total > 4096:
                self.buffer.clear()
                return
            if len(self.buffer) < total:
                return
            self.sections.append(bytes(self.buffer[:total]))
            del self.buffer[:total]

    def push(self, data, payload_start):
        if not data:
            return
        if payload_start:
            self.started = True
            pointer = data[0]
            boundary = 1 + pointer
            if boundary > len(data):
                self.buffer.clear()
                return
            if pointer and self.buffer:
                self.buffer.extend(data[1:boundary])
                self._consume()
            self.buffer.clear()
            self.buffer.extend(data[boundary:])
        else:
            if not self.started:
                return
            self.buffer.extend(data)
        self._consume()


def parse_bit_section(section, errors):
    """Return BIT identity and second-loop SI announcements."""
    result = {
        "onid": (section[3] << 8) | section[4],
        "version": (section[5] >> 1) & 0x1F,
        "broadcasters": [],
        "announced_tables": set(),
    }
    first_descriptors_length = ((section[8] & 0x0F) << 8) | section[9]
    offset = 10 + first_descriptors_length
    section_end = len(section) - 4
    while offset + 3 <= section_end:
        broadcaster_id = section[offset]
        descriptors_length = ((section[offset + 1] & 0x0F) << 8) | section[offset + 2]
        descriptor_offset = offset + 3
        descriptor_end = descriptor_offset + descriptors_length
        announced = []
        if descriptor_end > section_end:
            errors.append("BIT possui loop de broadcaster truncado")
            break
        while descriptor_offset + 2 <= descriptor_end:
            tag = section[descriptor_offset]
            length = section[descriptor_offset + 1]
            body_start = descriptor_offset + 2
            body_end = body_start + length
            if body_end > descriptor_end:
                errors.append("BIT possui descritor truncado")
                break
            body = section[body_start:body_end]
            if tag == 0xD7 and len(body) >= 3:
                table_offset = 3
                while table_offset + 2 <= len(body):
                    announced_table = body[table_offset]
                    description_length = body[table_offset + 1]
                    if table_offset + 2 + description_length > len(body):
                        errors.append("descritor SI 0xD7 possui descrição truncada")
                        break
                    result["announced_tables"].add(announced_table)
                    announced.append(announced_table)
                    table_offset += 2 + description_length
            descriptor_offset = body_end
        result["broadcasters"].append({
            "broadcaster_id": broadcaster_id,
            "announced_tables": announced,
        })
        offset = descriptor_end
    return result


def validate(path, expected_services, expected_tsid, expected_onid,
             epg_only=False, pmt_pids=None, require_logo=False,
             logo_services=None, require_content_category=False):
    if isinstance(expected_services, int):
        expected_services = [expected_services]
    expected_services = set(expected_services)
    logo_services = set(logo_services or (expected_services if require_logo else []))
    pmt_pids = set(pmt_pids or [0x1000])
    raw = path.read_bytes()
    errors = []
    if not raw or len(raw) % TS_SIZE:
        errors.append("arquivo não é uma sequência integral de pacotes de 188 bytes")
    packet_count = len(raw) // TS_SIZE
    pid_counts = collections.Counter()
    continuity_errors = collections.Counter()
    last_continuity = {}
    assemblers = collections.defaultdict(SectionAssembler)

    for index in range(packet_count):
        packet = raw[index * TS_SIZE:(index + 1) * TS_SIZE]
        if len(packet) != TS_SIZE or packet[0] != 0x47:
            errors.append(f"sync byte inválido no pacote {index}")
            continue
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        pid_counts[pid] += 1
        control = (packet[3] >> 4) & 0x03
        if control in (1, 3):
            continuity = packet[3] & 0x0F
            if pid in last_continuity and continuity != ((last_continuity[pid] + 1) & 0x0F):
                continuity_errors[pid] += 1
            last_continuity[pid] = continuity
        assemblers[pid].push(payload(packet), bool(packet[1] & 0x40))

    table_counts = collections.Counter()
    eit_tables = collections.Counter()
    pf_sections = collections.defaultdict(set)
    schedule_tables = set()
    running_status = {}
    crc_errors = 0
    crc_errors_by_table = collections.Counter()
    id_errors = 0
    id_error_details = []
    logo_descriptors = {}
    cdt_download_ids = set()
    logo_cdts = []
    logo_cdt_keys = set()
    sdt_versions = set()
    bit_versions = set()
    bit_onids = set()
    bit_broadcasters = []
    bit_announced_tables = set()
    content_nibbles = collections.Counter()
    synopsis_checks = 0
    repeated_synopsis_prefixes = 0
    event_details = {}
    for pid, assembler in assemblers.items():
        for section in assembler.sections:
            table_id = section[0]
            table_counts[table_id] += 1
            has_crc = table_id != 0x70
            if has_crc and mpeg_crc32(section) != 0:
                crc_errors += 1
                crc_errors_by_table[table_id] += 1
            if 0x4E <= table_id <= 0x6F and len(section) >= 18:
                eit_tables[table_id] += 1
                service = (section[3] << 8) | section[4]
                tsid = (section[8] << 8) | section[9]
                onid = (section[10] << 8) | section[11]
                if service not in expected_services or (tsid, onid) != (expected_tsid, expected_onid):
                    id_errors += 1
                    id_error_details.append(
                        f"EIT SID/TSID/ONID={service}/{tsid}/{onid}")
                section_number = section[6]
                if table_id == 0x4E:
                    pf_sections[service].add(section_number)
                    if len(section) > 24 and section[14] != 0xFF:
                        running_status[section_number] = (section[24] >> 5) & 0x07
                if 0x50 <= table_id <= 0x5F:
                    schedule_tables.add(table_id)
                event_offset = 14
                while event_offset + 12 <= len(section) - 4:
                    descriptors_length = ((section[event_offset + 10] & 0x0F) << 8) | section[event_offset + 11]
                    descriptor_offset = event_offset + 12
                    descriptor_end = descriptor_offset + descriptors_length
                    if descriptor_end > len(section) - 4:
                        errors.append("EIT possui loop de descritores truncado")
                        break
                    short_text = b""
                    event_title = b""
                    extended_parts = {}
                    descriptor_tags = []
                    event_categories = []
                    while descriptor_offset + 2 <= descriptor_end:
                        tag = section[descriptor_offset]
                        length = section[descriptor_offset + 1]
                        body_end = descriptor_offset + 2 + length
                        if body_end > descriptor_end:
                            errors.append("EIT possui descritor truncado")
                            break
                        body = section[descriptor_offset + 2:body_end]
                        descriptor_tags.append(tag)
                        if tag == 0x4D and len(body) >= 5:
                            name_length = body[3]
                            event_title = body[4:4 + name_length]
                            text_length_offset = 4 + name_length
                            if text_length_offset < len(body):
                                text_length = body[text_length_offset]
                                text_start = text_length_offset + 1
                                short_text = body[text_start:text_start + text_length]
                        elif tag == 0x4E and len(body) >= 6:
                            descriptor_number = body[0] >> 4
                            items_length = body[4]
                            text_length_offset = 5 + items_length
                            if text_length_offset < len(body):
                                text_length = body[text_length_offset]
                                text_start = text_length_offset + 1
                                extended_parts[descriptor_number] = body[
                                    text_start:text_start + text_length]
                        elif tag == 0x54:
                            for item in range(0, len(body) - 1, 2):
                                content_nibbles[body[item]] += 1
                                event_categories.append(body[item])
                        descriptor_offset = body_end
                    if extended_parts:
                        synopsis_checks += 1
                        extended_text = b"".join(
                            extended_parts[index] for index in sorted(extended_parts))
                        if short_text and extended_text.startswith(short_text):
                            repeated_synopsis_prefixes += 1
                            event_id = (section[event_offset] << 8) | section[event_offset + 1]
                            errors.append(
                                "EIT repete no 0x4E o prefixo já enviado no 0x4D "
                                f"(SID {service}, event_id {event_id})")
                    if table_id == 0x4E:
                        event_id = (section[event_offset] << 8) | section[event_offset + 1]
                        extended_text = b"".join(
                            extended_parts[index] for index in sorted(extended_parts))
                        event_details[(service, section_number, event_id)] = {
                            "service_id": service, "section_number": section_number,
                            "event_id": event_id,
                            "title": event_title.decode("iso-8859-15", "replace"),
                            "short_text_0x4d": short_text.decode("iso-8859-15", "replace"),
                            "extended_text_0x4e": extended_text.decode("iso-8859-15", "replace"),
                            "tv_text": (short_text + extended_text).decode(
                                "iso-8859-15", "replace"),
                            "descriptor_tags": [f"0x{tag:02X}" for tag in descriptor_tags],
                            "content_categories_0x54": [
                                f"0x{value:02X}" for value in event_categories],
                            "running_status": (section[event_offset + 10] >> 5) & 0x07,
                        }
                    event_offset = descriptor_end
            elif table_id == 0x00 and len(section) >= 12:
                tsid = (section[3] << 8) | section[4]
                programs = []
                for offset in range(8, len(section) - 4, 4):
                    program = (section[offset] << 8) | section[offset + 1]
                    if program:
                        programs.append(program)
                if tsid != expected_tsid or not expected_services.issubset(programs):
                    id_errors += 1
                    id_error_details.append(f"PAT TSID/programas={tsid}/{programs}")
            elif table_id == 0x02 and len(section) >= 12:
                program = (section[3] << 8) | section[4]
                if program not in expected_services:
                    id_errors += 1
                    id_error_details.append(f"PMT program_number={program}")
            elif table_id == 0x42 and len(section) >= 16:
                sdt_versions.add((section[5] >> 1) & 0x1F)
                tsid = (section[3] << 8) | section[4]
                onid = (section[8] << 8) | section[9]
                services = []
                offset = 11
                while offset + 5 <= len(section) - 4:
                    service = (section[offset] << 8) | section[offset + 1]
                    services.append(service)
                    descriptors_length = ((section[offset + 3] & 0x0F) << 8) | section[offset + 4]
                    descriptor_offset = offset + 5
                    descriptor_end = descriptor_offset + descriptors_length
                    while descriptor_offset + 2 <= descriptor_end:
                        tag = section[descriptor_offset]
                        length = section[descriptor_offset + 1]
                        body = section[descriptor_offset + 2:descriptor_offset + 2 + length]
                        if tag == 0xCF and len(body) >= 7 and body[0] == 0x01:
                            logo_descriptors[service] = {
                                "logo_id": ((body[1] << 8) | body[2]) & 0x01FF,
                                "logo_version": ((body[3] << 8) | body[4]) & 0x0FFF,
                                "download_data_id": (body[5] << 8) | body[6],
                            }
                        descriptor_offset += 2 + length
                    offset += 5 + descriptors_length
                if tsid != expected_tsid or onid != expected_onid or not expected_services.issubset(services):
                    id_errors += 1
                    id_error_details.append(
                        f"SDT TSID/ONID/serviços={tsid}/{onid}/{services}")
            elif table_id == 0xC8 and len(section) >= 20:
                download_data_id = (section[3] << 8) | section[4]
                cdt_download_ids.add(download_data_id)
                logo_type = section[13]
                data_size = (section[18] << 8) | section[19]
                png = section[20:20 + data_size]
                chunks = []
                width = height = bit_depth = color_type = None
                offset = 8
                if png.startswith(b"\x89PNG\r\n\x1a\n"):
                    while offset + 12 <= len(png):
                        chunk_length = struct.unpack(">I", png[offset:offset + 4])[0]
                        kind = png[offset + 4:offset + 8].decode("ascii", "replace")
                        body = png[offset + 8:offset + 8 + chunk_length]
                        chunks.append(kind)
                        if kind == "IHDR" and len(body) == 13:
                            width, height, bit_depth, color_type = struct.unpack(">IIBB", body[:10])
                        offset += 12 + chunk_length
                        if kind == "IEND":
                            break
                item = {
                    "download_data_id": download_data_id,
                    "logo_type": logo_type,
                    "section_number": section[6],
                    "last_section_number": section[7],
                    "logo_id": ((section[14] << 8) | section[15]) & 0x01FF,
                    "logo_version": ((section[16] << 8) | section[17]) & 0x0FFF,
                    "data_size": data_size,
                    "width": width, "height": height,
                    "bit_depth": bit_depth, "color_type": color_type,
                    "chunks": chunks,
                }
                key = (download_data_id, logo_type, item["logo_version"])
                if key not in logo_cdt_keys:
                    logo_cdt_keys.add(key)
                    logo_cdts.append(item)
            elif table_id == 0xC4 and len(section) >= 14:
                parsed_bit = parse_bit_section(section, errors)
                bit_onids.add(parsed_bit["onid"])
                bit_versions.add(parsed_bit["version"])
                bit_broadcasters.extend(parsed_bit["broadcasters"])
                bit_announced_tables.update(parsed_bit["announced_tables"])

    if pid_counts[0x0012] == 0:
        errors.append("PID EIT 0x0012 ausente")
    if pid_counts[0x0014] == 0:
        errors.append("PID TDT/TOT 0x0014 ausente")
    if table_counts[0x70] == 0 or table_counts[0x73] == 0:
        errors.append("TDT ou TOT ausente")
    missing_pf = sorted(service for service in expected_services
                        if not {0, 1}.issubset(pf_sections[service]))
    if missing_pf:
        errors.append(f"EIT p/f não contém as seções 0 e 1 para os serviços {missing_pf}")
    if running_status.get(0) != 4:
        errors.append("evento presente não está com running_status=4")
    if running_status.get(1) == 4:
        errors.append("evento seguinte foi marcado como running")
    if epg_only:
        if 0x50 not in schedule_tables:
            errors.append("EIT schedule 0x50 ausente")
    elif not {0x50, 0x51}.issubset(schedule_tables):
        errors.append("grade de sete dias não alcançou as tabelas 0x50 e 0x51")
    if crc_errors:
        errors.append(f"{crc_errors} seções com CRC MPEG inválido")
    if id_errors:
        errors.append(f"{id_errors} ocorrências PSI/SI com SID/TSID/ONID divergentes")
    if require_content_category and not content_nibbles:
        errors.append("descritor de categoria 0x54 ausente na EIT")
    # A rolling capture may begin after an earlier packet of the same PID. A
    # single discontinuity at its cut boundary is acceptable; repeated errors
    # indicate a live transport problem.
    repeated_cc = {f"0x{pid:04X}": count for pid, count in continuity_errors.items() if count > 1}
    if repeated_cc:
        errors.append(f"descontinuidades repetidas: {repeated_cc}")
    if epg_only:
        required_signalling = {0x0000, 0x0011, 0x0012, 0x0014} | pmt_pids
        if require_logo or logo_services:
            required_signalling.update({0x0024, 0x0029})
        missing_signalling = sorted(required_signalling.difference(pid_counts))
        if missing_signalling:
            errors.append(
                "PIDs obrigatórios ausentes no transporte EPG-only: "
                + ", ".join(f"0x{pid:04X}" for pid in missing_signalling))
        if table_counts[0x00] == 0 or table_counts[0x02] == 0 or table_counts[0x42] == 0:
            errors.append("PAT, PMT ou SDT auxiliar ausente")
        if require_logo or logo_services:
            if table_counts[0xC8] == 0:
                errors.append("CDT de logo 0xC8 ausente")
            if table_counts[0xC4] == 0:
                errors.append("BIT 0xC4 ausente")
            if bit_onids and bit_onids != {expected_onid}:
                errors.append(f"BIT possui ONID divergente: {sorted(bit_onids)}")
            if 0xC8 not in bit_announced_tables:
                errors.append("BIT não anuncia CDT 0xC8 no descritor SI 0xD7 do segundo loop")
            missing_logo_services = sorted(logo_services.difference(logo_descriptors))
            if missing_logo_services:
                errors.append(f"SDT sem descriptor de logo para os serviços {missing_logo_services}")
            unmatched = sorted(service for service, logo in logo_descriptors.items()
                               if service in logo_services and
                               logo["download_data_id"] not in cdt_download_ids)
            if unmatched:
                errors.append(f"descriptor de logo sem CDT correspondente nos serviços {unmatched}")
            expected_dimensions = {
                0: (48, 24), 1: (36, 24), 2: (48, 27),
                3: (72, 36), 4: (54, 36), 5: (64, 36),
            }
            for service in logo_services:
                descriptor = logo_descriptors.get(service)
                if not descriptor:
                    continue
                sections = [item for item in logo_cdts
                            if item["download_data_id"] == descriptor["download_data_id"]]
                types = {item["logo_type"] for item in sections}
                if types != set(range(6)):
                    errors.append(f"CDT do serviço {service} não contém os tipos 0..5: {sorted(types)}")
                for item in sections:
                    expected = expected_dimensions.get(item["logo_type"])
                    if (item["section_number"] != item["logo_type"] or
                            item["last_section_number"] != 5):
                        errors.append(f"CDT tipo {item['logo_type']} possui numeração de seção inválida")
                    if expected and (item["width"], item["height"]) != expected:
                        errors.append(f"CDT tipo {item['logo_type']} possui dimensão inválida")
                    if item["bit_depth"] != 8 or item["color_type"] != 3:
                        errors.append(f"CDT tipo {item['logo_type']} não é PNG indexado de 8 bits")
                    if item["chunks"] != ["IHDR", "IDAT", "IEND"]:
                        errors.append(f"CDT tipo {item['logo_type']} possui chunks não permitidos: {item['chunks']}")
        unexpected_pids = sorted(set(pid_counts).difference(
            required_signalling | {0x0024, 0x0029, 0x1FFF}))
        if unexpected_pids:
            errors.append(
                "PIDs inesperados no transporte EPG-only: "
                + ", ".join(f"0x{pid:04X}" for pid in unexpected_pids))

    report = {
        "ok": not errors,
        "file_bytes": len(raw),
        "packet_count": packet_count,
        "pid_packets": {f"0x{pid:04X}": count for pid, count in sorted(pid_counts.items())},
        "table_counts": {f"0x{table:02X}": count for table, count in sorted(table_counts.items())},
        "eit_table_ids": [f"0x{table:02X}" for table in sorted(eit_tables)],
        "present_following_sections": {
            str(service): sorted(sections) for service, sections in sorted(pf_sections.items())},
        "present_following_running_status": {str(key): value for key, value in sorted(running_status.items())},
        "schedule_table_ids": [f"0x{table:02X}" for table in sorted(schedule_tables)],
        "crc_errors": crc_errors,
        "crc_errors_by_table": {f"0x{table:02X}": count for table, count in sorted(crc_errors_by_table.items())},
        "id_errors": id_errors,
        "id_error_details": sorted(set(id_error_details)),
        "logo_descriptors": logo_descriptors,
        "cdt_download_data_ids": sorted(cdt_download_ids),
        "logo_cdts": logo_cdts,
        "sdt_versions": sorted(sdt_versions),
        "bit_versions": sorted(bit_versions),
        "bit_onids": sorted(bit_onids),
        "bit_broadcasters": bit_broadcasters,
        "bit_announced_tables": [f"0x{table:02X}" for table in sorted(bit_announced_tables)],
        "content_categories": {f"0x{value:02X}": count for value, count in sorted(content_nibbles.items())},
        "synopsis_checks": synopsis_checks,
        "repeated_synopsis_prefixes": repeated_synopsis_prefixes,
        "eit_present_following_events": list(event_details.values()),
        "continuity_errors": {f"0x{pid:04X}": count for pid, count in sorted(continuity_errors.items())},
        "errors": errors,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Valida uma amostra PSI/SI ISDB-TB do TVStream")
    parser.add_argument("sample", type=__import__("pathlib").Path)
    parser.add_argument("--service-id", type=int, required=True, action="append",
                        help="SID esperado; repita para validar uma portadora multissserviço")
    parser.add_argument("--tsid", type=int, required=True)
    parser.add_argument("--onid", type=int, required=True)
    parser.add_argument(
        "--epg-only", action="store_true",
        help="valida PAT/PMT/SDT auxiliares e rejeita PIDs fora do perfil EPG-only")
    parser.add_argument(
        "--pmt-pid", type=lambda value: int(value, 0), action="append",
        help="PID de PMT esperado; repita por serviço (padrão: 0x1000)")
    parser.add_argument("--require-logo", action="store_true",
                        help="exige SDT, BIT anunciando CDT e logo no PID 0x0029")
    parser.add_argument("--logo-service-id", type=int, action="append",
                        help="SID que deve possuir os seis formatos de logo; repita por canal")
    parser.add_argument("--require-content-category", action="store_true",
                        help="exige pelo menos um descritor de conteúdo 0x54 na EIT")
    args = parser.parse_args()
    report = validate(
        args.sample, args.service_id, args.tsid, args.onid,
        args.epg_only, args.pmt_pid or [0x1000], args.require_logo,
        args.logo_service_id, args.require_content_category)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
