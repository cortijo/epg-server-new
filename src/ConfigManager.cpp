#include "ConfigManager.h"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>
#include <chrono>
#include <ctime>
#include <iomanip>

namespace {
EpgSourceConfig defaultBrazilTvEpgSource() {
    EpgSourceConfig source;
    source.id = "braziltvepg";
    source.name = "BrazilTVEPG (padrão)";
    source.url = "https://github.com/limaalef/BrazilTVEPG/raw/refs/heads/main/claro.xml";
    source.isDefault = true;
    return source;
}
}

EpgSourceConfig EpgSourceConfig::fromJson(const Json::Value& root) {
    EpgSourceConfig source;
    source.id = root.get("id", "").asString();
    source.name = root.get("name", "").asString();
    source.url = root.get("url", "").asString();
    source.isDefault = root.get("is_default", false).asBool();
    return source;
}

Json::Value EpgSourceConfig::toJson() const {
    Json::Value root;
    root["id"] = id;
    root["name"] = name;
    root["url"] = url;
    root["is_default"] = isDefault;
    return root;
}

StreamOutputConfig StreamOutputConfig::fromJson(const Json::Value& root) {
    StreamOutputConfig output;
    output.outputType = root.get("output_type", "udp-cbr").asString();
    output.outputMode = root.get("output_mode", "listener").asString();
    output.outputHost = root.get("output_host", "127.0.0.1").asString();
    output.outputPort = root.get("output_port", 1234).asInt();
    return output;
}

Json::Value StreamOutputConfig::toJson() const {
    Json::Value root;
    root["output_type"] = outputType;
    root["output_mode"] = outputMode;
    root["output_host"] = outputHost;
    root["output_port"] = outputPort;
    return root;
}

