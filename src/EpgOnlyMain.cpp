#include "EpgInjector.h"

#include <boost/asio.hpp>
#include <curl/curl.h>
#include <jsoncpp/json/json.h>

#include <array>
#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr std::size_t kTsPacketSize = 188;
constexpr std::size_t kPacketsPerDatagram = 7;
constexpr std::size_t kDatagramSize = kTsPacketSize * kPacketsPerDatagram;
constexpr std::uint64_t kNanosecondsPerSecond = 1000000000ULL;
constexpr std::uint64_t kPatPmtIntervalNanoseconds = 100000000ULL;
constexpr std::uint64_t kSdtIntervalNanoseconds = 500000000ULL;
constexpr std::uint64_t kCdtIntervalNanoseconds = 1000000000ULL;
constexpr const char* kDefaultSourceUrl =
    "https://github.com/limaalef/BrazilTVEPG/raw/refs/heads/main/claro.xml";

volatile std::sig_atomic_t gStopRequested = 0;

class CurlRuntime {
public:
    CurlRuntime() {
        if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
            throw std::runtime_error("could not initialize libcurl");
        }
    }
    ~CurlRuntime() { curl_global_cleanup(); }
};

void handleSignal(int) {
    gStopRequested = 1;
}

std::string environment(const char* name, const std::string& fallback = {}) {
    const char* value = std::getenv(name);
    return value && *value ? value : fallback;
}

std::uint64_t unsignedEnvironment(const char* name,
                                  std::uint64_t fallback,
                                  std::uint64_t minimum,
                                  std::uint64_t maximum) {
    const std::string text = environment(name);
    if (text.empty()) return fallback;
    std::size_t consumed = 0;
    std::uint64_t value = 0;
    try {
        value = std::stoull(text, &consumed, 10);
    } catch (const std::exception&) {
        throw std::runtime_error(std::string(name) + " must be an unsigned integer");
    }
    if (consumed != text.size() || value < minimum || value > maximum) {
        throw std::runtime_error(std::string(name) + " is outside the accepted range");
    }
    return value;
}

std::uint64_t monotonicNanoseconds() {
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

std::uint32_t mpegCrc32(const std::uint8_t* data, std::size_t size) {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < size; ++index) {
        crc ^= static_cast<std::uint32_t>(data[index]) << 24;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x80000000U) ?
                (crc << 1) ^ 0x04C11DB7U : crc << 1;
        }
    }
    return crc;
}

void finishSection(std::vector<std::uint8_t>& section) {
    const std::size_t sectionLength = section.size() - 3 + 4;
    if (sectionLength > 0x0FFF) {
        throw std::runtime_error("PSI section is too large");
    }
    section[1] = static_cast<std::uint8_t>(
        (section[1] & 0xF0) | ((sectionLength >> 8) & 0x0F));
    section[2] = static_cast<std::uint8_t>(sectionLength & 0xFF);
    const std::uint32_t crc = mpegCrc32(section.data(), section.size());
    section.push_back(static_cast<std::uint8_t>((crc >> 24) & 0xFF));
    section.push_back(static_cast<std::uint8_t>((crc >> 16) & 0xFF));
    section.push_back(static_cast<std::uint8_t>((crc >> 8) & 0xFF));
    section.push_back(static_cast<std::uint8_t>(crc & 0xFF));
}

void append16(std::vector<std::uint8_t>& target, std::uint16_t value) {
    target.push_back(static_cast<std::uint8_t>((value >> 8) & 0xFF));
    target.push_back(static_cast<std::uint8_t>(value & 0xFF));
}

std::string descriptorText(std::string value, std::size_t maximum) {
    if (value.size() > maximum) value.resize(maximum);
    for (char& item : value) {
        const unsigned char byte = static_cast<unsigned char>(item);
        if (byte < 0x20 || byte == 0x7F) item = ' ';
    }
    return value;
}

