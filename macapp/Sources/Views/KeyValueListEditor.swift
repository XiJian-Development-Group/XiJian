import SwiftUI

/// 键值对编辑行（U3：结构化 UI 的一部分，每行一个键输入与一个值输入）
struct KeyValueRow: Identifiable {
    let id = UUID()
    var key: String
    var value: String
}

/// 结构化键值对编辑器：行列表（键 + 值 + 删除）+ 添加按钮 + k=v 文本导入区。
/// 导入的 k=v 文本实时解析，非法行标红提示，不再静默丢弃（U3）。
struct KeyValueListEditor: View {
    @Binding var rows: [KeyValueRow]
    var keyPlaceholder: String = "key"
    var valuePlaceholder: String = "value"

    @State private var importText = ""
    @State private var showImport = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if rows.isEmpty {
                Text(loc("暂无键值对。"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            ForEach($rows) { $row in
                HStack(spacing: 8) {
                    TextField(keyPlaceholder, text: $row.key)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: .infinity)
                    TextField(valuePlaceholder, text: $row.value)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: .infinity)
                    Button {
                        rows.removeAll { $0.id == row.id }
                    } label: {
                        Image(systemName: "trash")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }

            HStack(spacing: 12) {
                Button {
                    rows.append(KeyValueRow(key: "", value: ""))
                } label: {
                    Label(loc("添加键值"), systemImage: "plus.circle")
                        .font(.caption)
                }
                .buttonStyle(.plain)

                Button {
                    withAnimation { showImport.toggle() }
                } label: {
                    Label(loc("从 k=v 文本导入"), systemImage: "doc.text")
                        .font(.caption)
                }
                .buttonStyle(.plain)
            }

            if showImport {
                VStack(alignment: .leading, spacing: 6) {
                    Text(loc("每行一个 k=v，例如 location=home,time_of_day=evening 拆成两行。非法行会标红，不会被静默丢弃。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextEditor(text: $importText)
                        .font(.system(.body, design: .monospaced))
                        .frame(minHeight: 80)
                        .padding(6)
                        .background(
                            RoundedRectangle(cornerRadius: 8, style: .continuous)
                                .fill(Color(.textBackgroundColor))
                        )
                    if !invalidLines.isEmpty {
                        Text(loc("无法解析的行：%@", invalidLines.map { "\($0)" }.joined(separator: ", ")))
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                    Button(loc("解析并合并")) {
                        importKVText()
                    }
                    .buttonStyle(.bordered)
                    .disabled(importText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    /// 导入文本中无法解析的行号（1 起）
    private var invalidLines: [Int] {
        let lines = importText.components(separatedBy: .newlines)
        return lines.enumerated().compactMap { index, line in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty else { return nil }
            let kv = trimmed.split(separator: "=", maxSplits: 1)
            guard kv.count == 2, !kv[0].trimmingCharacters(in: .whitespaces).isEmpty else {
                return index + 1
            }
            return nil
        }
    }

    /// 把导入文本中合法行合并进列表（去重同 key，后值覆盖），非法行留在文本域标红
    private func importKVText() {
        let lines = importText.components(separatedBy: .newlines)
        var parsed: [(String, String)] = []
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty else { continue }
            let kv = trimmed.split(separator: "=", maxSplits: 1)
            guard kv.count == 2 else { continue }
            let key = kv[0].trimmingCharacters(in: .whitespaces)
            guard !key.isEmpty else { continue }
            parsed.append((key, kv[1].trimmingCharacters(in: .whitespaces)))
        }
        // 同 key 覆盖（后值优先），保持原有顺序
        var merged: [KeyValueRow] = rows
        for (key, value) in parsed {
            if let idx = merged.firstIndex(where: { $0.key == key }) {
                merged[idx].value = value
            } else {
                merged.append(KeyValueRow(key: key, value: value))
            }
        }
        rows = merged
        // 仅移除已成功解析的行，非法行留在文本域
        var kept: [String] = []
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty else { continue }
            let kv = trimmed.split(separator: "=", maxSplits: 1)
            let valid = kv.count == 2 && !kv[0].trimmingCharacters(in: .whitespaces).isEmpty
            if !valid { kept.append(line) }
        }
        importText = kept.joined(separator: "\n")
    }
}

/// 把键值对行列表转换为请求用的 [String: JSONValue]（数字/布尔自动识别，其余为字符串）
enum KVListParser {
    static func toJSONValue(_ value: String) -> JSONValue? {
        let trimmed = value.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        if let number = Double(trimmed) {
            return .number(number)
        }
        if trimmed == "true" { return .bool(true) }
        if trimmed == "false" { return .bool(false) }
        return .string(trimmed)
    }

    static func toJSON(_ rows: [KeyValueRow]) -> [String: JSONValue] {
        var result: [String: JSONValue] = [:]
        for row in rows {
            let key = row.key.trimmingCharacters(in: .whitespaces)
            guard !key.isEmpty else { continue }
            if let value = toJSONValue(row.value) {
                result[key] = value
            }
        }
        return result
    }

    static func fromJSON(_ dict: [String: JSONValue]) -> [KeyValueRow] {
        dict.keys.sorted().map { key in
            KeyValueRow(key: key, value: dict[key]?.stringValue ?? "")
        }
    }
}