StreamConfig StreamConfig::fromJson(const Json::Value& root) {
    StreamConfig config;
    config.id = root.get("id", "").asString();
    config.name = root.get("name", "").asString();
    config.inputUri = root.get("input_uri", "").asString();
    config.backupInputUri = root.get("backup_input_uri", "").asString();
    config.backupInputType = root.get("backup_input_type", "").asString();
    if (config.backupInputType.empty()) {
        const std::string backupUri = config.backupInputUri;
        config.backupInputType =
            backupUri.empty()
                ? "url"
                : (backupUri.rfind("file://", 0) == 0 || backupUri.find("://") == std::string::npos
                ? "file"
                : "url");
    }
    config.backupFileLoop = root.get("backup_file_loop", false).asBool();
    config.outputType = root.get("output_type", "udp-cbr").asString();
    config.outputMode = root.get("output_mode", "listener").asString();
    config.outputHost = root.get("output_host", "127.0.0.1").asString();
    config.outputPort = root.get("output_port", 1234).asInt();
    config.interfaceAddress = root.get("interface_address", "").asString();
    config.inputInterfaceAddressConfigured = root.isMember("input_interface_address");
    config.inputInterfaceAddress = root.get("input_interface_address", "").asString();
    config.inputMode = root.get("input_mode", "auto").asString();
    config.youtubeQuality = root.get("youtube_quality", "").asString();
    config.testPattern = root.get("test_pattern", false).asBool();
    config.autoStart = root.get("auto_start", false).asBool();
    config.remapEnabled = root.get("remap_enabled", false).asBool();
    config.cbr = root.get("cbr", true).asBool();
    config.targetBitrate = root.get("target_bitrate", Json::UInt64(2000000)).asUInt64();
    config.cacheSeconds = std::clamp<uint32_t>(root.get("cache_seconds", 0).asUInt(), 0, 30);
    config.transcodeEnabled = root.get("transcode_enabled", false).asBool();
    config.transcodeResolution = root.get("transcode_resolution", "1920x1080").asString();
    config.transcodeVideoBitrate = root.get("transcode_video_bitrate", Json::UInt64(6000000)).asUInt64();
    config.transcodeAudioCodec = root.get("transcode_audio_codec", "aac").asString();
    if (config.transcodeAudioCodec != "aac" && config.transcodeAudioCodec != "mp3" &&
        config.transcodeAudioCodec != "copy") {
        config.transcodeAudioCodec = "aac";
    }
    config.transcodeAudioBitrate = root.get("transcode_audio_bitrate", Json::UInt64(192000)).asUInt64();
    config.transcodeAudioBitrate = std::clamp<uint64_t>(config.transcodeAudioBitrate, 64000, 320000);
    config.audioVisualEnabled = root.get("audio_visual_enabled", false).asBool();
    config.audioVisualImage = root.get("audio_visual_image", "").asString();
    config.audioVisualResolution = root.get("audio_visual_resolution", "1280x720").asString();
    if (config.audioVisualResolution != "1920x1080" &&
        config.audioVisualResolution != "1280x720" &&
        config.audioVisualResolution != "720x576") {
        config.audioVisualResolution = "1280x720";
    }
    config.audioVisualVideoBitrate = std::clamp<uint64_t>(
        root.get("audio_visual_video_bitrate", Json::UInt64(700000)).asUInt64(),
        300000, 3000000);
    config.audioPid = root.get("audio_pid", 0).asUInt();
    config.videoPid = root.get("video_pid", 0).asUInt();
    config.serviceId = root.get("service_id", 1).asUInt();
    // input_service_id=0 means automatic PAT-based service detection.
    // Existing configurations that predate input_service_id keep their previous
    // explicit behaviour by inheriting service_id; newly created streams use 0.
    config.inputServiceId = root.isMember("input_service_id")
        ? root.get("input_service_id", 0).asUInt()
        : config.serviceId;
    config.serviceName = root.get("service_name", "").asString();
    config.serviceProvider = root.get("service_provider", "").asString();
    config.epgEnabled = root.get("epg_enabled", false).asBool();
    config.epgMode = root.get("epg_mode", "generic").asString();
    if (config.epgMode != "isdbtb") config.epgMode = "generic";
    if (config.epgMode == "isdbtb") {
        config.serviceId = std::clamp<uint32_t>(config.serviceId, 1, 65535);
    }
    config.epgTransportStreamId = std::clamp<uint32_t>(
        root.get("epg_transport_stream_id", 1).asUInt(), 1, 65535);
    config.epgOriginalNetworkId = std::clamp<uint32_t>(
        root.get("epg_original_network_id", 1).asUInt(), 1, 65535);
    config.epgSourceId = root.get("epg_source_id", "").asString();
    config.epgSourceUrl = root.get("epg_source_url",
        "https://github.com/limaalef/BrazilTVEPG/raw/refs/heads/main/claro.xml").asString();
    config.epgChannelId = root.get("epg_channel_id", "").asString();
    if (root.isMember("outputs") && root["outputs"].isArray() && root["outputs"].size() > 0) {
        const auto primary = StreamOutputConfig::fromJson(root["outputs"][0]);
        config.outputType = primary.outputType;
        config.outputMode = primary.outputMode;
        config.outputHost = primary.outputHost;
        config.outputPort = primary.outputPort;
        for (Json::ArrayIndex i = 1; i < root["outputs"].size(); ++i) {
            config.additionalOutputs.push_back(StreamOutputConfig::fromJson(root["outputs"][i]));
        }
    } else if (root.isMember("additional_outputs") && root["additional_outputs"].isArray()) {
        for (const auto& item : root["additional_outputs"]) {
            config.additionalOutputs.push_back(StreamOutputConfig::fromJson(item));
        }
    }
    return config;
}

Json::Value StreamConfig::toJson() const {
    Json::Value root;
    root["id"] = id;
    root["name"] = name;
    root["input_uri"] = inputUri;
    root["backup_input_uri"] = backupInputUri;
    root["backup_input_type"] = backupInputType;
    root["backup_file_loop"] = backupFileLoop;
    root["output_type"] = outputType;
    root["output_mode"] = outputMode;
    root["output_host"] = outputHost;
    root["output_port"] = outputPort;
    root["interface_address"] = interfaceAddress;
    if (inputInterfaceAddressConfigured) {
        root["input_interface_address"] = inputInterfaceAddress;
    }
    root["input_mode"] = inputMode;
    root["youtube_quality"] = youtubeQuality;
    root["test_pattern"] = testPattern;
    root["auto_start"] = autoStart;
    root["remap_enabled"] = remapEnabled;
    root["cbr"] = cbr;
    root["target_bitrate"] = Json::UInt64(targetBitrate);
    root["cache_seconds"] = cacheSeconds;
    root["transcode_enabled"] = transcodeEnabled;
    root["transcode_resolution"] = transcodeResolution;
    root["transcode_video_bitrate"] = Json::UInt64(transcodeVideoBitrate);
    root["transcode_audio_codec"] = transcodeAudioCodec;
    root["transcode_audio_bitrate"] = Json::UInt64(transcodeAudioBitrate);
    root["audio_visual_enabled"] = audioVisualEnabled;
    root["audio_visual_image"] = audioVisualImage;
    root["audio_visual_resolution"] = audioVisualResolution;
    root["audio_visual_video_bitrate"] = Json::UInt64(audioVisualVideoBitrate);
    root["audio_pid"] = audioPid;
    root["video_pid"] = videoPid;
    root["input_service_id"] = inputServiceId;
    root["service_id"] = serviceId;
    root["service_name"] = serviceName;
    root["service_provider"] = serviceProvider;
    root["epg_enabled"] = epgEnabled;
    root["epg_mode"] = epgMode;
    root["epg_transport_stream_id"] = epgTransportStreamId;
    root["epg_original_network_id"] = epgOriginalNetworkId;
    root["epg_source_id"] = epgSourceId;
    root["epg_source_url"] = epgSourceUrl;
    root["epg_channel_id"] = epgChannelId;
    Json::Value extraOutputs(Json::arrayValue);
    for (const auto& output : additionalOutputs) {
        extraOutputs.append(output.toJson());
    }
    root["additional_outputs"] = extraOutputs;
    return root;
}

