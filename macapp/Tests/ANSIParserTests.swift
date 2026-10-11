import XCTest
@testable import XiJianManagerKit

final class ANSIParserTests: XCTestCase {

    func test_parse_plainText() {
        var parser = ANSIParser()
        let tokens = parser.parse("hello world")
        XCTAssertEqual(tokens, [.text("hello world")])
    }

    func test_parse_simpleColorRed() {
        var parser = ANSIParser()
        let tokens = parser.parse("\u{1B}[31mhello\u{1B}[0m".data(using: .utf8)!)
        XCTAssertEqual(tokens.count, 3)
        XCTAssertEqual(tokens[0], .sgr([.fgColor(ansi: 31)]))
        XCTAssertEqual(tokens[1], .text("hello"))
        XCTAssertEqual(tokens[2], .sgr([.reset]))
    }

    func test_parse_cursorMove() {
        var parser = ANSIParser()
        let tokens = parser.parse("\u{1B}[10;20H".data(using: .utf8)!)
        XCTAssertEqual(tokens, [.cursorMove(row: 10, col: 20)])
    }

    func test_parse_clearScreen() {
        var parser = ANSIParser()
        let tokens = parser.parse("\u{1B}[2J".data(using: .utf8)!)
        XCTAssertEqual(tokens, [.clearScreen])
    }

    func test_parse_mixedTextAndEscape() {
        var parser = ANSIParser()
        // 输入 "hi " + ESC[1m + "bold" + ESC[0m + " end"
        let tokens = parser.parse("hi \u{1B}[1mbold\u{1B}[0m end".data(using: .utf8)!)
        // 期望：[text("hi "), sgr([bold]), text("bold"), sgr([reset]), text(" end")]
        XCTAssertEqual(tokens.count, 5)
        XCTAssertEqual(tokens[0], .text("hi "))
        XCTAssertEqual(tokens[1], .sgr([.bold]))
        XCTAssertEqual(tokens[2], .text("bold"))
        XCTAssertEqual(tokens[3], .sgr([.reset]))
        XCTAssertEqual(tokens[4], .text(" end"))
    }

    func test_parse_incrementalChunks() {
        var parser = ANSIParser()
        let tokens1 = parser.parse("\u{1B}[3".data(using: .utf8)!)
        XCTAssertEqual(tokens1.count, 0, "不完整 CSI 序列应保留在 buffer")
        let tokens2 = parser.parse("1mOK".data(using: .utf8)!)
        XCTAssertEqual(tokens2.count, 2)
        XCTAssertEqual(tokens2[0], .sgr([.fgColor(ansi: 31)]))
        XCTAssertEqual(tokens2[1], .text("OK"))
    }
}
