import XCTest
@testable import XiJianKit

/// CharacterViewModel 状态测试：MockURLProtocol 驱动 CoreManager 会话，验证
/// refreshState / adjustState / 变更日志缓存 / 错误路径。
@MainActor
final class CharacterViewModelTests: XCTestCase {

    private let characterID = "char_test"

    override func setUp() {
        super.setUp()
        CoreManager.shared.resetForTesting()
        CoreManager.shared.setRunningForTesting(port: 18500, token: "test-token")
        CoreManager.shared.clientSessionOverride = makeMockSession()
    }

    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        CoreManager.shared.resetForTesting()
        super.tearDown()
    }

    // MARK: - Fixtures

    private let characterJSON = """
    {"id":"char_test","object":"character","name":"Yuki","display_name":"Yuki","persona_doc":"温柔","loaded":true,"created_at":1718000000,"updated_at":1718000000}
    """

    private let stateJSON = """
    {"character_id":"char_test","affection":50,"mood":"neutral","recent_memory_summary":"x","updated_at":1718000000,
     "values":{"hunger":72.0,"thirst":45.0,"health":100.0,"mood":88.0},
     "max":{"hunger":100.0,"thirst":100.0,"health":100.0,"mood":100.0},
     "status":"healthy","can_dialogue":true,"active_behavior":[]}
    """

    private let logJSON = """
    {"entries":[
      {"id":"log_2","field":"health","old_value":60.0,"new_value":75.0,"reason":"manual","created_at":1718000001},
      {"id":"log_1","field":"hunger","old_value":80.0,"new_value":72.0,"reason":"tick","created_at":1718000000}
    ]}
    """

    /// 默认 handler：按路径分发状态 / 日志 / 角色
    private func installDefaultHandler() {
        MockURLProtocol.requestHandler = { [self] request in
            switch request.url?.path {
            case "/v1/xijian/characters/\(characterID)":
                return (200, Data(characterJSON.utf8), ["Content-Type": "application/json"])
            case "/v1/xijian/characters/\(characterID)/state":
                return (200, Data(stateJSON.utf8), ["Content-Type": "application/json"])
            case "/v1/xijian/characters/\(characterID)/state/log":
                return (200, Data(logJSON.utf8), ["Content-Type": "application/json"])
            default:
                return (404, Data("{}".utf8), ["Content-Type": "application/json"])
            }
        }
    }

    // MARK: - 状态加载

    func testLoadDetailLoadsStateAndEvents() async {
        installDefaultHandler()
        let vm = CharacterViewModel()
        await vm.loadDetail(characterID)

        XCTAssertEqual(vm.detail?.id, characterID)
        XCTAssertNotNil(vm.state, "应加载角色状态")
        XCTAssertEqual(vm.state?.summary?.value(for: .hunger), 72.0)
        XCTAssertEqual(vm.stateEvents.count, 2, "应缓存最近变更日志")
        XCTAssertEqual(vm.stateEvents.first?.field, "health", "日志应最新在前")
        XCTAssertFalse(vm.stateLoadFailed)
        XCTAssertFalse(vm.isRefreshingState)
    }

    func testRefreshStateReloadsStateAndLog() async {
        installDefaultHandler()
        let vm = CharacterViewModel()
        await vm.loadDetail(characterID)

        // 刷新后状态值变化（服务端返回新值）
        MockURLProtocol.requestHandler = { [self] request in
            switch request.url?.path {
            case "/v1/xijian/characters/\(characterID)/state":
                let json = """
                {"values":{"hunger":30.0,"thirst":10.0,"health":50.0,"mood":40.0},
                 "max":{"hunger":100.0},"status":"hungry","can_dialogue":true}
                """
                return (200, Data(json.utf8), ["Content-Type": "application/json"])
            case "/v1/xijian/characters/\(characterID)/state/log":
                return (200, Data(logJSON.utf8), ["Content-Type": "application/json"])
            default:
                return (404, Data("{}".utf8), ["Content-Type": "application/json"])
            }
        }

        await vm.refreshState()
        XCTAssertEqual(vm.state?.summary?.value(for: .hunger), 30.0)
        XCTAssertEqual(vm.state?.summary?.status, "hungry")
        XCTAssertFalse(vm.stateLoadFailed)
        XCTAssertFalse(vm.isRefreshingState)
    }

    func testStateLoadFailureSetsFlagAndError() async {
        MockURLProtocol.requestHandler = { [self] request in
            if request.url?.path == "/v1/xijian/characters/\(characterID)" {
                return (200, Data(characterJSON.utf8), ["Content-Type": "application/json"])
            }
            // 状态 / 日志接口 500
            return (500, Data(#"{"error":{"message":"backend down"}}"#.utf8), ["Content-Type": "application/json"])
        }
        let vm = CharacterViewModel()
        await vm.loadDetail(characterID)

        // loadDetail 内 state 用 try? 吞错（既有模式），失败置 stateLoadFailed 供视图重试
        XCTAssertTrue(vm.stateLoadFailed, "状态加载失败应置失败标记")
        XCTAssertNil(vm.state)
        XCTAssertFalse(vm.showError, "loadDetail 的状态请求为静默失败（try?），由失败标记驱动重试 UI")

        // 手动刷新失败：显式错误提示
        await vm.refreshState()
        XCTAssertTrue(vm.stateLoadFailed)
        XCTAssertTrue(vm.showError, "refreshState 失败应触发错误提示")

        // 重试成功后清除失败标记
        installDefaultHandler()
        await vm.refreshState()
        XCTAssertFalse(vm.stateLoadFailed)
        XCTAssertEqual(vm.stateEvents.count, 2)
    }

    // MARK: - 状态调节

    func testAdjustStatePostsPatchAndRefreshes() async throws {
        installDefaultHandler()
        let vm = CharacterViewModel()
        await vm.loadDetail(characterID)

        var captured: URLRequest?
        MockURLProtocol.requestHandler = { [self] request in
            switch request.url?.path {
            case "/v1/xijian/characters/\(characterID)/state":
                captured = request
                let json = """
                {"values":{"hunger":90.0,"thirst":45.0,"health":100.0,"mood":88.0},
                 "max":{"hunger":100.0},"status":"healthy","can_dialogue":true}
                """
                return (200, Data(json.utf8), ["Content-Type": "application/json"])
            case "/v1/xijian/characters/\(characterID)/state/log":
                return (200, Data(logJSON.utf8), ["Content-Type": "application/json"])
            default:
                return (404, Data("{}".utf8), ["Content-Type": "application/json"])
            }
        }

        let ok = await vm.adjustState(.hunger, to: 90)
        XCTAssertTrue(ok, "调整成功应返回 true")

        let request = try XCTUnwrap(captured)
        XCTAssertEqual(request.httpMethod, "POST", "角色状态更新使用 POST（Core 端点契约）")
        XCTAssertEqual(request.url?.path, "/v1/xijian/characters/\(characterID)/state")
        let body = try XCTUnwrap(captureRequestBody(request))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["hunger"] as? Double, 90.0, "请求体应携带被调节维度与数值")
        XCTAssertEqual(vm.state?.summary?.value(for: .hunger), 90.0, "提交后状态应更新")
    }

    func testAdjustStateFailureReturnsFalse() async {
        installDefaultHandler()
        let vm = CharacterViewModel()
        await vm.loadDetail(characterID)

        MockURLProtocol.requestHandler = { [self] request in
            switch request.url?.path {
            case "/v1/xijian/characters/\(characterID)/state":
                return (500, Data(#"{"error":{"message":"backend down"}}"#.utf8), ["Content-Type": "application/json"])
            case "/v1/xijian/characters/\(characterID)/state/log":
                return (200, Data(logJSON.utf8), ["Content-Type": "application/json"])
            default:
                return (404, Data("{}".utf8), ["Content-Type": "application/json"])
            }
        }

        let ok = await vm.adjustState(.mood, to: 10)
        XCTAssertFalse(ok, "调整失败应返回 false（视图据此不关闭弹窗）")
        XCTAssertTrue(vm.stateLoadFailed, "失败应置标记供重试")
        XCTAssertTrue(vm.showError, "失败应触发错误提示")
        XCTAssertEqual(vm.state?.summary?.value(for: .mood), 88.0, "失败时状态不应被覆盖")
    }

    func testAdjustStateWithoutDetailReturnsFalse() async {
        // 未加载详情（detail 为 nil）时直接失败，不应崩溃
        let vm = CharacterViewModel()
        let ok = await vm.adjustState(.health, to: 50)
        XCTAssertFalse(ok)
    }
}