struct EpgService {
    struct LogoAsset {
        std::uint8_t type = 0;
        std::string path;
    };
    std::string id;
    std::string name;
    std::string channelId;
    std::uint16_t serviceId = 1;
    std::string sourceUrl;
    std::vector<LogoAsset> logoAssets;
    std::uint16_t logoId = 0;
    std::uint16_t logoVersion = 0;
    std::uint16_t downloadDataId = 0;
};

struct EpgOnlyConfiguration {
    std::string streamId;
    std::string sourceUrl;
    std::uint16_t transportStreamId = 1;
    std::uint16_t originalNetworkId = 1;
    std::uint8_t signalVersion = 0;
    std::vector<EpgService> services;
};

std::vector<std::uint8_t> makePatSection(std::uint16_t transportStreamId,
                                         const std::vector<EpgService>& services,
                                         std::uint16_t pmtPid) {
    std::vector<std::uint8_t> section {0x00, 0xB0, 0x00};
    append16(section, transportStreamId);
    section.insert(section.end(), {0xC1, 0x00, 0x00});
    for (std::size_t index = 0; index < services.size(); ++index) {
        append16(section, services[index].serviceId);
        const auto servicePmtPid = static_cast<std::uint16_t>(pmtPid + index);
        section.push_back(static_cast<std::uint8_t>(0xE0 | ((servicePmtPid >> 8) & 0x1F)));
        section.push_back(static_cast<std::uint8_t>(servicePmtPid & 0xFF));
    }
    finishSection(section);
    return section;
}

std::vector<std::uint8_t> makePmtSection(std::uint16_t serviceId) {
    std::vector<std::uint8_t> section {0x02, 0xB0, 0x00};
    append16(section, serviceId);
    section.insert(section.end(), {0xC1, 0x00, 0x00});
    // The auxiliary service carries no elementary streams and therefore has
    // no PCR.  PID 0x1FFF is the standards-defined no-PCR value.
    section.insert(section.end(), {0xFF, 0xFF, 0xF0, 0x00});
    finishSection(section);
    return section;
}

std::vector<std::uint8_t> makeSdtSection(std::uint16_t transportStreamId,
                                         std::uint16_t originalNetworkId,
                                         const std::vector<EpgService>& services,
                                         std::uint8_t signalVersion) {
    const std::string provider = "TVStream";
    std::vector<std::uint8_t> section {0x42, 0xF0, 0x00};
    append16(section, transportStreamId);
    section.insert(section.end(), {
        static_cast<std::uint8_t>(0xC1 | ((signalVersion & 0x1F) << 1)), 0x00, 0x00});
    append16(section, originalNetworkId);
    section.push_back(0xFF);
    for (const auto& service : services) {
        // Keep a 64-service SDT inside the 4093-byte MPEG section limit.
        const std::string name = descriptorText(service.name, 32);
        std::vector<std::uint8_t> descriptor {0x48, 0x00, 0x01};
        descriptor.push_back(static_cast<std::uint8_t>(provider.size()));
        descriptor.insert(descriptor.end(), provider.begin(), provider.end());
        descriptor.push_back(static_cast<std::uint8_t>(name.size()));
        descriptor.insert(descriptor.end(), name.begin(), name.end());
        descriptor[1] = static_cast<std::uint8_t>(descriptor.size() - 2);
        if (!service.logoAssets.empty()) {
            descriptor.push_back(0xCF);
            descriptor.push_back(0x07);
            descriptor.push_back(0x01);
            append16(descriptor, static_cast<std::uint16_t>(0xFE00 | (service.logoId & 0x01FF)));
            append16(descriptor, static_cast<std::uint16_t>(0xF000 | (service.logoVersion & 0x0FFF)));
            append16(descriptor, service.downloadDataId);
        }
        append16(section, service.serviceId);
        section.push_back(0xFF);
        const std::size_t descriptorsLength = descriptor.size();
        section.push_back(static_cast<std::uint8_t>(
            0x80 | ((descriptorsLength >> 8) & 0x0F)));
        section.push_back(static_cast<std::uint8_t>(descriptorsLength & 0xFF));
        section.insert(section.end(), descriptor.begin(), descriptor.end());
    }
    finishSection(section);
    return section;
}

