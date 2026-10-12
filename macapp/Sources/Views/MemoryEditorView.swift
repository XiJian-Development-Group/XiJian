import SwiftUI

/// 记忆编辑视图（Phase 2.2 实现完整版）
struct MemoryEditorView: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "brain.head.profile")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)
            Text(xj: "记忆管理")
                .font(.title2)
            Text("Phase 2.2 实现 SOUL/MEMORY/USER.md 编辑器")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
