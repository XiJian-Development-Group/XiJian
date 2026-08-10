import XCTest
@testable import XiJianKit

/// 聊天请求用户资料注入测试（user 字段 + xijian.user_profile 扩展块）
final class ChatUserProfileTests: XCTestCase {

    private var client: APIClient!
    private var session: URLSession!

    override func setUp() {
        super.setUp()
        session = makeMockSession()
        client = APIClient(baseURL: URL(string: "http://127.0.0.1:18500")!, token: "test-token", session: session)
    }

    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        super.tearDown()
    }

    /// 捕获流式请求 body 并解析为 [String: Any]
    private func capturedBodyJSON(request: APIClient.ChatRequest) async throws -> [String: Any] {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            return (200, Data("{}".utf8), ["Content-Type": "application/json"])
        }
        await awaitClient(request)
        let urlRequest = try XCTUnwrap(captured)
        let body = try XCTUnwrap(captureRequestBody(urlRequest))
        return try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
    }

    private func awaitClient(_ request: APIClient.ChatRequest) async {
        _ = try? await client.streamChat(request: request).first(where: { _ in true })
    }

    // MARK: - 注入行为

    func testUserNameInjectedIntoUserField() async throws {
        let request = APIClient.ChatRequest(
            model: "m",
            messages: [ChatMessage(role: "user", content: "hi")],
            userName: "星空喵"
        )
        let json = try await capturedBodyJSON(request: request)
        XCTAssertEqual(json["user"] as? String, "星空喵", "用户名应写入 OAI user 字段")
    }

    func testNilUserNameOmitsUserField() async throws {
        let request = APIClient.ChatRequest(
            model: "m",
            messages: [ChatMessage(role: "user", content: "hi")],
            userName: nil
        )
        let json = try await capturedBodyJSON(request: request)
        XCTAssertNil(json["user"], "用户名为空时不应发送 user 字段")
    }

    func testEmptyUserNameOmitsUserField() async throws {
        let request = APIClient.ChatRequest(
            model: "m",
            messages: [ChatMessage(role: "user", content: "hi")],
            userName: ""
        )
        let json = try await capturedBodyJSON(request: request)
        XCTAssertNil(json["user"], "空白用户名不应发送 user 字段")
    }

    func testUserProfileInjectedIntoXijianBlock() async throws {
        let request = APIClient.ChatRequest(
            model: "m",
            messages: [ChatMessage(role: "user", content: "hi")],
            userProfile: [
                "identity": .string("来自新艾利都的绳匠"),
                "aliases": .array([.string("拉米尔"), .string("小星")]),
            ]
        )
        let json = try await capturedBodyJSON(request: request)
        let xijian = try XCTUnwrap(json["xijian"] as? [String: Any])
        let profile = try XCTUnwrap(xijian["user_profile"] as? [String: Any])
        XCTAssertEqual(profile["identity"] as? String, "来自新艾利都的绳匠")
        let aliases = try XCTUnwrap(profile["aliases"] as? [String])
        XCTAssertEqual(aliases, ["拉米尔", "小星"])
    }

    func testEmptyUserProfileOmitsBlock() async throws {
        let request = APIClient.ChatRequest(
            model: "m",
            messages: [ChatMessage(role: "user", content: "hi")],
            userProfile: [:]
        )
        let json = try await capturedBodyJSON(request: request)
        let xijian = try XCTUnwrap(json["xijian"] as? [String: Any])
        XCTAssertNil(xijian["user_profile"], "空 user_profile 不应发送扩展块")
    }

    func testNilUserProfileOmitsBlock() async throws {
        let request = APIClient.ChatRequest(
            model: "m",
            messages: [ChatMessage(role: "user", content: "hi")],
            userProfile: nil
        )
        let json = try await capturedBodyJSON(request: request)
        let xijian = try XCTUnwrap(json["xijian"] as? [String: Any])
        XCTAssertNil(xijian["user_profile"])
    }

    // MARK: - 原有字段不受影响

    func testExistingFieldsStillPresent() async throws {
        let request = APIClient.ChatRequest(
            model: "m",
            messages: [ChatMessage(role: "user", content: "hi")],
            characterID: "char_x",
            worldID: "world_y",
            userName: "星空喵",
            userProfile: ["identity": .string("id")]
        )
        let json = try await capturedBodyJSON(request: request)
        XCTAssertEqual(json["model"] as? String, "m")
        XCTAssertEqual(json["stream"] as? Bool, true)
        let xijian = try XCTUnwrap(json["xijian"] as? [String: Any])
        XCTAssertEqual(xijian["character_id"] as? String, "char_x")
        XCTAssertEqual(xijian["world_id"] as? String, "world_y")
        XCTAssertEqual(json["user"] as? String, "星空喵")
        XCTAssertNotNil(xijian["user_profile"])
    }
}