std::vector<std::uint8_t> readLogoFile(const std::string& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) throw std::runtime_error("could not open logo PNG: " + path);
    const auto length = input.tellg();
    if (length <= 0 || length > 2 * 1024 * 1024) {
        throw std::runtime_error("logo PNG size is invalid: " + path);
    }
    std::vector<std::uint8_t> data(static_cast<std::size_t>(length));
    input.seekg(0);
    if (!input.read(reinterpret_cast<char*>(data.data()), length)) {
        throw std::runtime_error("could not read logo PNG: " + path);
    }
    return data;
}

std::vector<std::uint8_t> makeCdtSection(std::uint16_t originalNetworkId,
                                         const EpgService& service,
                                         const EpgService::LogoAsset& asset) {
    const auto png = readLogoFile(asset.path);
    std::vector<std::uint8_t> section {0xC8, 0xF0, 0x00};
    append16(section, service.downloadDataId);
    section.push_back(static_cast<std::uint8_t>(0xC1 | ((service.logoVersion & 0x1F) << 1)));
    section.push_back(asset.type);
    section.push_back(0x05);
    append16(section, originalNetworkId);
    section.push_back(0x01);
    section.insert(section.end(), {0xF0, 0x00});
    section.push_back(asset.type);
    append16(section, static_cast<std::uint16_t>(0xFE00 | (service.logoId & 0x01FF)));
    append16(section, static_cast<std::uint16_t>(0xF000 | (service.logoVersion & 0x0FFF)));
    if (png.size() > 0xFFFF) throw std::runtime_error("logo PNG exceeds CDT data_size");
    append16(section, static_cast<std::uint16_t>(png.size()));
    section.insert(section.end(), png.begin(), png.end());
    finishSection(section);
    return section;
}

void enqueueSection(const std::vector<std::uint8_t>& section, std::uint16_t pid,
                    std::uint8_t& continuity,
                    std::deque<std::array<std::uint8_t, kTsPacketSize>>& packets) {
    std::size_t offset = 0;
    bool first = true;
    while (offset < section.size()) {
        std::array<std::uint8_t, kTsPacketSize> packet {};
        packet.fill(0xFF);
        packet[0] = 0x47;
        packet[1] = static_cast<std::uint8_t>((first ? 0x40 : 0x00) | ((pid >> 8) & 0x1F));
        packet[2] = static_cast<std::uint8_t>(pid & 0xFF);
        packet[3] = static_cast<std::uint8_t>(0x10 | (continuity & 0x0F));
        const std::size_t payloadOffset = first ? 5 : 4;
        if (first) packet[4] = 0x00;
        const std::size_t copySize = std::min(section.size() - offset,
                                               kTsPacketSize - payloadOffset);
        std::copy_n(section.begin() + offset, copySize, packet.begin() + payloadOffset);
        offset += copySize;
        packets.push_back(packet);
        continuity = static_cast<std::uint8_t>((continuity + 1) & 0x0F);
        first = false;
    }
}

class SignallingCarousel {
public:
    SignallingCarousel(const EpgOnlyConfiguration& config, std::uint16_t pmtPid)
        : pmtPid_(pmtPid),
          pat_(makePatSection(config.transportStreamId, config.services, pmtPid)),
          sdt_(makeSdtSection(config.transportStreamId, config.originalNetworkId,
                              config.services, config.signalVersion)) {
        for (const auto& service : config.services) {
            pmts_.push_back(makePmtSection(service.serviceId));
            pmtContinuities_.push_back(0);
            for (const auto& asset : service.logoAssets) {
                cdts_.push_back(makeCdtSection(config.originalNetworkId, service, asset));
            }
        }
    }

