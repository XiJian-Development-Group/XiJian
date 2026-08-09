import Foundation

// MARK: - 通话领域模型

/// 通话方向
enum VoiceCallDirection: String, Codable, Equatable {
    case userInitiated = "user_initiated"
    case characterInitiated = "character_initiated"
}

/// 通话状态（服务端状态机 idle → ringing → active → ended，见 core/xijian_api/stubs/voice_calls.py）
enum VoiceCallStatus: String, Codable, Equatable {
    case idle
    case ringing
    case active
    case ended

    /// 中文展示
    var displayName: String {
        switch self {
        case .idle: return loc("待接通")
        case .ringing: return loc("响铃中")
        case .active: return loc("通话中")
        case .ended: return loc("已结束")
        }
    }
}

/// 通话记录（`POST /v1/xijian/voice-calls` 创建及生命周期端点的完整响应）
struct VoiceCallRecord: Codable, Equatable, Identifiable {
    let id: String
    let character_id: String
    let user_id: String?
    let direction: String?
    let status: String?
    let started_at: Double?
    let ended_at: Double?
    let duration_sec: Int?
    let recording_path: String?
    let ended_reason: String?
    let barge_in_active: Bool?
    let tts_busy: Bool?
    let current_turn: Int?
    let dialogue_context: [VoiceCallDialogueEntry]?
    let created_at: Double?
    let updated_at: Double?

    var statusEnum: VoiceCallStatus { VoiceCallStatus(rawValue: status ?? "") ?? .idle }
    var directionEnum: VoiceCallDirection? { direction.flatMap(VoiceCallDirection.init(rawValue:)) }
    var isBargeInActive: Bool { barge_in_active ?? false }
}

/// 通话记录的对话上下文条目（`dialogue_context`）
struct VoiceCallDialogueEntry: Codable, Equatable, Hashable {
    let role: String?
    let text: String?
}

/// WS `call.state_changed` 事件的 data（服务端 public view，键为 `call_id` 而非 `id`）
struct VoiceCallStateView: Codable, Equatable {
    let call_id: String?
    let character_id: String?
    let user_id: String?
    let direction: String?
    let status: String?
    let started_at: Double?
    let ended_at: Double?
    let duration_sec: Int?
    let ended_reason: String?

    var statusEnum: VoiceCallStatus { VoiceCallStatus(rawValue: status ?? "") ?? .idle }
}

/// 通话事件（`GET /v1/xijian/voice-calls/<call_id>/events` 的条目）
struct VoiceCallEvent: Codable, Equatable, Identifiable {
    let id: String
    let call_id: String
    let kind: String
    let payload: JSONValue?
    let created_at: Double?

    var payloadObject: [String: JSONValue]? { payload?.objectValue }
}

/// WS `call.event` 事件的 data
struct VoiceCallEventPush: Codable, Equatable {
    let call_id: String?
    let event_id: String?
    let kind: String?
    let payload: JSONValue?
}

/// `POST .../speech` 的响应（STT→AI→TTS 管线；默认异步，`reply` 为空时经 WS `call.event` 送达）
struct SpeechResult: Codable, Equatable {
    let ok: Bool
    let turn: Int?
    let user_text: String?
    let reply: String?
    let interrupted_previous: Bool?
    let user_event_id: String?
    let reply_event_id: String?
    let synchronous: Bool?
    /// `ok == false` 时的错误说明（STT 后端不可用等）
    let error: String?
}

/// `POST .../song` 的响应（DiffSinger 接口桩，默认 `unavailable`）
struct SongResult: Codable, Equatable {
    let ok: Bool
    let status: String?
    let reason: String?
    let message: String?
}

// MARK: - 服务协议（ViewModel 测试注入）

/// A6 通话服务抽象 — 生产实现 `VoiceCallService`；测试注入 Mock。
protocol VoiceCallServicing {
    func createCall(characterId: String, direction: VoiceCallDirection, userId: String) async throws -> VoiceCallRecord
    func getCall(callId: String) async throws -> VoiceCallRecord
    func ring(callId: String) async throws -> VoiceCallRecord
    func accept(callId: String) async throws -> VoiceCallRecord
    func reject(callId: String) async throws -> VoiceCallRecord
    func end(callId: String) async throws -> VoiceCallRecord
    func sendSpeech(callId: String, text: String) async throws -> SpeechResult
    func setBargeIn(callId: String, active: Bool) async throws -> VoiceCallRecord
    func sing(callId: String, lyrics: String) async throws -> SongResult
    func listEvents(callId: String, limit: Int) async throws -> [VoiceCallEvent]
}

// MARK: - 服务实现

/// A6 实时通话 API 客户端 — 与 APIClient 同风格的 URLSession 封装。
///
/// 独立文件实现（不改动 APIClient.swift）：baseURL / token 由
/// `CoreManager.shared.baseURL` / `CoreManager.shared.token` 提供，
/// 请求构造、Bearer 认证头、错误信封解析（`APIError`）与 APIClient 完全一致。
///
/// 端点契约见 core/xijian_api/routes/xijian_voice_calls.py：
/// 创建 / ring / accept / reject / end / speech / barge-in / song / events。
struct VoiceCallService: VoiceCallServicing {
    let baseURL: URL
    let token: String
    let session: URLSession
    let timeout: TimeInterval

