import XCTest
@testable import XiJianKit

// MARK: - sendAudio 请求构造测试（MockURLProtocol，无真实网络）

/// 验证 `VoiceCallService.sendAudio` 的请求体：`audio_base64` 可解码回原始音频字节，
/// `language` 可选；复用与文本路径相同的 speech 端点与错误信封。
final class VoiceCallAudioServiceTests: XCTestCase {

    private var service: VoiceCallService!
    private var session: URLSession!

    override func setUp() {
        super.setUp()
        session = makeMockSession()
        service = VoiceCallService(
            baseURL: URL(string: "http://127.0.0.1:18500")!,
            token: "test-token",
            session: session
        )
    }

    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        service = nil
        session = nil
        super.tearDown()
    }

    func testSendAudioBodyBase64RoundTrip() async throws {
        // 任意二进制音频字节（含非 UTF-8 值，验证 base64 编码无损）
        let original = Data([0x52, 0x49, 0x46, 0x46, 0x00, 0x01, 0x02, 0xFE, 0xFF, 0x80, 0x7F])
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            let body = #"{"ok":true,"turn":2,"user_text":"你好","reply":"","interrupted_previous":false,"user_event_id":"evt_u2","reply_event_id":null,"synchronous":false}"#
            return (200, Data(body.utf8), ["Content-Type": "application/json"])
        }

        let result = try await service.sendAudio(callId: "call_1", audioData: original, language: "zh")

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/v1/xijian/voice-calls/call_1/speech")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")

        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(captureRequestBody(request))) as? [String: Any])
        let b64 = try XCTUnwrap(obj["audio_base64"] as? String)
        XCTAssertEqual(Data(base64Encoded: b64), original, "audio_base64 应可解码回原始音频字节")
        XCTAssertEqual(obj["language"] as? String, "zh")
        XCTAssertNil(obj["text"], "音频路径不应携带 text")

        XCTAssertTrue(result.ok)
        XCTAssertEqual(result.turn, 2)
        XCTAssertEqual(result.user_text, "你好")
    }

    func testSendAudioWithoutLanguageOmitsKey() async throws {
        let original = Data("wav-payload".utf8)
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            let body = #"{"ok":true,"turn":1,"user_text":"hi","reply":"hello","interrupted_previous":false,"user_event_id":null,"reply_event_id":null,"synchronous":true}"#
            return (200, Data(body.utf8), ["Content-Type": "application/json"])
        }

        let result = try await service.sendAudio(callId: "call_1", audioData: original, language: nil)

        let request = try XCTUnwrap(captured)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(captureRequestBody(request))) as? [String: Any])
        XCTAssertEqual(Data(base64Encoded: try XCTUnwrap(obj["audio_base64"] as? String)), original)
        XCTAssertNil(obj["language"], "language 为 nil 时不应出现在请求体")

        XCTAssertTrue(result.ok)
        XCTAssertEqual(result.reply, "hello")
    }

    func testSendAudioSTTUnavailableSurfacesHTTPError() async throws {
        // 服务端 STT 后端不可用：503 + {"ok":false,"error":"..."} → 抛 APIError.httpStatus
        MockURLProtocol.requestHandler = { _ in
            let body = #"{"ok":false,"error":"STT backend unavailable: no whisper engine wired","turn":null}"#
            return (503, Data(body.utf8), ["Content-Type": "application/json"])
        }

        do {
            _ = try await service.sendAudio(callId: "call_1", audioData: Data([0x01, 0x02]), language: "zh")
            XCTFail("STT 不可用应抛 APIError")
        } catch let error as APIError {
            guard case .httpStatus(let code, let detail) = error else {
                XCTFail("错误类型不符：\(error)")
                return
            }
            XCTAssertEqual(code, 503)
            XCTAssertEqual(detail, "STT backend unavailable: no whisper engine wired")
        }
    }
}

// MARK: - speech 事件 payload → 音频 Data 解析（纯函数）

final class VoiceCallAudioPayloadTests: XCTestCase {

    func testAudioDataFromPayloadDecodesBase64() {
        let original = Data([0x52, 0x49, 0x46, 0x46, 0x00, 0xFF])  // "RIFF" 头
        let payload: [String: JSONValue] = [
            "role": .string("assistant"),
            "text": .string("你好"),
            "audio_base64": .string(original.base64EncodedString()),
            "audio_size_bytes": .number(Double(original.count)),
        ]
        XCTAssertEqual(VoiceCallAudioPayload.audioData(from: payload), original)
    }

    func testAudioDataMissingOrInvalidReturnsNil() {
        // 缺失 / 空字符串 / 非法 base64 / 非字符串
        XCTAssertNil(VoiceCallAudioPayload.audioData(from: ["role": .string("assistant")]))
        XCTAssertNil(VoiceCallAudioPayload.audioData(from: ["audio_base64": .string("")]))
        XCTAssertNil(VoiceCallAudioPayload.audioData(from: ["audio_base64": .string("not base64!!")]))
        XCTAssertNil(VoiceCallAudioPayload.audioData(from: ["audio_base64": .number(1)]))
        XCTAssertNil(VoiceCallAudioPayload.audioData(from: [:]))
    }
}
