#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <ctime>
#include <memory>
#include <string>
#include <vector>

#include "ConfigManager.h"

struct XmltvChannelInfo {
    std::string id;
    std::string name;
    std::vector<std::string> displayNames;
    std::string iconUrl;
    std::size_t programmeCount = 0;
};

struct EpgAuditSnapshot {
    bool configured = false;
    bool active = false;
    bool guideLoaded = false;
    std::string profile;
    std::string channelId;
    std::string sourceLabel;
    std::string currentTitle;
    std::string nextTitle;
    std::string lastError;
    std::uint16_t serviceId = 0;
    std::uint16_t transportStreamId = 0;
    std::uint16_t originalNetworkId = 0;
    std::uint8_t eitVersion = 0;
    std::size_t programmeCount = 0;
    std::size_t presentFollowingSections = 0;
    std::size_t scheduleSections = 0;
    std::size_t presentFollowingPackets = 0;
    std::size_t schedulePackets = 0;
    std::uint64_t emittedPresentFollowingPackets = 0;
    std::uint64_t emittedSchedulePackets = 0;
    std::uint64_t emittedClockPackets = 0;
    std::uint64_t lastPresentFollowingCycleMilliseconds = 0;
    std::time_t guideStart = 0;
    std::time_t guideEnd = 0;
    std::time_t loadedAt = 0;
};

bool getEpgAuditSnapshot(const std::string& streamId, EpgAuditSnapshot& snapshot);

// Reads the complete channel catalogue declared by an XMLTV source. Results
// are cached briefly so opening the channel form does not repeatedly download
// large provider files.
bool loadXmltvChannels(const std::string& url,
                       std::vector<XmltvChannelInfo>& channels,
                       std::string& error);

// Downloads an XMLTV guide and turns it into standards-compliant DVB EIT
// packets. Packets are emitted only into free transport slots, so enabling
// EPG never changes the configured multicast bitrate.
class EpgInjector {
public:
    explicit EpgInjector(const StreamConfig& config);
    ~EpgInjector();

    EpgInjector(const EpgInjector&) = delete;
    EpgInjector& operator=(const EpgInjector&) = delete;

    bool enabled() const;
    bool takePacket(std::array<std::uint8_t, 188>& packet,
                    std::uint64_t monotonicNanoseconds);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
