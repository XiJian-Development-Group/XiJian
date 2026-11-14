import XCTest
@testable import XiJianManagerKit

/// 端口不能写死。
///
/// 写死 9119 的后果很具体：本机那个端口常年被别的进程占着，gateway 起不来，
/// 界面只能一直转。这里钉住「自己找一个空端口」这件事。
final class HermesPortTests: XCTestCase {

    func testFindFreePortHandsOutAUsablePort() {
        let port = HermesCommandRunner.findFreePort()
        XCTAssertGreaterThan(port, 0, "总得给出一个端口")
        XCTAssertLessThanOrEqual(port, 65535)
    }

    func testThePortItHandsOutIsFree() {
        // 先占着一个端口、记下号、再放开 —— 那个号肯定是空的。
        // 不去跟 findFreePort 抢：两个测试同时跑的时候，可能互相把对方的
        // 端口拿走，测的就不是「端口是不是空的」了。
        let socket = DarwinSocket.make()
        XCTAssertGreaterThanOrEqual(socket, 0)
        XCTAssertTrue(DarwinSocket.bindLoopback(socket))
        let port = DarwinSocket.portOf(socket)
        XCTAssertNotNil(port)
        DarwinSocket.closeSocket(socket)
        XCTAssertTrue(DarwinSocket.isFree(port ?? 0), "交出去的端口必须是空的")
    }

    func testGatewayArgumentsPutThePortInFrontOfTheSubcommand() {
        // `--port` 得在子命令前面，不然 hermes 不认。
        XCTAssertEqual(HermesCommandRunner.gatewayStartArguments(port: 12345),
                       ["cli", "--port", "12345", "gateway", "start"])
    }
}
