import XCTest
@testable import XiJianKit

// MARK: - URLProtocol Mock

/// 可编程的 URLProtocol Mock：按请求路径返回预设响应
final class MockURLProtocol: URLProtocol {
    /// 请求处理器：返回 (statusCode, bodyData, headers)
    static var requestHandler: ((URLRequest) throws -> (Int, Data, [String: String]))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = MockURLProtocol.requestHandler else {
            fatalError("MockURLProtocol.requestHandler 未设置")
        }
        do {
            let (statusCode, body, headers) = try handler(request)
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: statusCode,
                httpVersion: "HTTP/1.1",
                headerFields: headers
            )!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

/// 为测试创建带 Mock 的 URLSession
func makeMockSession() -> URLSession {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [MockURLProtocol.self]
    return URLSession(configuration: config)
}

/// 读取请求体（URLSession 会把 httpBody 转为流，需从 httpBodyStream 读取）
func captureRequestBody(_ request: URLRequest) -> Data? {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { return nil }
    stream.open()
    defer { stream.close() }
    var data = Data()
    let bufferSize = 4096
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
    defer { buffer.deallocate() }
    while stream.hasBytesAvailable {
        let read = stream.read(buffer, maxLength: bufferSize)
        if read <= 0 { break }
        data.append(buffer, count: read)
    }
    return data
}

// MARK: - 测试

final class APIClientTests: XCTestCase {

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

    // MARK: - 请求构造

    func testAllRequestsCarryBearerToken() async throws {
        var captured: [URLRequest] = []
        MockURLProtocol.requestHandler = { request in
            captured.append(request)
            let body: String
            switch request.url?.path {
            case "/v1/models":
                body = #"{"object":"list","data":[],"has_more":false}"#
            default:
                body = #"{"object":"list","data":[],"has_more":false}"#
            }
            return (200, Data(body.utf8), ["Content-Type": "application/json"])
        }

        _ = try await client.listModels()
        _ = try await client.listCharacters()
        _ = try await client.listWorlds()

        XCTAssertEqual(captured.count, 3)
        for request in captured {
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token",
                           "所有请求都应携带 Bearer token")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        }
    }

    func testChatRequestPathAndBody() async throws {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            return (200, Data("{}".utf8), ["Content-Type": "application/json"])
        }

        let request = APIClient.ChatRequest(
            model: "test-model",
            messages: [ChatMessage(role: "user", content: "你好")],
            characterID: "char_yuki",
            worldID: "world_1",
            recallEnabled: true,
            requestID: "req-123"
        )
        _ = try await client.streamChat(request: request).first(where: { _ in true })

        let urlRequest = try XCTUnwrap(captured)
        XCTAssertEqual(urlRequest.url?.path, "/v1/chat/completions")
        XCTAssertEqual(urlRequest.httpMethod, "POST")
        XCTAssertEqual(urlRequest.value(forHTTPHeaderField: "X-XiJian-Request-Id"), "req-123")
        XCTAssertEqual(urlRequest.value(forHTTPHeaderField: "Accept"), "text/event-stream")

