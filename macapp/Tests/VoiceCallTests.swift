import XCTest
@testable import XiJianKit

// MARK: - 测试记录工厂

extension VoiceCallRecord {
    static func testRecord(
        id: String,
        status: VoiceCallStatus,
        characterId: String = "char_yuki",
        bargeIn: Bool = false,
        startedAt: Double? = 0
    ) -> VoiceCallRecord {
        VoiceCallRecord(
            id: id,
            character_id: characterId,
            user_id: "local_user",
            direction: "user_initiated",
            status: status.rawValue,
            started_at: startedAt,
            ended_at: nil,
            duration_sec: nil,
            recording_path: nil,
            ended_reason: nil,
            barge_in_active: bargeIn,
            tts_busy: false,
            current_turn: 0,
            dialogue_context: nil,
            created_at: 0,
            updated_at: 0
        )
    }
}

// MARK: - Mock 服务

/// 可编程的 VoiceCallServicing Mock：记录调用参数、按预设结果返回。
final class MockVoiceCallService: VoiceCallServicing {
    var createResult: Result<VoiceCallRecord, Error> = .success(.testRecord(id: "call_1", status: .idle))
    var getCallResult: Result<VoiceCallRecord, Error> = .success(.testRecord(id: "call_1", status: .active))
    var ringResult: Result<VoiceCallRecord, Error> = .success(.testRecord(id: "call_1", status: .ringing))
    var acceptResult: Result<VoiceCallRecord, Error> = .success(.testRecord(id: "call_1", status: .active))
    var rejectResult: Result<VoiceCallRecord, Error> = .success(.testRecord(id: "call_1", status: .ended))
    var endResult: Result<VoiceCallRecord, Error> = .success(.testRecord(id: "call_1", status: .ended))
    var sendSpeechResult: Result<SpeechResult, Error> = .success(SpeechResult(
        ok: true, turn: 1, user_text: "你好", reply: "你好呀！",
        interrupted_previous: false, user_event_id: nil, reply_event_id: nil,
        synchronous: true, error: nil
    ))
    var sendAudioResult: Result<SpeechResult, Error> = .success(SpeechResult(
        ok: true, turn: 1, user_text: "你好", reply: "",
        interrupted_previous: false, user_event_id: nil, reply_event_id: nil,
        synchronous: false, error: nil
    ))
    var setBargeInResult: Result<VoiceCallRecord, Error> = .success(.testRecord(id: "call_1", status: .active, bargeIn: true))
    var singResult: Result<SongResult, Error> = .success(SongResult(
        ok: false, status: "unavailable", reason: "diffsinger_engine_not_wired", message: "引擎未接入"
    ))
    var listEventsResult: Result<[VoiceCallEvent], Error> = .success([])

    private(set) var createCalls: [(characterId: String, direction: VoiceCallDirection, userId: String)] = []
    private(set) var getCallCalls: [String] = []
    private(set) var ringCalls: [String] = []
    private(set) var acceptCalls: [String] = []
    private(set) var rejectCalls: [String] = []
    private(set) var endCalls: [String] = []
    private(set) var speechCalls: [(callId: String, text: String)] = []
    private(set) var audioCalls: [(callId: String, audioData: Data, language: String?)] = []
    private(set) var bargeInCalls: [(callId: String, active: Bool)] = []
    private(set) var singCalls: [(callId: String, lyrics: String)] = []
    private(set) var listEventsCalls: [(callId: String, limit: Int)] = []

    func createCall(characterId: String, direction: VoiceCallDirection, userId: String) async throws -> VoiceCallRecord {
        createCalls.append((characterId, direction, userId))
        return try createResult.get()
    }

    func getCall(callId: String) async throws -> VoiceCallRecord {
        getCallCalls.append(callId)
        return try getCallResult.get()
    }

    func ring(callId: String) async throws -> VoiceCallRecord {
        ringCalls.append(callId)
        return try ringResult.get()
    }

    func accept(callId: String) async throws -> VoiceCallRecord {
        acceptCalls.append(callId)
        return try acceptResult.get()
    }

