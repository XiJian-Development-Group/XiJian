import SwiftUI

extension Color {
    static let xiJianAccent = Color("AccentColor")

    static let xiJianPurple = Color(red: 0.45, green: 0.35, blue: 0.75)
    static let xiJianPurpleLight = Color(red: 0.60, green: 0.50, blue: 0.90)
    static let xiJianPurpleDark = Color(red: 0.30, green: 0.20, blue: 0.55)

    static let xiJianBackground = Color(red: 0.97, green: 0.97, blue: 0.98)
    static let xiJianBackgroundDark = Color(red: 0.12, green: 0.12, blue: 0.15)

    static let xiJianUserBubble = Color(red: 0.45, green: 0.35, blue: 0.75).opacity(0.15)
    static let xiJianAssistantBubble = Color(red: 0.95, green: 0.95, blue: 0.97)
    static let xiJianAssistantBubbleDark = Color(red: 0.18, green: 0.18, blue: 0.22)

    static let xiJianText = Color(red: 0.15, green: 0.15, blue: 0.18)
    static let xiJianTextDark = Color(red: 0.92, green: 0.92, blue: 0.95)

    static let xiJianGreen = Color(red: 0.30, green: 0.75, blue: 0.45)
    static let xiJianRed = Color(red: 0.85, green: 0.30, blue: 0.30)
    static let xiJianOrange = Color(red: 0.90, green: 0.55, blue: 0.20)
}

extension ShapeStyle where Self == Color {
    static var xiJianPurple: Color { .xiJianPurple }
    static var xiJianPurpleLight: Color { .xiJianPurpleLight }
    static var xiJianGreen: Color { .xiJianGreen }
    static var xiJianRed: Color { .xiJianRed }
}
