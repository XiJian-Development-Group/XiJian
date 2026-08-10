import SwiftUI
import XiJianKit
import MarkdownUI

/// 聊天气泡（Markdown 渲染、流式打字效果）
struct MessageBubbleView: View {
    let message: ChatMessage
    var isStreaming: Bool = false

    @Environment(ThemeSettings.self) private var theme

    private var isUser: Bool { message.isUser }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            if isUser { Spacer(minLength: 60) }

            VStack(alignment: isUser ? .trailing : .leading, spacing: 3) {
                if theme.showTimestamps, let ts = message.created_at {
                    Text(ts.xijianDate.xijianTimeText)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }

                content
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(background)
                    .clipShape(RoundedRectangle(cornerRadius: theme.cornerRadius, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: theme.cornerRadius, style: .continuous)
                            .strokeBorder(strokeColor, lineWidth: theme.bubbleStyle == .outlined ? 1 : 0)
                    )
                    // Apple 风格：用户气泡带轻投影增加层次（助手气泡不投影，保持扁平）
                    .shadow(color: isUser ? Color.black.opacity(0.08) : .clear, radius: 6, y: 3)
                    .transition(.asymmetric(
                        insertion: .opacity.combined(with: .offset(y: 8)),
                        removal: .opacity
                    ))
            }

            if !isUser { Spacer(minLength: 60) }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 3)
    }

    private var strokeColor: Color {
        isUser ? theme.accentColor.opacity(0.6) : Color.secondary.opacity(0.2)
    }

    private var background: Color {
        if isUser {
            return theme.userBubbleColor
        } else {
            return theme.assistantBubbleColor
        }
    }

    @ViewBuilder
    private var content: some View {
        if isUser {
            Text(message.content)
                .font(.system(size: theme.fontSize))
                .foregroundStyle(theme.userTextColor)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            if isStreaming && message.content.isEmpty {
                // 流式开始前显示打字动画
                HStack(spacing: 4) {
                    ForEach(0..<3, id: \.self) { i in
                        Circle()
                            .fill(Color.secondary.opacity(0.6))
                            .frame(width: 5, height: 5)
                            .opacity(0.4 + Double(i) * 0.3)
                    }
                }
                .padding(.vertical, 4)
            } else {
                Markdown(message.content.isEmpty ? " " : message.content)
                    .markdownTheme(.gitHub)
                    .markdownTextStyle(\.text) {
                        FontSize(theme.fontSize)
                    }
                    .markdownTextStyle(\.code) {
                        FontSize(theme.fontSize - 1)
                    }
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

/// 输入栏
struct ChatInputBar: View {
    @Binding var text: String
    var isStreaming: Bool
    var onSend: () -> Void
    var onStop: () -> Void

    @Environment(ThemeSettings.self) private var theme
    @FocusState private var focused: Bool

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextEditor(text: $text)
                .font(.system(size: max(theme.fontSize, 13)))
                .frame(minHeight: 34, maxHeight: 120)
                .padding(6)
                .background(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(Color(.textBackgroundColor))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
                )
                .focused($focused)
                .onSubmit {
                    if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isStreaming {
                        onSend()
                    }
                }

            Button(action: {
                if isStreaming {
                    onStop()
                } else {
                    onSend()
                }
            }) {
                Image(systemName: isStreaming ? "stop.fill" : "arrow.up.circle.fill")
                    .font(.system(size: 26))
                    .foregroundStyle(isStreaming ? Color.red : theme.accentColor)
            }
            .buttonStyle(.plain)
            .disabled(!isStreaming && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .help(isStreaming ? loc("停止生成") : loc("发送消息"))
        }
        .padding(10)
        .background(.bar)
    }
}
