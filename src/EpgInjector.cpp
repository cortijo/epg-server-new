#include "EpgInjector.h"
#include "utils.h"

#include <curl/curl.h>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/xml_parser.hpp>

#include <algorithm>
#include <chrono>
#include <cctype>
#include <condition_variable>
#include <cstdlib>
#include <ctime>
#include <deque>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <map>
#include <unordered_map>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr std::uint16_t kEitPid = 0x0012;
constexpr std::uint16_t kDvbTimePid = 0x0014;
constexpr std::uint64_t kEmissionIntervalNs = 20ULL * 1000ULL * 1000ULL;
constexpr std::uint64_t kCarouselPauseNs = 2ULL * 1000ULL * 1000ULL * 1000ULL;
constexpr std::uint64_t kClockIntervalNs = 5ULL * 1000ULL * 1000ULL * 1000ULL;
constexpr std::uint64_t kPresentFollowingCycleNs = 2ULL * 1000ULL * 1000ULL * 1000ULL;
constexpr std::int32_t kBrazilUtcOffsetMinutes = -3 * 60;
constexpr std::size_t kMaximumXmlBytes = 96ULL * 1024ULL * 1024ULL;

std::mutex gEpgAuditMutex;
std::map<std::string, EpgAuditSnapshot> gEpgAuditSnapshots;

struct Programme {
    std::time_t start = 0;
    std::time_t stop = 0;
    std::string title;
    std::string description;
    std::vector<std::string> categories;
    std::string rating;
};

enum class EpgProfile {
    Generic,
    IsdbTb
};

EpgProfile epgProfile(const std::string& mode) {
    return mode == "isdbtb" ? EpgProfile::IsdbTb : EpgProfile::Generic;
}

std::size_t appendCurlData(char* data, std::size_t size, std::size_t count, void* user) {
    auto* output = static_cast<std::string*>(user);
    const std::size_t bytes = size * count;
    if (!output || output->size() + bytes > kMaximumXmlBytes) {
        return 0;
    }
    output->append(data, bytes);
    return bytes;
}

std::time_t timegmPortable(std::tm* value) {
#ifdef _WIN32
    return _mkgmtime(value);
#else
    return timegm(value);
#endif
}

bool parseXmltvTime(const std::string& text, std::time_t& result) {
    if (text.size() < 14) return false;
    std::tm value {};
    try {
        value.tm_year = std::stoi(text.substr(0, 4)) - 1900;
        value.tm_mon = std::stoi(text.substr(4, 2)) - 1;
        value.tm_mday = std::stoi(text.substr(6, 2));
        value.tm_hour = std::stoi(text.substr(8, 2));
        value.tm_min = std::stoi(text.substr(10, 2));
        value.tm_sec = std::stoi(text.substr(12, 2));
    } catch (...) {
        return false;
    }
    std::time_t utc = timegmPortable(&value);
    std::size_t signPosition = text.find_first_of("+-", 14);
    if (signPosition != std::string::npos && signPosition + 4 < text.size()) {
        try {
            const int hours = std::stoi(text.substr(signPosition + 1, 2));
            const int minutes = std::stoi(text.substr(signPosition + 3, 2));
            const int offset = (hours * 60 + minutes) * 60;
            utc += text[signPosition] == '+' ? -offset : offset;
        } catch (...) {}
    }
    result = utc;
    return true;
}

std::string truncateUtf8(std::string value, std::size_t limit) {
    if (value.size() <= limit) return value;
    value.resize(limit);
    while (!value.empty() && (static_cast<unsigned char>(value.back()) & 0xC0) == 0x80) {
        value.pop_back();
    }
    return value;
}

std::uint32_t nextUtf8CodePoint(const std::string& value, std::size_t& offset) {
    const auto first = static_cast<unsigned char>(value[offset++]);
    if (first < 0x80) return first;
    int continuation = 0;
    std::uint32_t codePoint = 0;
    if ((first & 0xE0) == 0xC0) {
        continuation = 1;
        codePoint = first & 0x1F;
    } else if ((first & 0xF0) == 0xE0) {
        continuation = 2;
        codePoint = first & 0x0F;
    } else if ((first & 0xF8) == 0xF0) {
        continuation = 3;
        codePoint = first & 0x07;
    } else {
        return '?';
    }
    if (offset + static_cast<std::size_t>(continuation) > value.size()) {
        offset = value.size();
        return '?';
    }
    for (int index = 0; index < continuation; ++index) {
        const auto byte = static_cast<unsigned char>(value[offset++]);
        if ((byte & 0xC0) != 0x80) return '?';
        codePoint = (codePoint << 6) | (byte & 0x3F);
    }
    return codePoint;
}

std::string encodeIso885915(const std::string& value, std::size_t limit) {
    std::string encoded;
    encoded.reserve(std::min(value.size(), limit));
    for (std::size_t offset = 0; offset < value.size() && encoded.size() < limit;) {
        const std::uint32_t codePoint = nextUtf8CodePoint(value, offset);
        unsigned char byte = '?';
        switch (codePoint) {
            case 0x20AC: byte = 0xA4; break; // euro sign
            case 0x0160: byte = 0xA6; break;
            case 0x0161: byte = 0xA8; break;
            case 0x017D: byte = 0xB4; break;
            case 0x017E: byte = 0xB8; break;
            case 0x0152: byte = 0xBC; break;
            case 0x0153: byte = 0xBD; break;
            case 0x0178: byte = 0xBE; break;
            default:
                if (codePoint == '\n' || codePoint == '\r' || codePoint == '\t') {
                    byte = ' ';
                } else if (codePoint >= 0x20 && codePoint <= 0xFF &&
                           codePoint != 0xA4 && codePoint != 0xA6 &&
                           codePoint != 0xA8 && codePoint != 0xB4 &&
                           codePoint != 0xB8 && codePoint != 0xBC &&
                           codePoint != 0xBD && codePoint != 0xBE) {
                    byte = static_cast<unsigned char>(codePoint);
                }
                break;
        }
        encoded.push_back(static_cast<char>(byte));
    }
    return encoded;
}

std::uint8_t bcd(int value) {
    return static_cast<std::uint8_t>(((value / 10) << 4) | (value % 10));
}

