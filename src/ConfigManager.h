#pragma once

#include <jsoncpp/json/json.h>
#include <chrono>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

struct StreamOutputConfig {
    std::string outputType = "udp-cbr";
    std::string outputMode = "listener";
    std::string outputHost = "127.0.0.1";
    int outputPort = 1234;

    Json::Value toJson() const;
    static StreamOutputConfig fromJson(const Json::Value& root);
};

struct EpgSourceConfig {
    std::string id;
    std::string name;
    std::string url;
    bool isDefault = false;

    Json::Value toJson() const;
    static EpgSourceConfig fromJson(const Json::Value& root);
};

struct StreamConfig {
    std::string id;
    std::string name;
    std::string inputUri;
    std::string backupInputUri;
    std::string backupInputType = "url";
    bool backupFileLoop = false;
    std::string outputType = "udp-cbr";
    std::string outputMode = "listener";
    std::string outputHost = "127.0.0.1";
    int outputPort = 1234;
    std::string interfaceAddress;
    std::string inputInterfaceAddress;
    bool inputInterfaceAddressConfigured = false;
    std::string inputMode = "auto";
    // Runtime-only marker. Set after a YouTube page is resolved to its signed
    // media URL; it is deliberately not written to the persistent JSON.
    bool runtimeYoutubeInput = false;
    // Runtime-only: stores the resolved media URL to keep the same quality
    // across renewals. Not persisted to JSON.
    std::string runtimeYoutubeResolvedUrl;
    // Runtime-only: timestamp when the URL was resolved (to avoid unnecessary renewals)
    // Not persisted to JSON.
    std::chrono::steady_clock::time_point runtimeYoutubeResolvedTime;
    // Runtime-only expiry parsed from the signed HLS URL returned by Streamlink.
    // It uses wall-clock Unix time because the URL carries an epoch timestamp.
    std::optional<std::chrono::system_clock::time_point> runtimeYoutubeUrlExpiresAt;
    // Optional: quality selector for YouTube (e.g., "1080p", "720p", "best")
    std::string youtubeQuality;
    bool testPattern = false;
    bool autoStart = false;
    bool remapEnabled = false;
    bool cbr = true;
    uint64_t targetBitrate = 2000000;
    // 0 keeps the protocol-specific automatic value (5 s normally and 10 s
    // for a resolved YouTube input). Values from 1 to 30 are per-channel.
    uint32_t cacheSeconds = 0;
    bool transcodeEnabled = false;
    std::string transcodeResolution = "1920x1080";
    uint64_t transcodeVideoBitrate = 6000000;
    std::string transcodeAudioCodec = "aac";
    uint64_t transcodeAudioBitrate = 192000;
    bool audioVisualEnabled = false;
    std::string audioVisualImage;
    std::string audioVisualResolution = "1280x720";
    uint64_t audioVisualVideoBitrate = 700000;
    uint32_t audioPid = 0;
    uint32_t videoPid = 0;
    uint32_t inputServiceId = 0;
    uint32_t serviceId = 1;
    std::string serviceName;
    std::string serviceProvider;
    bool epgEnabled = false;
    // "generic" preserves the legacy DVB-SI-oriented payload. "isdbtb"
    // applies the Brazilian ABNT NBR 15603 EIT profile.
    std::string epgMode = "generic";
    uint32_t epgTransportStreamId = 1;
    uint32_t epgOriginalNetworkId = 1;
    // Initial MPEG-TS PSI/SI version used by the EPG-only emitter. The
    // supervisor persists and advances it across process restarts.
    uint32_t epgSignalVersion = 0;
    std::string epgSourceId = "braziltvepg";
    std::string epgSourceUrl = "https://github.com/limaalef/BrazilTVEPG/raw/refs/heads/main/claro.xml";
    std::string epgChannelId;
    // Optional fallback used only when XMLTV has no recognized category.
    std::string epgDefaultCategory;
    // ISDB-TB civil clock settings. The defaults preserve the existing
    // Brazilian UTC-03:00 output. Correction keeps advancing with host time.
    int32_t epgClockUtcOffsetMinutes = -180;
    int32_t epgClockCorrectionSeconds = 0;
    std::vector<StreamOutputConfig> additionalOutputs;

    Json::Value toJson() const;
    static StreamConfig fromJson(const Json::Value& root);
};

struct AppConfig {
    std::string login = "admin";
    std::string password = "admin";
    std::string serverName = "TVStreamer5";
    int httpPort = 9000;
    std::string language = "pt";
    std::string telegramToken;
    std::string telegramChatId;
    std::vector<EpgSourceConfig> epgSources {{
        "braziltvepg",
        "BrazilTVEPG (padrão)",
        "https://github.com/limaalef/BrazilTVEPG/raw/refs/heads/main/claro.xml",
        true
    }};
    std::vector<StreamConfig> streams;

    Json::Value toJson() const;
    static AppConfig fromJson(const Json::Value& root);
};

// Restores the runtime EPG source URL from its persisted identifier. The URL
// may be deliberately redacted from API responses, so callers that construct
// a StreamConfig from an HTTP payload must hydrate it before starting output.
bool resolveEpgSource(StreamConfig& stream, const std::vector<EpgSourceConfig>& sources);

struct SubscriberConfig {
    std::string name;
    std::string primaryIp;
    std::string backupIp;
    std::string addedAt;
    bool enabled = true;
    std::vector<std::string> streamIds;

    Json::Value toJson() const;
    static SubscriberConfig fromJson(const Json::Value& root);
};

struct SubscriberListConfig {
    bool filteringEnabled = false;
    std::vector<SubscriberConfig> subscribers;

    Json::Value toJson() const;
    static SubscriberListConfig fromJson(const Json::Value& root);
};

class ConfigManager {
public:
    ConfigManager();
    bool load();
    bool save();
    bool loadSubscribers();
    bool saveSubscribers();

    AppConfig config;
    SubscriberListConfig subscribers;

private:
    std::filesystem::path configPath;
    std::mutex fileMutex;
};
