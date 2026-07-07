import SwiftUI

struct StatusIndicatorView: View {
    let state: CoreState

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 10, height: 10)
            .overlay(
                Circle()
                    .stroke(Color.white.opacity(0.3), lineWidth: 1)
            )
            .shadow(color: color.opacity(0.4), radius: 3)
    }

    private var color: Color {
        switch state {
        case .stopped: return .xiJianRed
        case .starting, .extracting: return .xiJianOrange
        case .running: return .xiJianGreen
        case .error: return .xiJianRed
        }
    }
}