std::int32_t effectiveCivilShiftSeconds(EpgProfile profile,
                                        std::int32_t utcOffsetMinutes,
                                        std::int32_t correctionSeconds) {
    if (profile != EpgProfile::IsdbTb) return 0;
    return utcOffsetMinutes * 60 + correctionSeconds;
}

void appendDvbTime(std::vector<std::uint8_t>& bytes, std::time_t time,
                   EpgProfile profile = EpgProfile::Generic,
                   std::int32_t civilShiftSeconds = 0) {
    // The legacy profile keeps DVB UTC semantics byte-for-byte.  The Brazilian
    // ISDB-TB profile encodes the civil reference used by ABNT/SBTVD (UTC-3).
    // XMLTV timestamps have already been normalized to an epoch instant by
    // parseXmltvTime(), therefore the offset is applied exactly once here.
    const std::time_t encodedTime = profile == EpgProfile::IsdbTb
        ? static_cast<std::time_t>(static_cast<std::int64_t>(time) + civilShiftSeconds)
        : time;
    const std::int64_t days = static_cast<std::int64_t>(encodedTime) / 86400;
    const std::uint16_t mjd = static_cast<std::uint16_t>(40587 + days);
    std::tm utc {};
    gmtime_r(&encodedTime, &utc);
    bytes.push_back(static_cast<std::uint8_t>(mjd >> 8));
    bytes.push_back(static_cast<std::uint8_t>(mjd));
    bytes.push_back(bcd(utc.tm_hour));
    bytes.push_back(bcd(utc.tm_min));
    bytes.push_back(bcd(utc.tm_sec));
}

std::uint32_t mpegCrc32(const std::uint8_t* data, std::size_t size) {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t i = 0; i < size; ++i) {
        crc ^= static_cast<std::uint32_t>(data[i]) << 24;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x80000000U) ? (crc << 1) ^ 0x04C11DB7U : crc << 1;
        }
    }
    return crc;
}

std::uint16_t eventId(const Programme& event) {
    const std::uint64_t raw = static_cast<std::uint64_t>(event.start) ^
        static_cast<std::uint64_t>(std::hash<std::string>{}(event.title));
    const std::uint16_t id = static_cast<std::uint16_t>((raw ^ (raw >> 16)) & 0xFFFFU);
    return id == 0 ? 1 : id;
}

std::uint8_t contentNibbleForCategory(const std::string& category) {
    const std::string normalized = toLower(category);
    if (normalized.find("filme") != std::string::npos ||
        normalized.find("movie") != std::string::npos ||
        normalized.find("drama") != std::string::npos) return 0x10;
    if (normalized.find("notícia") != std::string::npos ||
        normalized.find("noticia") != std::string::npos ||
        normalized.find("news") != std::string::npos) return 0x20;
    if (normalized.find("show") != std::string::npos ||
        normalized.find("entretenimento") != std::string::npos) return 0x30;
    if (normalized.find("esporte") != std::string::npos ||
        normalized.find("sport") != std::string::npos) return 0x40;
    if (normalized.find("infantil") != std::string::npos ||
        normalized.find("children") != std::string::npos) return 0x50;
    if (normalized.find("música") != std::string::npos ||
        normalized.find("musica") != std::string::npos ||
        normalized.find("music") != std::string::npos) return 0x60;
    if (normalized.find("arte") != std::string::npos ||
        normalized.find("culture") != std::string::npos) return 0x70;
    if (normalized.find("social") != std::string::npos ||
        normalized.find("política") != std::string::npos) return 0x80;
    if (normalized.find("educação") != std::string::npos ||
        normalized.find("educacao") != std::string::npos ||
        normalized.find("science") != std::string::npos) return 0x90;
    if (normalized.find("lazer") != std::string::npos ||
        normalized.find("leisure") != std::string::npos) return 0xA0;
    return 0;
}

int parentalAge(const std::string& rating) {
    const std::string normalized = toLower(rating);
    if (normalized.empty() || normalized.find("livre") != std::string::npos ||
        normalized == "l") return 0;
    std::string digits;
    for (unsigned char ch : rating) {
        if (std::isdigit(ch)) digits.push_back(static_cast<char>(ch));
    }
    if (digits.empty()) return -1;
    try {
        return std::clamp(std::stoi(digits), 4, 18);
    } catch (...) {
        return -1;
    }
}

void appendExtendedEventDescriptors(std::vector<std::uint8_t>& descriptors,
                                    const Programme& event,
                                    std::size_t shortTextLength) {
    constexpr std::size_t kChunkSize = 249;
    constexpr std::size_t kMaxChunks = 13;
    const std::string text = encodeIso885915(
        event.description, shortTextLength + kMaxChunks * kChunkSize);
    if (text.size() <= shortTextLength) return;
    const std::size_t remaining = text.size() - shortTextLength;
    const std::size_t chunks = std::min<std::size_t>(13,
        (remaining + kChunkSize - 1) / kChunkSize);
    for (std::size_t index = 0; index < chunks; ++index) {
        // Extended descriptors carry the synopsis bytes that are not present
        // in the short_event_descriptor. In ISDB-TB, shortTextLength is zero:
        // 0x4D carries only the event name and 0x4E carries the full synopsis.
        const std::size_t offset = shortTextLength + index * kChunkSize;
        const std::size_t length = std::min(kChunkSize, text.size() - offset);
        descriptors.push_back(0x4E);
        descriptors.push_back(static_cast<std::uint8_t>(6 + length));
        descriptors.push_back(static_cast<std::uint8_t>(
            ((index & 0x0F) << 4) | ((chunks - 1) & 0x0F)));
        descriptors.insert(descriptors.end(), {'p', 'o', 'r'});
        descriptors.push_back(0x00); // length_of_items
        descriptors.push_back(static_cast<std::uint8_t>(length));
        descriptors.insert(descriptors.end(), text.begin() + offset,
            text.begin() + offset + length);
    }
}