    bool take(std::array<std::uint8_t, kTsPacketSize>& packet, std::uint64_t now) {
        if (!pending_.empty()) {
            packet = pending_.front();
            pending_.pop_front();
            return true;
        }
        if (now >= nextPatPmt_) {
            enqueueSection(pat_, 0x0000, patContinuity_, pending_);
            for (std::size_t index = 0; index < pmts_.size(); ++index) {
                enqueueSection(pmts_[index], static_cast<std::uint16_t>(pmtPid_ + index),
                               pmtContinuities_[index], pending_);
            }
            nextPatPmt_ = now + kPatPmtIntervalNanoseconds;
            packet = pending_.front();
            pending_.pop_front();
            return true;
        }
        if (now >= nextSdt_) {
            enqueueSection(sdt_, 0x0011, sdtContinuity_, pending_);
            nextSdt_ = now + kSdtIntervalNanoseconds;
            packet = pending_.front();
            pending_.pop_front();
            return true;
        }
        if (!cdts_.empty() && now >= nextCdt_) {
            for (const auto& cdt : cdts_) enqueueSection(cdt, 0x0029, cdtContinuity_, pending_);
            nextCdt_ = now + kCdtIntervalNanoseconds;
            packet = pending_.front();
            pending_.pop_front();
            return true;
        }
        return false;
    }

private:
    std::uint16_t pmtPid_;
    std::vector<std::uint8_t> pat_;
    std::vector<std::vector<std::uint8_t>> pmts_;
    std::vector<std::uint8_t> sdt_;
    std::vector<std::vector<std::uint8_t>> cdts_;
    std::vector<std::uint8_t> pmtContinuities_;
    std::deque<std::array<std::uint8_t, kTsPacketSize>> pending_;
    std::uint8_t patContinuity_ = 0;
    std::uint8_t sdtContinuity_ = 0;
    std::uint8_t cdtContinuity_ = 0;
    std::uint64_t nextPatPmt_ = 0;
    std::uint64_t nextSdt_ = 0;
    std::uint64_t nextCdt_ = 0;
};

void makeNullPacket(std::array<std::uint8_t, kTsPacketSize>& packet,
                    std::uint8_t& continuity) {
    packet.fill(0xFF);
    packet[0] = 0x47;
    packet[1] = 0x1F;
    packet[2] = 0xFF;
    packet[3] = static_cast<std::uint8_t>(0x10 | (continuity & 0x0F));
    continuity = static_cast<std::uint8_t>((continuity + 1) & 0x0F);
}

EpgOnlyConfiguration loadConfiguration() {
    EpgOnlyConfiguration config;
    config.streamId = environment("EPG_STREAM_ID", "epg-only");
    config.sourceUrl = environment("EPG_SOURCE_URL", kDefaultSourceUrl);
    config.transportStreamId = static_cast<std::uint16_t>(
        unsignedEnvironment("EPG_TSID", 1, 1, 65535));
    config.originalNetworkId = static_cast<std::uint16_t>(
        unsignedEnvironment("EPG_ONID", 1, 1, 65535));
    config.signalVersion = static_cast<std::uint8_t>(
        unsignedEnvironment("EPG_SIGNAL_VERSION", 0, 0, 31));
    const std::string servicesJson = environment("EPG_SERVICES_JSON");
    if (!servicesJson.empty()) {
        Json::Value root;
        Json::CharReaderBuilder reader;
        std::string errors;
        std::istringstream input(servicesJson);
        if (!Json::parseFromStream(reader, input, &root, &errors) || !root.isArray()) {
            throw std::runtime_error("EPG_SERVICES_JSON is invalid: " + errors);
        }
        for (const auto& value : root) {
            EpgService service;
            service.id = value.get("id", "").asString();
            service.name = value.get("name", "").asString();
            service.channelId = value.get("epg_channel_id", "").asString();
            service.serviceId = static_cast<std::uint16_t>(value.get("service_id", 0).asUInt());
            service.sourceUrl = value.get("source_url", config.sourceUrl).asString();
            const auto logo = value["logo"];
            if (logo.isObject() && logo.get("enabled", false).asBool()) {
                service.logoId = static_cast<std::uint16_t>(logo.get("logo_id", service.serviceId & 0x01FF).asUInt());
                service.logoVersion = static_cast<std::uint16_t>(logo.get("logo_version", 0).asUInt());
                service.downloadDataId = static_cast<std::uint16_t>(logo.get("download_data_id", service.serviceId).asUInt());
                const auto variants = logo["variants"];
                if (variants.isObject()) {
                    for (unsigned type = 0; type <= 5; ++type) {
                        const std::string path = variants.get(std::to_string(type), "").asString();
                        if (!path.empty()) service.logoAssets.push_back({static_cast<std::uint8_t>(type), path});
                    }
                }
                if (!service.logoAssets.empty() && service.logoAssets.size() != 6) {
                    throw std::runtime_error("Enabled logo requires all six ARIB variants");
                }
            }
            config.services.push_back(std::move(service));
        }
    } else {
        EpgService service;
        service.id = "legacy";
        service.name = environment("EPG_STREAM_NAME", config.streamId);
        service.channelId = environment("EPG_CHANNEL_ID");
        service.sourceUrl = config.sourceUrl;
        service.serviceId = static_cast<std::uint16_t>(
            unsignedEnvironment("EPG_SERVICE_ID", 1, 1, 65535));
        config.services.push_back(std::move(service));
    }
    if (config.services.empty() || config.services.size() > 64) {
        throw std::runtime_error("EPG services must contain between 1 and 64 entries");
    }
    std::vector<std::uint16_t> ids;
    for (std::size_t index = 0; index < config.services.size(); ++index) {
        auto& service = config.services[index];
        if (service.id.empty()) service.id = "service-" + std::to_string(index + 1);
        if (service.name.empty() || service.channelId.empty() || service.serviceId == 0) {
            throw std::runtime_error("Every EPG service requires name, channel ID and service ID");
        }
        if (service.sourceUrl.empty()) service.sourceUrl = config.sourceUrl;
        if (std::find(ids.begin(), ids.end(), service.serviceId) != ids.end()) {
            throw std::runtime_error("EPG service IDs must be unique");
        }
        ids.push_back(service.serviceId);
    }
    if (config.sourceUrl.empty()) {
        throw std::runtime_error("EPG_SOURCE_URL is required");
    }
    return config;
}