Json::Value AppConfig::toJson() const {
    Json::Value root;
    root["login"] = login;
    root["password"] = password;
    root["server_name"] = serverName;
    root["http_port"] = httpPort;
    root["language"] = language;
    root["telegram_token"] = telegramToken;
    root["telegram_chat_id"] = telegramChatId;
    Json::Value epgSourcesJson(Json::arrayValue);
    for (const auto& source : epgSources) epgSourcesJson.append(source.toJson());
    root["epg_sources"] = epgSourcesJson;
    Json::Value list(Json::arrayValue);
    for (const auto& stream : streams) {
        list.append(stream.toJson());
    }
    root["streams"] = list;
    return root;
}

AppConfig AppConfig::fromJson(const Json::Value& root) {
    AppConfig config;
    config.login = root.get("login", "admin").asString();
    config.password = root.get("password", "admin").asString();
    config.serverName = root.get("server_name", "TVStreamer5").asString();
    config.httpPort = root.get("http_port", 9000).asInt();
    config.language = root.get("language", "pt").asString();
    if (config.language == "ru") {
        config.language = "pt";
    }
    if (config.language != "pt" && config.language != "en") {
        config.language = "pt";
    }
    config.telegramToken = root.get("telegram_token", "").asString();
    config.telegramChatId = root.get("telegram_chat_id", "").asString();
    if (root.isMember("epg_sources") && root["epg_sources"].isArray()) {
        config.epgSources.clear();
        for (const auto& item : root["epg_sources"]) {
            auto source = EpgSourceConfig::fromJson(item);
            if (!source.id.empty() && !source.name.empty() && !source.url.empty()) {
                config.epgSources.push_back(std::move(source));
            }
        }
    }
    if (config.epgSources.empty()) config.epgSources.push_back(defaultBrazilTvEpgSource());
    bool hasDefault = false;
    for (auto& source : config.epgSources) {
        if (source.id == "braziltvepg") source.isDefault = true;
        if (source.isDefault && !hasDefault) hasDefault = true;
        else if (source.isDefault) source.isDefault = false;
    }
    if (!hasDefault) config.epgSources.front().isDefault = true;
    if (root.isMember("streams") && root["streams"].isArray()) {
        for (const auto& item : root["streams"]) {
            config.streams.push_back(StreamConfig::fromJson(item));
        }
    }
    for (auto& stream : config.streams) resolveEpgSource(stream, config.epgSources);
    return config;
}

bool resolveEpgSource(StreamConfig& stream, const std::vector<EpgSourceConfig>& sources) {
    if (!stream.epgSourceId.empty()) {
        const auto source = std::find_if(sources.begin(), sources.end(),
            [&](const EpgSourceConfig& item) { return item.id == stream.epgSourceId; });
        if (source == sources.end()) return false;
        stream.epgSourceUrl = source->url;
        return !stream.epgSourceUrl.empty();
    }

    if (stream.epgSourceUrl.empty()) return false;
    const auto source = std::find_if(sources.begin(), sources.end(),
        [&](const EpgSourceConfig& item) { return item.url == stream.epgSourceUrl; });
    if (source == sources.end()) return false;
    stream.epgSourceId = source->id;
    return !stream.epgSourceId.empty();
}