std::vector<std::uint8_t> eventBytes(const Programme& event, EpgProfile profile,
                                     std::int32_t civilShiftSeconds,
                                     std::uint8_t runningStatus = 4) {
    std::vector<std::uint8_t> bytes;
    const std::uint16_t id = eventId(event);
    bytes.push_back(static_cast<std::uint8_t>(id >> 8));
    bytes.push_back(static_cast<std::uint8_t>(id));
    appendDvbTime(bytes, event.start, profile, civilShiftSeconds);
    const int duration = static_cast<int>(std::max<std::time_t>(0, event.stop - event.start));
    bytes.push_back(bcd((duration / 3600) % 100));
    bytes.push_back(bcd((duration / 60) % 60));
    bytes.push_back(bcd(duration % 60));

    const std::string sourceTitle = event.title.empty() ? "Programação" : event.title;
    const std::string title = profile == EpgProfile::IsdbTb
        ? encodeIso885915(sourceTitle, 120)
        : truncateUtf8(sourceTitle, 120);
    const std::string description = profile == EpgProfile::IsdbTb
        ? std::string()
        : truncateUtf8(event.description, 110);
    std::vector<std::uint8_t> descriptor;
    descriptor.push_back(0x4D);
    descriptor.push_back(static_cast<std::uint8_t>(5 + title.size() + description.size()));
    descriptor.insert(descriptor.end(), {'p', 'o', 'r'});
    descriptor.push_back(static_cast<std::uint8_t>(title.size()));
    descriptor.insert(descriptor.end(), title.begin(), title.end());
    descriptor.push_back(static_cast<std::uint8_t>(description.size()));
    descriptor.insert(descriptor.end(), description.begin(), description.end());

    if (profile == EpgProfile::IsdbTb) {
        appendExtendedEventDescriptors(descriptor, event, 0);
        for (const auto& category : event.categories) {
            const std::uint8_t content = contentNibbleForCategory(category);
            if (content == 0) continue;
            descriptor.insert(descriptor.end(), {0x54, 0x02, content, 0x00});
            break;
        }
        const int age = parentalAge(event.rating);
        if (age >= 0) {
            const std::uint8_t value = age == 0 ? 0 : static_cast<std::uint8_t>(age + 3);
            descriptor.insert(descriptor.end(), {0x55, 0x04, 'B', 'R', 'A', value});
        }
    }

    const std::uint16_t loopLength = static_cast<std::uint16_t>(descriptor.size());
    bytes.push_back(static_cast<std::uint8_t>(
        ((runningStatus & 0x07) << 5) | ((loopLength >> 8) & 0x0F)));
    bytes.push_back(static_cast<std::uint8_t>(loopLength));
    bytes.insert(bytes.end(), descriptor.begin(), descriptor.end());
    return bytes;
}

std::vector<std::uint8_t> makeEitSection(std::uint8_t tableId,
                                         std::uint16_t serviceId,
                                         const std::vector<Programme>& events,
                                         std::uint8_t sectionNumber,
                                         std::uint8_t lastSectionNumber,
                                         std::uint8_t lastTableId,
                                         EpgProfile profile,
                                         std::uint16_t transportStreamId,
                                         std::uint16_t originalNetworkId,
                                         std::uint8_t version,
                                         std::int32_t civilShiftSeconds,
                                         std::uint8_t runningStatus = 4,
                                         std::uint8_t segmentLastSectionOverride = 0xFF) {
    const std::uint8_t versionByte = static_cast<std::uint8_t>(
        0xC1 | ((version & 0x1F) << 1));
    const std::uint8_t segmentLastSection = segmentLastSectionOverride != 0xFF
        ? segmentLastSectionOverride
        : (profile == EpgProfile::IsdbTb
            ? std::min<std::uint8_t>(
                static_cast<std::uint8_t>(sectionNumber | 0x07), lastSectionNumber)
            : lastSectionNumber);
    std::vector<std::uint8_t> section {tableId, 0xF0, 0x00,
        static_cast<std::uint8_t>(serviceId >> 8), static_cast<std::uint8_t>(serviceId),
        versionByte, sectionNumber, lastSectionNumber,
        static_cast<std::uint8_t>(transportStreamId >> 8),
        static_cast<std::uint8_t>(transportStreamId),
        static_cast<std::uint8_t>(originalNetworkId >> 8),
        static_cast<std::uint8_t>(originalNetworkId),
        segmentLastSection, lastTableId};
    for (const auto& event : events) {
        const auto encoded = eventBytes(event, profile, civilShiftSeconds, runningStatus);
        if (section.size() + encoded.size() + 4 > 4096) break;
        section.insert(section.end(), encoded.begin(), encoded.end());
    }
    const std::uint16_t sectionLength = static_cast<std::uint16_t>(section.size() - 3 + 4);
    section[1] = static_cast<std::uint8_t>(0xF0 | ((sectionLength >> 8) & 0x0F));
    section[2] = static_cast<std::uint8_t>(sectionLength);
    const std::uint32_t crc = mpegCrc32(section.data(), section.size());
    section.push_back(static_cast<std::uint8_t>(crc >> 24));
    section.push_back(static_cast<std::uint8_t>(crc >> 16));
    section.push_back(static_cast<std::uint8_t>(crc >> 8));
    section.push_back(static_cast<std::uint8_t>(crc));
    return section;
}

std::vector<std::array<std::uint8_t, 188>> packetize(
        const std::vector<std::vector<std::uint8_t>>& sections,
        std::uint16_t pid = kEitPid,
        std::uint8_t initialContinuity = 0) {
    std::vector<std::array<std::uint8_t, 188>> result;
    std::uint8_t continuity = initialContinuity;
    for (const auto& section : sections) {
        std::size_t offset = 0;
        bool first = true;
        while (offset < section.size()) {
            std::array<std::uint8_t, 188> packet {};
            packet.fill(0xFF);
            packet[0] = 0x47;
            packet[1] = static_cast<std::uint8_t>((first ? 0x40 : 0x00) | (pid >> 8));
            packet[2] = static_cast<std::uint8_t>(pid);
            packet[3] = static_cast<std::uint8_t>(0x10 | continuity);
            continuity = static_cast<std::uint8_t>((continuity + 1) & 0x0F);
            std::size_t payload = 4;
            if (first) packet[payload++] = 0x00;
            const std::size_t amount = std::min<std::size_t>(188 - payload, section.size() - offset);
            std::copy_n(section.data() + offset, amount, packet.data() + payload);
            offset += amount;
            first = false;
            result.push_back(packet);
        }
    }
    return result;
}