StreamConfig injectorConfiguration(const EpgOnlyConfiguration& carrier,
                                   const EpgService& service,
                                   std::size_t index) {
    StreamConfig config;
    config.id = carrier.streamId + "-" + service.id + "-" + std::to_string(index);
    config.name = service.name;
    config.epgEnabled = true;
    config.epgMode = "isdbtb";
    config.epgSourceUrl = service.sourceUrl;
    config.epgChannelId = service.channelId;
    config.serviceId = service.serviceId;
    config.epgTransportStreamId = carrier.transportStreamId;
    config.epgOriginalNetworkId = carrier.originalNetworkId;
    return config;
}

std::uint16_t packetPid(const std::array<std::uint8_t, kTsPacketSize>& packet) {
    return static_cast<std::uint16_t>(((packet[1] & 0x1F) << 8) | packet[2]);
}

void rewriteContinuity(std::array<std::uint8_t, kTsPacketSize>& packet,
                       std::uint8_t& continuity) {
    packet[3] = static_cast<std::uint8_t>((packet[3] & 0xF0) | (continuity & 0x0F));
    continuity = static_cast<std::uint8_t>((continuity + 1) & 0x0F);
}

bool takeAggregatedPacket(std::vector<std::unique_ptr<EpgInjector>>& injectors,
                          std::size_t& cursor,
                          std::size_t& activeInjector,
                          std::size_t& activeSectionRemaining,
                          std::array<std::uint8_t, kTsPacketSize>& packet,
                          std::uint64_t now,
                          std::uint8_t& eitContinuity,
                          std::uint8_t& clockContinuity) {
    if (injectors.empty()) return false;
    for (std::size_t attempt = 0; attempt < injectors.size() * 3; ++attempt) {
        const bool continuing = activeSectionRemaining > 0 && activeInjector < injectors.size();
        const std::size_t index = continuing ? activeInjector : cursor++ % injectors.size();
        if (!injectors[index]->takePacket(packet, now)) continue;
        const auto pid = packetPid(packet);
        if (pid == 0x0014 && index != 0) {
            activeSectionRemaining = 0;
            continue;
        }
        const bool payloadStart = (packet[1] & 0x40) != 0;
        std::size_t payloadOffset = 4;
        const auto adaptationControl = static_cast<std::uint8_t>((packet[3] >> 4) & 0x03);
        if (adaptationControl == 3) payloadOffset += 1 + packet[4];
        if (payloadOffset >= packet.size()) {
            activeSectionRemaining = 0;
            continue;
        }
        if (payloadStart) {
            const std::size_t pointer = packet[payloadOffset];
            const std::size_t sectionOffset = payloadOffset + 1 + pointer;
            if (sectionOffset + 3 > packet.size()) {
                activeSectionRemaining = 0;
                continue;
            }
            const std::size_t sectionSize = 3 +
                (((packet[sectionOffset + 1] & 0x0F) << 8) | packet[sectionOffset + 2]);
            const std::size_t carried = packet.size() - sectionOffset;
            activeSectionRemaining = sectionSize > carried ? sectionSize - carried : 0;
            activeInjector = index;
        } else if (continuing) {
            const std::size_t carried = packet.size() - payloadOffset;
            activeSectionRemaining = activeSectionRemaining > carried
                ? activeSectionRemaining - carried : 0;
        } else {
            continue;
        }
        if (pid == 0x0012) rewriteContinuity(packet, eitContinuity);
        else if (pid == 0x0014) rewriteContinuity(packet, clockContinuity);
        else continue;
        return true;
    }
    return false;
}