    func reject(callId: String) async throws -> VoiceCallRecord {
        rejectCalls.append(callId)
        return try rejectResult.get()
    }

    func end(callId: String) async throws -> VoiceCallRecord {
        endCalls.append(callId)
        return try endResult.get()
    }

    func sendSpeech(callId: String, text: String) async throws -> SpeechResult {
        speechCalls.append((callId, text))
        return try sendSpeechResult.get()
    }

    func sendAudio(callId: String, audioData: Data, language: String?) async throws -> SpeechResult {
        audioCalls.append((callId, audioData, language))
        return try sendAudioResult.get()
    }

    func setBargeIn(callId: String, active: Bool) async throws -> VoiceCallRecord {
        bargeInCalls.append((callId, active))
        return try setBargeInResult.get()
    }

    func sing(callId: String, lyrics: String) async throws -> SongResult {
        singCalls.append((callId, lyrics))
        return try singResult.get()
    }

    func listEvents(callId: String, limit: Int) async throws -> [VoiceCallEvent] {
        listEventsCalls.append((callId, limit))
        return try listEventsResult.get()
    }
}

// MARK: - 可控 WebSocket 传输 Mock

/// 可投递入站消息的 WebSocket 传输 Mock：`receive()` 挂起等待，
/// 直到 `enqueue` 投递新消息（模拟服务端实时推送，测试可确定性驱动）。
final class VoiceCallMockTransport: WebSocketTransport {
    private var inbound: [URLSessionWebSocketTask.Message] = []
    private var waiter: CheckedContinuation<Void, Never>?
    private var hasPending = false
    private(set) var resumeCount = 0
    private(set) var cancelCount = 0
    private(set) var sentTexts: [String] = []

    func enqueue(_ message: URLSessionWebSocketTask.Message) {
        inbound.append(message)
        hasPending = true
        waiter?.resume()
        waiter = nil
    }

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

    func cancel(with closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        cancelCount += 1
    }

    func receive() async throws -> URLSessionWebSocketTask.Message {
        while true {
            if !inbound.isEmpty {
                return inbound.removeFirst()
            }
            hasPending = false
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                if hasPending {
                    continuation.resume()
                } else {
                    waiter = continuation
                }
            }
        }
    }
}

// MARK: - VoiceCallService 测试（MockURLProtocol，无真实网络）

final class VoiceCallServiceTests: XCTestCase {

    private var service: VoiceCallService!
    private var session: URLSession!

