import SwiftUI
import MarkdownUI

struct MessageBubbleView: View {
    let message: Message
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if message.role == .user {
                Spacer(minLength: 60)
            } else {
                Image(systemName: message.role == .assistant ? "brain.head.profile" : "gearshape.2")
                    .font(.title3)
                    .foregroundStyle(message.role == .assistant ? .xiJianPurple : .secondary)
                    .frame(width: 28, height: 28)
                    .background(
                        Circle()
                            .fill(message.role == .assistant ? Color.xiJianPurple.opacity(0.1) : Color.secondary.opacity(0.1))
                    )
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(message.roleLabel)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if message.isStreaming {
                        ProgressView()
                            .scaleEffect(0.6)
                    }
                }

                if message.role == .assistant || message.role == .system {
                    Markdown(message.content)
                        .markdownTheme(.basic)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    Text(message.content)
                        .textSelection(.enabled)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(bubbleColor)
            .clipShape(RoundedRectangle(cornerRadius: 14))

            if message.role == .assistant || message.role == .system {
                Spacer(minLength: 60)
            }
        }
    }

    private var bubbleColor: Color {
        switch message.role {
        case .user:
            return .xiJianUserBubble
        case .assistant:
            return colorScheme == .dark ? .xiJianAssistantBubbleDark : .xiJianAssistantBubble
        case .system:
            return Color.secondary.opacity(0.08)
        }
    }
}
