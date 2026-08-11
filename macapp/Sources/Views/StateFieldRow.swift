import SwiftUI

/// 状态字段编辑行：字段名（固定）+ 按值类型渲染的输入控件。
/// 数字用 Slider（配合上限），布尔用 Toggle，其余用文本框。U2。
struct StateFieldRow: View {
    /// 字段名（显示用，不可在此编辑；改字段名请删除后重新添加）
    let key: String
    /// 当前值（决定渲染类型）
    let value: JSONValue
    /// 数字字段上限（Slider 上界；默认 100）
    var maxValue: Double = 100

    /// 编辑结果（保存时提交）
    @Binding var edited: JSONValue

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(key)
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)

            switch value {
            case .number(let number):
                HStack(spacing: 10) {
                    Slider(value: Binding(
                        get: { edited.doubleValue ?? number },
                        set: { edited = .number($0) }
                    ), in: 0...maxValue, step: 1)
                    Text(String(format: "%.0f", edited.doubleValue ?? number))
                        .font(.body.monospacedDigit())
                        .frame(width: 44, alignment: .trailing)
                }
            case .bool:
                Toggle("", isOn: Binding(
                    get: { edited.boolValue ?? value.boolValue ?? false },
                    set: { edited = .bool($0) }
                ))
                .labelsHidden()
            default:
                TextField(loc("文本值"), text: Binding(
                    get: { edited.stringValue ?? value.stringValue ?? "" },
                    set: { edited = .string($0) }
                ))
                .textFieldStyle(.roundedBorder)
            }
        }
    }
}

/// 状态字段候选：预定义常用字段 + 当前状态已有字段（去重、排序）。
/// 角色与世界共用；`extraCandidates` 提供各自领域的高频字段。U2。
enum StateFieldCandidates {
    /// 通用候选（数值型）
    static let common: [String] = [
        "intimacy", "stamina", "energy", "favorability",
    ]

    /// 角色领域候选
    static let character: [String] = [
        "hunger", "thirst", "health", "mood",
    ] + common

    /// 世界领域候选
    static let world: [String] = [
        "economy", "diet", "mentality", "population", "era",
    ] + common

    /// 合并候选与已有字段：已有字段优先保留（用户已填的值不丢），
    /// 然后按预定义顺序补候选，最后字母序排已有字段。
    static func merged(existing: [String], predefined: [String]) -> [String] {
        var seen = Set<String>()
        var result: [String] = []
        for key in predefined where !seen.contains(key) {
            seen.insert(key)
            result.append(key)
        }
        for key in existing.sorted() where !seen.contains(key) {
            seen.insert(key)
            result.append(key)
        }
        return result
    }
}