void logAudit(const std::string& streamId) {
    EpgAuditSnapshot audit;
    if (!getEpgAuditSnapshot(streamId, audit)) return;
    std::cerr << "EPG-only status: active=" << (audit.active ? "yes" : "no")
              << " guide=" << (audit.guideLoaded ? "loaded" : "waiting")
              << " programmes=" << audit.programmeCount
              << " emitted_eit_pf=" << audit.emittedPresentFollowingPackets
              << " emitted_eit_schedule=" << audit.emittedSchedulePackets
              << " emitted_clock=" << audit.emittedClockPackets;
    if (!audit.lastError.empty()) std::cerr << " error=" << audit.lastError;
    std::cerr << std::endl;
}

} // namespace

int main() {
    try {
        const EpgOnlyConfiguration config = loadConfiguration();
        const std::string destinationText = environment("EPG_DESTINATION");
        const std::string interfaceText = environment("EPG_INTERFACE");
        const auto port = static_cast<unsigned short>(
            unsignedEnvironment("EPG_PORT", 0, 1, 65535));
        const std::uint64_t bitrate =
            unsignedEnvironment("EPG_BITRATE", 1000000, 100000, 100000000);
        const auto ttl = static_cast<unsigned char>(
            unsignedEnvironment("EPG_TTL", 32, 1, 255));
        const auto pmtPid = static_cast<std::uint16_t>(
            unsignedEnvironment("EPG_PMT_PID", 0x1000, 0x0020, 0x1FFE));
        if (static_cast<std::uint64_t>(pmtPid) + config.services.size() - 1 > 0x1FFE) {
            throw std::runtime_error("EPG PMT PID range exceeds 0x1FFE");
        }

        if (destinationText.empty()) throw std::runtime_error("EPG_DESTINATION is required");
        if (interfaceText.empty()) throw std::runtime_error("EPG_INTERFACE is required");
        if (port == 0) throw std::runtime_error("EPG_PORT is required");

        const auto destinationAddress = boost::asio::ip::make_address_v4(destinationText);
        const auto interfaceAddress = boost::asio::ip::make_address_v4(interfaceText);
        if (!destinationAddress.is_multicast()) {
            throw std::runtime_error("EPG_DESTINATION must be an IPv4 multicast address");
        }

        CurlRuntime curlRuntime;

        int result = 0;
        {
            boost::asio::io_context ioContext;
            boost::asio::ip::udp::socket socket(ioContext);
            socket.open(boost::asio::ip::udp::v4());
            socket.set_option(boost::asio::ip::multicast::outbound_interface(interfaceAddress));
            socket.set_option(boost::asio::ip::multicast::hops(ttl));
            socket.set_option(boost::asio::socket_base::send_buffer_size(1024 * 1024));
            const boost::asio::ip::udp::endpoint destination(destinationAddress, port);

            std::vector<std::unique_ptr<EpgInjector>> injectors;
            std::vector<std::string> auditIds;
            for (std::size_t index = 0; index < config.services.size(); ++index) {
                const auto serviceConfig = injectorConfiguration(config, config.services[index], index);
                auditIds.push_back(serviceConfig.id);
                auto injector = std::make_unique<EpgInjector>(serviceConfig);
                if (!injector->enabled()) throw std::runtime_error("EPG injector is disabled");
                injectors.push_back(std::move(injector));
            }
            SignallingCarousel signalling(config, pmtPid);

            std::signal(SIGINT, handleSignal);
            std::signal(SIGTERM, handleSignal);

            const std::uint64_t datagramIntervalNanoseconds =
                (kDatagramSize * 8ULL * kNanosecondsPerSecond) / bitrate;
            if (datagramIntervalNanoseconds == 0) {
                throw std::runtime_error("EPG_BITRATE is too high for the pacing clock");
            }

            std::cerr << "EPG-only started: destination=" << destinationText << ':' << port
                      << " interface=" << interfaceText
                      << " bitrate=" << bitrate
                      << " ttl=" << static_cast<unsigned>(ttl)
                      << " services=" << config.services.size()
                      << " tsid=" << config.transportStreamId
                      << " onid=" << config.originalNetworkId
                      << " pmt_pid_base=" << pmtPid << std::endl;

            std::array<std::uint8_t, kDatagramSize> datagram {};
            std::uint8_t nullContinuity = 0;
            std::uint8_t eitContinuity = 0;
            std::uint8_t clockContinuity = 0;
            std::size_t injectorCursor = 0;
            std::size_t activeInjector = injectors.size();
            std::size_t activeSectionRemaining = 0;
            std::uint64_t nextDeadline = monotonicNanoseconds();
            auto nextAuditLog = std::chrono::steady_clock::now();

            while (!gStopRequested) {
                for (std::size_t slot = 0; slot < kPacketsPerDatagram; ++slot) {
                    std::array<std::uint8_t, kTsPacketSize> packet {};
                    const std::uint64_t slotTime = nextDeadline +
                        (datagramIntervalNanoseconds * slot / kPacketsPerDatagram);
                    if (!signalling.take(packet, slotTime) &&
                        !takeAggregatedPacket(injectors, injectorCursor, activeInjector,
                                              activeSectionRemaining, packet, slotTime,
                                              eitContinuity, clockContinuity)) {
                        makeNullPacket(packet, nullContinuity);
                    }
                    std::memcpy(datagram.data() + slot * kTsPacketSize,
                                packet.data(), packet.size());
                }

                boost::system::error_code error;
                const std::size_t sent = socket.send_to(
                    boost::asio::buffer(datagram), destination, 0, error);
                if (error || sent != datagram.size()) {
                    std::cerr << "EPG-only UDP send failed: "
                              << (error ? error.message() : "short datagram") << std::endl;
                    result = 1;
                    break;
                }

                nextDeadline += datagramIntervalNanoseconds;
                const std::uint64_t now = monotonicNanoseconds();
                if (now > nextDeadline + datagramIntervalNanoseconds * 4ULL) {
                    nextDeadline = now + datagramIntervalNanoseconds;
                }
                std::this_thread::sleep_until(
                    std::chrono::steady_clock::time_point(std::chrono::nanoseconds(nextDeadline)));

                const auto steadyNow = std::chrono::steady_clock::now();
                if (steadyNow >= nextAuditLog) {
                    for (const auto& auditId : auditIds) logAudit(auditId);
                    nextAuditLog = steadyNow + std::chrono::seconds(10);
                }
            }

            std::cerr << "EPG-only stopped" << std::endl;
        }
        return result;
    } catch (const std::exception& error) {
        std::cerr << "EPG-only configuration error: " << error.what() << std::endl;
        return 2;
    }
}
