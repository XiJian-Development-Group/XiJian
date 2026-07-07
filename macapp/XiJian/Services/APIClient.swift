import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case httpError(Int, String)
    case decodingFailed(String)
    case connectionRefused
    case notRunning

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "无效的服务器地址"
        case .httpError(let code, let msg): return "HTTP \(code): \(msg)"
        case .decodingFailed(let detail): return "数据解析失败: \(detail)"
        case .connectionRefused: return "无法连接到服务器"
        case .notRunning: return "核心服务未启动"
        }
    }
}

final class APIClient {
    private let session: URLSession
    private let decoder: JSONDecoder

    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 300
        self.session = URLSession(configuration: config)
        self.decoder = JSONDecoder()
    }

    // MARK: - Chat Completions (SSE stream)

    func streamChat(
        baseURL: String,
        model: String,
        messages: [[String: String]],
        onChunk: @escaping (String) -> Void
    ) -> AsyncStream<String> {
        AsyncStream { continuation in
            Task {
                do {
                    let url = URL(string: "\(baseURL)/v1/chat/completions")!
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    request.httpBody = try JSONSerialization.data(withJSONObject: [
                        "model": model,
                        "messages": messages,
                        "stream": true,
                    ])

                    let (bytes, response) = try await session.bytes(for: request)

                    guard let httpResp = response as? HTTPURLResponse else {
                        continuation.finish()
                        return
                    }

                    guard (200...299).contains(httpResp.statusCode) else {
                        continuation.finish()
                        return
                    }

                    for try await line in bytes.lines {
                        if line == "data: [DONE]" { break }
                        guard line.hasPrefix("data: ") else { continue }

                        let jsonStr = String(line.dropFirst(6))
                        guard let data = jsonStr.data(using: .utf8),
                              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                              let choices = json["choices"] as? [[String: Any]],
                              let delta = choices.first?["delta"] as? [String: Any],
                              let content = delta["content"] as? String
                        else { continue }

                        continuation.yield(content)
                    }

                    continuation.finish()
                } catch {
                    continuation.finish()
                }
            }
        }
    }

    // MARK: - Models

    func listModels(baseURL: String) async throws -> [[String: Any]] {
        let url = URL(string: "\(baseURL)/v1/models")!
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        try checkResponse(response)

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let models = json["data"] as? [[String: Any]] else {
            throw APIError.decodingFailed("无效的模型列表响应")
        }
        return models
    }

    // MARK: - Characters

    func listCharacters(baseURL: String) async throws -> [[String: Any]] {
        let url = URL(string: "\(baseURL)/v1/xijian/characters")!
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        try checkResponse(response)

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let characters = json["data"] as? [[String: Any]] ?? json["characters"] as? [[String: Any]] else {
            return []
        }
        return characters
    }

    func createCharacter(baseURL: String, name: String, persona: String) async throws -> [String: Any] {
        let url = URL(string: "\(baseURL)/v1/xijian/characters")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "name": name,
            "persona": persona,
        ])

        let (data, response) = try await session.data(for: request)
        try checkResponse(response)

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw APIError.decodingFailed("创建角色响应解析失败")
        }
        return json
    }

    func updateCharacter(baseURL: String, id: String, name: String, persona: String) async throws -> [String: Any] {
        let url = URL(string: "\(baseURL)/v1/xijian/characters/\(id)")!
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "name": name,
            "persona": persona,
        ])

        let (data, response) = try await session.data(for: request)
        try checkResponse(response)

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw APIError.decodingFailed("更新角色响应解析失败")
        }
        return json
    }

    // MARK: - Health

    func checkHealth(baseURL: String) async -> Bool {
        guard let url = URL(string: "\(baseURL)/health") else { return false }
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 5

        guard let (_, response) = try? await session.data(for: request),
              let httpResp = response as? HTTPURLResponse,
              (200...299).contains(httpResp.statusCode) else {
            return false
        }
        return true
    }

    // MARK: - Helpers

    private func checkResponse(_ response: URLResponse) throws {
        guard let httpResp = response as? HTTPURLResponse else {
            throw APIError.connectionRefused
        }
        guard (200...299).contains(httpResp.statusCode) else {
            throw APIError.httpError(httpResp.statusCode, HTTPURLResponse.localizedString(forStatusCode: httpResp.statusCode))
        }
    }
}