    init(baseURL: URL, token: String, session: URLSession = .shared, timeout: TimeInterval = 30) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
        self.timeout = timeout
    }

    // MARK: 基础请求（与 APIClient.makeRequest / send 同风格）

    private func makeRequest(
        _ method: String,
        _ path: String,
        query: [URLQueryItem]? = nil,
        body: [String: JSONValue]? = nil
    ) throws -> URLRequest {
        guard var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false) else {
            throw APIError.invalidResponse
        }
        if let query { components.queryItems = query }
        guard let url = components.url else { throw APIError.invalidResponse }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body {
            do {
                request.httpBody = try JSONEncoder().encode(AnyEncodable(body))
            } catch {
                throw APIError.network(loc("请求体编码失败：%@", error.localizedDescription))
            }
        }
        return request
    }

    /// 发送请求并解码 JSON 响应（错误信封解析与 APIClient 一致）
    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError {
            if error.code == .cancelled {
                throw APIError.network(loc("请求已取消"))
            }
            throw APIError.network(error.localizedDescription)
        } catch {
            throw APIError.network(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.httpStatus(http.statusCode, Self.extractErrorMessage(from: data))
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error.localizedDescription)
        }
    }

    /// 从错误体（OAI / JSON-RPC 风格）提取 message
    static func extractErrorMessage(from data: Data) -> String {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        if let error = obj["error"] as? [String: Any], let message = error["message"] as? String {
            return message
        }
        if let message = obj["message"] as? String { return message }
        if let error = obj["error"] as? String { return error }
        return ""
    }

    // MARK: 通话生命周期

    /// 创建通话会话（idle）→ 返回通话记录
    func createCall(
        characterId: String,
        direction: VoiceCallDirection = .userInitiated,
        userId: String = "local_user"
    ) async throws -> VoiceCallRecord {
        let body: [String: JSONValue] = [
            "character_id": .string(characterId),
            "direction": .string(direction.rawValue),
            "user_id": .string(userId),
        ]
        return try await send(makeRequest("POST", "/v1/xijian/voice-calls", body: body))
    }

    /// 获取通话详情
    func getCall(callId: String) async throws -> VoiceCallRecord {
        try await send(makeRequest("GET", "/v1/xijian/voice-calls/\(callId)"))
    }

    /// 拨打（idle → ringing）
    func ring(callId: String) async throws -> VoiceCallRecord {
        try await send(makeRequest("POST", "/v1/xijian/voice-calls/\(callId)/ring"))
    }

    /// 接听（→ active）
    func accept(callId: String) async throws -> VoiceCallRecord {
        try await send(makeRequest("POST", "/v1/xijian/voice-calls/\(callId)/accept"))
    }

    /// 拒绝（→ ended）
    func reject(callId: String) async throws -> VoiceCallRecord {
        try await send(makeRequest("POST", "/v1/xijian/voice-calls/\(callId)/reject"))
    }

    /// 挂断（→ ended）
    func end(callId: String) async throws -> VoiceCallRecord {
        try await send(makeRequest("POST", "/v1/xijian/voice-calls/\(callId)/end"))
    }

    // MARK: 通话循环

    /// 送入用户语音（本批走 text 路径；audio_base64 采集后续接入）
    func sendSpeech(callId: String, text: String) async throws -> SpeechResult {
        let body: [String: JSONValue] = ["text": .string(text)]
        return try await send(makeRequest("POST", "/v1/xijian/voice-calls/\(callId)/speech", body: body))
    }

    /// 设置 / 清除 barge-in 打断标志
    func setBargeIn(callId: String, active: Bool) async throws -> VoiceCallRecord {
        let body: [String: JSONValue] = ["active": .bool(active)]
        return try await send(makeRequest("POST", "/v1/xijian/voice-calls/\(callId)/barge-in", body: body))
    }

    /// 请求角色演唱（DiffSinger 接口桩；未接引擎时服务端返回 unavailable）
    func sing(callId: String, lyrics: String) async throws -> SongResult {
        let body: [String: JSONValue] = ["lyrics": .string(lyrics)]
        return try await send(makeRequest("POST", "/v1/xijian/voice-calls/\(callId)/song", body: body))
    }

    /// 通话事件列表（按创建时间升序，最多 limit 条）
    func listEvents(callId: String, limit: Int = 100) async throws -> [VoiceCallEvent] {
        struct EventsEnvelope: Decodable {
            let call_id: String?
            let events: [VoiceCallEvent]
        }
        let envelope: EventsEnvelope = try await send(makeRequest(
            "GET",
            "/v1/xijian/voice-calls/\(callId)/events",
            query: [URLQueryItem(name: "limit", value: "\(limit)")]
        ))
        return envelope.events
    }
}
