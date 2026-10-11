import Foundation

/// ANSI/VT100 转义序列解析器。
///
/// 基于 unicodeScalars 显式处理，避免 Character 索引歧义（Swift 6 Character
/// 的 firstIndex 行为不稳定）。
public struct ANSIParser {
    public enum SGRCode: Hashable, Sendable {
        case reset
        case bold
        case italic
        case underline
        case fgColor(ansi: Int)
        case bgColor(ansi: Int)
        case defaultFg
        case defaultBg
    }

    public enum Token: Hashable, Sendable {
        case text(String)
        case sgr([SGRCode])
        case cursorMove(row: Int, col: Int)
        case clearScreen
        case clearLine
        case bell
        case unknownEscape(String)
    }

    private var scalars: [Unicode.Scalar] = []
    private static let ESC: Unicode.Scalar = "\u{1B}"
    private static let LBRACKET: Unicode.Scalar = "["

    public init() {}

    public mutating func parse(_ data: Data) -> [Token] {
        guard let str = String(data: data, encoding: .utf8) else { return [] }
        scalars.append(contentsOf: str.unicodeScalars)
        return drainTokens()
    }

    public mutating func parse(_ string: String) -> [Token] {
        scalars.append(contentsOf: string.unicodeScalars)
        return drainTokens()
    }

    private mutating func drainTokens() -> [Token] {
        var tokens: [Token] = []
        while !scalars.isEmpty {
            // 找 CSI 起始 (ESC [)
            var csiIdx: Int? = nil
            for i in 0...scalars.count - 2 {
                if scalars[i] == Self.ESC && scalars[i + 1] == Self.LBRACKET {
                    csiIdx = i
                    break
                }
            }
            if let i = csiIdx {
                if i > 0 {
                    let text = String(String.UnicodeScalarView(scalars[0..<i]))
                    if !text.isEmpty { tokens.append(.text(text)) }
                    scalars.removeFirst(i)
                }
                // scalars 当前以 ESC [ 开头
                // 找 final byte 0x40-0x7E（从 index 2 开始）
                var finalIdx = 2
                while finalIdx < scalars.count {
                    let v = scalars[finalIdx].value
                    if v >= 0x40 && v <= 0x7E { break }
                    finalIdx += 1
                }
                if finalIdx >= scalars.count { return tokens }
                let params = String(String.UnicodeScalarView(scalars[2..<finalIdx]))
                let final = scalars[finalIdx]
                scalars.removeSubrange(0...finalIdx)
                tokens.append(contentsOf: parseCSI(params: params, final: final))
            } else {
                // 没有 CSI；处理单 ESC 或普通文本
                if let firstEsc = scalars.firstIndex(of: Self.ESC) {
                    if firstEsc > 0 {
                        let text = String(String.UnicodeScalarView(scalars[0..<firstEsc]))
                        if !text.isEmpty { tokens.append(.text(text)) }
                    }
                    if firstEsc + 1 < scalars.count {
                        scalars.removeSubrange(0...(firstEsc + 1))
                    } else {
                        scalars.removeFirst()
                    }
                } else {
                    let text = String(String.UnicodeScalarView(scalars))
                    if !text.isEmpty { tokens.append(.text(text)) }
                    scalars = []
                    return tokens
                }
            }
        }
        return tokens
    }

    private func parseCSI(params: String, final: Unicode.Scalar) -> [Token] {
        let finalStr = String(final)
        switch finalStr {
        case "m":
            let codes = parseSGR(params: params)
            return [.sgr(codes.isEmpty ? [.reset] : codes)]
        case "H", "f":
            let parts = params.split(separator: ";").map { Int($0) ?? 1 }
            let row = parts.first ?? 1
            let col = parts.count > 1 ? parts[1] : 1
            return [.cursorMove(row: row, col: col)]
        case "J":
            return [.clearScreen]
        case "K":
            return [.clearLine]
        case "G":
            return [.cursorMove(row: 1, col: Int(params) ?? 1)]
        default:
            return [.unknownEscape("[\(params)\(finalStr)")]
        }
    }

    private func parseSGR(params: String) -> [SGRCode] {
        if params.isEmpty { return [.reset] }
        let parts = params.split(separator: ";").compactMap { Int($0) }
        var codes: [SGRCode] = []
        for p in parts {
            switch p {
            case 0: codes.append(.reset)
            case 1: codes.append(.bold)
            case 3: codes.append(.italic)
            case 4: codes.append(.underline)
            case 22: codes.append(.reset)
            case 23: codes.append(.reset)
            case 24: codes.append(.reset)
            case 39: codes.append(.defaultFg)
            case 49: codes.append(.defaultBg)
            case 30, 31, 32, 33, 34, 35, 36, 37, 90, 91, 92, 93, 94, 95, 96, 97:
                codes.append(.fgColor(ansi: p))
            case 40, 41, 42, 43, 44, 45, 46, 47, 100, 101, 102, 103, 104, 105, 106, 107:
                codes.append(.bgColor(ansi: p))
            default:
                break
            }
        }
        return codes
    }
}