    override func setUp() {
        super.setUp()
        session = makeMockSession()
        service = VoiceCallService(baseURL: URL(string: "http://127.0.0.1:18500")!, token: "test-token", session: session)
    }

    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        service = nil
        session = nil
        super.tearDown()
    }

    // MARK: 创建

    func testCreateCallRequestAndParsing() async throws {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            let body = #"""
            {"id":"call_1","character_id":"char_yuki","user_id":"local_user","direction":"user_initiated",
             "status":"idle","started_at":1710000000,"ended_at":null,"duration_sec":null,"recording_path":null,
             "ended_reason":null,"barge_in_active":false,"tts_busy":false,"current_turn":0,
             "dialogue_context":[],"created_at":1710000000,"updated_at":1710000000}
            """#
            return (201, Data(body.utf8), ["Content-Type": "application/json"])
        }

        let record = try await service.createCall(characterId: "char_yuki")

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/v1/xijian/voice-calls")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")

        let bodyData = try XCTUnwrap(captureRequestBody(request))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: bodyData) as? [String: Any])
        XCTAssertEqual(obj["character_id"] as? String, "char_yuki")
        XCTAssertEqual(obj["direction"] as? String, "user_initiated")
        XCTAssertEqual(obj["user_id"] as? String, "local_user")

        XCTAssertEqual(record.id, "call_1")
        XCTAssertEqual(record.character_id, "char_yuki")
        XCTAssertEqual(record.statusEnum, .idle)
        XCTAssertEqual(record.directionEnum, .userInitiated)
        XCTAssertFalse(record.isBargeInActive)
    }

    // MARK: 生命周期

    func testLifecycleEndpointsPathsAndStatus() async throws {
        var paths: [String] = []
        MockURLProtocol.requestHandler = { request in
            let path = request.url?.path ?? ""
            paths.append(path)
            let status: String
            if path.hasSuffix("/ring") { status = "ringing" }
            else if path.hasSuffix("/accept") { status = "active" }
            else { status = "ended" }
            let body = #"{"id":"call_1","character_id":"char_yuki","status":"\#(status)"}"#
            return (200, Data(body.utf8), ["Content-Type": "application/json"])
        }

        _ = try await service.ring(callId: "call_1")
        let accepted = try await service.accept(callId: "call_1")
        _ = try await service.reject(callId: "call_1")
        let ended = try await service.end(callId: "call_1")

        XCTAssertEqual(paths, [
            "/v1/xijian/voice-calls/call_1/ring",
            "/v1/xijian/voice-calls/call_1/accept",
            "/v1/xijian/voice-calls/call_1/reject",
            "/v1/xijian/voice-calls/call_1/end",
        ])
        XCTAssertEqual(accepted.statusEnum, .active)
        XCTAssertEqual(ended.statusEnum, .ended)
        XCTAssertEqual(ended.ended_reason, nil)
    }

    // MARK: 通话循环

    func testSendSpeechBodyAndResponse() async throws {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            let body = #"{"ok":true,"turn":3,"user_text":"你好","reply":"你好呀！","interrupted_previous":false,"user_event_id":"evt_u","reply_event_id":null,"synchronous":false}"#
            return (200, Data(body.utf8), ["Content-Type": "application/json"])
        }

        let result = try await service.sendSpeech(callId: "call_1", text: "你好")

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.url?.path, "/v1/xijian/voice-calls/call_1/speech")
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(captureRequestBody(request))) as? [String: Any])
        XCTAssertEqual(obj["text"] as? String, "你好")

        XCTAssertTrue(result.ok)
        XCTAssertEqual(result.turn, 3)
        XCTAssertEqual(result.user_text, "你好")
        XCTAssertEqual(result.reply, "你好呀！")
        XCTAssertEqual(result.user_event_id, "evt_u")
    }

    func testSetBargeInBodyAndRecord() async throws {
        var bodies: [[String: Any]] = []
        var statusCode = 200
        MockURLProtocol.requestHandler = { request in
            bodies.append(try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(captureRequestBody(request))) as? [String: Any]))
            let active = bodies.last?["active"] as? Bool ?? false
            let body = #"{"id":"call_1","character_id":"char_yuki","status":"active","barge_in_active":\#(active)}"#
            return (statusCode, Data(body.utf8), ["Content-Type": "application/json"])
        }

        let on = try await service.setBargeIn(callId: "call_1", active: true)
        let off = try await service.setBargeIn(callId: "call_1", active: false)

        XCTAssertEqual(bodies.count, 2)
        XCTAssertEqual(bodies[0]["active"] as? Bool, true)
        XCTAssertEqual(bodies[1]["active"] as? Bool, false)
        XCTAssertTrue(on.isBargeInActive)
        XCTAssertFalse(off.isBargeInActive)
    }

    func testSingBodyAndResponse() async throws {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            let body = #"{"ok":false,"status":"unavailable","reason":"diffsinger_engine_not_wired","message":"DiffSinger 引擎未接入"}"#
            return (200, Data(body.utf8), ["Content-Type": "application/json"])
        }

        let result = try await service.sing(callId: "call_1", lyrics: "la la la")

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.url?.path, "/v1/xijian/voice-calls/call_1/song")
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(captureRequestBody(request))) as? [String: Any])
        XCTAssertEqual(obj["lyrics"] as? String, "la la la")

        XCTAssertFalse(result.ok)
        XCTAssertEqual(result.status, "unavailable")
        XCTAssertEqual(result.reason, "diffsinger_engine_not_wired")
    }

    // MARK: 事件

    func testListEventsParsing() async throws {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            let body = #"""
            {"call_id":"call_1","events":[
              {"id":"e1","call_id":"call_1","kind":"speech","payload":{"role":"user","text":"hi","turn":1},"created_at":1},
              {"id":"e2","call_id":"call_1","kind":"song","payload":{"lyrics":"la"},"created_at":2}
            ]}
            """#
            return (200, Data(body.utf8), ["Content-Type": "application/json"])
        }

        let events = try await service.listEvents(callId: "call_1", limit: 50)

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.url?.path, "/v1/xijian/voice-calls/call_1/events")
        let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
        XCTAssertEqual(components.queryItems?.first(where: { $0.name == "limit" })?.value, "50")

        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events[0].kind, "speech")
        XCTAssertEqual(events[0].payloadObject?["text"]?.stringValue, "hi")
        XCTAssertEqual(events[1].kind, "song")
        XCTAssertEqual(events[1].payloadObject?["lyrics"]?.stringValue, "la")
    }

    // MARK: 错误信封

    func testHTTPErrorEnvelopeParsing() async throws {
        MockURLProtocol.requestHandler = { _ in
            let body = #"{"error":{"message":"voice call not found","type":"not_found_error","code":"voice_call_not_found"}}"#
            return (404, Data(body.utf8), ["Content-Type": "application/json"])
        }

        do {
            _ = try await service.getCall(callId: "missing")
            XCTFail("应抛出 APIError")
        } catch let error as APIError {
            guard case .httpStatus(let code, let detail) = error else {
                XCTFail("错误类型不符：\(error)")
                return
            }
            XCTAssertEqual(code, 404)
            XCTAssertEqual(detail, "voice call not found")
            XCTAssertTrue(error.message.contains("404"))
        }
    }
}

// MARK: - VoiceCallViewModel 测试（Mock 服务 + Mock WS 传输）

@MainActor
final class VoiceCallViewModelTests: XCTestCase {

    private var mockService: MockVoiceCallService!
    private var mockTransport: VoiceCallMockTransport!
    private var ws: WebSocketClient!
    private var viewModel: VoiceCallViewModel!

    override func setUp() {
        super.setUp()
        mockService = MockVoiceCallService()
        mockTransport = VoiceCallMockTransport()
        ws = WebSocketClient(
            baseURL: URL(string: "http://127.0.0.1:18500")!,
            token: "test-token",
            transportFactory: { [mockTransport] _ in mockTransport }
        )
        viewModel = VoiceCallViewModel(service: mockService, ws: ws)
    }

    override func tearDown() {
        viewModel.close()
        ws.disconnect()
        viewModel = nil
        ws = nil
        mockTransport = nil
        mockService = nil
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

    // MARK: 状态机

    func testStartCallCreatesAndRings() async {
        await viewModel.startCall(characterId: "char_yuki", characterName: "雪")

        XCTAssertEqual(viewModel.phase, .ringing)
        XCTAssertEqual(viewModel.callID, "call_1")
        XCTAssertEqual(viewModel.characterID, "char_yuki")
        XCTAssertEqual(viewModel.characterName, "雪")
        XCTAssertEqual(mockService.createCalls.count, 1)
        XCTAssertEqual(mockService.createCalls.first?.characterId, "char_yuki")
        XCTAssertEqual(mockService.createCalls.first?.direction, .userInitiated)
        XCTAssertEqual(mockService.createCalls.first?.userId, "local_user")
        XCTAssertEqual(mockService.ringCalls, ["call_1"])
        // startCall 内已建立 WS 连接
        XCTAssertEqual(ws.connectionState, .connected)
        XCTAssertTrue(viewModel.isWSConnected)
    }

    func testAcceptMovesToActive() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()

        XCTAssertEqual(viewModel.phase, .active)
        XCTAssertEqual(mockService.acceptCalls, ["call_1"])
    }

    func testRejectMovesToEnded() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.reject()

        XCTAssertEqual(viewModel.phase, .ended)
        XCTAssertEqual(mockService.rejectCalls, ["call_1"])
        XCTAssertEqual(viewModel.transcript.last?.text, loc("已拒绝通话"))
    }

    func testEndMovesToEnded() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        await viewModel.end()

        XCTAssertEqual(viewModel.phase, .ended)
        XCTAssertEqual(mockService.endCalls, ["call_1"])
        XCTAssertEqual(viewModel.transcript.last?.text, loc("通话已结束"))
    }

    func testSendTextAppendsUserAndReply() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        await viewModel.sendText("你好")

        XCTAssertEqual(viewModel.currentTurn, 1)
        XCTAssertEqual(viewModel.transcript.count, 2)
        XCTAssertTrue(viewModel.transcript[0].isUser)
        XCTAssertEqual(viewModel.transcript[0].text, "你好")
        XCTAssertTrue(viewModel.transcript[1].isAssistant)
        XCTAssertEqual(viewModel.transcript[1].text, "你好呀！")
        XCTAssertEqual(mockService.speechCalls.first?.callId, "call_1")
        XCTAssertEqual(mockService.speechCalls.first?.text, "你好")
    }

    func testSendTextAsyncShowsPendingLine() async {
        mockService.sendSpeechResult = .success(SpeechResult(
            ok: true, turn: 1, user_text: "你好", reply: "",
            interrupted_previous: false, user_event_id: "evt_u", reply_event_id: nil,
            synchronous: false, error: nil
        ))
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        await viewModel.sendText("你好")

        XCTAssertEqual(viewModel.transcript.count, 2)
        XCTAssertTrue(viewModel.transcript[1].isSystem)
        XCTAssertEqual(viewModel.transcript[1].text, loc("回复生成中…"))
    }

    func testSendTextIgnoredWhenNotActive() async {
        await viewModel.startCall(characterId: "char_yuki")  // ringing
        await viewModel.sendText("你好")

        XCTAssertTrue(mockService.speechCalls.isEmpty)
        XCTAssertTrue(viewModel.transcript.isEmpty)
    }

    func testToggleBargeIn() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        await viewModel.toggleBargeIn()

        XCTAssertTrue(viewModel.bargeInActive)
        XCTAssertEqual(mockService.bargeInCalls.first?.callId, "call_1")
        XCTAssertEqual(mockService.bargeInCalls.first?.active, true)
    }

    func testSingRecordsSongEntryWithoutError() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        await viewModel.sing(lyrics: "la la la")

        XCTAssertEqual(mockService.singCalls.first?.callId, "call_1")
        XCTAssertEqual(mockService.singCalls.first?.lyrics, "la la la")
        let song = viewModel.transcript.last
        XCTAssertEqual(song?.kind, "song")
        XCTAssertEqual(song?.text, "🎵 la la la")
        XCTAssertEqual(song?.meta, "unavailable：diffsinger_engine_not_wired")
        XCTAssertFalse(viewModel.showError, "歌唱不可用是预期状态，不应弹错")
    }

    // MARK: 错误

    func testStartCallFailureShowsErrorAndStaysIdle() async {
        mockService.createResult = .failure(APIError.httpStatus(500, "boom"))
        await viewModel.startCall(characterId: "char_yuki")

        XCTAssertEqual(viewModel.phase, .idle)
        XCTAssertNil(viewModel.callID)
        XCTAssertTrue(viewModel.showError)
        XCTAssertTrue(viewModel.errorMessage?.contains("500") == true)
        XCTAssertTrue(mockService.ringCalls.isEmpty)
    }

    // MARK: WS 事件驱动

    func testWSCallStateChangedDrivesPhase() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        XCTAssertEqual(viewModel.phase, .active)

        mockTransport.enqueue(.string(#"{"id":"evt_1","type":"call.state_changed","ts":1,"data":{"call_id":"call_1","character_id":"char_yuki","user_id":"local_user","direction":"user_initiated","status":"ended","ended_reason":"ended"}}"#))
        await waitUntil(viewModel.phase == .ended)

        XCTAssertEqual(viewModel.phase, .ended)
        XCTAssertEqual(viewModel.transcript.last?.text, loc("通话已结束"))
    }

    func testWSStateChangedForOtherCallIsIgnored() async {
        await viewModel.startCall(characterId: "char_yuki")
        XCTAssertEqual(viewModel.phase, .ringing)

        mockTransport.enqueue(.string(#"{"id":"evt_2","type":"call.state_changed","ts":1,"data":{"call_id":"call_999","status":"ended"}}"#))
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(viewModel.phase, .ringing, "其他通话的状态事件不应影响本通话")
    }

    func testWSCallEventSpeechAppendsTranscriptAndDedupes() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()

        let json = #"{"id":"evt_3","type":"call.event","ts":1,"data":{"call_id":"call_1","event_id":"evt_speech_1","kind":"speech","payload":{"role":"assistant","text":"这是AI回复","turn":1}}}"#
        mockTransport.enqueue(.string(json))
        await waitUntil(viewModel.transcript.contains { $0.isAssistant && $0.text == "这是AI回复" })

        // 重复投递同一事件：speech 按 (role, turn) 去重
        mockTransport.enqueue(.string(json))
        try? await Task.sleep(nanoseconds: 100_000_000)
        let matches = viewModel.transcript.filter { $0.isAssistant && $0.text == "这是AI回复" }
        XCTAssertEqual(matches.count, 1, "重复的 WS speech 事件不应产生重复条目")
    }

    func testWSCallEventUserSpeechDedupedWithLocalEcho() async {
        mockService.sendSpeechResult = .success(SpeechResult(
            ok: true, turn: 1, user_text: "你好", reply: "",
            interrupted_previous: false, user_event_id: "evt_u", reply_event_id: nil,
            synchronous: false, error: nil
        ))
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        await viewModel.sendText("你好")
        XCTAssertEqual(viewModel.transcript.filter { $0.isUser }.count, 1)

        // 服务端随后推送同一轮的用户 speech 事件 + AI 回复
        mockTransport.enqueue(.string(#"{"id":"evt_4","type":"call.event","ts":1,"data":{"call_id":"call_1","event_id":"evt_u1","kind":"speech","payload":{"role":"user","text":"你好","turn":1}}}"#))
        mockTransport.enqueue(.string(#"{"id":"evt_5","type":"call.event","ts":1,"data":{"call_id":"call_1","event_id":"evt_a1","kind":"speech","payload":{"role":"assistant","text":"你好呀！","turn":1}}}"#))
        await waitUntil(viewModel.transcript.contains { $0.isAssistant && $0.text == "你好呀！" })

        XCTAssertEqual(viewModel.transcript.filter { $0.isUser }.count, 1, "WS 用户回显应与本地回显去重")
        XCTAssertEqual(viewModel.transcript.filter { $0.isAssistant }.count, 1)
    }

    func testRefreshMergesServerEvents() async {
        mockService.listEventsResult = .success([
            VoiceCallEvent(
                id: "evt_x", call_id: "call_1", kind: "speech",
                payload: .object(["role": .string("assistant"), "text": .string("历史回复"), "turn": .number(1)]),
                created_at: 1
            ),
        ])
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        await viewModel.refresh()

        XCTAssertEqual(mockService.listEventsCalls.first?.callId, "call_1")
        XCTAssertTrue(viewModel.transcript.contains { $0.isAssistant && $0.text == "历史回复" })
    }

    func testCloseEndsActiveCallAndDisconnects() async {
        await viewModel.startCall(characterId: "char_yuki")
        await viewModel.accept()
        XCTAssertEqual(viewModel.phase, .active)

        viewModel.close()

        XCTAssertEqual(viewModel.phase, .idle)
        XCTAssertNil(viewModel.callID)
        XCTAssertEqual(ws.connectionState, .disconnected)
        // close 对活跃通话尽力挂断（异步 Task）
        await waitUntil(mockService.endCalls == ["call_1"])
        XCTAssertEqual(mockService.endCalls, ["call_1"])
    }
}
