import XCTest
@testable import XiJianManagerKit

/// Hermes 命令与 dashboard 端口。
///
/// 这里钉的是两件事：
/// 1. 命令表里的每一条都是 Hermes **真有**的子命令（对着 `hermes --help` 核过）；
/// 2. profile 是全局参数，得放在子命令前面。
final class HermesCommandTests: XCTestCase {

    func testProfileGoesBeforeTheSubcommand() {
        // hermes -p ops gateway run，不是 hermes gateway run -p ops。
        XCTAssertEqual(HermesCommandRunner.arguments(for: .gatewayRun, profile: "ops"),
                       ["-p", "ops", "gateway", "run"])
    }

    func testNoProfileMeansNoProfileFlag() {
        XCTAssertEqual(HermesCommandRunner.arguments(for: .gatewayStatus),
                       ["gateway", "status"])
    }

    func testExtraArgumentsGoLast() {
        XCTAssertEqual(HermesCommandRunner.arguments(for: .chat, profile: "ops",
                                                     extraArguments: ["-q", "你好", "--oneshot"]),
                       ["-p", "ops", "chat", "-q", "你好", "--oneshot"])
    }

    func testDashboardStatusIsARegularCommand() {
        XCTAssertEqual(HermesCommandRunner.arguments(for: .dashboard,
                                                     extraArguments: ["--status"]),
                       ["dashboard", "--status"])
    }

    func testDashboardStartCommandGetsAFreshPortAndDoesNotOpenABrowser() {
        XCTAssertEqual(HermesCommandRunner.dashboardStartArguments(port: 53211),
                       ["dashboard", "--port", "53211", "--host", "127.0.0.1", "--no-open"])
    }

    func testReadsThePortOutOfTheStatusOutput() {
        // 真机器上的输出长这样（hermes dashboard --status）。
        let output = """
        1 hermes dashboard/serve process(es) running:
            PID 1456 [dashboard]: /usr/bin/python3 -I -c import sys; sys.argv = ['hermes', 'dashboard', '--port', '9119', '--host', '127.0.0.1', '--no-open']
        """
        XCTAssertEqual(HermesCommandRunner.parseDashboardPort(output), 9119)
    }

    func testNoDashboardMeansNoPort() {
        XCTAssertNil(HermesCommandRunner.parseDashboardPort("No dashboard process is running."))
    }

    func testTheCommandTableOnlyHoldsRealSubcommands() {
        // 这几条以前是编出来的：hermes 没有 profile list / profile use /
        // skill install，profile 是全局参数，技能在 hermes skills 下面。
        XCTAssertEqual(HermesCommandRunner.Command.gatewayRun.rawValue, "gateway run")
        XCTAssertEqual(HermesCommandRunner.Command.cronList.rawValue, "cron list")
        XCTAssertEqual(HermesCommandRunner.Command.sessionsList.rawValue, "sessions list")
        XCTAssertEqual(HermesCommandRunner.Command.memoryStatus.rawValue, "memory status")
        XCTAssertEqual(HermesCommandRunner.Command.skillsList.rawValue, "skills list")
        XCTAssertEqual(HermesCommandRunner.Command.doctor.rawValue, "doctor")
    }
}