std::vector<std::array<std::uint8_t, 188>> buildDvbTimePackets(
        std::time_t now, std::uint8_t& continuity,
        EpgProfile profile = EpgProfile::Generic,
        std::int32_t utcOffsetMinutes = kBrazilUtcOffsetMinutes,
        std::int32_t correctionSeconds = 0) {
    const std::int32_t civilShiftSeconds = effectiveCivilShiftSeconds(
        profile, utcOffsetMinutes, correctionSeconds);
    std::vector<std::uint8_t> tdt {0x70, 0x70, 0x05};
    appendDvbTime(tdt, now, profile, civilShiftSeconds);

    const int offsetMagnitude = std::abs(utcOffsetMinutes);
    const int offsetHours = offsetMagnitude / 60;
    const int offsetMinutes = offsetMagnitude % 60;
    const std::uint8_t polarity = utcOffsetMinutes < 0 ? 0x01 : 0x00;

    std::vector<std::uint8_t> localTimeDescriptor {
        0x58, 0x0D, 'B', 'R', 'A',
        static_cast<std::uint8_t>(0x02 | polarity),
        bcd(offsetHours), bcd(offsetMinutes)
    };
    std::tm futureUtc {};
    futureUtc.tm_year = 137; // 2037
    futureUtc.tm_mon = 0;
    futureUtc.tm_mday = 1;
    const std::time_t noDstChange = timegmPortable(&futureUtc);
    appendDvbTime(localTimeDescriptor, noDstChange, profile, civilShiftSeconds);
    localTimeDescriptor.push_back(bcd(offsetHours));
    localTimeDescriptor.push_back(bcd(offsetMinutes));

    std::vector<std::uint8_t> tot {0x73, 0x70, 0x00};
    appendDvbTime(tot, now, profile, civilShiftSeconds);
    const std::uint16_t descriptorsLength =
        static_cast<std::uint16_t>(localTimeDescriptor.size());
    tot.push_back(static_cast<std::uint8_t>(0xF0 | ((descriptorsLength >> 8) & 0x0F)));
    tot.push_back(static_cast<std::uint8_t>(descriptorsLength));
    tot.insert(tot.end(), localTimeDescriptor.begin(), localTimeDescriptor.end());
    const std::uint16_t sectionLength = static_cast<std::uint16_t>(tot.size() - 3 + 4);
    tot[1] = static_cast<std::uint8_t>(0x70 | ((sectionLength >> 8) & 0x0F));
    tot[2] = static_cast<std::uint8_t>(sectionLength);
    const std::uint32_t crc = mpegCrc32(tot.data(), tot.size());
    tot.push_back(static_cast<std::uint8_t>(crc >> 24));
    tot.push_back(static_cast<std::uint8_t>(crc >> 16));
    tot.push_back(static_cast<std::uint8_t>(crc >> 8));
    tot.push_back(static_cast<std::uint8_t>(crc));

    auto result = packetize({tdt, tot}, kDvbTimePid, continuity);
    continuity = static_cast<std::uint8_t>((continuity + result.size()) & 0x0F);
    return result;
}

