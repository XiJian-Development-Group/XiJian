import XCTest
@testable import XiJianKit

// MARK: - Mock 传输

/// 可编程的 WebSocket 传输 Mock：投递入站消息、记录出站文本，模拟断连与挂起。
final class MockWebSocketTransport: WebSocketTransport {
    /// 待投递的入站消息（队列，先进先出）
    var inbound: [URLSessionWebSocketTask.Message] = []
    /// 已发送的文本（按序）
    private(set) var sentTexts: [String] = []
    /// 入站队列耗尽后的行为：默认抛错模拟断连；true 则挂起保持连接
    var holdsWhenExhausted = false
    /// 每次 receive 直接抛出的错误（模拟握手后立即断连）
    var receiveError: Error?
    private(set) var resumeCount = 0
    private(set) var cancelCount = 0

    func resume() { resumeCount += 1 }

    func send(_ message: URLSessionWebSocketTask.Message) async throws {
        switch message {
        case .string(let text):
            sentTexts.append(text)
        case .data(let data):
            if let text = String(data: data, encoding: .utf8) { sentTexts.append(text) }
        @unknown default:
            break
        }
    }

    func receive() async throws -> URLSessionWebSocketTask.Message {
        if let receiveError { throw receiveError }
        if !inbound.isEmpty { return inbound.removeFirst() }
        if holdsWhenExhausted {
            // 挂起，模拟空闲连接（测试结束时随进程释放）
            while true { try? await Task.sleep(nanoseconds: 20_000_000) }
        }
        throw URLError(.networkConnectionLost)
    }

    func cancel(with closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        cancelCount += 1
    }
}

// MARK: - 测试

@MainActor
final class WebSocketClientTests: XCTestCase {

    private var client: WebSocketClient!
    private var mock: MockWebSocketTransport!

    override func setUp() {
        super.setUp()
        mock = MockWebSocketTransport()
        client = WebSocketClient(
            baseURL: URL(string: "http://127.0.0.1:18500")!,
            token: "test-token",
            transportFactory: { [mock] _ in mock }
        )
    }

    override func tearDown() {
        client?.disconnect()
        client = nil
        mock = nil
        super.tearDown()
    }

