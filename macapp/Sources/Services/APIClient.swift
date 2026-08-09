import Foundation

/// API 错误（中文描述）
enum APIError: LocalizedError, Equatable {
    /// Core 未运行
    case coreNotRunning
    /// 响应无法解析
    case invalidResponse
    /// HTTP 状态码错误（附服务端错误信息）
    case httpStatus(Int, String)
    /// JSON 解码失败
    case decoding(String)
    /// 网络错误
    case network(String)
    /// 流式连接异常中断
    case streamEnded

    var errorDescription: String? { message }

    var message: String {
        switch self {
        case .coreNotRunning:
            return loc("Core 未运行，无法访问 API。请在设置中启动 Core。")
        case .invalidResponse:
            return loc("服务器返回了无法识别的响应。")
        case .httpStatus(let code, let detail):
            let statusText: String
            switch code {
            case 400: statusText = loc("请求参数错误")
            case 401: statusText = loc("鉴权失败（token 无效）")
            case 403: statusText = loc("权限不足，操作被拒绝")
            case 404: statusText = loc("资源不存在")
            case 409: statusText = loc("资源冲突")
            case 422: statusText = loc("语义错误")
            case 429: statusText = loc("请求过于频繁")
            case 500: statusText = loc("服务器内部错误")
            case 503: statusText = loc("服务不可用（模型未加载或后端未就绪）")
            default: statusText = loc("请求失败")
            }
            let detailText = detail.isEmpty ? "" : loc("：%@", detail)
            return loc("%@（HTTP %lld）%@", statusText, code, detailText)
        case .decoding(let detail):
            return loc("响应解析失败：%@", detail)
        case .network(let detail):
            return loc("网络错误：%@", detail)
        case .streamEnded:
            return loc("流式响应意外中断。")
        }
    }
}

/// XiJian API 客户端 — 基于 URLSession，注入 baseURL 与 Bearer token。
/// 全部请求带 `Authorization: Bearer <token>`，SSE 流式解析 "data: " 行。
struct APIClient {
    let baseURL: URL
    let token: String
    let session: URLSession
    let timeout: TimeInterval