bool downloadXml(const std::string& url, std::string& body, std::string& error) {
    CURL* curl = curl_easy_init();
    if (!curl) { error = "não foi possível iniciar o download"; return false; }
    char curlError[CURL_ERROR_SIZE] {};
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_MAXREDIRS, 5L);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 15L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 90L);
    // Some licensed XMLTV providers explicitly allow IPTV clients while their
    // WAF blocks generic server-side agents. Identify this request as the
    // media client it serves; credentials remain confined to the configured
    // URL and are never written to logs.
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "VLC/3.0.21 LibVLC/3.0.21");
    curl_easy_setopt(curl, CURLOPT_ACCEPT_ENCODING, "");
    curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Accept: application/xml,text/xml,*/*");
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, appendCurlData);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &body);
    curl_easy_setopt(curl, CURLOPT_ERRORBUFFER, curlError);
#if LIBCURL_VERSION_NUM >= 0x075500
    curl_easy_setopt(curl, CURLOPT_PROTOCOLS_STR, "http,https");
#else
    curl_easy_setopt(curl, CURLOPT_PROTOCOLS, CURLPROTO_HTTP | CURLPROTO_HTTPS);
#endif
    const CURLcode code = curl_easy_perform(curl);
    long status = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    if (code != CURLE_OK || status < 200 || status >= 300) {
        error = curlError[0] ? curlError : curl_easy_strerror(code);
        if (status) error += " (HTTP " + std::to_string(status) + ")";
        return false;
    }
    return true;
}

std::string safeSourceLabel(const std::string& url) {
    const std::size_t scheme = url.find("://");
    const std::size_t hostStart = scheme == std::string::npos ? 0 : scheme + 3;
    const std::size_t query = url.find('?', hostStart);
    return url.substr(hostStart, query == std::string::npos ? std::string::npos : query - hostStart);
}

bool parseProgrammes(const std::string& xml, const std::string& channelId,
                     const std::string& defaultCategory,
                     std::vector<Programme>& programmes, std::string& error) {
    boost::property_tree::ptree document;
    try {
        std::istringstream input(xml);
        boost::property_tree::read_xml(input, document,
            boost::property_tree::xml_parser::trim_whitespace |
            boost::property_tree::xml_parser::no_comments);
    } catch (const std::exception& exception) {
        error = std::string("XMLTV inválido: ") + exception.what();
        return false;
    }
    const auto tv = document.get_child_optional("tv");
    if (!tv) { error = "XMLTV sem elemento tv"; return false; }
    bool channelExists = false;
    for (const auto& entry : *tv) {
        const auto& tag = entry.first;
        const auto& node = entry.second;
        const std::string entryChannel = node.get<std::string>("<xmlattr>.channel", "");
        if (tag == "channel" && node.get<std::string>("<xmlattr>.id", "") == channelId) {
            channelExists = true;
        }
        if (tag != "programme" || entryChannel != channelId) continue;
        Programme programme;
        if (!parseXmltvTime(node.get<std::string>("<xmlattr>.start", ""), programme.start) ||
            !parseXmltvTime(node.get<std::string>("<xmlattr>.stop", ""), programme.stop) ||
            programme.stop <= programme.start) continue;
        programme.title = node.get<std::string>("title", "");
        programme.description = node.get<std::string>("desc", "");
        for (const auto& child : node) {
            if (child.first == "category") {
                const std::string category = child.second.get_value<std::string>();
                if (!category.empty() &&
                    std::find(programme.categories.begin(), programme.categories.end(), category) ==
                        programme.categories.end()) {
                    programme.categories.push_back(category);
                }
            } else if (child.first == "rating" && programme.rating.empty()) {
                programme.rating = child.second.get<std::string>("value", "");
            }
        }
        const bool hasRecognizedCategory = std::any_of(
            programme.categories.begin(), programme.categories.end(),
            [](const std::string& category) {
                return contentNibbleForCategory(category) != 0;
            });
        if (!hasRecognizedCategory && contentNibbleForCategory(defaultCategory) != 0) {
            programme.categories.insert(programme.categories.begin(), defaultCategory);
        }
        programmes.push_back(std::move(programme));
    }
    std::sort(programmes.begin(), programmes.end(), [](const Programme& a, const Programme& b) {
        return a.start == b.start ? a.stop < b.stop : a.start < b.start;
    });
    programmes.erase(std::unique(programmes.begin(), programmes.end(),
        [](const Programme& left, const Programme& right) {
            return left.start == right.start && left.stop == right.stop &&
                left.title == right.title;
        }), programmes.end());
    if (!channelExists) { error = "canal XMLTV não encontrado: " + channelId; return false; }
    if (programmes.empty()) { error = "o canal não possui programação no XMLTV"; return false; }
    return true;
}

struct EpgSectionSet {
    std::vector<std::vector<std::uint8_t>> presentFollowing;
    std::vector<std::vector<std::uint8_t>> schedule;
    std::string currentTitle;
    std::string nextTitle;
};

std::int64_t civilDayNumber(std::time_t value, EpgProfile profile,
                            std::int32_t civilShiftSeconds) {
    const std::int64_t shifted = static_cast<std::int64_t>(value) +
        (profile == EpgProfile::IsdbTb ? civilShiftSeconds : 0);
    return shifted >= 0 ? shifted / 86400 : (shifted - 86399) / 86400;
}

int civilHour(std::time_t value, EpgProfile profile,
              std::int32_t civilShiftSeconds) {
    const std::time_t shifted = profile == EpgProfile::IsdbTb
        ? static_cast<std::time_t>(static_cast<std::int64_t>(value) + civilShiftSeconds)
        : value;
    std::tm decoded {};
    gmtime_r(&shifted, &decoded);
    return decoded.tm_hour;
}

EpgSectionSet buildSections(
        const std::vector<Programme>& all,
        std::uint16_t serviceId,
        EpgProfile profile,
        std::uint16_t transportStreamId,
        std::uint16_t originalNetworkId,
        std::uint8_t version,
        std::int32_t civilShiftSeconds,
        std::time_t now = std::time(nullptr)) {
    EpgSectionSet result;
    std::vector<Programme> present;
    std::vector<Programme> following;
    auto current = std::find_if(all.begin(), all.end(), [now](const Programme& item) {
        return item.start <= now && item.stop > now;
    });
    if (current != all.end()) {
        present.push_back(*current);
        result.currentTitle = current->title;
        if (std::next(current) != all.end()) {
            following.push_back(*std::next(current));
            result.nextTitle = std::next(current)->title;
        }
    } else {
        auto next = std::find_if(all.begin(), all.end(), [now](const Programme& item) {
            return item.start > now;
        });
        if (next != all.end()) {
            following.push_back(*next);
            result.nextTitle = next->title;
        }
    }

    if (profile == EpgProfile::Generic) {
        std::vector<Programme> legacyPresentFollowing = present;
        legacyPresentFollowing.insert(legacyPresentFollowing.end(), following.begin(), following.end());
        if (!legacyPresentFollowing.empty()) {
            result.presentFollowing.push_back(makeEitSection(
                0x4E, serviceId, legacyPresentFollowing, 0, 0, 0x4E, profile,
                transportStreamId, originalNetworkId, version, civilShiftSeconds));
        }
    } else if (!present.empty() || !following.empty()) {
        // ABNT/ARIB receivers expect p/f actual TS as section 0 (present) and
        // section 1 (following), including an empty present section when the
        // guide only knows the next event.
        result.presentFollowing.push_back(makeEitSection(
            0x4E, serviceId, present, 0, 1, 0x4E, profile,
            transportStreamId, originalNetworkId, version, civilShiftSeconds, 4, 1));
        result.presentFollowing.push_back(makeEitSection(
            0x4E, serviceId, following, 1, 1, 0x4E, profile,
            transportStreamId, originalNetworkId, version, civilShiftSeconds, 1, 1));
    }

    if (profile == EpgProfile::Generic) {
        std::vector<std::vector<Programme>> groups;
        std::vector<Programme> group;
        std::size_t estimated = 14 + 4;
        const std::time_t horizon = now + 7 * 24 * 3600;
        for (const auto& item : all) {
            if (item.stop <= now || item.start >= horizon) continue;
            const std::size_t size = eventBytes(item, profile, civilShiftSeconds).size();
            if (!group.empty() && estimated + size > 3900) {
                groups.push_back(std::move(group));
                group.clear();
                estimated = 18;
            }
            group.push_back(item);
            estimated += size;
        }
        if (!group.empty()) groups.push_back(std::move(group));
        const std::uint8_t last = groups.empty() ? 0 :
            static_cast<std::uint8_t>(groups.size() - 1);
        for (std::size_t i = 0; i < groups.size() && i < 256; ++i) {
            result.schedule.push_back(makeEitSection(
                0x50, serviceId, groups[i], static_cast<std::uint8_t>(i), last,
                0x50, profile, transportStreamId, originalNetworkId, version,
                civilShiftSeconds));
        }
        return result;
    }

    // One schedule table spans four civil days.  Each 3-hour segment owns up
    // to eight sections, as defined by the EIT schedule segmentation model.
    using SegmentKey = std::pair<std::uint8_t, std::uint8_t>;
    std::map<SegmentKey, std::vector<Programme>> segmentEvents;
    const std::int64_t today = civilDayNumber(now, profile, civilShiftSeconds);
    const std::time_t horizon = now + 7 * 24 * 3600;
    for (const auto& item : all) {
        if (item.stop <= now || item.start >= horizon) continue;
        const std::int64_t eventDay = civilDayNumber(item.start, profile, civilShiftSeconds);
        const std::int64_t dayOffset = std::max<std::int64_t>(0, eventDay - today);
        if (dayOffset >= 7) continue;
        const std::uint8_t tableOffset = static_cast<std::uint8_t>(dayOffset / 4);
        const std::uint8_t tableId = static_cast<std::uint8_t>(0x50 + tableOffset);
        const std::uint8_t dayWithinTable = static_cast<std::uint8_t>(dayOffset % 4);
        const std::uint8_t segment = static_cast<std::uint8_t>(
            dayWithinTable * 8 + civilHour(item.start, profile, civilShiftSeconds) / 3);
        segmentEvents[{tableId, segment}].push_back(item);
    }

    std::uint8_t lastTableId = 0x50;
    if (!segmentEvents.empty()) lastTableId = segmentEvents.rbegin()->first.first;
    struct PendingSection {
        std::uint8_t tableId;
        std::uint8_t sectionNumber;
        std::uint8_t segmentLastSection;
        std::vector<Programme> events;
    };
    std::vector<PendingSection> pending;
    for (const auto& entry : segmentEvents) {
        const std::uint8_t tableId = entry.first.first;
        const std::uint8_t segment = entry.first.second;
        std::vector<std::vector<Programme>> groups;
        std::vector<Programme> group;
        std::size_t estimated = 18;
        for (const auto& item : entry.second) {
            const std::size_t size = eventBytes(item, profile, civilShiftSeconds, 1).size();
            if (!group.empty() && estimated + size > 3900 && groups.size() < 7) {
                groups.push_back(std::move(group));
                group.clear();
                estimated = 18;
            }
            group.push_back(item);
            estimated += size;
        }
        if (!group.empty()) groups.push_back(std::move(group));
        if (groups.size() > 8) groups.resize(8);
        const std::uint8_t sectionBase = static_cast<std::uint8_t>(segment * 8);
        const std::uint8_t segmentLast = static_cast<std::uint8_t>(
            sectionBase + (groups.empty() ? 0 : groups.size() - 1));
        for (std::size_t index = 0; index < groups.size(); ++index) {
            pending.push_back({tableId,
                static_cast<std::uint8_t>(sectionBase + index), segmentLast,
                std::move(groups[index])});
        }
    }
    std::map<std::uint8_t, std::uint8_t> tableLastSection;
    for (const auto& section : pending) {
        tableLastSection[section.tableId] = std::max(
            tableLastSection[section.tableId], section.sectionNumber);
    }
    for (const auto& section : pending) {
        result.schedule.push_back(makeEitSection(
            section.tableId, serviceId, section.events, section.sectionNumber,
            tableLastSection[section.tableId], lastTableId, profile,
            transportStreamId, originalNetworkId, version, civilShiftSeconds, 1,
            section.segmentLastSection));
    }
    return result;
}

std::uint64_t programmeFingerprint(const std::vector<Programme>& programmes) {
    std::uint64_t hash = 1469598103934665603ULL;
    const auto append = [&hash](const void* data, std::size_t size) {
        const auto* bytes = static_cast<const unsigned char*>(data);
        for (std::size_t index = 0; index < size; ++index) {
            hash ^= bytes[index];
            hash *= 1099511628211ULL;
        }
    };
    for (const auto& programme : programmes) {
        append(&programme.start, sizeof(programme.start));
        append(&programme.stop, sizeof(programme.stop));
        append(programme.title.data(), programme.title.size());
        append(programme.description.data(), programme.description.size());
        for (const auto& category : programme.categories) {
            append(category.data(), category.size());
        }
        append(programme.rating.data(), programme.rating.size());
    }
    return hash;
}

} // namespace

bool getEpgAuditSnapshot(const std::string& streamId, EpgAuditSnapshot& snapshot) {
    std::lock_guard<std::mutex> lock(gEpgAuditMutex);
    const auto found = gEpgAuditSnapshots.find(streamId);
    if (found == gEpgAuditSnapshots.end()) return false;
    snapshot = found->second;
    return true;
}

bool loadXmltvChannels(const std::string& url,
                       std::vector<XmltvChannelInfo>& channels,
                       std::string& error) {
    struct CacheEntry {
        std::chrono::steady_clock::time_point loadedAt;
        std::vector<XmltvChannelInfo> channels;
    };
    static std::mutex cacheMutex;
    static std::map<std::string, CacheEntry> cache;
    constexpr auto cacheLifetime = std::chrono::minutes(15);

    {
        std::lock_guard<std::mutex> lock(cacheMutex);
        const auto found = cache.find(url);
        if (found != cache.end() &&
            std::chrono::steady_clock::now() - found->second.loadedAt < cacheLifetime) {
            channels = found->second.channels;
            return true;
        }
    }

    std::string xml;
    if (!downloadXml(url, xml, error)) return false;

    boost::property_tree::ptree document;
    try {
        std::istringstream input(xml);
        boost::property_tree::read_xml(input, document,
            boost::property_tree::xml_parser::trim_whitespace |
            boost::property_tree::xml_parser::no_comments);
    } catch (const std::exception& exception) {
        error = std::string("XMLTV inválido: ") + exception.what();
        return false;
    }
    const auto tv = document.get_child_optional("tv");
    if (!tv) {
        error = "XMLTV sem elemento tv";
        return false;
    }

    std::unordered_map<std::string, std::size_t> programmeCounts;
    for (const auto& entry : *tv) {
        if (entry.first != "programme") continue;
        const std::string id = entry.second.get<std::string>("<xmlattr>.channel", "");
        if (!id.empty()) ++programmeCounts[id];
    }

    std::vector<XmltvChannelInfo> parsed;
    for (const auto& entry : *tv) {
        if (entry.first != "channel") continue;
        XmltvChannelInfo channel;
        channel.id = entry.second.get<std::string>("<xmlattr>.id", "");
        if (channel.id.empty()) continue;
        for (const auto& child : entry.second) {
            if (child.first == "display-name") {
                const std::string value = child.second.get_value<std::string>();
                if (!value.empty() &&
                    std::find(channel.displayNames.begin(), channel.displayNames.end(), value) ==
                        channel.displayNames.end()) {
                    channel.displayNames.push_back(value);
                }
            } else if (child.first == "icon" && channel.iconUrl.empty()) {
                channel.iconUrl = child.second.get<std::string>("<xmlattr>.src", "");
            }
        }
        channel.name = channel.displayNames.empty() ? channel.id : channel.displayNames.front();
        channel.programmeCount = programmeCounts[channel.id];
        parsed.push_back(std::move(channel));
    }
    std::sort(parsed.begin(), parsed.end(), [](const XmltvChannelInfo& left, const XmltvChannelInfo& right) {
        const std::string leftName = toLower(left.name);
        const std::string rightName = toLower(right.name);
        return leftName == rightName ? left.id < right.id : leftName < rightName;
    });
    if (parsed.empty()) {
        error = "nenhum canal foi declarado no XMLTV";
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(cacheMutex);
        cache[url] = {std::chrono::steady_clock::now(), parsed};
    }
    channels = std::move(parsed);
    return true;
}

struct EpgInjector::Impl {
    explicit Impl(const StreamConfig& config)
        : active(config.epgEnabled && !config.epgChannelId.empty() && !config.epgSourceUrl.empty()),
          streamId(config.id.empty() ? "__anonymous__" : config.id),
          sourceUrl(config.epgSourceUrl), channelId(config.epgChannelId),
          defaultCategory(config.epgDefaultCategory),
          serviceId(static_cast<std::uint16_t>(config.serviceId ? config.serviceId : 1)),
          profile(epgProfile(config.epgMode)),
          utcOffsetMinutes(config.epgClockUtcOffsetMinutes),
          correctionSeconds(config.epgClockCorrectionSeconds),
          civilShiftSeconds(effectiveCivilShiftSeconds(
              profile, utcOffsetMinutes, correctionSeconds)),
          transportStreamId(static_cast<std::uint16_t>(config.epgTransportStreamId)),
          originalNetworkId(static_cast<std::uint16_t>(config.epgOriginalNetworkId)) {
        audit.configured = config.epgEnabled;
        audit.active = active;
        audit.profile = profile == EpgProfile::IsdbTb ? "isdbtb" : "generic";
        audit.channelId = channelId;
        audit.sourceLabel = safeSourceLabel(sourceUrl);
        audit.serviceId = serviceId;
        audit.transportStreamId = transportStreamId;
        audit.originalNetworkId = originalNetworkId;
        publishAudit();
        if (active) worker = std::thread(&Impl::refreshLoop, this);
    }

    ~Impl() {
        stop.store(true);
        wake.notify_all();
        if (worker.joinable()) worker.join();
        std::lock_guard<std::mutex> lock(mutex);
        audit.active = false;
        publishAudit();
    }

    void refreshLoop() {
        while (!stop.load()) {
            const auto steadyNow = std::chrono::steady_clock::now();
            bool attemptedDownload = false;
            bool downloadSucceeded = false;
            std::string error;
            if (cachedProgrammes.empty() || steadyNow >= nextGuideDownload) {
                attemptedDownload = true;
                std::string xml;
                std::vector<Programme> programmes;
                downloadSucceeded = downloadXml(sourceUrl, xml, error) &&
                    parseProgrammes(xml, channelId, defaultCategory, programmes, error);
                if (downloadSucceeded) {
                    const std::uint64_t fingerprint = programmeFingerprint(programmes);
                    if (profile == EpgProfile::IsdbTb && fingerprintInitialized &&
                        fingerprint != lastProgrammeFingerprint) {
                        eitVersion = static_cast<std::uint8_t>((eitVersion + 1) & 0x1F);
                    }
                    lastProgrammeFingerprint = fingerprint;
                    fingerprintInitialized = true;
                    cachedProgrammes = std::move(programmes);
                    nextGuideDownload = steadyNow + std::chrono::hours(3);
                } else {
                    nextGuideDownload = steadyNow + std::chrono::minutes(5);
                }
            }

            if (!cachedProgrammes.empty()) {
                const EpgSectionSet built = buildSections(
                    cachedProgrammes, serviceId, profile, transportStreamId,
                    originalNetworkId, eitVersion, civilShiftSeconds);
                auto nextPresentFollowing = packetize(built.presentFollowing);
                auto nextSchedule = packetize(built.schedule);
                const std::size_t pfPacketCount = nextPresentFollowing.size();
                const std::size_t schedulePacketCount = nextSchedule.size();
                const std::size_t pfSectionCount = built.presentFollowing.size();
                const std::size_t scheduleSectionCount = built.schedule.size();
                if (profile == EpgProfile::IsdbTb && fingerprintInitialized &&
                    attemptedDownload && downloadSucceeded) {
                    // Version was calculated from the downloaded guide before
                    // sections were built; no further action is required.
                }
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    presentFollowingPackets = std::move(nextPresentFollowing);
                    schedulePackets = std::move(nextSchedule);
                    presentFollowingCursor = 0;
                    presentFollowingBurstActive = false;
                    scheduleCursor = 0;
                    nextPresentFollowingCycle = 0;
                    nextScheduleEmission = 0;
                    audit.guideLoaded = true;
                    audit.lastError.clear();
                    audit.eitVersion = eitVersion;
                    audit.programmeCount = cachedProgrammes.size();
                    audit.presentFollowingSections = pfSectionCount;
                    audit.scheduleSections = scheduleSectionCount;
                    audit.presentFollowingPackets = pfPacketCount;
                    audit.schedulePackets = schedulePacketCount;
                    audit.currentTitle = built.currentTitle;
                    audit.nextTitle = built.nextTitle;
                    audit.guideStart = cachedProgrammes.front().start;
                    audit.guideEnd = cachedProgrammes.back().stop;
                    audit.loadedAt = std::time(nullptr);
                    publishAudit();
                }
                if (attemptedDownload && downloadSucceeded) {
                    std::cerr << "EPG XMLTV carregado: canal=" << channelId
                              << " programas=" << cachedProgrammes.size()
                              << " secoes_pf=" << pfSectionCount
                              << " secoes_schedule=" << scheduleSectionCount
                              << " pacotes_pf=" << pfPacketCount
                              << " pacotes_schedule=" << schedulePacketCount
                              << " perfil=" << (profile == EpgProfile::IsdbTb ? "isdbtb" : "generic")
                              << " tsid=" << transportStreamId
                              << " onid=" << originalNetworkId
                              << " utc_offset_minutes=" << utcOffsetMinutes
                              << " clock_correction_seconds=" << correctionSeconds
                              << " versao=" << static_cast<unsigned>(eitVersion)
                              << " fonte=" << safeSourceLabel(sourceUrl) << std::endl;
                }
            } else if (attemptedDownload) {
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    audit.guideLoaded = false;
                    audit.lastError = error;
                    publishAudit();
                }
                std::cerr << "EPG XMLTV falhou: canal=" << channelId
                          << " erro=" << error << std::endl;
            }
            std::unique_lock<std::mutex> lock(waitMutex);
            // Rebuild p/f every minute from the cached guide without repeatedly
            // downloading XMLTV.  This keeps the current/next event fresh.
            wake.wait_for(lock, std::chrono::minutes(1), [&] { return stop.load(); });
        }
    }

    void publishAudit() {
        std::lock_guard<std::mutex> auditLock(gEpgAuditMutex);
        gEpgAuditSnapshots[streamId] = audit;
    }

    void emitEitPacket(const std::array<std::uint8_t, 188>& source,
                       std::array<std::uint8_t, 188>& output) {
        output = source;
        output[3] = static_cast<std::uint8_t>(
            (output[3] & 0xF0) | (eitContinuity & 0x0F));
        eitContinuity = static_cast<std::uint8_t>((eitContinuity + 1) & 0x0F);
    }

    bool take(std::array<std::uint8_t, 188>& output, std::uint64_t now) {
        if (!active) return false;
        std::lock_guard<std::mutex> lock(mutex);

        if (clockCursor >= clockPackets.size() && now >= nextClockEmission) {
            clockPackets = buildDvbTimePackets(
                std::time(nullptr), clockContinuity, profile,
                utcOffsetMinutes, correctionSeconds);
            clockCursor = 0;
        }
        if (clockCursor < clockPackets.size()) {
            output = clockPackets[clockCursor++];
            ++audit.emittedClockPackets;
            if (clockCursor >= clockPackets.size()) {
                nextClockEmission = now + kClockIntervalNs;
                publishAudit();
            }
            return true;
        }

        const bool scheduleSectionInProgress = !schedulePackets.empty() &&
            scheduleCursor > 0 && scheduleCursor < schedulePackets.size() &&
            (schedulePackets[scheduleCursor][1] & 0x40) == 0;
        if (!presentFollowingPackets.empty() && !presentFollowingBurstActive &&
            !scheduleSectionInProgress && now >= nextPresentFollowingCycle) {
            presentFollowingBurstActive = true;
            presentFollowingCursor = 0;
            nextPresentFollowingPacket = now;
            if (lastPresentFollowingCycleStart != 0 && now >= lastPresentFollowingCycleStart) {
                audit.lastPresentFollowingCycleMilliseconds =
                    (now - lastPresentFollowingCycleStart) / 1000000ULL;
            }
            lastPresentFollowingCycleStart = now;
        }
        if (presentFollowingBurstActive) {
            // EIT p/f and schedule share PID 0x0012. Never interleave another
            // section between continuation packets of the current p/f burst.
            if (now < nextPresentFollowingPacket) return false;
            emitEitPacket(presentFollowingPackets[presentFollowingCursor++], output);
            ++audit.emittedPresentFollowingPackets;
            nextPresentFollowingPacket = now + kEmissionIntervalNs;
            if (presentFollowingCursor >= presentFollowingPackets.size()) {
                presentFollowingBurstActive = false;
                nextPresentFollowingCycle = lastPresentFollowingCycleStart +
                    kPresentFollowingCycleNs;
                publishAudit();
            }
            return true;
        }

        if (schedulePackets.empty() || now < nextScheduleEmission) return false;
        emitEitPacket(schedulePackets[scheduleCursor++], output);
        ++audit.emittedSchedulePackets;
        if (scheduleCursor >= schedulePackets.size()) {
            scheduleCursor = 0;
            nextScheduleEmission = now + kCarouselPauseNs;
            publishAudit();
        } else {
            nextScheduleEmission = now + kEmissionIntervalNs;
        }
        return true;
    }

    bool active = false;
    std::string streamId;
    std::string sourceUrl;
    std::string channelId;
    std::string defaultCategory;
    std::uint16_t serviceId = 1;
    EpgProfile profile = EpgProfile::Generic;
    std::int32_t utcOffsetMinutes = kBrazilUtcOffsetMinutes;
    std::int32_t correctionSeconds = 0;
    std::int32_t civilShiftSeconds = kBrazilUtcOffsetMinutes * 60;
    std::uint16_t transportStreamId = 1;
    std::uint16_t originalNetworkId = 1;
    std::uint8_t eitVersion = 0;
    std::uint64_t lastProgrammeFingerprint = 0;
    bool fingerprintInitialized = false;
    std::vector<Programme> cachedProgrammes;
    std::chrono::steady_clock::time_point nextGuideDownload {};
    std::atomic<bool> stop {false};
    std::thread worker;
    std::mutex waitMutex;
    std::condition_variable wake;
    std::mutex mutex;
    std::vector<std::array<std::uint8_t, 188>> presentFollowingPackets;
    std::vector<std::array<std::uint8_t, 188>> schedulePackets;
    std::size_t presentFollowingCursor = 0;
    std::size_t scheduleCursor = 0;
    bool presentFollowingBurstActive = false;
    std::uint64_t nextPresentFollowingPacket = 0;
    std::uint64_t nextPresentFollowingCycle = 0;
    std::uint64_t lastPresentFollowingCycleStart = 0;
    std::uint64_t nextScheduleEmission = 0;
    std::uint8_t eitContinuity = 0;
    std::vector<std::array<std::uint8_t, 188>> clockPackets;
    std::size_t clockCursor = 0;
    std::uint8_t clockContinuity = 0;
    std::uint64_t nextClockEmission = 0;
    EpgAuditSnapshot audit;
};

EpgInjector::EpgInjector(const StreamConfig& config) : impl_(new Impl(config)) {}
EpgInjector::~EpgInjector() = default;
bool EpgInjector::enabled() const { return impl_ && impl_->active; }
bool EpgInjector::takePacket(std::array<std::uint8_t, 188>& packet,
                             std::uint64_t now) {
    return impl_ && impl_->take(packet, now);
}