ConfigManager::ConfigManager() {
    configPath = std::filesystem::current_path() / "tvstreamer5-config.json";
}

Json::Value SubscriberConfig::toJson() const {
    Json::Value root;
    root["name"] = name;
    root["primary_ip"] = primaryIp;
    root["backup_ip"] = backupIp;
    root["added_at"] = addedAt;
    root["enabled"] = enabled;
    Json::Value streams(Json::arrayValue);
    for (const auto& id : streamIds) streams.append(id);
    root["stream_ids"] = streams;
    return root;
}

SubscriberConfig SubscriberConfig::fromJson(const Json::Value& root) {
    SubscriberConfig subscriber;
    subscriber.name = root.get("name", "").asString();
    subscriber.primaryIp = root.get("primary_ip", "").asString();
    subscriber.backupIp = root.get("backup_ip", "").asString();
    subscriber.addedAt = root.get("added_at", "").asString();
    subscriber.enabled = root.get("enabled", true).asBool();
    if (root["stream_ids"].isArray()) {
        for (const auto& id : root["stream_ids"]) {
            subscriber.streamIds.push_back(id.asString());
        }
    }
    return subscriber;
}

Json::Value SubscriberListConfig::toJson() const {
    Json::Value root;
    root["filtering_enabled"] = filteringEnabled;
    Json::Value list(Json::arrayValue);
    for (const auto& subscriber : subscribers) {
        list.append(subscriber.toJson());
    }
    root["subscribers"] = list;
    return root;
}

SubscriberListConfig SubscriberListConfig::fromJson(const Json::Value& root) {
    SubscriberListConfig config;
    config.filteringEnabled = root.get("filtering_enabled", false).asBool();
    if (root["subscribers"].isArray()) {
        for (const auto& item : root["subscribers"]) {
            config.subscribers.push_back(SubscriberConfig::fromJson(item));
        }
    }
    return config;
}

bool ConfigManager::load() {
    if (!std::filesystem::exists(configPath)) {
        std::cerr << "Config file not found, creating default configuration: " << configPath << std::endl;
        AppConfig defaultConfig;
        {
            std::lock_guard<std::mutex> lock(fileMutex);
            config = defaultConfig;
        }
        if (!save()) return false;
        return loadSubscribers();
    }

    {
        std::lock_guard<std::mutex> lock(fileMutex);
        std::ifstream input(configPath);
        if (!input.is_open()) {
            return false;
        }
        Json::Value root;
        Json::CharReaderBuilder readerBuilder;
        std::string errs;
        bool ok = Json::parseFromStream(readerBuilder, input, &root, &errs);
        if (!ok) {
            std::cerr << "Failed to parse config: " << errs << std::endl;
            return false;
        }
        config = AppConfig::fromJson(root);
    }
    return loadSubscribers();
}

bool ConfigManager::loadSubscribers() {
    const auto path = std::filesystem::current_path() / "tvstreamer5-subscribers.json";
    if (!std::filesystem::exists(path)) {
        subscribers = SubscriberListConfig{};
        return saveSubscribers();
    }
    std::lock_guard<std::mutex> lock(fileMutex);
    std::ifstream input(path);
    if (!input.is_open()) return false;
    Json::Value root;
    Json::CharReaderBuilder readerBuilder;
    std::string errs;
    if (!Json::parseFromStream(readerBuilder, input, &root, &errs)) {
        std::cerr << "Failed to parse subscribers config: " << errs << std::endl;
        return false;
    }
    subscribers = SubscriberListConfig::fromJson(root);
    return true;
}

bool ConfigManager::save() {
    std::lock_guard<std::mutex> lock(fileMutex);
    std::ofstream output(configPath);
    if (!output.is_open()) {
        std::cerr << "Unable to open config file for writing: " << configPath << std::endl;
        return false;
    }
    Json::StreamWriterBuilder writer;
    writer["indentation"] = "  ";
    std::string str = Json::writeString(writer, config.toJson());
    output << str;
    return true;
}

bool ConfigManager::saveSubscribers() {
    std::lock_guard<std::mutex> lock(fileMutex);
    const auto path = std::filesystem::current_path() / "tvstreamer5-subscribers.json";
    std::ofstream output(path);
    if (!output.is_open()) return false;
    Json::StreamWriterBuilder writer;
    writer["indentation"] = "  ";
    output << Json::writeString(writer, subscribers.toJson());
    return true;
}