    /// 轮询等待条件成立（最长 timeout 秒）
    private func waitUntil(
        _ condition: @autoclosure () -> Bool,
        timeout: TimeInterval = 2.0,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() && Date() < deadline {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertTrue(condition(), "等待条件超时（\(timeout)s）", file: file, line: line)
    }

    // MARK: - URL 构造

    func testMakeWebSocketURLFromHTTPKeepsPort() throws {
        let url = try XCTUnwrap(WebSocketClient.makeWebSocketURL(from: URL(string: "http://127.0.0.1:18500")!))
        XCTAssertEqual(url.absoluteString, "ws://127.0.0.1:18500/v1/ws")
    }

    func testMakeWebSocketURLFromHTTPSKeepsPort() throws {
        let url = try XCTUnwrap(WebSocketClient.makeWebSocketURL(from: URL(string: "https://example.com:8443")!))
        XCTAssertEqual(url.absoluteString, "wss://example.com:8443/v1/ws")
    }

    func testMakeWebSocketURLStripsBasePath() throws {
        let url = try XCTUnwrap(WebSocketClient.makeWebSocketURL(from: URL(string: "http://127.0.0.1:18500/api/v1")!))
        XCTAssertEqual(url.absoluteString, "ws://127.0.0.1:18500/v1/ws")
    }

    func testMakeWebSocketURLRejectsUnsupportedScheme() {
        XCTAssertNil(WebSocketClient.makeWebSocketURL(from: URL(string: "ftp://127.0.0.1:21")!))
    }

    func testClientExposesDerivedWebSocketURL() throws {
        XCTAssertEqual(client.wsURL?.absoluteString, "ws://127.0.0.1:18500/v1/ws")
    }

    // MARK: - 认证子协议头

    func testSubprotocolHeaderValue() {
        XCTAssertEqual(WebSocketClient.subprotocolHeaderValue(token: "abc123"), "xijian.v1, bearer.abc123")
    }

    /// 连接时真实携带的请求：路径 /v1/ws + Sec-WebSocket-Protocol 头
    func testConnectSetsSubprotocolHeaderAndPath() async throws {
        var capturedRequest: URLRequest?
        let mock = MockWebSocketTransport()
        let capturedClient = WebSocketClient(
            baseURL: URL(string: "http://127.0.0.1:18500")!,
            token: "tok-123",
            transportFactory: { request in
                capturedRequest = request
                return mock
            }
        )
        mock.holdsWhenExhausted = true
        capturedClient.connect()

        let request = try XCTUnwrap(capturedRequest)
        XCTAssertEqual(request.url?.absoluteString, "ws://127.0.0.1:18500/v1/ws")
        XCTAssertEqual(
            request.value(forHTTPHeaderField: "Sec-WebSocket-Protocol"),
            "xijian.v1, bearer.tok-123"
        )
        capturedClient.disconnect()
    }

    // MARK: - 信封编解码

    func testParseEnvelopeWithChineseData() throws {
        let json = #"{"id":"evt_abc123","type":"character.updated","ts":1718000000,"data":{"name":"雪","message":"你好呀"}}"#
        let event = try XCTUnwrap(WebSocketClient.parseEnvelope(json))
        XCTAssertEqual(event.id, "evt_abc123")
        XCTAssertEqual(event.type, "character.updated")
        XCTAssertEqual(event.ts, 1718000000)
        XCTAssertEqual(event.dataObject?["name"]?.stringValue, "雪")
        XCTAssertEqual(event.dataObject?["message"]?.stringValue, "你好呀")
    }

    func testParseEnvelopeMissingOptionalFields() throws {
        let event = try XCTUnwrap(WebSocketClient.parseEnvelope(#"{"type":"auth.ok"}"#))
        XCTAssertEqual(event.type, "auth.ok")
        XCTAssertNil(event.id)
        XCTAssertNil(event.ts)
        XCTAssertNil(event.data)
    }

    func testParseEnvelopeInvalidJSONReturnsNil() {
        XCTAssertNil(WebSocketClient.parseEnvelope("not json"))
        XCTAssertNil(WebSocketClient.parseEnvelope(""))
    }

    func testEncodeOutgoingWithoutDataOmitsDataKey() throws {
        let json = try XCTUnwrap(WebSocketClient.encodeOutgoing(type: "ping"))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "ping")
        XCTAssertNil(obj["data"], "无 data 时应省略 data 字段")
    }

    func testEncodeOutgoingWithData() throws {
        let json = try XCTUnwrap(
            WebSocketClient.encodeOutgoing(type: "client.cancel_request", data: .object(["request_id": .string("req-1")]))
        )
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "client.cancel_request")
        let data = try XCTUnwrap(obj["data"] as? [String: String])
        XCTAssertEqual(data["request_id"], "req-1")
    }

    // MARK: - ping → pong 逻辑

    func testPongPayloadEchoesPingData() {
        let ping = WebSocketEvent(id: "evt_1", type: "ping", ts: 1, data: .object(["seq": .number(5)]))
        XCTAssertEqual(WebSocketClient.pongPayload(for: ping), .object(["seq": .number(5)]))
    }

    func testPongPayloadFallsBackToEmptyObject() {
        let ping = WebSocketEvent(id: "evt_1", type: "ping", ts: 1, data: nil)
        XCTAssertEqual(WebSocketClient.pongPayload(for: ping), .object([:]))
    }

    func testPongPayloadForNonPingIsNil() {
        let event = WebSocketEvent(id: "evt_1", type: "hello", ts: 1, data: .object([:]))
        XCTAssertNil(WebSocketClient.pongPayload(for: event))
    }

    // MARK: - 完整接收循环（Mock 传输）

    /// 收到服务端 ping 时自动回 pong，data 原样回传
    func testAutoRepliesPongOnServerPing() async throws {
        mock.inbound = [.string(#"{"id":"evt_ping1","type":"ping","ts":1718000000,"data":{"seq":5}}"#)]
        client.maxReconnectAttempts = 0

        client.connect()
        await waitUntil(!self.mock.sentTexts.isEmpty)

        XCTAssertEqual(mock.sentTexts.count, 1, "仅应发送一条 pong")
        let sent = try XCTUnwrap(mock.sentTexts.first)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(sent.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "pong")
        let data = try XCTUnwrap(obj["data"] as? [String: Int])
        XCTAssertEqual(data["seq"], 5, "pong 应原样回传 ping 的 data")
    }

    /// hello + auth.ok 后进入已认证连接态
    func testHelloAndAuthOkTransitionToConnected() async {
        mock.holdsWhenExhausted = true
        mock.inbound = [
            .string(#"{"id":"evt_h1","type":"hello","ts":1,"data":{"server_version":"0.1.0"}}"#),
            .string(#"{"id":"evt_a1","type":"auth.ok","ts":1,"data":{}}"#),
        ]

        client.connect()
        await waitUntil(self.client.isAuthenticated)

        XCTAssertEqual(client.connectionState, .connected)
        XCTAssertEqual(client.serverVersion, "0.1.0")
        XCTAssertNil(client.lastError)
    }

    /// auth.failed 后断开且不重连，reason 进入 lastError
    func testAuthFailedDisconnectsWithReason() async {
        mock.holdsWhenExhausted = true
        mock.inbound = [.string(#"{"id":"evt_f1","type":"auth.failed","ts":1,"data":{"reason":"invalid_token"}}"#)]

        client.connect()
        await waitUntil(self.client.connectionState == .disconnected)

        XCTAssertFalse(client.isAuthenticated)
        XCTAssertTrue(client.lastError?.contains("invalid_token") == true, "实际：\(client.lastError ?? "nil")")
        XCTAssertEqual(mock.resumeCount, 1, "认证失败不应重连")
    }

    /// 其他事件（character.initiated_action）经 onEvent 与 lastEvent 分发
    func testUnknownEventDispatchesToCallbackAndLastEvent() async {
        mock.holdsWhenExhausted = true
        var received: WebSocketEvent?
        client.onEvent = { received = $0 }
        mock.inbound = [.string(#"{"id":"evt_1","type":"character.initiated_action","ts":1,"data":{"action":"主动打招呼","character_id":"char_1"}}"#)]

        client.connect()
        await waitUntil(received != nil)

        XCTAssertEqual(received?.type, "character.initiated_action")
        XCTAssertEqual(received?.dataObject?["action"]?.stringValue, "主动打招呼")
        XCTAssertEqual(received?.dataObject?["character_id"]?.stringValue, "char_1")
        XCTAssertEqual(client.lastEvent?.type, "character.initiated_action")
    }

    /// 服务端 pong 应被忽略（无分发、无回复）
    func testPongIsIgnored() async {
        mock.holdsWhenExhausted = true
        mock.inbound = [.string(#"{"id":"evt_p1","type":"pong","ts":1,"data":{"seq":1}}"#)]

        client.connect()
        // 给接收循环一点处理时间
        try? await Task.sleep(nanoseconds: 150_000_000)

        XCTAssertTrue(mock.sentTexts.isEmpty, "pong 不应触发任何发送")
        XCTAssertNil(client.lastEvent)
    }

    /// 无法解析的 JSON 消息应被忽略
    func testInvalidJSONMessageIgnored() async {
        mock.holdsWhenExhausted = true
        mock.inbound = [.string("this is not json")]

        client.connect()
        try? await Task.sleep(nanoseconds: 150_000_000)

        XCTAssertNil(client.lastEvent)
        XCTAssertTrue(mock.sentTexts.isEmpty)
        XCTAssertEqual(client.connectionState, .connected)
    }

    // MARK: - 发送

    func testSendCancelRequestWireFormat() async throws {
        mock.holdsWhenExhausted = true
        client.connect()
        client.sendCancelRequest(requestId: "req-42")
        await waitUntil(!self.mock.sentTexts.isEmpty)

        let sent = try XCTUnwrap(mock.sentTexts.first)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(sent.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "client.cancel_request")
        let data = try XCTUnwrap(obj["data"] as? [String: String])
        XCTAssertEqual(data["request_id"], "req-42")
    }

    func testSendPingWireFormat() async throws {
        mock.holdsWhenExhausted = true
        client.connect()
        client.sendPing()
        await waitUntil(!self.mock.sentTexts.isEmpty)

        let sent = try XCTUnwrap(mock.sentTexts.first)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(sent.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "ping")
    }

    func testSendAuthWireFormat() async throws {
        mock.holdsWhenExhausted = true
        client.connect()
        client.sendAuth(token: "tok-9")
        await waitUntil(!self.mock.sentTexts.isEmpty)

        let sent = try XCTUnwrap(mock.sentTexts.first)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(sent.utf8)) as? [String: Any])
        XCTAssertEqual(obj["type"] as? String, "auth")
        let data = try XCTUnwrap(obj["data"] as? [String: String])
        XCTAssertEqual(data["token"], "tok-9")
    }

    // MARK: - 生命周期

    func testDisconnectStopsAndDoesNotReconnect() async {
        mock.holdsWhenExhausted = true
        client.connect()
        client.disconnect()

        XCTAssertEqual(client.connectionState, .disconnected)
        XCTAssertFalse(client.isAuthenticated)
        XCTAssertEqual(mock.cancelCount, 1, "disconnect 应取消传输")

        // 断开后再次 connect 应重新走完整流程
        client.connect()
        XCTAssertEqual(client.connectionState, .connected)
        XCTAssertEqual(mock.resumeCount, 2)
    }

    func testConnectIsIdempotentWhenConnected() async {
        mock.holdsWhenExhausted = true
        client.connect()
        client.connect()
        XCTAssertEqual(client.connectionState, .connected)
        XCTAssertEqual(mock.resumeCount, 1, "已连接时重复 connect 不应重复建连")
    }

    /// 非主动断开：按退避重连（1s/2s/4s，最多 3 次；测试用缩短的延迟）
    func testReconnectWithBackoffUpToMaxAttempts() async {
        var factoryCallCount = 0
        let reconnectingClient = WebSocketClient(
            baseURL: URL(string: "http://127.0.0.1:18500")!,
            token: "test-token",
            transportFactory: { _ in
                factoryCallCount += 1
                let failing = MockWebSocketTransport()
                failing.receiveError = URLError(.cannotConnectToHost)
                return failing
            }
        )
        reconnectingClient.maxReconnectAttempts = 3
        reconnectingClient.reconnectDelays = [0.05, 0.05, 0.05]

        reconnectingClient.connect()
        await waitUntil(factoryCallCount >= 4 && reconnectingClient.connectionState == .disconnected, timeout: 3)

        XCTAssertEqual(factoryCallCount, 4, "初次连接 + 最多 3 次退避重连")
        XCTAssertEqual(reconnectingClient.connectionState, .disconnected)
        XCTAssertTrue(reconnectingClient.lastError?.contains("连接中断") == true)
        reconnectingClient.disconnect()
    }

    /// 重连次数可配置为 0（不重连）
    func testNoReconnectWhenMaxAttemptsZero() async {
        var factoryCallCount = 0
        let reconnectingClient = WebSocketClient(
            baseURL: URL(string: "http://127.0.0.1:18500")!,
            token: "test-token",
            transportFactory: { _ in
                factoryCallCount += 1
                let failing = MockWebSocketTransport()
                failing.receiveError = URLError(.cannotConnectToHost)
                return failing
            }
        )
        reconnectingClient.maxReconnectAttempts = 0

        reconnectingClient.connect()
        await waitUntil(reconnectingClient.connectionState == .disconnected, timeout: 3)

        XCTAssertEqual(factoryCallCount, 1, "maxReconnectAttempts = 0 时不应重连")
        reconnectingClient.disconnect()
    }
}
