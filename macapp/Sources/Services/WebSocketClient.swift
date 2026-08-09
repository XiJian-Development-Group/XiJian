import Combine
import Foundation

// MARK: - 连接状态

/// WebSocket 连接状态（对外暴露的最小状态机）
enum WebSocketConnectionState: Equatable {
    /// 未连接（含主动断开、认证失败、重连次数耗尽）
    case disconnected
    /// 正在连接（含退避等待重连）
    case connecting
    /// 已连接（握手成功；认证状态见 `isAuthenticated`）
    case connected
}

// MARK: - 事件信封

/// WebSocket 事件信封（服务端统一 `{id, type, ts, data}`，见 core/xijian_api/routes/ws_routes.py）。
///
/// - `id` / `ts` 由服务端生成（客户端发送的消息无此字段，因此声明为可选）。
/// - `data` 用与 APIClient 一致的 `JSONValue` 承载任意 JSON，
///   避免为每种事件类型（character.* / world.* / memory.* / safety.* 等）各建模型。
struct WebSocketEvent: Codable, Equatable, Identifiable {
    let id: String?
    let type: String
    let ts: Int?
    let data: JSONValue?

    /// data 以对象形式访问（事件 data 通常为 `{...}`）
    var dataObject: [String: JSONValue]? { data?.objectValue }
}

// MARK: - 传输抽象

/// WebSocket 传输抽象：生产环境由 `URLSessionWebSocketTask` 承担；
/// 单元测试注入 Mock 驱动接收循环（真实握手/网络无法在无宿主测试中建立）。
protocol WebSocketTransport: AnyObject {
    func resume()
    func send(_ message: URLSessionWebSocketTask.Message) async throws
    func receive() async throws -> URLSessionWebSocketTask.Message
    func cancel(with closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?)
}

extension URLSessionWebSocketTask: WebSocketTransport {}

// MARK: - 客户端

/// XiJian WebSocket 客户端 — 消费 Core `/v1/ws` 事件推送（A6 实时通话 / A7 主动聊天 / 桌宠的公共前置）。
///
/// 协议要点（与 core/xijian_api/routes/ws_routes.py 对齐）：
/// - 端点 `ws(s)://<host>:<port>/v1/ws`（由 http(s) baseURL 推导）
/// - 认证：请求头 `Sec-WebSocket-Protocol: xijian.v1, bearer.<token>`；
///   服务端认证成功发 `auth.ok`，失败发 `auth.failed` 后断开
/// - 心跳：服务端空闲 30 秒发 `ping`，客户端自动回 `pong`（data 原样回传）；
///   也可主动 `sendPing()`，服务端回 `pong`
/// - 事件信封 `{id, type, ts, data}`：`hello` / `auth.ok` 更新状态；
///   `ping` 自动回 `pong`；`pong` 忽略；其余经 `lastEvent` 与 `onEvent` 分发
/// - 客户端消息：`client.cancel_request`（`data.request_id`）取消服务端生成
///
/// 注意：`URLSessionWebSocketTask` 无握手回调，resume 后即置 `connected`；
/// 认证结果以 `auth.ok` / `auth.failed` 信封为准。非主动断开按 `reconnectDelays`
/// 退避重连（默认 1s/2s/4s，最多 `maxReconnectAttempts` 次）；主动 `disconnect()` 不重连。
/// 使用方需在退出时调用 `disconnect()` 释放连接。
@MainActor
final class WebSocketClient: ObservableObject {

    // MARK: 配置

    /// 服务端 baseURL（http/https），内部推导 ws(s) 地址
    let baseURL: URL
    /// 访问令牌（Bearer）
    let token: String
    /// 非主动断开时最多重连次数（默认 3，可配置）
    var maxReconnectAttempts: Int = 3
    /// 重连退避延迟（秒）；第 N 次重连取 `reconnectDelays[min(N, count-1)]`
    var reconnectDelays: [TimeInterval] = [1, 2, 4]
    /// 连接超时（秒）
    var connectionTimeout: TimeInterval = 15

    // MARK: 可观察状态

    /// 连接状态
    @Published private(set) var connectionState: WebSocketConnectionState = .disconnected
    /// 是否已通过服务端认证（收到 auth.ok）
    @Published private(set) var isAuthenticated = false
    /// 最近一次分发的事件（hello/auth/ping/pong 等协议消息不在此列）
    @Published private(set) var lastEvent: WebSocketEvent?
    /// 最近一次错误（中文描述；连接中断/认证失败时填充）
    @Published private(set) var lastError: String?
    /// 服务端版本（来自 hello 信封的 data.server_version）
    @Published private(set) var serverVersion: String?