        let body = try XCTUnwrap(captureRequestBody(urlRequest))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["model"] as? String, "test-model")
        XCTAssertEqual(json["stream"] as? Bool, true)
        let messages = try XCTUnwrap(json["messages"] as? [[String: String]])
        XCTAssertEqual(messages.first?["content"], "你好")
        let xijian = try XCTUnwrap(json["xijian"] as? [String: Any])
        XCTAssertEqual(xijian["character_id"] as? String, "char_yuki")
        XCTAssertEqual(xijian["world_id"] as? String, "world_1")
    }

    // MARK: - 错误映射

    func testHTTPErrorMapsToChineseMessage() async {
        MockURLProtocol.requestHandler = { _ in
            let body = #"{"error": {"message": "模型未加载", "type": "server_error", "code": "backend_unavailable"}}"#
            return (503, Data(body.utf8), ["Content-Type": "application/json"])
        }

        do {
            let _: [ModelInfo] = try await client.get("/v1/models")
            XCTFail("应抛出错误")
        } catch let error as APIError {
            guard case .httpStatus(let code, let detail) = error else {
                XCTFail("应为 httpStatus 错误，实际：\(error)")
                return
            }
            XCTAssertEqual(code, 503)
            XCTAssertTrue(detail.contains("模型未加载"), "应包含服务端错误信息，实际：\(detail)")
            XCTAssertTrue(error.message.contains("服务不可用"), "中文错误信息应包含状态描述，实际：\(error.message)")
        } catch {
            XCTFail("意外错误类型：\(error)")
        }
    }

    func test401MapsToAuthError() async {
        MockURLProtocol.requestHandler = { _ in
            (401, Data(#"{"error":{"message":"invalid token"}}"#.utf8), ["Content-Type": "application/json"])
        }
        do {
            let _: [ModelInfo] = try await client.get("/v1/models")
            XCTFail("应抛出错误")
        } catch let error as APIError {
            XCTAssertTrue(error.message.contains("鉴权失败"), "实际：\(error.message)")
        } catch {
            XCTFail("意外错误类型：\(error)")
        }
    }

    // MARK: - SSE 解析

    func testParseSSELineChunk() {
        let line = #"data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}"#
        guard let event = APIClient.parseSSELine(line) else {
            XCTFail("应解析出 chunk 事件")
            return
        }
        guard case .chunk(let chunk) = event else {
            XCTFail("应为 chunk 事件")
            return
        }
        XCTAssertEqual(chunk.deltaContent, "你好")
        XCTAssertEqual(chunk.id, "chatcmpl-1")
    }

    func testParseSSELineDone() {
        guard let event = APIClient.parseSSELine("data: [DONE]") else {
            XCTFail("应解析出 done 事件")
            return
        }
        guard case .done = event else {
            XCTFail("应为 done 事件")
            return
        }
    }

    func testParseSSELineAbort() {
        guard let event = APIClient.parseSSELine("event: abort") else {
            XCTFail("应解析出 abort 事件")
            return
        }
        guard case .aborted = event else {
            XCTFail("应为 aborted 事件")
            return
        }
    }

    func testParseSSELineNonDataIgnored() {
        XCTAssertNil(APIClient.parseSSELine(": comment"))
        XCTAssertNil(APIClient.parseSSELine(""))
    }

    /// 完整 SSE 流：多块 + [DONE]
    func testStreamChatParsesFullSSE() async throws {
        let sseBody = """
        data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

        data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}

        data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"呀"},"finish_reason":null}]}

        data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

        data: [DONE]

        """
        MockURLProtocol.requestHandler = { _ in
            (200, Data(sseBody.utf8), ["Content-Type": "text/event-stream"])
        }

        let request = APIClient.ChatRequest(model: "m", messages: [ChatMessage(role: "user", content: "hi")])
        var text = ""
        var finished = false
        var aborted = false

        for try await event in client.streamChat(request: request) {
            switch event {
            case .chunk(let chunk):
                text += chunk.deltaContent
                if chunk.finishReason == "stop" { finished = true }
            case .done:
                finished = true
            case .aborted:
                aborted = true
            }
        }

        XCTAssertEqual(text, "你好呀")
        XCTAssertTrue(finished, "应收到完成信号")
        XCTAssertFalse(aborted)
    }

    // MARK: - 其他端点

    func testListModelsDecodes() async throws {
        let json = """
        {"object":"list","data":[{"id":"qwen2.5-7b-mlx-4bit","object":"model","created":1718000000,"owned_by":"xijian","xijian":{"backend":"mlx","family":"qwen2.5","size_b":7.0,"quant":"4bit","context_length":32768,"loaded":true}}],"has_more":false}
        """
        MockURLProtocol.requestHandler = { _ in (200, Data(json.utf8), ["Content-Type": "application/json"]) }

        let models = try await client.listModels()
        XCTAssertEqual(models.count, 1)
        XCTAssertEqual(models.first?.id, "qwen2.5-7b-mlx-4bit")
        XCTAssertEqual(models.first?.xijian?.backend, "mlx")
        XCTAssertEqual(models.first?.xijian?.loaded, true)
    }

    func testHealthReturnsTrueOn200() async throws {
        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.url?.path, "/healthz")
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"), "healthz 不应需要鉴权")
            return (200, Data("ok".utf8), [:])
        }
        let result = try await client.health()
        XCTAssertTrue(result)
    }

    func testHealthReturnsFalseOnError() async throws {
        MockURLProtocol.requestHandler = { _ in
            (500, Data("error".utf8), [:])
        }
        let result = try await client.health()
        XCTAssertFalse(result)
    }

    // MARK: - 资源包

    func testListPacksPath() async throws {
        let json = """
        [{"package_id":"p1","kind":"character","name":"包1","version":"1.0.0","path":"/packs/p1","loaded":true,
          "manifest":{"name":"包1","version":"1.0.0","kind":"character"}}]
        """
        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.url?.path, "/v1/xijian/packs")
            XCTAssertEqual(request.httpMethod, "GET")
            return (200, Data(json.utf8), ["Content-Type": "application/json"])
        }
        let packs = try await client.listPacks()
        XCTAssertEqual(packs.count, 1)
        XCTAssertEqual(packs.first?.package_id, "p1")
        XCTAssertEqual(packs.first?.displayKind, "角色")
        XCTAssertTrue(packs.first?.loaded == true)
    }

    func testImportResourcePostsBody() async throws {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            // 真实 Core 的 202 响应只返回 job_id
            return (202, Data(#"{"job_id":"imp_1","status":"queued"}"#.utf8), ["Content-Type": "application/json"])
        }

        let job = try await client.importResource(name: "test.7z", kind: "mixed", path: "/tmp/test.7z")

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.url?.path, "/v1/xijian/resources/import")
        XCTAssertEqual(request.httpMethod, "POST")
        let body = try XCTUnwrap(captureRequestBody(request))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["name"] as? String, "test.7z")
        XCTAssertEqual(json["kind"] as? String, "mixed")
        XCTAssertEqual(json["path"] as? String, "/tmp/test.7z")
        // job_id 应兜底解码为 id
        XCTAssertEqual(job.id, "imp_1")
        XCTAssertTrue(job.isRunning)
    }

    func testGetImportJobPath() async throws {
        let json = """
        {"id":"imp_1","object":"resource.import","status":"running","name":"x","created_at":1}
        """
        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.url?.path, "/v1/xijian/resources/imports/imp_1")
            XCTAssertEqual(request.httpMethod, "GET")
            return (200, Data(json.utf8), ["Content-Type": "application/json"])
        }
        let job = try await client.getImportJob("imp_1")
        XCTAssertEqual(job.id, "imp_1")
        XCTAssertTrue(job.isRunning)
    }

    func testUninstallPackDeletePath() async throws {
        let json = """
        {"package_id":"p1","kind":"character","name":"包1","version":"1.0.0","path":"/packs/p1","loaded":false,
         "manifest":{"name":"包1","version":"1.0.0","kind":"character"}}
        """
        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.url?.path, "/v1/xijian/packs/p1")
            XCTAssertEqual(request.httpMethod, "DELETE")
            return (200, Data(json.utf8), ["Content-Type": "application/json"])
        }
        let pack = try await client.uninstallPack("p1")
        XCTAssertEqual(pack.package_id, "p1")
    }

    func testRescanPacksPostsPath() async throws {
        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.url?.path, "/v1/xijian/packs/rescan")
            XCTAssertEqual(request.httpMethod, "POST")
            return (200, Data(#"{"rescanned":2,"packs":["p1","p2"]}"#.utf8), ["Content-Type": "application/json"])
        }
        let result = try await client.rescanPacks()
        XCTAssertEqual(result["rescanned"]?.doubleValue, 2)
        XCTAssertEqual(result["packs"]?.stringValue, nil)
    }

    func testInstallPackPostsBodyWithPath() async throws {
        var captured: URLRequest?
        MockURLProtocol.requestHandler = { request in
            captured = request
            return (200, Data(#"{"package_id":"p1","kind":"mixed","name":"包1","version":"1.0.0","path":"/packs/p1","loaded":true,"manifest":{"name":"包1","version":"1.0.0"}}"#.utf8), ["Content-Type": "application/json"])
        }

        let pack = try await client.installPack(path: "/tmp/test.7z")

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.url?.path, "/v1/xijian/packs/install")
        XCTAssertEqual(request.httpMethod, "POST")
        let body = try XCTUnwrap(captureRequestBody(request))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["path"] as? String, "/tmp/test.7z")
        XCTAssertEqual(pack.package_id, "p1")
    }
}