    init(baseURL: URL, token: String, session: URLSession = .shared, timeout: TimeInterval = 60) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
        self.timeout = timeout
    }

    // MARK: - 基础请求

    private func makeRequest(
        _ method: String,
        _ path: String,
        query: [URLQueryItem]? = nil,
        body: (any Encodable)? = nil,
        extraHeaders: [String: String] = [:]
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
        for (key, value) in extraHeaders {
            request.setValue(value, forHTTPHeaderField: key)
        }
        if let body {
            let encoder = JSONEncoder()
            do {
                request.httpBody = try encoder.encode(AnyEncodable(body))
            } catch {
                throw APIError.network(loc("请求体编码失败：%@", error.localizedDescription))
            }
        }
        return request
    }

    /// 发送请求并解码 JSON 响应
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

    /// 从 OAI / JSON-RPC 错误体中提取 message
    static func extractErrorMessage(from data: Data) -> String {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        if let error = obj["error"] as? [String: Any], let message = error["message"] as? String {
            return message
        }
        if let message = obj["message"] as? String { return message }
        if let error = obj["error"] as? String { return error }
        return ""
    }

    /// 发送请求并忽略响应体
    private func sendVoid(_ request: URLRequest) async throws {
        _ = try await send(EmptyResponse.self, request: request)
    }

    private func send<T: Decodable>(_ type: T.Type, request: URLRequest) async throws -> T {
        try await send(request)
    }

    private struct EmptyResponse: Decodable {}

    // MARK: - 通用方法

    func get<T: Decodable>(_ path: String, query: [URLQueryItem]? = nil) async throws -> T {
        try await send(makeRequest("GET", path, query: query))
    }

    func post<T: Decodable>(_ path: String, body: (any Encodable)? = nil, query: [URLQueryItem]? = nil) async throws -> T {
        try await send(makeRequest("POST", path, query: query, body: body))
    }

    func patch<T: Decodable>(_ path: String, body: (any Encodable)? = nil) async throws -> T {
        try await send(makeRequest("PATCH", path, body: body))
    }

    func delete<T: Decodable>(_ path: String) async throws -> T {
        try await send(makeRequest("DELETE", path))
    }

    func postVoid(_ path: String, body: (any Encodable)? = nil) async throws {
        try await sendVoid(makeRequest("POST", path, body: body))
    }

    func deleteVoid(_ path: String) async throws {
        try await sendVoid(makeRequest("DELETE", path))
    }

    // MARK: - 健康检查（无鉴权）

    func health() async throws -> Bool {
        var request = URLRequest(url: baseURL.appendingPathComponent("healthz"))
        request.timeoutInterval = 5
        do {
            let (_, response) = try await session.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    // MARK: - 模型

    func listModels() async throws -> [ModelInfo] {
        let envelope: ListEnvelope<ModelInfo> = try await get("/v1/models")
        return envelope.data
    }

    func loadModel(_ modelID: String, gpuLayers: Int = -1, contextLength: Int? = nil) async throws -> [String: JSONValue] {
        var body: [String: JSONValue] = ["gpu_layers": .number(Double(gpuLayers))]
        if let contextLength { body["context_length"] = .number(Double(contextLength)) }
        let obj: [String: JSONValue] = try await post("/v1/models/\(modelID)/load", body: body)
        return obj
    }

    func unloadModel(_ modelID: String) async throws -> [String: JSONValue] {
        let obj: [String: JSONValue] = try await post("/v1/models/\(modelID)/unload")
        return obj
    }

    // MARK: - 聊天（SSE 流式）

    /// 聊天请求参数
    struct ChatRequest {
        var model: String
        var messages: [ChatMessage]
        var temperature: Double = 0.7
        var maxTokens: Int = 2048
        var characterID: String?
        var worldID: String?
        var recallEnabled: Bool = true
        var requestID: String = UUID().uuidString
    }

    /// 流式聊天。返回的流逐块产出 SSE 事件；取消流（task.cancel）会自动
    /// 关闭连接，随后应调用 chatAbort 通知服务端停止生成。
    func streamChat(
        request: ChatRequest,
        session: URLSession? = nil
    ) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let body: [String: JSONValue] = [
                        "model": .string(request.model),
                        "messages": .array(request.messages.map { msg in
                            .object(["role": .string(msg.role), "content": .string(msg.content)])
                        }),
                        "temperature": .number(request.temperature),
                        "max_tokens": .number(Double(request.maxTokens)),
                        "stream": .bool(true),
                        "stream_options": .object(["include_usage": .bool(true)]),
                        "xijian": .object([
                            "character_id": request.characterID.map { .string($0) } ?? .null,
                            "world_id": request.worldID.map { .string($0) } ?? .null,
                            "recall": .object(["enabled": .bool(request.recallEnabled)]),
                        ]),
                    ]
                    let urlRequest = try makeRequest(
                        "POST", "/v1/chat/completions",
                        body: body,
                        extraHeaders: [
                            "Accept": "text/event-stream",
                            "X-XiJian-Request-Id": request.requestID,
                        ]
                    )
                    let streamSession = session ?? self.session
                    let (bytes, response) = try await streamSession.bytes(for: urlRequest)
                    guard let http = response as? HTTPURLResponse else {
                        throw APIError.invalidResponse
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        var errorData = Data()
                        for try await byte in bytes {
                            errorData.append(byte)
                            if errorData.count > 4096 { break }
                        }
                        throw APIError.httpStatus(http.statusCode, Self.extractErrorMessage(from: errorData))
                    }

                    var currentLine: [UInt8] = []
                    for try await byte in bytes {
                        if Task.isCancelled { break }
                        if byte == 0x0A { // \n
                            let line = String(decoding: currentLine, as: UTF8.self)
                                .trimmingCharacters(in: .whitespacesAndNewlines)
                            currentLine = []
                            if line.isEmpty { continue }
                            if let event = Self.parseSSELine(line) {
                                continuation.yield(event)
                                if case .done = event { break }
                            }
                        } else {
                            currentLine.append(byte)
                        }
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    /// 解析单行 SSE（"data: " 前缀）
    static func parseSSELine(_ line: String) -> SSEEvent? {
        if line == "event: abort" || line == "event:error" {
            return .aborted
        }
        guard line.hasPrefix("data:") else { return nil }
        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
        if payload == "[DONE]" { return .done }
        guard let data = payload.data(using: .utf8) else { return nil }
        if let chunk = try? JSONDecoder().decode(ChatStreamChunk.self, from: data) {
            return .chunk(chunk)
        }
        return nil
    }

    /// 中止指定请求的生成（POST /v1/chat/abort）
    func chatAbort(requestID: String) async throws {
        struct AbortBody: Encodable { let request_id: String }
        try await postVoid("/v1/chat/abort", body: AbortBody(request_id: requestID))
    }

    // MARK: - 资源包管理

    func listPacks() async throws -> [PackInfo] {
        try await get("/v1/xijian/packs")
    }

    func getPack(_ packageID: String) async throws -> PackInfo {
        try await get("/v1/xijian/packs/\(packageID)")
    }

    /// 安装资源包（同步，服务端本地路径）
    func installPack(path: String) async throws -> PackInfo {
        try await post("/v1/xijian/packs/install", body: ["path": path])
    }

    /// 卸载资源包
    func uninstallPack(_ packageID: String) async throws -> PackInfo {
        try await delete("/v1/xijian/packs/\(packageID)")
    }

    /// 重新扫描资源包目录
    func rescanPacks() async throws -> [String: JSONValue] {
        try await post("/v1/xijian/packs/rescan")
    }

    // MARK: - 资源导入（异步）

    /// 启动异步导入任务
    func importResource(name: String, kind: String, path: String) async throws -> ImportJobInfo {
        let body: [String: JSONValue] = [
            "name": .string(name),
            "kind": .string(kind),
            "path": .string(path)
        ]
        return try await post("/v1/xijian/resources/import", body: body)
    }

    /// 查询导入任务状态
    func getImportJob(_ jobID: String) async throws -> ImportJobInfo {
        try await get("/v1/xijian/resources/imports/\(jobID)")
    }

    /// 轮询导入任务直到完成或失败
    func pollImportJob(_ jobID: String, interval: TimeInterval = 0.5, timeout: TimeInterval = 120) async throws -> ImportJobInfo {
        let startTime = Date()
        while Date().timeIntervalSince(startTime) < timeout {
            let job = try await getImportJob(jobID)
            if job.isCompleted || job.isFailed {
                return job
            }
            try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
        }
        // 超时返回最新状态
        return try await getImportJob(jobID)
    }

    // MARK: - 角色

    func listCharacters() async throws -> [CharacterInfo] {
        let envelope: ListEnvelope<CharacterInfo> = try await get("/v1/xijian/characters")
        return envelope.data
    }

    func getCharacter(_ id: String) async throws -> CharacterInfo {
        try await get("/v1/xijian/characters/\(id)")
    }

    func createCharacter(name: String, displayName: String?, personaDoc: String, voiceProfile: String?, defaultEmotion: String?, tags: [String]) async throws -> CharacterInfo {
        let body = CharacterPayload(
            name: name, display_name: displayName, persona_doc: personaDoc,
            voice_profile: voiceProfile, default_emotion: defaultEmotion, tags: tags
        )
        return try await post("/v1/xijian/characters", body: body)
    }

    func updateCharacter(_ id: String, patchBody: [String: JSONValue]) async throws -> CharacterInfo {
        try await patch("/v1/xijian/characters/\(id)", body: patchBody)
    }

    func deleteCharacter(_ id: String) async throws {
        try await deleteVoid("/v1/xijian/characters/\(id)")
    }

    func loadCharacter(_ id: String) async throws -> CharacterInfo {
        try await post("/v1/xijian/characters/\(id)/load")
    }

    func unloadCharacter(_ id: String) async throws -> CharacterInfo {
        try await post("/v1/xijian/characters/\(id)/unload")
    }

    func getCharacterState(_ id: String) async throws -> CharacterStateInfo {
        try await get("/v1/xijian/characters/\(id)/state")
    }

    /// 角色状态变更日志（最新在前；Core 返回 {"entries": [...]} 包装对象）
    func getCharacterStateLog(_ id: String, limit: Int = 10) async throws -> [CharacterStateLogEntry] {
        struct LogEnvelope: Decodable {
            let entries: [CharacterStateLogEntry]
        }
        let envelope: LogEnvelope = try await get(
            "/v1/xijian/characters/\(id)/state/log",
            query: [URLQueryItem(name: "limit", value: "\(limit)")]
        )
        return envelope.entries
    }

    func updateCharacterState(_ id: String, patch: [String: JSONValue]) async throws -> CharacterStateInfo {
        try await post("/v1/xijian/characters/\(id)/state", body: patch)
    }

    func listInteractions() async throws -> [InteractionInfo] {
        let envelope: ListEnvelope<InteractionInfo> = try await get("/v1/xijian/interactions")
        return envelope.data
    }

    /// 触发互动
    func triggerInteraction(interactionID: String, characterID: String?, context: [String: JSONValue]?, nsfwAllowed: Bool) async throws -> [String: JSONValue] {
        var body: [String: JSONValue] = ["nsfw_allowed": .bool(nsfwAllowed)]
        if let characterID { body["character_id"] = .string(characterID) }
        if let context { body["context"] = .object(context) }
        return try await post("/v1/xijian/interactions/\(interactionID)/trigger", body: body)
    }

    // MARK: - 世界

    func listWorlds() async throws -> [WorldInfo] {
        let envelope: ListEnvelope<WorldInfo> = try await get("/v1/xijian/worlds")
        return envelope.data
    }

    func getWorld(_ id: String) async throws -> WorldInfo {
        try await get("/v1/xijian/worlds/\(id)")
    }

    func createWorld(name: String) async throws -> WorldInfo {
        try await post("/v1/xijian/worlds", body: ["name": name])
    }

    func deleteWorld(_ id: String) async throws -> [String: JSONValue] {
        try await delete("/v1/xijian/worlds/\(id)")
    }

    func switchWorld(_ id: String) async throws -> WorldInfo {
        try await post("/v1/xijian/worlds/\(id)/switch")
    }

    func getWorldState(_ id: String) async throws -> WorldStateInfo {
        try await get("/v1/xijian/worlds/\(id)/state")
    }

    func updateWorldState(_ id: String, patchBody: [String: JSONValue]) async throws -> [String: JSONValue] {
        try await patch("/v1/xijian/worlds/\(id)/state", body: patchBody)
    }

    /// 世界地点转换
    func transitionWorld(_ id: String, fromLocation: String?, toLocation: String?, transport: String?, etaSeconds: Int?) async throws -> WorldInfo {
        var body: [String: JSONValue] = [:]
        if let fromLocation { body["from_location"] = .string(fromLocation) }
        if let toLocation { body["to_location"] = .string(toLocation) }
        if let transport { body["transport"] = .string(transport) }
        if let etaSeconds { body["eta_seconds"] = .number(Double(etaSeconds)) }
        return try await post("/v1/xijian/worlds/\(id)/transition", body: body)
    }

    /// 注入世界事件
    func injectWorldEvent(_ id: String, name: String, description: String, sceneRefID: String?, priority: Int, isEnabled: Bool) async throws -> [String: JSONValue] {
        var body: [String: JSONValue] = [
            "name": .string(name),
            "description": .string(description),
            "priority": .number(Double(priority)),
            "is_enabled": .bool(isEnabled),
        ]
        if let sceneRefID { body["scene_ref_id"] = .string(sceneRefID) }
        return try await post("/v1/xijian/worlds/\(id)/event", body: body)
    }

    // MARK: - 记忆

    func listMemoryEntries(characterID: String?, limit: Int = 100) async throws -> [MemoryEntry] {
        var query: [URLQueryItem] = [URLQueryItem(name: "limit", value: "\(limit)")]
        if let characterID { query.append(URLQueryItem(name: "character_id", value: characterID)) }
        let envelope: ListEnvelope<MemoryEntry> = try await get("/v1/xijian/memory/entries", query: query)
        return envelope.data
    }

    func getMemoryEntry(_ id: String) async throws -> MemoryEntry {
        try await get("/v1/xijian/memory/entries/\(id)")
    }

    func createMemoryEntry(characterID: String?, content: String, importance: Double?, decay: String?, category: String?, tags: [String]) async throws -> MemoryEntry {
        var body: [String: JSONValue] = ["content": .string(content), "tags": .array(tags.map { .string($0) })]
        if let characterID { body["character_id"] = .string(characterID) }
        if let importance { body["importance"] = .number(importance) }
        if let decay { body["decay"] = .string(decay) }
        if let category { body["category"] = .string(category) }
        return try await post("/v1/xijian/memory/entries", body: body)
    }

    func updateMemoryEntry(_ id: String, patchBody: [String: JSONValue]) async throws -> MemoryEntry {
        try await patch("/v1/xijian/memory/entries/\(id)", body: patchBody)
    }

    func deleteMemoryEntry(_ id: String) async throws {
        try await deleteVoid("/v1/xijian/memory/entries/\(id)")
    }

    func searchMemory(query: String, characterID: String?, topK: Int, minScore: Double?) async throws -> [MemoryEntry] {
        var body: [String: JSONValue] = ["query": .string(query), "top_k": .number(Double(topK))]
        if let characterID { body["character_id"] = .string(characterID) }
        if let minScore { body["min_score"] = .number(minScore) }
        let envelope: ListEnvelope<MemoryEntry> = try await post("/v1/xijian/memory/search", body: body)
        return envelope.data
    }

    /// 触发记忆整理（异步）
    func consolidateMemory(characterID: String?) async throws -> [String: JSONValue] {
        var body: [String: JSONValue] = [:]
        if let characterID { body["character_id"] = .string(characterID) }
        return try await post("/v1/xijian/memory/consolidate", body: body)
    }

    /// 触发遗忘
    func forgetMemory(entryIDs: [String]?, decay: String?) async throws -> [String: JSONValue] {
        var body: [String: JSONValue] = [:]
        if let entryIDs { body["entry_ids"] = .array(entryIDs.map { .string($0) }) }
        if let decay { body["decay"] = .string(decay) }
        return try await post("/v1/xijian/memory/forget", body: body)
    }

    // MARK: - 会话

    func createSession(title: String) async throws -> SessionInfo {
        try await post("/v1/xijian/sessions", body: ["title": title])
    }

    func listSessionMessages(_ sessionID: String) async throws -> [ChatMessage] {
        let envelope: ListEnvelope<ChatMessage> = try await get("/v1/xijian/sessions/\(sessionID)/messages")
        return envelope.data
    }

    func appendSessionMessage(_ sessionID: String, role: String, content: String) async throws -> ChatMessage {
        try await post("/v1/xijian/sessions/\(sessionID)/messages", body: ["role": role, "content": content])
    }

    func deleteSession(_ sessionID: String) async throws {
        try await deleteVoid("/v1/xijian/sessions/\(sessionID)")
    }

    // MARK: - 设置

    func getSettings() async throws -> ServerSettings {
        try await get("/v1/xijian/settings")
    }

    func patchSettings(_ patchBody: [String: JSONValue]) async throws -> ServerSettings {
        try await patch("/v1/xijian/settings", body: patchBody)
    }

    // MARK: - 安全模块

    func gateStatus() async throws -> GateStatus {
        try await get("/v1/xijian/safety/gate/status")
    }

    func enableGate() async throws -> GateStatus {
        try await post("/v1/xijian/safety/gate/enable")
    }

    /// 关闭保护第一步：发起挑战
    func startDisableGate(confirmation: String) async throws -> DisableChallenge {
        try await post("/v1/xijian/safety/gate/disable", body: ["confirmation": confirmation])
    }

    /// 关闭保护第二步：确认挑战短语
    func confirmDisableGate(challengeID: String, phrase: String) async throws -> DisableChallenge {
        try await post("/v1/xijian/safety/gate/disable", body: ["challenge_id": challengeID, "phrase": phrase])
    }

    func scanInput(text: String, characterID: String?, worldID: String?) async throws -> SafetyScanResult {
        try await scan("/v1/xijian/safety/scan/input", text: text, characterID: characterID, worldID: worldID)
    }

    func scanOutput(text: String, characterID: String?, worldID: String?) async throws -> SafetyScanResult {
        try await scan("/v1/xijian/safety/scan/output", text: text, characterID: characterID, worldID: worldID)
    }

    private func scan(_ path: String, text: String, characterID: String?, worldID: String?) async throws -> SafetyScanResult {
        var body: [String: JSONValue] = ["text": .string(text)]
        if let characterID { body["character_id"] = .string(characterID) }
        if let worldID { body["world_id"] = .string(worldID) }
        return try await post(path, body: body)
    }

    // MARK: - 备份与受保护模块

    func listProtectedModules(characterID: String?) async throws -> [ProtectedModule] {
        var query: [URLQueryItem]? = nil
        if let characterID { query = [URLQueryItem(name: "character_id", value: characterID)] }
        let envelope: ListEnvelope<ProtectedModule> = try await get("/v1/protected-modules", query: query)
        return envelope.data
    }

    func getCharacterProtection(_ characterID: String) async throws -> [String: JSONValue] {
        try await get("/v1/characters/\(characterID)/protected-modules")
    }

    func setAutoBackup(characterID: String, moduleName: String, enabled: Bool) async throws -> [String: JSONValue] {
        let body: [String: JSONValue] = [
            "module_name": .string(moduleName),
            "enabled": .bool(enabled),
        ]
        return try await patch("/v1/characters/\(characterID)/protected-modules", body: body)
    }

    func createBackup(characterID: String, scope: String, createdBy: String) async throws -> BackupRecord {
        let body: [String: JSONValue] = [
            "character_id": .string(characterID),
            "scope": .string(scope),
            "created_by": .string(createdBy),
        ]
        return try await post("/v1/backups", body: body)
    }

    func listBackups(characterID: String?, limit: Int = 50) async throws -> [BackupRecord] {
        var query: [URLQueryItem] = [URLQueryItem(name: "limit", value: "\(limit)")]
        if let characterID { query.append(URLQueryItem(name: "character_id", value: characterID)) }
        let envelope: ListEnvelope<BackupRecord> = try await get("/v1/backups", query: query)
        return envelope.data
    }

    func deleteBackup(_ backupID: String) async throws {
        _ = try await delete("/v1/backups/\(backupID)") as [String: JSONValue]
    }

    func restoreBackup(_ backupID: String, scope: String?, targetCharacterID: String?) async throws -> [String: JSONValue] {
        var body: [String: JSONValue] = [:]
        if let scope { body["scope"] = .string(scope) }
        if let targetCharacterID { body["target_character_id"] = .string(targetCharacterID) }
        return try await post("/v1/backups/\(backupID)/restore", body: body)
    }

    // MARK: - 剧情

    func listPlotDesigns() async throws -> [PlotDesign] {
        let envelope: ListEnvelope<PlotDesign> = try await get("/v1/xijian/plots/designs")
        return envelope.data
    }

    func createPlotRuntime(plotID: String, worldID: String, initialVariables: [String: JSONValue]?) async throws -> PlotRuntime {
        var body: [String: JSONValue] = ["plot_id": .string(plotID), "world_id": .string(worldID)]
        if let initialVariables { body["initial_variables"] = .object(initialVariables) }
        return try await post("/v1/xijian/plots/runtime", body: body)
    }

    func listPlotRuntimes(worldID: String?) async throws -> [PlotRuntime] {
        var query: [URLQueryItem]? = nil
        if let worldID { query = [URLQueryItem(name: "world_id", value: worldID)] }
        let envelope: ListEnvelope<PlotRuntime> = try await get("/v1/xijian/plots/runtime", query: query)
        return envelope.data
    }

    func getPlotRuntime(_ runtimeID: String) async throws -> PlotRuntime {
        try await get("/v1/xijian/plots/runtime/\(runtimeID)")
    }

    func advancePlotRuntime(_ runtimeID: String, chooseEdgeID: String?) async throws -> [String: JSONValue] {
        var body: [String: JSONValue] = [:]
        if let chooseEdgeID { body["choose_edge_id"] = .string(chooseEdgeID) }
        return try await post("/v1/xijian/plots/runtime/\(runtimeID)/advance", body: body)
    }

    func pausePlotRuntime(_ runtimeID: String) async throws -> [String: JSONValue] {
        try await post("/v1/xijian/plots/runtime/\(runtimeID)/pause")
    }

    func resumePlotRuntime(_ runtimeID: String) async throws -> [String: JSONValue] {
        try await post("/v1/xijian/plots/runtime/\(runtimeID)/resume")
    }

    func deletePlotRuntime(_ runtimeID: String) async throws {
        try await deleteVoid("/v1/xijian/plots/runtime/\(runtimeID)")
    }
}

// MARK: - 辅助类型

/// 角色创建/更新载荷
struct CharacterPayload: Encodable {
    let name: String
    let display_name: String?
    let persona_doc: String
    let voice_profile: String?
    let default_emotion: String?
    let tags: [String]
}

/// 任意 Encodable 包装（用于异构 body 字典）
struct AnyEncodable: Encodable {
    private let encodeClosure: (Encoder) throws -> Void

    init(_ wrapped: any Encodable) {
        self.encodeClosure = wrapped.encode(to:)
    }

    func encode(to encoder: Encoder) throws {
        try encodeClosure(encoder)
    }
}