    /// 事件分发回调（与 `lastEvent` 同时触发，供非 SwiftUI 消费者使用）
    var onEvent: ((WebSocketEvent) -> Void)?

    /// 推导出的 WebSocket 地址（ws(s)://host:port/v1/ws）
    var wsURL: URL? { Self.makeWebSocketURL(from: baseURL) }

    // MARK: 内部状态

    private let session: URLSession
    private let transportFactory: (URLRequest) -> WebSocketTransport
    private var transport: WebSocketTransport?
    private var receiveTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?
    private var reconnectCount = 0
    private var isUserInitiatedDisconnect = false
    private var failureHandled = false
    private var authFailed = false

    // MARK: 初始化

    /// - Parameters:
    ///   - baseURL: Core 的 http(s) baseURL（如 `http://127.0.0.1:18500`，取自 CoreManager.shared.baseURL）
    ///   - token: Bearer token（CoreManager.shared.token）
    ///   - session: URLSession（默认 .shared；测试注入）
    ///   - transportFactory: 传输工厂（默认由 session 创建 URLSessionWebSocketTask；测试注入 Mock）
    init(
        baseURL: URL,
        token: String,
        session: URLSession = .shared,
        transportFactory: ((URLRequest) -> WebSocketTransport)? = nil
    ) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
        self.transportFactory = transportFactory ?? { request in
            session.webSocketTask(with: request) as WebSocketTransport
        }
    }

    // MARK: 连接生命周期

    /// 建立连接（幂等：已连接/连接中直接返回）。主动调用会重置重连计数。
    func connect() {
        guard connectionState == .disconnected else { return }
        isUserInitiatedDisconnect = false
        authFailed = false
        failureHandled = false
        reconnectCount = 0
        reconnectTask?.cancel()
        reconnectTask = nil
        openConnection()
    }

    /// 主动断开（不触发重连）
    func disconnect() {
        isUserInitiatedDisconnect = true
        failureHandled = true
        reconnectTask?.cancel()
        reconnectTask = nil
        teardownTransport()
        connectionState = .disconnected
        isAuthenticated = false
    }

    // MARK: 发送

    /// 发送任意信封 `{type, data}`（连接未建立时静默丢弃）
    func sendEnvelope(type: String, data: JSONValue? = nil) {
        guard let payload = Self.encodeOutgoing(type: type, data: data) else { return }
        send(text: payload)
    }

    /// 主动心跳：`{"type": "ping"}`（服务端回 `pong`）
    func sendPing() {
        sendEnvelope(type: "ping")
    }

    /// 取消服务端生成请求：`{"type": "client.cancel_request", "data": {"request_id": ...}}`
    func sendCancelRequest(requestId: String) {
        sendEnvelope(type: "client.cancel_request", data: .object(["request_id": .string(requestId)]))
    }

    /// 信封认证 `{"type": "auth", "data": {"token": ...}}`。
    /// 服务端兼容形式；推荐子协议认证（构造时已带），一般无需调用。
    func sendAuth(token: String) {
        sendEnvelope(type: "auth", data: .object(["token": .string(token)]))
    }

    // MARK: 纯函数（可单测）

    /// http(s) baseURL → ws(s) WebSocket URL：保留主机与端口，路径固定为 `/v1/ws`，
    /// 丢弃 base 路径/查询/片段。非 http(s) scheme 返回 nil。
    static func makeWebSocketURL(from baseURL: URL) -> URL? {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { return nil }
        switch components.scheme?.lowercased() {
        case "http": components.scheme = "ws"
        case "https": components.scheme = "wss"
        default: return nil
        }
        components.path = "/v1/ws"
        components.query = nil
        components.fragment = nil
        return components.url
    }

    /// `Sec-WebSocket-Protocol` 请求头值：`xijian.v1, bearer.<token>`
    static func subprotocolHeaderValue(token: String) -> String {
        "xijian.v1, bearer.\(token)"
    }

    /// 解析服务端信封 JSON；无法解析时返回 nil（异常消息静默忽略）
    static func parseEnvelope(_ text: String) -> WebSocketEvent? {
        guard let data = text.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(WebSocketEvent.self, from: data)
    }

    /// 编码发送信封 `{type, data}`；`data` 为 nil 时省略该字段
    static func encodeOutgoing(type: String, data: JSONValue? = nil) -> String? {
        let envelope = WSOutgoingEnvelope(type: type, data: data)
        guard let data = try? JSONEncoder().encode(envelope) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// ping → pong 自动回复载荷：仅对 `ping` 返回其 `data`（原样回传），其余返回 nil
    static func pongPayload(for event: WebSocketEvent) -> JSONValue? {
        guard event.type == "ping" else { return nil }
        return event.data ?? .object([:])
    }

    // MARK: 内部实现

    /// 打开连接（不重置重连计数，供首次连接与退避重连复用）
    private func openConnection() {
        guard connectionState != .connected else { return }
        connectionState = .connecting
        lastError = nil
        failureHandled = false

        guard let url = Self.makeWebSocketURL(from: baseURL) else {
            lastError = "无法从 baseURL 构造 WebSocket 地址：\(baseURL.absoluteString)"
            connectionState = .disconnected
            return
        }

        var request = URLRequest(url: url)
        request.timeoutInterval = connectionTimeout
        request.setValue(Self.subprotocolHeaderValue(token: token), forHTTPHeaderField: "Sec-WebSocket-Protocol")

        let newTransport = transportFactory(request)
        transport = newTransport
        newTransport.resume()
        // URLSessionWebSocketTask 无握手回调：resume 后即视为已连接，
        // 握手/网络失败通过 receive() 抛错进入 handleTransportFailure。
        connectionState = .connected
        startReceiveLoop()
    }

    private func startReceiveLoop() {
        receiveTask?.cancel()
        receiveTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                guard let transport = self.transport else { break }
                do {
                    let message = try await transport.receive()
                    await self.handle(message)
                } catch {
                    if Task.isCancelled { break }
                    await self.handleTransportFailure(error)
                    break
                }
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) async {
        switch message {
        case .string(let text):
            process(text: text)
        case .data(let data):
            if let text = String(data: data, encoding: .utf8) {
                process(text: text)
            }
        @unknown default:
            break
        }
    }

    private func process(text: String) {
        guard let event = Self.parseEnvelope(text) else { return }
        process(event)
    }

    private func process(_ event: WebSocketEvent) {
        switch event.type {
        case "hello":
            serverVersion = event.dataObject?["server_version"]?.stringValue
        case "auth.ok":
            authFailed = false
            isAuthenticated = true
            lastError = nil
        case "auth.failed":
            isAuthenticated = false
            authFailed = true
            let reason = event.dataObject?["reason"]?.stringValue ?? "未知原因"
            lastError = "WebSocket 认证失败：\(reason)"
            closeAfterAuthFailure()
        case "ping":
            // 自动回 pong，data 原样回传
            sendEnvelope(type: "pong", data: Self.pongPayload(for: event))
        case "pong":
            break  // 心跳确认，忽略
        default:
            lastEvent = event
            onEvent?(event)
        }
    }

    /// 认证失败：服务端随后会断开，本地直接收尾且不重连
    private func closeAfterAuthFailure() {
        failureHandled = true
        teardownTransport()
        connectionState = .disconnected
    }

    /// 传输失败（receive/send 抛错）：收尾连接，非主动断开时按退避重连
    private func handleTransportFailure(_ error: Error) {
        guard !failureHandled else { return }
        failureHandled = true
        lastError = "WebSocket 连接中断：\(error.localizedDescription)"
        teardownTransport()
        connectionState = .disconnected
        isAuthenticated = false
        guard !isUserInitiatedDisconnect, !authFailed else { return }
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        guard reconnectCount < maxReconnectAttempts else {
            reconnectCount = 0
            return
        }
        let index = reconnectDelays.isEmpty ? 0 : min(reconnectCount, reconnectDelays.count - 1)
        let delay = reconnectDelays.isEmpty ? 1 : reconnectDelays[index]
        reconnectCount += 1
        connectionState = .connecting
        reconnectTask?.cancel()
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self, !Task.isCancelled, !self.isUserInitiatedDisconnect else { return }
            self.openConnection()
        }
    }

    private func teardownTransport() {
        receiveTask?.cancel()
        receiveTask = nil
        transport?.cancel(with: .goingAway, reason: nil)
        transport = nil
    }

    private func send(text: String) {
        guard let transport else { return }
        Task { [weak self, weak transport] in
            guard let transport else { return }
            do {
                try await transport.send(.string(text))
            } catch {
                await self?.handleSendFailure(error)
            }
        }
    }

    private func handleSendFailure(_ error: Error) {
        guard !isUserInitiatedDisconnect, connectionState == .connected else { return }
        handleTransportFailure(error)
    }
}

// MARK: - 发送信封

/// 客户端发送信封：`{type, data}`，`data` 为 nil 时省略
private struct WSOutgoingEnvelope: Encodable {
    let type: String
    let data: JSONValue?

    enum CodingKeys: String, CodingKey { case type, data }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(type, forKey: .type)
        try container.encodeIfPresent(data, forKey: .data)
    }
}
